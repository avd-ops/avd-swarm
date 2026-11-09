# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Tests for scripts/check_actions.py (spec section 5a) and scripts/check_timelock.py (governance section 2.6)."""

from datetime import datetime, timedelta, timezone
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_actions  # noqa: E402
import check_timelock  # noqa: E402


class ActionCatalogTests(unittest.TestCase):
    def test_repo_action_catalog_passes(self):
        self.assertEqual(check_actions.check(ROOT), [])

    def with_copy(self, edit):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        shutil.copytree(ROOT / "catalog/actions", tmp / "catalog/actions")
        edit(tmp)
        return check_actions.check(tmp)

    def test_missing_s2_action_fails(self):
        errors = self.with_copy(lambda r: (r / "catalog/actions/host.stop.yml").unlink())
        self.assertTrue(any("missing required S2 action(s): host.stop" in e for e in errors), errors)

    def test_preconditions_and_zero_sessions_enforced(self):
        def loosen_power_lock(r):
            p = r / "catalog/actions/host.drain.yml"
            p.write_text(p.read_text(encoding="utf-8").replace("notPowerLocked: true", "notPowerLocked: false"), encoding="utf-8")

        errors = self.with_copy(loosen_power_lock)
        self.assertTrue(any("preconditions.notPowerLocked must be True" in e for e in errors), errors)

        def allow_active_sessions_on_restart(r):
            p = r / "catalog/actions/host.restart-agent.yml"
            p.write_text(p.read_text(encoding="utf-8").replace("noActiveSessions: true", "noActiveSessions: false"), encoding="utf-8")

        errors2 = self.with_copy(allow_active_sessions_on_restart)
        self.assertTrue(any("host.restart-agent requires preconditions.noActiveSessions: true" in e for e in errors2), errors2)

    def test_limits_and_positive_verification_enforced(self):
        def bad_limits_and_verify(r):
            p = r / "catalog/actions/host.stop.yml"
            text = (
                p.read_text(encoding="utf-8")
                .replace("circuitBreakerFailures24h: 3", "circuitBreakerFailures24h: 5")
                .replace("positiveEvidenceRequired: true", "positiveEvidenceRequired: false")
            )
            p.write_text(text, encoding="utf-8")

        errors = self.with_copy(bad_limits_and_verify)
        self.assertTrue(any("circuitBreakerFailures24h must be an integer between 1 and 3" in e for e in errors), errors)
        self.assertTrue(any("verify.positiveEvidenceRequired must be true" in e for e in errors), errors)

    def test_run_command_script_hash_and_version_header_enforced(self):
        def tamper_script(r):
            script = r / "catalog/actions/scripts/Restart-AvdAgent.ps1"
            script.write_text("Restart-Service RDAgentBootLoader\n", encoding="utf-8")

        errors = self.with_copy(tamper_script)
        self.assertTrue(any("must start with '#Requires -Version 5.1'" in e for e in errors), errors)
        self.assertTrue(any("doesn't match catalog/actions/scripts/Restart-AvdAgent.ps1 sha256" in e for e in errors), errors)

    def test_reregister_requires_secrets_in_action_only(self):
        def drop_secret_flag(r):
            p = r / "catalog/actions/host.reregister.yml"
            p.write_text(p.read_text(encoding="utf-8").replace("secretsInActionOnly: true", "secretsInActionOnly: false"), encoding="utf-8")

        errors = self.with_copy(drop_secret_flag)
        self.assertTrue(any("host.reregister must set secretsInActionOnly: true" in e for e in errors), errors)


class TimelockTests(unittest.TestCase):
    def setUp(self):
        self.policy = yaml.safe_load((ROOT / "governance/protected-paths.yml").read_text(encoding="utf-8"))
        self.drain_yaml = (ROOT / "catalog/actions/host.drain.yml").read_text(encoding="utf-8")
        self.gateway_yaml = (ROOT / "governance/gateway.yml").read_text(encoding="utf-8")
        self.hosts_allow = (ROOT / "governance/allowlists/hosts.yml").read_text(encoding="utf-8")
        self.protected_yaml = (ROOT / "governance/protected-paths.yml").read_text(encoding="utf-8")

    def test_action_manifest_tightening_lowers_and_loosening_raises(self):
        tighter = self.drain_yaml.replace("perHour: 4", "perHour: 2").replace("noActiveSessions: false", "noActiveSessions: true")
        res_low = check_timelock.classify_change("catalog/actions/host.drain.yml", self.drain_yaml, tighter, self.policy)
        self.assertEqual(res_low["effect"], "lowers")

        looser = self.drain_yaml.replace("perHour: 4", "perHour: 8")
        res_high = check_timelock.classify_change("catalog/actions/host.drain.yml", self.drain_yaml, looser, self.policy)
        self.assertEqual(res_high["effect"], "raises")

        added = check_timelock.classify_change("catalog/actions/host.new.yml", None, self.drain_yaml, self.policy)
        self.assertEqual(added["effect"], "raises")

        removed = check_timelock.classify_change("catalog/actions/host.drain.yml", self.drain_yaml, None, self.policy)
        self.assertEqual(removed["effect"], "lowers")

    def test_gateway_allowlist_protected_paths_and_workflows_classification(self):
        gw_low = check_timelock.classify_change(
            "governance/gateway.yml",
            self.gateway_yaml,
            self.gateway_yaml.replace("dailyUsd: 5.00", "dailyUsd: 2.50"),
            self.policy,
        )
        self.assertEqual(gw_low["effect"], "lowers")

        gw_high = check_timelock.classify_change(
            "governance/gateway.yml",
            self.gateway_yaml,
            self.gateway_yaml.replace("dailyUsd: 5.00", "dailyUsd: 10.00"),
            self.policy,
        )
        self.assertEqual(gw_high["effect"], "raises")

        allow_low = check_timelock.classify_change(
            "governance/allowlists/hosts.yml",
            self.hosts_allow,
            self.hosts_allow.replace("  - cloudpc_anc_list\n", ""),
            self.policy,
        )
        self.assertEqual(allow_low["effect"], "lowers")

        allow_high = check_timelock.classify_change(
            "governance/allowlists/hosts.yml",
            self.hosts_allow,
            self.hosts_allow + "  - extra_tool\n",
            self.policy,
        )
        self.assertEqual(allow_high["effect"], "raises")

        prot_low = check_timelock.classify_change(
            "governance/protected-paths.yml",
            self.protected_yaml,
            self.protected_yaml + "  - buddy/**\n",
            self.policy,
        )
        self.assertEqual(prot_low["effect"], "lowers")

        wf_high = check_timelock.classify_change(
            ".github/workflows/guard.yml",
            "name: Guard\n",
            "name: GuardUpdated\n",
            self.policy,
        )
        self.assertEqual(wf_high["effect"], "raises")

        compliance_yaml = (ROOT / "governance/compliance.yml").read_text(encoding="utf-8")
        comp_low = check_timelock.classify_change(
            "governance/compliance.yml",
            compliance_yaml,
            compliance_yaml.replace("floor: 70.0", "floor: 80.0"),
            self.policy,
        )
        self.assertEqual(comp_low["effect"], "lowers")
        comp_high = check_timelock.classify_change(
            "governance/compliance.yml",
            compliance_yaml,
            compliance_yaml.replace("floor: 70.0", "floor: 60.0"),
            self.policy,
        )
        self.assertEqual(comp_high["effect"], "raises")

        unprotected = check_timelock.classify_change("docs/spec.md", "a", "b", self.policy)
        self.assertFalse(unprotected["protected"])
        self.assertEqual(unprotected["effect"], "unprotected")

        rb_text = (ROOT / "runbooks/hosts/agent-unhealthy.md").read_text(encoding="utf-8")
        rb_prose = check_timelock.classify_change(
            "runbooks/hosts/agent-unhealthy.md", rb_text, rb_text + "\nMore notes.\n", self.policy
        )
        self.assertFalse(rb_prose["protected"])
        self.assertEqual(rb_prose["effect"], "unprotected")

        rb_propose = rb_text.replace("runMode: read-only", "runMode: propose\nproposes: host.restart-agent")
        rb_high = check_timelock.classify_change(
            "runbooks/hosts/agent-unhealthy.md", rb_text, rb_propose, self.policy
        )
        self.assertTrue(rb_high["protected"])
        self.assertEqual(rb_high["effect"], "raises")

        rb_low = check_timelock.classify_change(
            "runbooks/hosts/agent-unhealthy.md", rb_propose, rb_text, self.policy
        )
        self.assertTrue(rb_low["protected"])
        self.assertEqual(rb_low["effect"], "lowers")

        rb_budget_high = check_timelock.classify_change(
            "runbooks/hosts/agent-unhealthy.md",
            rb_text,
            rb_text.replace("tokensUsd: 0.50", "tokensUsd: 1.50"),
            self.policy,
        )
        self.assertTrue(rb_budget_high["protected"])
        self.assertEqual(rb_budget_high["effect"], "raises")

    def test_solo_mode_timelock_and_clock_reset(self):
        t0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
        low_change = check_timelock.classify_change(
            "governance/gateway.yml",
            self.gateway_yaml,
            self.gateway_yaml.replace("dailyUsd: 5.00", "dailyUsd: 2.50"),
            self.policy,
        )
        solo_low = check_timelock.evaluate_timelock(
            [low_change],
            approvers=["owner"],
            last_commit_utc=t0,
            now_utc=t0 + timedelta(hours=1),
        )
        self.assertTrue(solo_low["passed"])
        self.assertFalse(solo_low["timelockRequired"])

        high_change = check_timelock.classify_change(
            "governance/gateway.yml",
            self.gateway_yaml,
            self.gateway_yaml.replace("dailyUsd: 5.00", "dailyUsd: 9.00"),
            self.policy,
        )
        pending = check_timelock.evaluate_timelock(
            [high_change],
            approvers=["owner"],
            last_commit_utc=t0,
            now_utc=t0 + timedelta(hours=71, minutes=59),
        )
        self.assertFalse(pending["passed"])
        self.assertEqual(pending["status"], "pending")
        self.assertTrue(pending["timelockRequired"])

        passed = check_timelock.evaluate_timelock(
            [high_change],
            approvers=["owner"],
            last_commit_utc=t0,
            now_utc=t0 + timedelta(hours=72),
            pinned_issue_open=True,
        )
        self.assertTrue(passed["passed"])
        self.assertIn("`pass`", check_timelock.format_summary_markdown(passed))

        no_issue = check_timelock.evaluate_timelock(
            [high_change],
            approvers=["owner"],
            last_commit_utc=t0,
            now_utc=t0 + timedelta(hours=75),
            pinned_issue_open=False,
        )
        self.assertFalse(no_issue["passed"])

    def test_team_mode_requires_human_approvals_and_ignores_ai_bots(self):
        t0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
        high_change = check_timelock.classify_change(
            "governance/gateway.yml",
            self.gateway_yaml,
            self.gateway_yaml.replace("dailyUsd: 5.00", "dailyUsd: 9.00"),
            self.policy,
        )
        reviews_with_bots_and_author = [
            {"login": "claude[bot]", "state": "APPROVED", "type": "Bot"},
            {"login": "copilot-pull-request-reviewer", "state": "APPROVED", "type": "User"},
            {"login": "alice", "state": "APPROVED", "type": "User"},  # PR author
            {"login": "bob", "state": "APPROVED", "type": "User"},    # 1st valid human approver
        ]
        pending_team = check_timelock.evaluate_timelock(
            [high_change],
            approvers=["alice", "bob", "carol"],
            last_commit_utc=t0,
            now_utc=t0 + timedelta(hours=2),
            reviews=reviews_with_bots_and_author,
            pr_author="alice",
        )
        self.assertEqual(pending_team["mode"], "team")
        self.assertFalse(pending_team["passed"])
        self.assertEqual(pending_team["validApprovals"], 1)
        self.assertEqual(pending_team["requiredApprovals"], 2)
        self.assertEqual(len(pending_team["ignoredReviews"]), 3)

        approved_team = check_timelock.evaluate_timelock(
            [high_change],
            approvers=["alice", "bob", "carol"],
            last_commit_utc=t0,
            now_utc=t0 + timedelta(hours=2),
            reviews=reviews_with_bots_and_author + [{"login": "carol", "state": "APPROVED", "type": "User"}],
            pr_author="alice",
        )
        self.assertTrue(approved_team["passed"])
        self.assertEqual(approved_team["validApprovals"], 2)


if __name__ == "__main__":
    unittest.main()
