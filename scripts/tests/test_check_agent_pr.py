# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Tests for scripts/check_agent_pr.py (spec section 6b, section 6e; governance section 2.1)."""

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_agent_pr  # noqa: E402

POLICY = yaml.safe_load((ROOT / "governance/protected-paths.yml").read_text(encoding="utf-8"))


def pr(author, commit_logins=()):
    return {"author": {"login": author}, "commits": [{"authors": [{"login": c}]} for c in commit_logins]}


class AgentPr(unittest.TestCase):
    def test_improve_app_pr_touching_governance_fails(self):
        changes = check_agent_pr.parse_name_status("M\tgovernance/protected-paths.yml\nM\tdocs/spec.md\n")
        findings = check_agent_pr.check(POLICY, pr("app/avd-swarm-improve"), changes)
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("governance/protected-paths.yml", findings[0])

    def test_agent_commit_on_a_persons_pr_still_counts(self):
        changes = check_agent_pr.parse_name_status("M\t.github/workflows/guard.yml\n")
        self.assertTrue(check_agent_pr.check(POLICY, pr("nickprignano", ["avd-swarm-scribe[bot]"]), changes))

    def test_rename_out_of_a_protected_path_counts(self):
        changes = check_agent_pr.parse_name_status("R100\tCLAUDE.md\tdocs/old-claude.md\n")
        self.assertTrue(check_agent_pr.check(POLICY, pr("app/avd-swarm-scribe"), changes))

    def test_evals_are_append_only_for_agents(self):
        added = check_agent_pr.parse_name_status("A\tspikes/s1a/evals/new-case.json\n")
        changed = check_agent_pr.parse_name_status("M\tspikes/s1a/evals/questions.json\n")
        deleted = check_agent_pr.parse_name_status("D\tspikes/s1a/evals/questions.json\n")
        self.assertEqual(check_agent_pr.check(POLICY, pr("app/avd-swarm-improve"), added), [])
        self.assertTrue(check_agent_pr.check(POLICY, pr("app/avd-swarm-improve"), changed))
        self.assertTrue(check_agent_pr.check(POLICY, pr("app/avd-swarm-improve"), deleted))

    def test_agent_pr_outside_protected_paths_passes(self):
        changes = check_agent_pr.parse_name_status("M\tdocs/spec.md\nA\tdocs/lessons/0001.md\n")
        self.assertEqual(check_agent_pr.check(POLICY, pr("app/avd-swarm-improve"), changes), [])

    def test_a_persons_pr_is_not_checked_here(self):
        changes = check_agent_pr.parse_name_status("M\tgovernance/protected-paths.yml\n")
        self.assertEqual(check_agent_pr.check(POLICY, pr("nickprignano", ["nickprignano"]), changes), [])

    def test_the_checks_and_their_policy_protect_themselves(self):
        for path in ("scripts/check_agent_pr.py", "scripts/check_workflows.py", "governance/protected-paths.yml",
                     ".github/workflows/guard.yml", "scripts/tests/test_check_agent_pr.py", "gateway/avd_gateway/meter.py"):
            self.assertTrue(any(check_agent_pr.matches(path, p) for p in POLICY["protected"]), path)


if __name__ == "__main__":
    unittest.main()
