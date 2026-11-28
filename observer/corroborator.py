# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Layer 1: 5-minute external corroborator for avd-swarm (docs/outside-observer-spec.md)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatch
from typing import Any, Callable, Protocol

from .models import (
    ActivityWrite,
    ApprovedPlanEvent,
    CorroborationFinding,
    CorroborationResult,
    GraphAuditEvent,
    HeartbeatRecord,
    TokenMeterState,
    is_monotonic_upgrade,
    strongest_position,
)

FORBIDDEN_POLICY_OR_DEFENDER_PREFIXES = (
    "microsoft.authorization/policyassignments/",
    "microsoft.authorization/policyexemptions/",
    "microsoft.authorization/roleassignments/",
    "microsoft.security/",
)


def _parse_iso(ts: str) -> datetime | None:
    raw = ts.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


@dataclass
class ObserverConfig:
    """Configuration for the Layer 1 corroborator."""

    allowed_avd_rg_globs: tuple[str, ...] = (
        "/subscriptions/*/resourcegroups/rg-avd-*/*",
        "/subscriptions/*/resourcegroups/rg-*-avd-*/*",
    )
    allowed_run_rg_globs: tuple[str, ...] = (
        "/subscriptions/*/resourcegroups/rg-*-run-*/*",
        "/subscriptions/*/resourcegroups/rg-run-*/*",
    )
    observer_silence_minutes: int = 15
    swarm_roles: tuple[str, ...] = ("approve", "hosts", "cost", "intune", "stop-spend", "recovery-test")
    sre_agent_principal_id: str = ""
    trip_url: str = ""
    deadman_url: str = ""
    owner_notify_url: str = ""
    current_switch_position: str = "run"


class CorroboratorTransport(Protocol):
    """Pluggable HTTP transport for calling trip, dead-man's switch, and owner notifications."""

    def post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[int, str]:
        ...

    def get(self, url: str, headers: dict[str, str] | None = None) -> tuple[int, str]:
        ...


class UrllibObserverTransport:
    """Zero-dependency urllib transport for Consumption Azure Functions."""

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds

    def post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[int, str]:
        req_headers = {"Content-Type": "application/json"}
        if headers:
            req_headers.update(headers)
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as err:
            return err.code, err.read().decode("utf-8", errors="replace")

    def get(self, url: str, headers: dict[str, str] | None = None) -> tuple[int, str]:
        req = urllib.request.Request(url, headers=headers or {}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as err:
            return err.code, err.read().decode("utf-8", errors="replace")


def _matches_any_glob(resource_id: str, globs: tuple[str, ...]) -> bool:
    lowered = resource_id.strip().lower()
    return any(fnmatch(lowered, g.lower()) for g in globs)


def _is_write_operation(operation_name: str) -> bool:
    op = operation_name.strip().lower()
    return op.endswith("/write") or op.endswith("/delete") or op.endswith("/action")


def _is_forbidden_control_plane_op(operation_name: str) -> bool:
    op = operation_name.strip().lower()
    return any(op.startswith(prefix) for prefix in FORBIDDEN_POLICY_OR_DEFENDER_PREFIXES)


def _matches_approved_plan(write: ActivityWrite, approved_plans: list[ApprovedPlanEvent]) -> bool:
    write_ts = _parse_iso(write.timestamp)
    target_lower = write.resource_id.strip().lower()
    for plan in approved_plans:
        if not plan.signature_valid:
            continue
        if not any(t.strip().lower() == target_lower for t in plan.targets):
            continue
        approved_ts = _parse_iso(plan.approved_at)
        expires_ts = _parse_iso(plan.expires_at)
        if write_ts and approved_ts and write_ts < approved_ts:
            continue
        if write_ts and expires_ts and write_ts > expires_ts:
            continue
        return True
    return False


class Corroborator:
    """Evaluates Activity Log, Entra Graph logs, audit blobs, heartbeat, and token meter."""

    def __init__(
        self,
        config: ObserverConfig | None = None,
        transport: CorroboratorTransport | None = None,
        token_provider: Callable[[str], str] | None = None,
    ) -> None:
        self.config = config or ObserverConfig()
        self.transport = transport or UrllibObserverTransport()
        self.token_provider = token_provider

    def evaluate(
        self,
        *,
        activity_writes: list[ActivityWrite],
        graph_events: list[GraphAuditEvent],
        approved_plans: list[ApprovedPlanEvent],
        heartbeat: HeartbeatRecord | None,
        token_meter: TokenMeterState | None,
        now: datetime | None = None,
    ) -> list[CorroborationFinding]:
        findings: list[CorroborationFinding] = []
        current_time = now or datetime.now(timezone.utc)

        # 1. Corroborate Azure Activity Log writes
        for w in activity_writes:
            if not _is_write_operation(w.operation_name):
                continue

            # SRE Agent write (governance.md section 2.4)
            if w.caller_role == "sre-agent" or (
                self.config.sre_agent_principal_id
                and w.caller_id.lower() == self.config.sre_agent_principal_id.lower()
            ):
                findings.append(
                    CorroborationFinding(
                        rule_id="adapter.sre-agent-write",
                        position="halt-all",
                        reason=f"Azure SRE Agent write observed: {w.operation_name} on {w.resource_id}",
                        page_owner=True,
                        evidence_id=w.event_id,
                    )
                )
                continue

            # Policy / RBAC / Defender writes by any swarm identity
            if w.caller_role in self.config.swarm_roles and _is_forbidden_control_plane_op(w.operation_name):
                findings.append(
                    CorroborationFinding(
                        rule_id="compliance.exemption-or-policy-write",
                        position="halt-all",
                        reason=f"Swarm identity {w.caller_role} performed forbidden write {w.operation_name}",
                        page_owner=True,
                        evidence_id=w.event_id,
                    )
                )
                continue

            # stop-spend may only write inside run resource groups
            if w.caller_role == "stop-spend":
                if not _matches_any_glob(w.resource_id, self.config.allowed_run_rg_globs):
                    findings.append(
                        CorroborationFinding(
                            rule_id="write.outside-scope",
                            position="halt-all",
                            reason=f"stop-spend write outside run resource groups: {w.resource_id}",
                            page_owner=True,
                            evidence_id=w.event_id,
                        )
                    )
                continue

            # recovery-test identity scope check
            if w.caller_role == "recovery-test":
                if not _matches_any_glob(w.resource_id, self.config.allowed_run_rg_globs):
                    findings.append(
                        CorroborationFinding(
                            rule_id="recovery.write-outside-scope",
                            position="halt-all",
                            reason=f"recovery-test write outside run resource groups: {w.resource_id}",
                            page_owner=True,
                            evidence_id=w.event_id,
                        )
                    )
                continue

            # General swarm scope check
            if w.caller_role in self.config.swarm_roles:
                if not _matches_any_glob(w.resource_id, self.config.allowed_avd_rg_globs):
                    findings.append(
                        CorroborationFinding(
                            rule_id="write.outside-scope",
                            position="halt-all",
                            reason=f"Swarm write outside allowed AVD scope: {w.resource_id}",
                            page_owner=True,
                            evidence_id=w.event_id,
                        )
                    )
                    continue

            # Approve identity writes must match a signed plan.approved event
            if w.caller_role == "approve":
                if not _matches_approved_plan(w, approved_plans):
                    findings.append(
                        CorroborationFinding(
                            rule_id="write.unapproved",
                            position="halt-all",
                            reason=f"Approve write {w.operation_name} on {w.resource_id} has no matching signed plan.approved",
                            page_owner=True,
                            evidence_id=w.event_id,
                        )
                    )

        # 2. Check Entra Graph Audit/Sign-in logs for SRE Agent role assignments or unapproved credential changes
        for g in graph_events:
            act_lower = g.activity.lower()
            if self.config.sre_agent_principal_id and g.target_id.lower() == self.config.sre_agent_principal_id.lower():
                if "role" in act_lower or "assignment" in act_lower or "member" in act_lower:
                    findings.append(
                        CorroborationFinding(
                            rule_id="adapter.sre-agent-write",
                            position="halt-all",
                            reason=f"Entra role/assignment change targeting SRE Agent: {g.activity}",
                            page_owner=True,
                            evidence_id=g.event_id,
                        )
                    )
            if "federated" in act_lower or "credential" in act_lower:
                if g.actor_id and g.actor_id.lower() != "human-operator":
                    findings.append(
                        CorroborationFinding(
                            rule_id="write.unapproved",
                            position="halt-all",
                            reason=f"Suspicious credential change in Entra audit log: {g.activity} by {g.actor_id}",
                            page_owner=True,
                            evidence_id=g.event_id,
                        )
                    )

        # 3. Check 15-minute heartbeat freshness
        if heartbeat is None:
            findings.append(
                CorroborationFinding(
                    rule_id="observer.silent",
                    position="halt-actions",
                    reason="Missing avd-swarm heartbeat record",
                    page_owner=False,
                )
            )
        else:
            hb_ts = _parse_iso(heartbeat.timestamp)
            if hb_ts is None:
                findings.append(
                    CorroborationFinding(
                        rule_id="observer.silent",
                        position="halt-actions",
                        reason=f"Unparseable avd-swarm heartbeat timestamp: {heartbeat.timestamp!r}",
                        page_owner=False,
                    )
                )
            else:
                age_minutes = (current_time - hb_ts).total_seconds() / 60.0
                if age_minutes > self.config.observer_silence_minutes:
                    findings.append(
                        CorroborationFinding(
                            rule_id="observer.silent",
                            position="halt-actions",
                            reason=f"avd-swarm heartbeat stale ({age_minutes:.1f}m > {self.config.observer_silence_minutes}m)",
                            page_owner=False,
                        )
                    )

        # 4. Check token-meter vs halt-agents
        if token_meter is not None:
            if token_meter.daily_cap_usd > 0 and token_meter.daily_spend_usd >= token_meter.daily_cap_usd:
                findings.append(
                    CorroborationFinding(
                        rule_id="spend.daily-token-cap",
                        position="halt-agents",
                        reason=f"Daily token spend ${token_meter.daily_spend_usd:.2f} reached cap ${token_meter.daily_cap_usd:.2f}",
                        page_owner=False,
                    )
                )

            switch_pos = heartbeat.switch_position if heartbeat else self.config.current_switch_position
            if switch_pos in ("halt-agents", "halt-all") and token_meter.last_model_call_at and token_meter.halted_at:
                call_ts = _parse_iso(token_meter.last_model_call_at)
                halt_ts = _parse_iso(token_meter.halted_at)
                if call_ts and halt_ts and call_ts > halt_ts:
                    findings.append(
                        CorroborationFinding(
                            rule_id="model.usage-after-halt",
                            position="halt-all",
                            reason="Model usage metered after halt-agents took effect",
                            page_owner=True,
                        )
                    )

        return findings

    def run_once(
        self,
        *,
        activity_writes: list[ActivityWrite],
        graph_events: list[GraphAuditEvent],
        approved_plans: list[ApprovedPlanEvent],
        heartbeat: HeartbeatRecord | None,
        token_meter: TokenMeterState | None,
        now: datetime | None = None,
    ) -> CorroborationResult:
        """Evaluates all signals, calls monotonic `trip` on mismatch, and pings dead-man's switch when healthy."""
        findings = self.evaluate(
            activity_writes=activity_writes,
            graph_events=graph_events,
            approved_plans=approved_plans,
            heartbeat=heartbeat,
            token_meter=token_meter,
            now=now,
        )

        result = CorroborationResult(
            healthy=len(findings) == 0,
            tripped=False,
            findings=findings,
            checked_writes=len(activity_writes),
            checked_graph_events=len(graph_events),
        )

        if not findings:
            if self.config.deadman_url:
                try:
                    status, _ = self.transport.get(self.config.deadman_url)
                    result.deadman_pinged = 200 <= status < 300
                    if not result.deadman_pinged:
                        result.errors.append(f"deadman ping returned HTTP {status}")
                except Exception as exc:
                    result.errors.append(f"deadman ping failed: {exc}")
            return result

        target_pos = strongest_position([f.position for f in findings])
        if target_pos and is_monotonic_upgrade(self.config.current_switch_position, target_pos):
            combined_reason = "; ".join(f"[{f.rule_id}] {f.reason}" for f in findings[:5])
            if self.config.trip_url:
                headers: dict[str, str] = {}
                if self.token_provider is not None:
                    headers["Authorization"] = f"Bearer {self.token_provider('https://management.azure.com/')}"
                try:
                    status, body = self.transport.post_json(
                        self.config.trip_url,
                        {"position": target_pos, "reason": combined_reason},
                        headers=headers,
                    )
                    if 200 <= status < 300:
                        result.tripped = True
                        result.trip_position = target_pos
                    else:
                        result.errors.append(f"trip endpoint returned HTTP {status}: {body[:200]}")
                except Exception as exc:
                    result.errors.append(f"trip call failed: {exc}")
            else:
                result.tripped = True
                result.trip_position = target_pos

            if any(f.page_owner for f in findings) and self.config.owner_notify_url:
                try:
                    self.transport.post_json(
                        self.config.owner_notify_url,
                        {
                            "eventType": "observer.trip",
                            "position": target_pos,
                            "reason": combined_reason,
                        },
                    )
                except Exception as exc:
                    result.errors.append(f"owner notification failed: {exc}")

        return result


def config_from_env() -> ObserverConfig:
    """Builds ObserverConfig from Azure Function app settings."""
    avd_globs = tuple(
        g.strip()
        for g in os.environ.get(
            "OBSERVER_ALLOWED_AVD_GLOBS",
            "/subscriptions/*/resourcegroups/rg-avd-*/*,/subscriptions/*/resourcegroups/rg-*-avd-*/*",
        ).split(",")
        if g.strip()
    )
    run_globs = tuple(
        g.strip()
        for g in os.environ.get(
            "OBSERVER_ALLOWED_RUN_GLOBS",
            "/subscriptions/*/resourcegroups/rg-*-run-*/*,/subscriptions/*/resourcegroups/rg-run-*/*",
        ).split(",")
        if g.strip()
    )
    return ObserverConfig(
        allowed_avd_rg_globs=avd_globs,
        allowed_run_rg_globs=run_globs,
        observer_silence_minutes=int(os.environ.get("OBSERVER_SILENCE_MINUTES", "15")),
        sre_agent_principal_id=os.environ.get("SRE_AGENT_PRINCIPAL_ID", "").strip(),
        trip_url=os.environ.get("KILL_SWITCH_TRIP_URL", "").strip(),
        deadman_url=os.environ.get("DEADMAN_SWITCH_URL", "").strip(),
        owner_notify_url=os.environ.get("OWNER_NOTIFY_URL", "").strip(),
        current_switch_position=os.environ.get("CURRENT_SWITCH_POSITION", "run").strip() or "run",
    )
