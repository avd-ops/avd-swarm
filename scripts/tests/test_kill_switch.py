# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Tests for scripts/check_kill_switch.py, templates/kill-switch/main.bicep, and governance/watchdog.yml
(governance.md sections 2.3 and 2.4; R5, R6, R13, V1-3, V1-15, WP5)."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_kill_switch  # noqa: E402
import check_timelock  # noqa: E402


class KillSwitchTests(unittest.TestCase):
    def test_repo_kill_switch_and_watchdog_pass(self):
        self.assertEqual(check_kill_switch.check(ROOT), [])

    def test_four_position_drill_invariants(self):
        drill = check_kill_switch.simulate_drill(ROOT)
        self.assertTrue(drill["passed"])
        self.assertTrue(drill["machineResetRefused"])
        self.assertTrue(drill["retrippedOnIncompleteReset"])
        self.assertIn("sre-agent", drill["adaptersCovered"])
        self.assertIn("foundry", drill["adaptersCovered"])

        steps = {s["position"]: s for s in drill["steps"][:4]}
        # halt-actions disables approve writes while keeping agent model calls and reads
        self.assertFalse(steps["halt-actions"]["approveWritesAllowed"])
        self.assertTrue(steps["halt-actions"]["agentModelCallsAllowed"])
        # halt-agents stops agent model calls and refuses non-human MCP callers while keeping person reads
        self.assertFalse(steps["halt-agents"]["agentModelCallsAllowed"])
        self.assertEqual(
            steps["halt-agents"]["mcpCallers"],
            {"person": True, "sre-agent": False, "foundry": False, "claude-code": False},
        )
        # halt-all stops operator reads too, while stop-spend remains allowed at every position
        self.assertFalse(steps["halt-all"]["operatorReadsAllowed"])
        for pos, snap in steps.items():
            self.assertTrue(snap["stopSpendWritesAllowed"], pos)
            self.assertEqual(
                snap["preservedRoleAssignments"],
                {"stop-spend": True, "landing-zone-deploy": True, "operator": True},
            )

    def with_copy(self, edit):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        shutil.copytree(ROOT / "governance", tmp / "governance")
        shutil.copytree(ROOT / "templates/kill-switch", tmp / "templates/kill-switch")
        shutil.copytree(ROOT / "adapters", tmp / "adapters")
        edit(tmp)
        return check_kill_switch.check(tmp)

    def test_bicep_forbids_capability_adding_actions_and_requires_abac(self):
        def add_role_write(r):
            p = r / "templates/kill-switch/main.bicep"
            p.write_text(
                p.read_text(encoding="utf-8").replace(
                    "'Microsoft.Authorization/roleAssignments/delete'",
                    "'Microsoft.Authorization/roleAssignments/delete'\n          'Microsoft.Authorization/roleAssignments/write'",
                ),
                encoding="utf-8",
            )

        errors = self.with_copy(add_role_write)
        self.assertTrue(any("forbidden capability-adding action(s) (R5)" in e for e in errors), errors)

        def drop_abac(r):
            p = r / "templates/kill-switch/main.bicep"
            p.write_text(p.read_text(encoding="utf-8").replace("conditionVersion: '2.0'", ""), encoding="utf-8")

        errors2 = self.with_copy(drop_abac)
        self.assertTrue(any("conditionVersion '2.0' ABAC delegation condition" in e for e in errors2), errors2)

    def test_watchdog_rules_and_timelock_classification(self):
        def drop_rule(r):
            p = r / "governance/watchdog.yml"
            text = p.read_text(encoding="utf-8").replace("  - id: write.unapproved\n", "  - id: custom.other\n")
            p.write_text(text, encoding="utf-8")

        errors = self.with_copy(drop_rule)
        self.assertTrue(any("missing required section 2.4 watchdog rule(s): write.unapproved" in e for e in errors), errors)

        policy = yaml.safe_load((ROOT / "governance/protected-paths.yml").read_text(encoding="utf-8"))
        watchdog_yaml = (ROOT / "governance/watchdog.yml").read_text(encoding="utf-8")

        # Tighter threshold or higher switch position lowers capability
        tighter = watchdog_yaml.replace("maxPlansProposedPerHour: 6", "maxPlansProposedPerHour: 4")
        res_low = check_timelock.classify_change("governance/watchdog.yml", watchdog_yaml, tighter, policy)
        self.assertEqual(res_low["effect"], "lowers")

        # Looser threshold or weaker switch position raises capability
        looser = watchdog_yaml.replace("maxPlansProposedPerHour: 6", "maxPlansProposedPerHour: 10")
        res_high = check_timelock.classify_change("governance/watchdog.yml", watchdog_yaml, looser, policy)
        self.assertEqual(res_high["effect"], "raises")


if __name__ == "__main__":
    unittest.main()
