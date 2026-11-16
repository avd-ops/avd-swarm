# avd-swarm S2 drill runner unit tests (avd-swarm#43).
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import run_s2_drill  # noqa: E402


class TestS2DrillRunner(unittest.TestCase):
    def test_refuses_bare_go_or_wrong_amount(self) -> None:
        for bad in ("go", "yes", "go batch s2-drill-01", "go batch s2-drill-01 $2.00", "go batch other-01 $1.50"):
            with self.assertRaises(ValueError):
                run_s2_drill.parse_approval(bad)

    def test_accepts_exact_approval_and_passes_5_steps(self) -> None:
        ev = run_s2_drill.run_drill(approval_phrase="go batch s2-drill-01 $1.50", mode="harness")
        self.assertEqual(ev["status"], "passed", json.dumps(ev))
        self.assertEqual(ev["batchId"], "s2-drill-01")
        self.assertEqual(ev["approvedTotalUsd"], 1.50)

        # Step 1: 4-position kill switch drill
        step1 = ev["step1KillSwitchDrill"]
        self.assertTrue(step1["passed"])
        self.assertEqual([p["position"] for p in step1["positions"]], ["run", "halt-actions", "halt-agents", "halt-all"])
        self.assertEqual(step1["positions"][0]["runnerWriteGateExit"], 0)
        self.assertEqual(step1["positions"][1]["runnerWriteGateExit"], 3)
        self.assertEqual(step1["positions"][1]["gateApplyStatus"], "refused")
        self.assertFalse(step1["positions"][2]["gatewayModelCallsAllowed"])
        self.assertEqual(
            step1["positions"][2]["mcpCallersAllowed"],
            {"person": True, "foundry": False, "runner": False, "sre-agent": False, "claude-code": False},
        )
        self.assertEqual(step1["resetPosition"], "run")

        # Step 2: First approved write (`host.drain`)
        step2 = ev["step2FirstWriteDrain"]
        self.assertEqual(step2["action"], "host.drain")
        self.assertEqual(step2["gateCheckStatus"], "ready")
        self.assertEqual(step2["gatePassedCount"], 4)
        self.assertEqual(step2["gateFailedCount"], 0)
        self.assertEqual(step2["applyStatus"], "applied-and-verified")
        self.assertEqual(len(step2["planHash"]), 64)

        # Step 3: Post-action verification & live-drift guard
        step3 = ev["step3PostActionVerifyAndDriftGuard"]
        self.assertTrue(step3["passed"])
        self.assertIs(step3["postDrainAllowNewSession"], False)
        self.assertEqual(step3["tamperedHashStatus"], "refused")
        self.assertEqual(step3["otherActorDriftStatus"], "refused")

        # Step 4: Rollback (`undrain`)
        step4 = ev["step4RollbackUndrain"]
        self.assertEqual(step4["gateCheckStatus"], "ready")
        self.assertEqual(step4["applyStatus"], "applied-and-verified")
        self.assertIs(step4["postRollbackAllowNewSession"], True)

        # Step 5: Teardown & $0.00/day meter check
        step5 = ev["step5TeardownAndMeter"]
        self.assertEqual(step5["spendStatus"], "closed")
        self.assertTrue(step5["teardownClean"])
        self.assertEqual(step5["leftoversCount"], 0)
        self.assertEqual(step5["standingPostTeardownUsdPerDay"], 0.0)

    def test_azure_mode_requires_subscription(self) -> None:
        with self.assertRaises(ValueError):
            run_s2_drill.run_drill(approval_phrase="go batch s2-drill-01 $1.50", mode="azure", subscription="")

    def test_azure_mode_uses_real_subscription_and_resource_group_for_flag_checks(self) -> None:
        sub = "11111111-1111-1111-1111-111111111111"
        rg = "rg-custom-avd"
        state = {"flag": "run", "allowNewSession": True}
        seen_urls: list[str] = []

        def fake_az_json(self_backend, *args: str):
            url = args[args.index("--url") + 1]
            method = args[args.index("--method") + 1].lower()
            seen_urls.append(url)
            self.assertNotIn("sub-redacted", url)
            self.assertNotIn("rg-avd-swarm-lab", url)
            if "/sessionHosts/" in url:
                if method == "patch":
                    body = json.loads(args[args.index("--body") + 1])
                    state["allowNewSession"] = body["properties"]["allowNewSession"]
                return {"properties": {"allowNewSession": state["allowNewSession"], "sessions": 0, "status": "Available"}}
            if f"/subscriptions/{sub}/resourcegroups/{rg}" in url.lower():
                if method == "patch":
                    body = json.loads(args[args.index("--body") + 1])
                    state["flag"] = body["properties"]["tags"]["avd-swarm-switch"]
                return {
                    "tags": {"avd-swarm-switch": state["flag"]},
                    "properties": {"tags": {"avd-swarm-switch": state["flag"]}},
                }
            raise AssertionError(f"Unexpected URL: {url}")

        with mock.patch.object(run_s2_drill._AzureBackend, "_az_json", fake_az_json):
            ev = run_s2_drill.run_drill(
                approval_phrase="go batch s2-drill-01 $1.50",
                mode="azure",
                subscription=sub,
                resource_group=rg,
                host_pool="vdpool-test",
                session_host="sh-0",
            )

        self.assertEqual(ev["status"], "passed", json.dumps(ev))
        self.assertTrue(ev["step1KillSwitchDrill"]["passed"])
        self.assertEqual(ev["step1KillSwitchDrill"]["resetPosition"], "run")
        self.assertTrue(any(f"/subscriptions/{sub}/resourcegroups/{rg}" in u.lower() for u in seen_urls))
        self.assertNotIn(sub, json.dumps(ev))


if __name__ == "__main__":
    unittest.main()
