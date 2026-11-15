# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Offline unit tests for evals/README.md, evals/agents/cases.yml, and scripts/check_evals.py."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
for rel in ("scripts", "gateway", "runner", "spikes/s1a/agent_api"):
    path_str = str(ROOT / rel)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

import check_evals  # noqa: E402
import specialists  # noqa: E402


class AgentEvalSuiteTests(unittest.TestCase):
    def test_check_evals_passes_on_repo(self):
        self.assertEqual(check_evals.check_evals(ROOT), [])

    def test_coordinator_cases_resolve_through_specialists_handoff(self):
        doc = yaml.safe_load((ROOT / "evals" / "agents" / "cases.yml").read_text(encoding="utf-8"))
        coord_cases = [c for c in doc["cases"] if c["agent"] == "coordinator"]
        self.assertGreaterEqual(len(coord_cases), 5)

        for case in coord_cases:
            target = case.get("expect_handoff")
            if target is None:
                self.assertIsNone(
                    specialists.resolve_handoff([{"type": "text", "text": "Outside scope."}]),
                    case["id"],
                )
            else:
                tool_name = specialists.SPECIALISTS[target].handoff_tool_name
                self.assertEqual(
                    specialists.resolve_handoff([{"type": "tool_use", "name": tool_name, "id": "1", "input": {}}]),
                    target,
                    case["id"],
                )

    def test_specialist_prompts_include_write_refusal_rules(self):
        doc = yaml.safe_load((ROOT / "evals" / "agents" / "cases.yml").read_text(encoding="utf-8"))
        for case in doc["cases"]:
            if not case.get("must_refuse_write"):
                continue
            profile = specialists.SPECIALISTS[case["agent"]]
            for pattern in case.get("expect_patterns") or []:
                self.assertRegex(profile.system_prompt, pattern, case["id"])


if __name__ == "__main__":
    unittest.main()
