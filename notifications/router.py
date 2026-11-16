# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Core Notification & Escalation Router (docs/notifications.md; decision 0011)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from .channels import (
    EmailChannel,
    HttpTransport,
    SecretProvider,
    SmsVoiceChannel,
    TeamsAdaptiveCardChannel,
    WebPushChannel,
)
from .models import DeliveryResult, IncidentState, NotificationEvent, RedactedNotification
from .redaction import build_redacted_notification
from .store import StateStore

ROOT = Path(__file__).resolve().parents[1]


def load_notifications_policy(root: Path | None = None) -> dict[str, Any]:
    base = root or ROOT
    path = base / "governance" / "notifications.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


class NotificationRouter:
    """Routes swarm Event Grid events with urgency tiers, deduplication, quotas, failover, and escalation."""

    def __init__(
        self,
        policy: dict[str, Any],
        store: StateStore,
        transport: HttpTransport,
        secrets: SecretProvider,
        *,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._policy = policy
        self._store = store
        self._secrets = secrets
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

        ch_cfg = policy.get("channels") or {}
        vapid_ref = (ch_cfg.get("web_push") or {}).get("vapidKeySecretRef", "kv://vapid-private-key")
        sms_cap = float((ch_cfg.get("sms_voice") or {}).get("monthlySpendCapUsd", 25.00))

        chatops_enabled = bool((policy.get("chatopsApproval") or {}).get("enabled", False))
        self.web_push = WebPushChannel(transport, secrets, vapid_ref=vapid_ref)
        self.teams = TeamsAdaptiveCardChannel(transport, secrets, chatops_enabled=chatops_enabled)
        self.email = EmailChannel(transport, secrets)
        self.sms_voice = SmsVoiceChannel(transport, secrets, store, monthly_spend_cap_usd=sms_cap)

    def classify_urgency(self, event: NotificationEvent) -> str:
        if event.event_type == "switch.changed" and event.switch_position.startswith("halt-"):
            return "critical"
        tiers = self._policy.get("urgencyTiers") or {}
        for tier_name in ("critical", "actionable", "info"):
            events = (tiers.get(tier_name) or {}).get("events") or []
            if event.event_type in events:
                return tier_name
        return "info"

    def _in_quiet_hours(self, now: datetime) -> bool:
        qh = self._policy.get("quietHours") or {}
        if not qh.get("enabled", False):
            return False
        start_h = int(qh.get("startHour", 22))
        end_h = int(qh.get("endHour", 7))
        hour = now.astimezone(timezone.utc).hour
        if start_h > end_h:
            return hour >= start_h or hour < end_h
        return start_h <= hour < end_h

    def _is_deduplicated(self, event: NotificationEvent, now: datetime) -> bool:
        dedup_cfg = self._policy.get("deduplication") or {}
        sec_cfg = dedup_cfg.get("securityNoise") or {}
        sec_events = set(sec_cfg.get("events") or ("injection.suspected", "decoy.tripped"))

        if event.event_type in sec_events:
            agent_id = event.agent_id or "unknown-agent"
            key = f"sec:{event.event_type}:{agent_id}"
            cooldown = timedelta(minutes=int(sec_cfg.get("cooldownMinutes", 15)))
        elif event.plan_id:
            plan_cfg = dedup_cfg.get("planEvents") or {}
            key = f"plan:{event.event_type}:{event.plan_id}"
            cooldown = timedelta(minutes=int(plan_cfg.get("windowMinutes", 15)))
        else:
            return False

        last = self._store.get_dedup_timestamp(key)
        if last is not None and (now - last) < cooldown:
            return True
        self._store.set_dedup_timestamp(key, now)
        return False

    def _consume_rate_limit(self, urgency: str, now: datetime) -> bool:
        tiers = self._policy.get("urgencyTiers") or {}
        bucket = (tiers.get(urgency) or {}).get("rateLimitBucket") or ("emergency" if urgency == "critical" else "standard")
        if urgency != "critical" and bucket == "emergency":
            bucket = "standard"
        limits = self._policy.get("rateLimits") or {}
        max_per_hour = int((limits.get(bucket) or {}).get("maxPerHour", 20))
        hour_key = now.astimezone(timezone.utc).strftime("%Y%m%d%H")
        return self._store.increment_rate_bucket(bucket, hour_key, max_per_hour)

    def _deliver_channel(
        self,
        channel_name: str,
        contact: dict[str, Any],
        notification: RedactedNotification,
        *,
        now: datetime,
        buddy_stopped: bool,
        is_escalated: bool = False,
    ) -> DeliveryResult:
        contact_id = str(contact.get("id") or "operator")
        endpoint_ref = str((contact.get("channels") or {}).get(channel_name) or "")
        month_key = now.astimezone(timezone.utc).strftime("%Y-%m")

        if channel_name == "web_push":
            return self.web_push.send(contact_id, endpoint_ref, notification, buddy_stopped=buddy_stopped)
        if channel_name == "teams":
            return self.teams.send(contact_id, endpoint_ref, notification)
        if channel_name == "email":
            return self.email.send(contact_id, endpoint_ref, notification)
        if channel_name == "sms_voice":
            return self.sms_voice.send(
                contact_id,
                endpoint_ref,
                notification,
                month_key=month_key,
                is_escalated=is_escalated,
            )
        return DeliveryResult(channel=channel_name, contact_id=contact_id, ok=False, status_code=400, error="unknown-channel")

    def _deliver_with_immediate_failover(
        self,
        channels: list[str],
        notification: RedactedNotification,
        *,
        now: datetime,
        buddy_stopped: bool,
        contact_tier: str = "primary",
        is_escalated: bool = False,
    ) -> list[DeliveryResult]:
        contacts_cfg = self._policy.get("contacts") or {}
        primary = contacts_cfg.get("primary") or {"id": "operator-primary", "channels": {}}
        fallbacks = contacts_cfg.get("fallback") or []
        contact_list = [primary, *fallbacks] if contact_tier == "primary" else list(fallbacks) or [primary]

        results: list[DeliveryResult] = []
        for contact in contact_list:
            for ch in channels:
                if buddy_stopped and ch == "web_push":
                    res = DeliveryResult(
                        channel="web_push",
                        contact_id=str(contact.get("id") or "operator"),
                        ok=False,
                        status_code=503,
                        error="avd-buddy-stopped",
                    )
                else:
                    res = self._deliver_channel(
                        ch,
                        contact,
                        notification,
                        now=now,
                        buddy_stopped=buddy_stopped,
                        is_escalated=is_escalated,
                    )
                results.append(res)
                if res.ok:
                    return results
        return results

    def route_event(
        self,
        event: NotificationEvent,
        *,
        switch_position: str = "run",
        buddy_stopped: bool = False,
    ) -> dict[str, Any]:
        now = self._now_fn().astimezone(timezone.utc)
        effective_buddy_stopped = buddy_stopped or (switch_position == "halt-all")

        # Auto-cancel pending escalations on resolution events
        auto_cancel = set((self._policy.get("resilience") or {}).get("autoCancelOnEvents") or ("plan.approved", "plan.rejected", "plan.expired"))
        cancelled_keys: list[str] = []
        if event.event_type in auto_cancel and event.plan_id:
            cancelled_keys = self._store.cancel_incidents_for_plan(event.plan_id, now)

        # Suppress standard plan.gated approvals when kill switch is halted (allow stop-spend)
        if switch_position in ("halt-actions", "halt-agents", "halt-all") and event.event_type == "plan.gated" and not event.stop_spend:
            return {
                "status": "suppressed-kill-switch",
                "switchPosition": switch_position,
                "cancelled": cancelled_keys,
                "deliveries": [],
            }

        urgency = self.classify_urgency(event)

        # Deduplication
        if self._is_deduplicated(event, now):
            return {
                "status": "deduplicated",
                "urgency": urgency,
                "cancelled": cancelled_keys,
                "deliveries": [],
            }

        # Quiet hours check (critical bypasses; actionable bypasses if urgent=True)
        tier_cfg = (self._policy.get("urgencyTiers") or {}).get(urgency) or {}
        bypasses_qh = bool(tier_cfg.get("bypassQuietHours")) or (
            urgency == "actionable" and event.urgent and bool(tier_cfg.get("urgentBypassQuietHours", True))
        )
        if self._in_quiet_hours(now) and not bypasses_qh:
            return {
                "status": "deferred-quiet-hours",
                "urgency": urgency,
                "cancelled": cancelled_keys,
                "deliveries": [],
            }

        # Rate-limit bucket check (critical uses reserved emergency bucket)
        if not self._consume_rate_limit(urgency, now):
            return {
                "status": "rate-limited",
                "urgency": urgency,
                "cancelled": cancelled_keys,
                "deliveries": [],
            }

        prefix_len = int((self._policy.get("redaction") or {}).get("planHashPrefixLength", 8))
        notification = build_redacted_notification(event, urgency=urgency, prefix_length=prefix_len)
        channels = list(tier_cfg.get("channels") or ["teams"])

        deliveries = self._deliver_with_immediate_failover(
            channels,
            notification,
            now=now,
            buddy_stopped=effective_buddy_stopped,
            contact_tier="primary",
        )

        # Schedule escalation timer for open critical/actionable incidents
        next_esc_at: str | None = None
        if urgency == "critical" and tier_cfg.get("escalateToFallback"):
            ack_mins = int(tier_cfg.get("ackTimeoutMinutes", 5))
            next_esc_at = (now + timedelta(minutes=ack_mins)).isoformat()
        elif urgency == "actionable" and tier_cfg.get("escalateAfterMinutes") is not None:
            esc_mins = int(tier_cfg["escalateAfterMinutes"])
            next_esc_at = (now + timedelta(minutes=esc_mins)).isoformat()

        if next_esc_at and event.plan_id:
            inc_key = f"{event.event_type}:{event.plan_id}"
            self._store.put_incident(
                IncidentState(
                    incident_key=inc_key,
                    event_type=event.event_type,
                    plan_id=event.plan_id,
                    urgency=urgency,
                    status="open",
                    current_step=0,
                    next_escalation_at=next_esc_at,
                    contact_tier="primary",
                    payload=notification.to_dict(),
                    updated_at=now.isoformat(),
                )
            )

        any_ok = any(d.ok for d in deliveries)
        return {
            "status": "delivered" if any_ok else "delivery-failed",
            "urgency": urgency,
            "cancelled": cancelled_keys,
            "deliveries": [d.__dict__ for d in deliveries],
            "nextEscalationAt": next_esc_at,
        }

    def acknowledge(self, plan_id: str) -> list[str]:
        now = self._now_fn().astimezone(timezone.utc)
        acked: list[str] = []
        for inc in self._store.list_open_incidents():
            if inc.plan_id == plan_id:
                inc.status = "acknowledged"
                inc.next_escalation_at = None
                inc.updated_at = now.isoformat()
                self._store.put_incident(inc)
                acked.append(inc.incident_key)
        return acked

    def process_escalations(self, *, buddy_stopped: bool = False) -> list[dict[str, Any]]:
        now = self._now_fn().astimezone(timezone.utc)
        escalated: list[dict[str, Any]] = []
        tiers = self._policy.get("urgencyTiers") or {}

        for inc in self._store.list_open_incidents():
            if not inc.next_escalation_at:
                continue
            due_at = datetime.fromisoformat(inc.next_escalation_at)
            if due_at > now:
                continue

            payload = inc.payload
            notif = RedactedNotification(
                plan_id=payload.get("planId", inc.plan_id),
                plan_hash_prefix=payload.get("planHashPrefix", ""),
                action=payload.get("action", inc.event_type),
                urgency=inc.urgency,
                expires_at=payload.get("expiresAt", ""),
                buddy_deep_link=payload.get("buddyDeepLink", ""),
                github_run_url=payload.get("githubRunUrl", ""),
                trip_url=payload.get("tripUrl", ""),
            )

            tier_cfg = tiers.get(inc.urgency) or {}
            if inc.urgency == "critical":
                channels = list(tier_cfg.get("channels") or ["teams", "email", "sms_voice"])
                results = self._deliver_with_immediate_failover(
                    channels,
                    notif,
                    now=now,
                    buddy_stopped=buddy_stopped,
                    contact_tier="fallback",
                    is_escalated=True,
                )
                inc.status = "escalated"
                inc.contact_tier = "fallback"
                inc.next_escalation_at = None
            else:
                chain = list(tier_cfg.get("escalationChain") or ["email", "sms_voice"])
                step_idx = min(inc.current_step, len(chain) - 1)
                step_channels = chain[step_idx:]
                results = self._deliver_with_immediate_failover(
                    step_channels,
                    notif,
                    now=now,
                    buddy_stopped=buddy_stopped,
                    contact_tier="primary",
                    is_escalated=True,
                )
                inc.current_step += 1
                if inc.current_step >= len(chain):
                    inc.status = "escalated"
                    inc.next_escalation_at = None
                else:
                    esc_mins = int(tier_cfg.get("escalateAfterMinutes", 15))
                    inc.next_escalation_at = (now + timedelta(minutes=esc_mins)).isoformat()

            inc.updated_at = now.isoformat()
            self._store.put_incident(inc)
            escalated.append(
                {
                    "incidentKey": inc.incident_key,
                    "status": inc.status,
                    "deliveries": [r.__dict__ for r in results],
                }
            )
        return escalated

    def handle_dead_letter(self, dead_letter_event: dict[str, Any], *, buddy_stopped: bool = False) -> dict[str, Any]:
        """Routes Event Grid dead-letter events as critical alerts."""
        dl_id = str(dead_letter_event.get("planId") or dead_letter_event.get("id") or "dead-letter")
        ev = NotificationEvent(
            event_type="budget.alert" if dead_letter_event.get("kind") == "budget" else "switch.changed",
            plan_id=dl_id,
            plan_hash=str(dead_letter_event.get("planHash") or ""),
            action="eventgrid.deadletter",
            switch_position="halt-actions",
            urgent=True,
            buddy_host=str(dead_letter_event.get("buddyHost") or "avd-buddy.example.invalid"),
        )
        return self.route_event(ev, switch_position="halt-actions", buddy_stopped=buddy_stopped)

    def emit_heartbeat(self) -> dict[str, Any]:
        now = self._now_fn().astimezone(timezone.utc)
        self._store.set_last_heartbeat(now)
        return {
            "eventType": "router.heartbeat",
            "timestamp": now.isoformat(),
            "status": "ok",
        }
