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

    def test_catalog_rate_limits_circuit_breaker_run_command_and_secrets(self):
        # Unknown action outside catalog/actions/ is refused
        unknown = valid_plan()
        unknown["action"] = "host.nonexistent"
        res_unknown = gate_apply.check_plan(unknown)
        self.assertEqual(res_unknown["status"], "refused")
        self.assertTrue(any("not in catalog/actions/" in r for r in res_unknown["reasons"]))

        # Per-hour rate limit (host.drain cap is 4/hour)
        over_hour = valid_live_state()
        over_hour["plansInLastHour"] = 4
        res_hour = gate_apply.check_plan(valid_plan(over_hour))
        self.assertEqual(res_hour["status"], "refused")
        self.assertTrue(any("limits.perHour" in r for r in res_hour["reasons"]))

        # Per-host-per-day rate limit (host.restart-agent cap is 1/day)
        over_day = valid_live_state()
        over_day["plansForHostInLast24h"] = 1
        restart_plan = valid_plan(over_day)
        restart_plan["action"] = "host.restart-agent"
        res_day = gate_apply.check_plan(restart_plan)
        self.assertEqual(res_day["status"], "refused")
        self.assertTrue(any("limits.perHostPerDay" in r for r in res_day["reasons"]))

        # 24-hour circuit breaker trips at 3 failures
        tripped = valid_live_state()
        tripped["failuresInLast24h"] = 3
        res_cb = gate_apply.check_plan(valid_plan(tripped))
        self.assertEqual(res_cb["status"], "refused")
        self.assertTrue(any("circuit breaker tripped" in r for r in res_cb["reasons"]))

        # Run Command validation: valid pinned hash & identity pass; tampered hash, wrong identity, or parameters fail
        good_rc = valid_plan()
        good_rc["action"] = "host.restart-agent"
        good_rc["runCommand"] = {
            "script": "catalog/actions/scripts/Restart-AvdAgent.ps1",
            "sha256": "a1fe2c42e8aa6b589455e108ca6935be63382ad0b5f232128d29b504713996c8",
            "identity": "approve-restart-agent",
            "deleteRunCommandAfter": True,
        }
        self.assertEqual(gate_apply.check_plan(good_rc)["status"], "ready")

        bad_rc = json.loads(json.dumps(good_rc))
        bad_rc["runCommand"]["sha256"] = "0" * 64
        bad_rc["runCommand"]["identity"] = "approve-group"
        bad_rc["runCommand"]["parameters"] = ["--force"]
        res_bad_rc = gate_apply.check_plan(bad_rc)
        self.assertEqual(res_bad_rc["status"], "refused")
        self.assertTrue(any("runCommand.sha256" in r for r in res_bad_rc["reasons"]))
        self.assertTrue(any("runCommand.identity" in r for r in res_bad_rc["reasons"]))
        self.assertTrue(any("takes no parameters" in r for r in res_bad_rc["reasons"]))

        # Secrets in plan or state are always refused (host.reregister secretsInActionOnly)
        secret_plan = valid_plan()
        secret_plan["action"] = "host.reregister"
        secret_plan["registrationToken"] = "redacted-placeholder"
        res_secret = gate_apply.check_plan(secret_plan)
        self.assertEqual(res_secret["status"], "refused")
        self.assertTrue(any("secretsInActionOnly" in r for r in res_secret["reasons"]))

    def test_compliance_gate_floor_observer_and_regression(self):
        # may-lower plan refused by default when control is not in governance/compliance.yml
        may_lower = valid_plan()
        may_lower["complianceEffect"] = "may-lower NS-1"
        res_default = gate_apply.check_plan(may_lower)
        self.assertEqual(res_default["status"], "refused")
        self.assertTrue(any("not allowed in governance/compliance.yml" in r for r in res_default["reasons"]))
        self.assertTrue(any("observer verdict" in r for r in res_default["reasons"]))

        # With NS-1 allowed in a temp copy of compliance.yml, check floor and observer verdict
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            (tmp_root / "catalog").mkdir()
            import shutil

            shutil.copytree(ROOT / "catalog/actions", tmp_root / "catalog/actions")
            (tmp_root / "governance").mkdir()
            (tmp_root / "governance/compliance.yml").write_text(
                "floor: 70.0\nverifyMinutes: 30\nstandards:\n  - microsoft-cloud-security-benchmark\nallowedMayLowerControls:\n  - NS-1\n",
                encoding="utf-8",
            )

            low_score_state = valid_live_state()
            low_score_state["observerVerdict"] = "allow"
            low_score_state["secureScore"] = 65.0
            low_score_plan = valid_plan(low_score_state)
            low_score_plan["complianceEffect"] = "may-lower NS-1"
            res_low = gate_apply.check_plan(low_score_plan, root=tmp_root)
            self.assertEqual(res_low["status"], "refused")
            self.assertTrue(any("below compliance floor 70.0" in r for r in res_low["reasons"]))

            # Stopping spend is never blocked by the score floor (governance.md section 2.3, 2.5)
            stop_plan = valid_plan(low_score_state)
            stop_plan["action"] = "host.stop"
            stop_plan["complianceEffect"] = "may-lower NS-1"
            self.assertEqual(gate_apply.check_plan(stop_plan, root=tmp_root)["status"], "ready")

            # Above the floor with observer allow passes
            high_score_state = valid_live_state()
            high_score_state["observerVerdict"] = "allow"
            high_score_state["secureScore"] = 82.5
            high_score_plan = valid_plan(high_score_state)
            high_score_plan["complianceEffect"] = "may-lower NS-1"
            self.assertEqual(gate_apply.check_plan(high_score_plan, root=tmp_root)["status"], "ready")

        # Post-action compliance regression rolls back and fails verification
        plan = valid_plan()
        approved_hash = gate_apply.check_plan(plan)["planHash"]
        regressed = gate_apply.apply_approved_plan(
            plan=plan,
            approved_hash=approved_hash,
            read_flag=lambda: "run",
            read_live_state=valid_live_state,
            apply_action=lambda _p, _s: {"drained": True, "complianceRegressed": True},
            verify_action=lambda _p, _s, _e: True,
        )
        self.assertEqual(regressed["status"], "verification-failed")
        self.assertTrue(any("compliance.regressed" in r for r in regressed["reasons"]))


if __name__ == "__main__":
    unittest.main()

