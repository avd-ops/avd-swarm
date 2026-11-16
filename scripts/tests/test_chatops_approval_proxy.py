# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Offline tests for Phase 2 Direct ChatOps Approval Proxy (docs/notifications.md section 5)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "gate"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from avd_gate import apply as gate_apply  # noqa: E402
from notifications.chatops import ChatOpsApprovalProxy, ChatOpsApprovalRequest  # noqa: E402


class FakeGitHubTransport:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def post_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> tuple[int, str]:
        self.calls.append((url, payload, dict(headers or {})))
        return self.status, "{}"


class ChatOpsApprovalProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.full_hash = "b" * 64
        self.transport = FakeGitHubTransport()
        self.switch_pos = "run"
        self.current_hash = self.full_hash
        self.proxy = ChatOpsApprovalProxy(
            transport=self.transport,
            read_switch_position=lambda: self.switch_pos,
            read_current_plan_hash=lambda _pid: self.current_hash,
        )

    def _base_req(self, **overrides: Any) -> ChatOpsApprovalRequest:
        defaults: dict[str, Any] = {
            "platform": "teams",
            "entra_oid": "oid-operator-1",
            "github_user_token": "ghu_per_user_oauth_token_123",
            "github_actor": "operator-gh",
            "plan_id": "plan-chatops-01",
            "full_plan_hash": self.full_hash,
            "owner": "avd-ops",
            "repo": "avd-swarm",
            "run_id": 12345,
            "environment_id": 99,
        }
        defaults.update(overrides)
        return ChatOpsApprovalRequest(**defaults)

    def test_valid_per_user_oauth_approval_calls_deployment_review_api(self) -> None:
        res = self.proxy.approve(self._base_req())
        self.assertEqual(res["status"], "approved")
        self.assertEqual(res["approvedBy"], "operator-gh")
        self.assertEqual(len(self.transport.calls), 1)
        url, payload, headers = self.transport.calls[0]
        self.assertEqual(url, "https://api.github.com/repos/avd-ops/avd-swarm/actions/runs/12345/pending_deployments")
        self.assertEqual(payload["environment_ids"], [99])
        self.assertEqual(payload["state"], "approved")
        self.assertEqual(headers["Authorization"], "Bearer ghu_per_user_oauth_token_123")

    def test_rejects_shared_bot_and_installation_tokens(self) -> None:
        for bad in (
            {"github_user_token": "ghs_installation_token"},
            {"token_type": "installation"},
            {"is_shared_bot_token": True},
            {"github_actor": "avd-swarm[bot]"},
        ):
            res = self.proxy.approve(self._base_req(**bad))
            self.assertEqual(res["status"], "refused")
            self.assertEqual(len(self.transport.calls), 0)

    def test_rejects_missing_entra_oid_prefix_hash_and_hash_drift(self) -> None:
        # Missing Entra OID
        r1 = self.proxy.approve(self._base_req(entra_oid=""))
        self.assertEqual(r1["status"], "refused")

        # 8-char prefix hash rejected
        r2 = self.proxy.approve(self._base_req(full_plan_hash=self.full_hash[:8]))
        self.assertEqual(r2["status"], "refused")

        # Hash drift rejected
        self.current_hash = "c" * 64
        r3 = self.proxy.approve(self._base_req())
        self.assertEqual(r3["status"], "refused")
        self.assertEqual(len(self.transport.calls), 0)

    def test_kill_switch_blocks_standard_plans_but_allows_stop_spend(self) -> None:
        self.switch_pos = "halt-actions"
        r_std = self.proxy.approve(self._base_req(stop_spend=False))
        self.assertEqual(r_std["status"], "refused")
        self.assertEqual(len(self.transport.calls), 0)

        r_stop = self.proxy.approve(self._base_req(stop_spend=True))
        self.assertEqual(r_stop["status"], "approved")
        self.assertEqual(len(self.transport.calls), 1)

    def test_avd_gate_apply_still_refuses_on_live_drift_after_chatops_approval(self) -> None:
        plan = {
            "planId": "plan-chatops-drift",
            "action": "host.drain",
            "cloud": "commercial",
            "targetSurface": "avd",
            "targetScope": "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-avd-prod/providers/Microsoft.DesktopVirtualization/hostPools/hp-01/sessionHosts/sh-01",
            "evidence": [{"threadStartedBy": "avd-buddy"}],
            "why": {"kind": "fix", "decision": "0001"},
            "rollback": {"restoreAllowNewSession": True},
        }
        state = {
            "cloud": "commercial",
            "targetDigest": "sha256:sh-01-v1",
            "targetScope": plan["targetScope"],
            "allowNewSession": True,
            "heartbeatFresh": True,
            "activeSessions": 0,
            "recentConnectionsInWindow": False,
            "powerLocked": False,
            "scalingExcluded": False,
            "powerState": "running",
            "availableHostsAfterAction": 2,
            "requiredAvailableHostsFloor": 2,
            "inFlightPlanOnPool": None,
            "serviceHealthClear": True,
            "hostsInPlan": 1,
            "hostsTargetedOnPool": 1,
            "usersChangedCount": 0,
            "totalPlansInLastHour": 1,
            "writesInLast24h": 2,
            "spendEstimate": {"estimatedUsd": 0.5, "monthToDateUsd": 1.0, "pendingUsd": 0.0, "unpricedCount": 0, "openRunsOnSandbox": 0},
            "inBusinessHours": False,
        }
        approved_hash = gate_apply.canonical_plan_hash(plan, state)
        self.current_hash = approved_hash
        self.assertEqual(
            self.proxy.approve(self._base_req(plan_id=plan["planId"], full_plan_hash=approved_hash))["status"],
            "approved",
        )

        # Live state drifts before apply_approved_plan runs -> gate still refuses!
        drifted_state = {**state, "targetDigest": "sha256:sh-01-v2"}
        apply_res = gate_apply.apply_approved_plan(
            plan,
            approved_hash,
            read_flag=lambda: "run",
            read_live_state=lambda: drifted_state,
            apply_action=lambda _p, _s: {"applied": True},
            verify_action=lambda _p, _s, _e: True,
            root=ROOT,
        )
        self.assertEqual(apply_res["status"], "refused")


if __name__ == "__main__":
    unittest.main()
