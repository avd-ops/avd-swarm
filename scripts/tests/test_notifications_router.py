# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Offline unit tests for Notification & Escalation Router (docs/notifications.md; decision 0011)."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import check_agent_pr  # noqa: E402
from notifications.channels import InMemorySecretProvider, TeamsAdaptiveCardChannel, WebPushChannel  # noqa: E402
from notifications.models import NotificationEvent  # noqa: E402
from notifications.redaction import assert_r17_compliant, build_redacted_notification  # noqa: E402
from notifications.router import NotificationRouter, load_notifications_policy  # noqa: E402
from notifications.store import InMemoryStateStore  # noqa: E402


class FakeTransport:
    def __init__(self, responses: dict[str, tuple[int, str] | Exception] | None = None) -> None:
        self.responses = dict(responses or {})
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[int, str]:
        self.calls.append((url, payload))
        outcome = self.responses.get(url, (200, "ok"))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _default_secrets() -> InMemorySecretProvider:
    return InMemorySecretProvider(
        {
            "kv://vapid-private-key": "test-vapid-key",
            "kv://vapid-primary-sub": "https://push.example.invalid/primary",
            "kv://teams-webhook-primary": "https://teams.example.invalid/primary",
            "kv://email-primary": "https://email.example.invalid/primary",
            "kv://sms-primary": "https://sms.example.invalid/primary",
            "kv://teams-webhook-fallback-1": "https://teams.example.invalid/fallback1",
            "kv://email-fallback-1": "https://email.example.invalid/fallback1",
            "kv://sms-fallback-1": "https://sms.example.invalid/fallback1",
        }
    )


class PolicyAndProtectedPathTests(unittest.TestCase):
    def test_notifications_policy_is_protected_from_agent_prs(self) -> None:
        policy = load_notifications_policy(ROOT)
        self.assertEqual(policy["version"], "avd-swarm-notifications/v1")
        self.assertTrue(policy["rateLimits"]["emergency"]["reserved"])
        self.assertFalse(policy["rateLimits"]["standard"]["reserved"])

        protected = yaml.safe_load((ROOT / "governance/protected-paths.yml").read_text(encoding="utf-8"))
        patterns = protected["protected"]
        self.assertTrue(any(check_agent_pr.matches("governance/notifications.yml", p) for p in patterns))
        self.assertTrue(any(check_agent_pr.matches("notifications/router.py", p) for p in patterns))

        findings = check_agent_pr.check(
            policy=protected,
            pr={"author": {"login": "avd-swarm-improve[bot]"}, "commits": []},
            changes=[("M", "governance/notifications.yml")],
        )
        self.assertTrue(findings)


class RouterBehaviorTests(unittest.TestCase):
    def _make_router(
        self,
        *,
        now: datetime | None = None,
        responses: dict[str, tuple[int, str] | Exception] | None = None,
    ) -> tuple[NotificationRouter, FakeTransport, InMemoryStateStore, list[datetime]]:
        clock = [now or datetime(2026, 11, 16, 14, 0, tzinfo=timezone.utc)]
        store = InMemoryStateStore()
        transport = FakeTransport(responses=responses)
        router = NotificationRouter(
            load_notifications_policy(ROOT),
            store,
            transport,
            _default_secrets(),
            now_fn=lambda: clock[0],
        )
        return router, transport, store, clock

    def test_immediate_failover_on_4xx_5xx_and_timeout(self) -> None:
        router, transport, _, _ = self._make_router(
            responses={
                "https://push.example.invalid/primary": (500, "push error"),
                "https://teams.example.invalid/primary": (200, "ok"),
            }
        )
        ev = NotificationEvent(
            event_type="plan.gated",
            plan_id="plan-01",
            plan_hash="a" * 64,
            action="host.drain",
            github_run_url="https://github.com/avd-ops/avd-swarm/actions/runs/123",
        )
        res = router.route_event(ev)
        self.assertEqual(res["status"], "delivered")
        self.assertEqual([d["channel"] for d in res["deliveries"]], ["web_push", "teams"])
        self.assertFalse(res["deliveries"][0]["ok"])
        self.assertTrue(res["deliveries"][1]["ok"])
        self.assertEqual(len(transport.calls), 2)

        # Also verify timeout failover across primary -> fallback
        router2, transport2, _, _ = self._make_router(
            responses={
                "https://push.example.invalid/primary": TimeoutError("timed out"),
                "https://teams.example.invalid/primary": (429, "rate limited"),
                "https://teams.example.invalid/fallback1": (200, "ok"),
            }
        )
        res2 = router2.route_event(
            NotificationEvent(event_type="plan.gated", plan_id="plan-02", plan_hash="b" * 64, action="host.drain")
        )
        self.assertEqual(res2["status"], "delivered")
        self.assertEqual(res2["deliveries"][-1]["contact_id"], "operator-fallback-1")
        self.assertEqual(len(transport2.calls), 3)

    def test_deduplication_plan_and_security_noise_15m_cooldown(self) -> None:
        router, _, _, clock = self._make_router()
        ev = NotificationEvent(event_type="plan.gated", plan_id="plan-dup", plan_hash="c" * 64, action="host.drain")
        self.assertEqual(router.route_event(ev)["status"], "delivered")
        self.assertEqual(router.route_event(ev)["status"], "deduplicated")

        clock[0] += timedelta(minutes=16)
        self.assertEqual(router.route_event(ev)["status"], "delivered")

        sec_a1 = NotificationEvent(event_type="injection.suspected", agent_id="hosts-agent", plan_id="p1")
        sec_a2 = NotificationEvent(event_type="injection.suspected", agent_id="users-agent", plan_id="p2")
        self.assertEqual(router.route_event(sec_a1)["status"], "delivered")
        self.assertEqual(router.route_event(sec_a1)["status"], "deduplicated")
        # Different agentId is not collapsed
        self.assertEqual(router.route_event(sec_a2)["status"], "delivered")

    def test_reserved_emergency_quota_not_exhausted_by_standard_flood(self) -> None:
        router, _, _, _ = self._make_router()
        # Exhaust standard bucket (maxPerHour = 20)
        for i in range(20):
            r = router.route_event(
                NotificationEvent(event_type="plan.gated", plan_id=f"plan-{i}", plan_hash="d" * 64, action="host.drain")
            )
            self.assertEqual(r["status"], "delivered")

        blocked = router.route_event(
            NotificationEvent(event_type="plan.gated", plan_id="plan-21", plan_hash="d" * 64, action="host.drain")
        )
        self.assertEqual(blocked["status"], "rate-limited")

        # Critical event uses reserved emergency bucket and still delivers
        crit = router.route_event(
            NotificationEvent(
                event_type="switch.changed",
                plan_id="halt-01",
                switch_position="halt-all",
                action="kill-switch.halt-all",
            ),
            switch_position="halt-all",
        )
        self.assertEqual(crit["status"], "delivered")
        self.assertEqual(crit["urgency"], "critical")

    def test_quiet_hours_defers_actionable_but_critical_bypasses(self) -> None:
        # 23:00 UTC is inside quiet hours (22:00 - 07:00)
        router, _, _, _ = self._make_router(now=datetime(2026, 11, 16, 23, 0, tzinfo=timezone.utc))
        deferred = router.route_event(
            NotificationEvent(event_type="plan.gated", plan_id="plan-qh", plan_hash="e" * 64, action="host.drain")
        )
        self.assertEqual(deferred["status"], "deferred-quiet-hours")

        crit = router.route_event(
            NotificationEvent(event_type="budget.alert", plan_id="budget-1", action="budget.alert")
        )
        self.assertEqual(crit["status"], "delivered")

    def test_escalation_and_auto_cancel_on_resolution(self) -> None:
        router, transport, store, clock = self._make_router()
        ev = NotificationEvent(event_type="plan.gated", plan_id="plan-esc", plan_hash="f" * 64, action="host.drain")
        res = router.route_event(ev)
        self.assertEqual(res["status"], "delivered")
        self.assertEqual(len(store.list_open_incidents()), 1)

        # Auto-cancel on plan.approved before timer fires
        cancel_res = router.route_event(NotificationEvent(event_type="plan.approved", plan_id="plan-esc"))
        self.assertEqual(cancel_res["cancelled"], ["plan.gated:plan-esc"])
        self.assertEqual(len(store.list_open_incidents()), 0)

        # Now test escalation timer firing after 15 minutes
        ev2 = NotificationEvent(event_type="plan.gated", plan_id="plan-esc-2", plan_hash="1" * 64, action="host.drain")
        router.route_event(ev2)
        clock[0] += timedelta(minutes=16)
        escalated = router.process_escalations()
        self.assertEqual(len(escalated), 1)
        self.assertTrue(any(url.startswith("https://email.example.invalid") for url, _ in transport.calls))

    def test_halt_all_and_stop_spend_deliver_when_buddy_stopped(self) -> None:
        router, transport, _, _ = self._make_router()
        # Standard plan.gated is suppressed during halt-all
        suppressed = router.route_event(
            NotificationEvent(event_type="plan.gated", plan_id="plan-std", plan_hash="2" * 64, action="host.drain"),
            switch_position="halt-all",
            buddy_stopped=True,
        )
        self.assertEqual(suppressed["status"], "suppressed-kill-switch")

        # stop-spend.pending still delivers (skips web_push, uses teams)
        stop_spend = router.route_event(
            NotificationEvent(
                event_type="stop-spend.pending",
                plan_id="plan-stop-spend",
                plan_hash="3" * 64,
                action="host.stop",
                stop_spend=True,
                github_run_url="https://github.com/avd-ops/avd-swarm/actions/runs/999",
            ),
            switch_position="halt-all",
            buddy_stopped=True,
        )
        self.assertEqual(stop_spend["status"], "delivered")
        self.assertEqual(stop_spend["deliveries"][0]["channel"], "web_push")
        self.assertEqual(stop_spend["deliveries"][0]["error"], "avd-buddy-stopped")
        self.assertEqual(stop_spend["deliveries"][1]["channel"], "teams")
        self.assertTrue(stop_spend["deliveries"][1]["ok"])
        self.assertFalse(any("push.example.invalid" in url for url, _ in transport.calls))

    def test_r17_redaction_and_channel_payloads(self) -> None:
        ev = NotificationEvent(
            event_type="plan.gated",
            plan_id="plan-r17",
            plan_hash="0123456789abcdef" * 4,
            action="host.drain",
            expires_at="2026-11-16T18:00:00Z",
            buddy_host="buddy.example.invalid",
            github_run_url="https://github.com/avd-ops/avd-swarm/actions/runs/42?target=secret-host",
        )
        redacted = build_redacted_notification(ev, urgency="actionable")
        self.assertEqual(redacted.plan_hash_prefix, "01234567")
        self.assertEqual(redacted.buddy_deep_link, "https://buddy.example.invalid/plans/plan-r17")
        self.assertEqual(redacted.github_run_url, "https://github.com/avd-ops/avd-swarm/actions/runs/42")

        teams = TeamsAdaptiveCardChannel(FakeTransport(), _default_secrets())
        card = teams.build_card(redacted)
        assert_r17_compliant(card)
        actions = card["attachments"][0]["content"]["actions"]
        titles = [a["title"] for a in actions]
        self.assertEqual(titles, ["Review in avd-buddy", "Approve in GitHub", "Trip Kill Switch", "Co-sign (Optional)"])

        # Web push fixed payload
        transport = FakeTransport()
        wp = WebPushChannel(transport, _default_secrets())
        wp.send("operator-primary", "kv://vapid-primary-sub", redacted)
        self.assertEqual(transport.calls[0][1], {"title": "avd-buddy needs you", "body": "avd-buddy needs you"})

        # R17 validator rejects forbidden keys and query params
        with self.assertRaises(ValueError):
            assert_r17_compliant({"hostNames": ["sh-01"]})
        with self.assertRaises(ValueError):
            assert_r17_compliant({"url": "https://buddy.example.invalid/plans/p1?target=sh-01"})

    def test_dead_letter_and_heartbeat_and_sms_cap(self) -> None:
        router, _, store, _ = self._make_router()
        dl = router.handle_dead_letter({"id": "dl-1", "planId": "plan-dl"})
        self.assertEqual(dl["urgency"], "critical")
        self.assertEqual(dl["status"], "delivered")

        hb = router.emit_heartbeat()
        self.assertEqual(hb["eventType"], "router.heartbeat")
        self.assertIsNotNone(store.get_last_heartbeat())

        # SMS cap check
        store.add_monthly_sms_spend("2026-11", 25.00)
        redacted = build_redacted_notification(
            NotificationEvent(event_type="budget.alert", plan_id="p-cap", action="budget.alert"),
            urgency="critical",
        )
        sms_res = router.sms_voice.send("operator-primary", "kv://sms-primary", redacted, month_key="2026-11")
        self.assertFalse(sms_res.ok)
        self.assertEqual(sms_res.error, "sms-monthly-spend-cap-exceeded")


if __name__ == "__main__":
    unittest.main()
