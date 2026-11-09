# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Tests for gate/avd_gate/apply.py and templates/workflows/approve.yml (spec section 5, 5a, 6d, 6f; avd-swarm#43)."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gate"))
sys.path.insert(0, str(ROOT / "scripts"))
import check_workflows  # noqa: E402
from avd_gate import apply as gate_apply  # noqa: E402


def valid_live_state() -> dict:
    return {
        "targetDigest": "host-digest-01",
        "allowNewSession": True,
        "heartbeatFresh": True,
        "activeSessions": 0,
        "recentConnectionsInWindow": False,
        "powerLocked": False,
        "scalingExcluded": False,
        "powerState": "running",
        "availableHostsAfterAction": 3,
        "requiredAvailableHostsFloor": 2,
        "inFlightPlanOnPool": None,
        "serviceHealthClear": True,
    }


def valid_plan(live: dict | None = None) -> dict:
    state = live if live is not None else valid_live_state()
    return {
        "planId": "plan-20261108-0001",
        "action": "host.drain",
        "targetSurface": "avd",
        "evidence": [{"threadStartedBy": "avd-buddy", "factId": "c11.sessionHosts.status@hp-3fa2c1d0"}],
        "why": {"kind": "fix", "decision": "dec-0001"},
        "rollback": {"restoreAllowNewSession": True},
        "preconditionState": state,
    }


class GateApplyTests(unittest.TestCase):
    def test_valid_plan_passes_gate_and_applies_when_live_state_matches_hash(self):
        plan = valid_plan()
        checked = gate_apply.check_plan(plan)
        self.assertEqual(checked["status"], "ready", checked)
        self.assertEqual(checked["failedCount"], 0)
        self.assertEqual(checked["passedCount"], 4)

        applied_calls = []
        res = gate_apply.apply_approved_plan(
            plan=plan,
            approved_hash=checked["planHash"],
            read_flag=lambda: "run",
            read_live_state=valid_live_state,
            apply_action=lambda p, s: (applied_calls.append(p["planId"]) or {"drained": True}),
            verify_action=lambda _p, _s, eff: eff.get("drained") is True,
        )
        self.assertEqual(res["status"], "applied-and-verified")
        self.assertEqual(applied_calls, ["plan-20261108-0001"])

        md = gate_apply.redacted_summary_markdown(res)
        self.assertIn("plan-20261108-0001", md)
        self.assertIn("applied-and-verified", md)
        self.assertNotIn("host-digest-01", md)

    def test_kill_switch_halt_actions_or_unreadable_refuses_before_apply(self):
        plan = valid_plan()
        approved_hash = gate_apply.check_plan(plan)["planHash"]
        applied = []
        for flag_fn in (lambda: "halt-actions", lambda: "halt-all", lambda: (_ for _ in ()).throw(RuntimeError("boom"))):
            res = gate_apply.apply_approved_plan(
                plan=plan,
                approved_hash=approved_hash,
                read_flag=flag_fn,
                read_live_state=valid_live_state,
                apply_action=lambda _p, _s: (applied.append(1) or {}),
                verify_action=lambda _p, _s, _e: True,
            )
            self.assertEqual(res["status"], "refused")
        self.assertEqual(applied, [])

    def test_live_state_drift_changes_hash_or_trips_precondition_and_refuses(self):
        plan = valid_plan()
        approved_hash = gate_apply.check_plan(plan)["planHash"]
        applied = []

        # Case 1: another admin already drained the host before apply ran (notDrainedByOthers + hash drift)
        drifted = valid_live_state()
        drifted["allowNewSession"] = False
        res = gate_apply.apply_approved_plan(
            plan=plan,
            approved_hash=approved_hash,
            read_flag=lambda: "run",
            read_live_state=lambda: drifted,
            apply_action=lambda _p, _s: (applied.append(1) or {}),
            verify_action=lambda _p, _s, _e: True,
        )
        self.assertEqual(res["status"], "refused")
        self.assertEqual(applied, [])

        # Case 2: preconditions pass, but targetDigest changed underneath the approved hash
        retargeted = valid_live_state()
        retargeted["targetDigest"] = "host-digest-02"
        res2 = gate_apply.apply_approved_plan(
            plan=plan,
            approved_hash=approved_hash,
            read_flag=lambda: "run",
            read_live_state=lambda: retargeted,
            apply_action=lambda _p, _s: (applied.append(1) or {}),
            verify_action=lambda _p, _s, _e: True,
        )
        self.assertEqual(res2["status"], "refused")
        self.assertTrue(any("differs from approved hash" in r for r in res2["reasons"]))
        self.assertEqual(applied, [])

    def test_missing_positive_verification_fails_closed(self):
        plan = valid_plan()
        approved_hash = gate_apply.check_plan(plan)["planHash"]
        res = gate_apply.apply_approved_plan(
            plan=plan,
            approved_hash=approved_hash,
            read_flag=lambda: "run",
            read_live_state=valid_live_state,
            apply_action=lambda _p, _s: {"drained": True},
            verify_action=lambda _p, _s, _e: False,
        )
        self.assertEqual(res["status"], "verification-failed")
        self.assertEqual(res["failedCount"], 1)

    def test_host_preconditions_and_why_rules_fail_closed(self):
        bad_plan = valid_plan()
        bad_plan["why"] = {"kind": "fix", "draft": {"alternatives": []}}
        self.assertTrue(any("C18" in r for r in gate_apply.why_refusals(bad_plan)))

        occupied = valid_live_state()
        occupied["activeSessions"] = 2
        self.assertTrue(any("noActiveSessions" in r for r in gate_apply.host_precondition_refusals("host.restart-agent", occupied)))

        deallocated = valid_live_state()
        deallocated["powerState"] = "deallocated"
        self.assertTrue(any("deallocated" in r for r in gate_apply.host_precondition_refusals("host.drain", deallocated)))

    def test_cli_and_approve_workflow_template(self):
        wf_path = ROOT / "templates/workflows/approve.yml"
        self.assertTrue(wf_path.is_file())
        self.assertEqual(
            check_workflows.check(str(wf_path), wf_path.read_text(encoding="utf-8"), public=True, template=True),
            [],
        )
        self.assertEqual(
            check_workflows.check(str(wf_path), wf_path.read_text(encoding="utf-8"), public=False, template=False),
            [],
        )

        with tempfile.TemporaryDirectory() as tmp:
            plan = valid_plan()
            plan_file = Path(tmp) / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            check_proc = subprocess.run(
                [sys.executable, "-m", "avd_gate", "check", "--plan", str(plan_file)],
                cwd=str(ROOT),
                env={"PYTHONPATH": str(ROOT / "gate")},
                capture_output=True,
                text=True,
                check=True,
            )
            check_out = json.loads(check_proc.stdout)
            self.assertEqual(check_out["status"], "ready")

            apply_proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "avd_gate",
                    "apply",
                    "--plan",
                    str(plan_file),
                    "--approved-hash",
                    check_out["planHash"],
                ],
                cwd=str(ROOT),
                env={"PYTHONPATH": str(ROOT / "gate")},
                capture_output=True,
                text=True,
                check=True,
            )
            apply_out = json.loads(apply_proc.stdout)
            self.assertEqual(apply_out["status"], "applied-and-verified")

    def test_usgov_c26_contract_and_cloud_boundary_gate(self):
        c26 = json.loads((ROOT / "contracts/cloud-environments.json").read_text(encoding="utf-8"))
        self.assertEqual(c26["schema"], "avd-cloud-environments/v1")
        self.assertEqual(c26["contract"], "C26")
        self.assertEqual(set(c26["clouds"]), {"commercial", "usgov"})
        self.assertEqual(c26["clouds"]["usgov"]["resourceManager"], "https://management.usgovcloudapi.net")
        self.assertEqual(c26["clouds"]["usgov"]["graphEndpoint"], "https://graph.microsoft.us")
        self.assertEqual(c26["clouds"]["usgov"]["loginEndpoint"], "https://login.microsoftonline.us")

        # Valid usgov plan with matching C18 why.cloud, live state cloud, and runner OIDC environment
        usgov_live = valid_live_state()
        usgov_live["cloud"] = "usgov"
        usgov_live["azureEnvironment"] = "AzureUSGovernment"
        usgov_live["graphEnvironment"] = "USGov"
        usgov_plan = valid_plan(usgov_live)
        usgov_plan["cloud"] = "usgov"
        usgov_plan["why"]["cloud"] = "usgov"
        checked = gate_apply.check_plan(usgov_plan)
        self.assertEqual(checked["status"], "ready", checked)
        self.assertNotEqual(checked["planHash"], gate_apply.check_plan(valid_plan())["planHash"])

        # Mismatched C18 why.cloud fails closed
        mismatch_why = valid_plan(usgov_live)
        mismatch_why["cloud"] = "usgov"
        mismatch_why["why"]["cloud"] = "commercial"
        self.assertEqual(gate_apply.check_plan(mismatch_why)["status"], "refused")

        # Mismatched live estate cloud fails closed
        commercial_live = valid_live_state()
        commercial_live["cloud"] = "commercial"
        res = gate_apply.apply_approved_plan(
            plan=usgov_plan,
            approved_hash=checked["planHash"],
            read_flag=lambda: "run",
            read_live_state=lambda: commercial_live,
            apply_action=lambda _p, _s: {"drained": True},
            verify_action=lambda _p, _s, _e: True,
        )
        self.assertEqual(res["status"], "refused")
        self.assertTrue(any("does not match approved C9 plan cloud" in r for r in res["reasons"]))

        # Mismatched runner OIDC Azure/Graph environment fails closed before any action
        bad_oidc = gate_apply.check_plan(
            usgov_plan,
            runner_env={
                "azureEnvironment": "AzureCloud",
                "graphEnvironment": "Global",
                "armEndpoint": "https://management.azure.com",
                "graphEndpoint": "https://graph.microsoft.com",
            },
        )
        self.assertEqual(bad_oidc["status"], "refused")
        self.assertGreaterEqual(bad_oidc["failedCount"], 4)

        # Unsupported Windows 365 capabilities in usgov (MHN and Frontline) fail closed
        w365_plan = valid_plan(usgov_live)
        w365_plan["cloud"] = "usgov"
        w365_plan["why"]["cloud"] = "usgov"
        w365_plan["targetSurface"] = "win365"
        w365_plan["networkConnectionType"] = "mhn"
        w365_plan["provisioningMode"] = "frontline"
        w365_check = gate_apply.check_plan(w365_plan)
        self.assertEqual(w365_check["status"], "refused")
        self.assertTrue(any("MHN" in r for r in w365_check["reasons"]))
        self.assertTrue(any("Frontline" in r for r in w365_check["reasons"]))

    def test_usgov_readonly_agent_and_runner_endpoint_routing(self):
        sys.path.insert(0, str(ROOT / "spikes/s1a/agent_api"))
        sys.path.insert(0, str(ROOT / "gateway"))
        sys.path.insert(0, str(ROOT / "runner"))
        import runbook_endpoint  # noqa: E402
        from avd_gateway import flag as gw_flag  # noqa: E402
        from avd_runner import alert as runner_alert  # noqa: E402

        gov = runbook_endpoint.resolve_cloud_endpoints("usgov", "contoso.onmicrosoft.us")
        self.assertEqual(gov["resourceManager"], "https://management.usgovcloudapi.net")
        self.assertEqual(gov["graphEndpoint"], "https://graph.microsoft.us")
        self.assertEqual(gov["loginEndpoint"], "https://login.microsoftonline.us")
        self.assertEqual(gov["logAnalyticsEndpoint"], "https://api.loganalytics.us")
        self.assertEqual(gov["issuer"], "https://login.microsoftonline.us/contoso.onmicrosoft.us/v2.0")
        with self.assertRaises(ValueError):
            runbook_endpoint.resolve_cloud_endpoints("dod")

        urls = []
        reader = gw_flag.arm_flag_reader(
            lambda u: (urls.append(u) or {"tags": {"avd-swarm-switch": "run"}}),
            "sub-1",
            "rg-1",
            "avd-swarm-switch",
            cloud="usgov",
        )
        self.assertEqual(reader(), "run")
        self.assertTrue(urls[0].startswith("https://management.usgovcloudapi.net/subscriptions/sub-1/"))

        q = runner_alert.Queue("govacct", "alerts", cloud="usgov")
        self.assertEqual(q.base, "https://govacct.queue.core.usgovcloudapi.net/alerts/messages")


if __name__ == "__main__":
    unittest.main()

