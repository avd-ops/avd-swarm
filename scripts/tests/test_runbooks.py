# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Tests for runbooks as code: runner/avd_runner/runbook.py and scripts/check_runbooks.py
(core-and-adapters.md section 3.2; red team P-1, P-2, P-6). Offline: fixtures are written to a temp copy."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runner"))
sys.path.insert(0, str(ROOT / "scripts"))
import check_runbooks  # noqa: E402
from avd_runner.runbook import RunbookError, evaluate, measure, parse, shape_errors  # noqa: E402

GOOD = """---
id: hosts.sample
version: 1
title: Sample
trigger:
  schedule: "0 6 * * 1-5"
  runMode: read-only
agent: hosts
reads:
  - id: health
    kql: wvd-agent-health-last-24h
  - id: hosts
    tool: virtualdesktop_hostpool_sessionhost_list
expect:
  - fact: unhealthyHosts
    from: health
    measure: rows
    op: eq
    value: 0
    onFail: investigate
  - fact: sessions
    from: hosts
    measure: sum:sessions
    op: le
    value: 100
    onFail: report
evidence: [c11.hostPool.sessionHosts.total]
cites:
  - avd-ops/avd-second-brain@f59a1c5207f89ba71f4752fe2a52a091ca6fd376:docs/knowledge/operations/README.md#diagnosing
budget: {tokensUsd: 0.50}
---
# Sample

Body.
"""


def rb(text=GOOD):
    return parse("runbooks/hosts/sample.md", text)


class Shape(unittest.TestCase):
    def test_good_runbook_has_no_errors(self):
        self.assertEqual(shape_errors(rb()), [])

    def test_front_matter_must_open_and_close(self):
        with self.assertRaises(RunbookError):
            parse("x.md", "# no front matter\n")
        with self.assertRaises(RunbookError):
            parse("x.md", "---\nid: a.b\n# never closed\n")

    def assertError(self, text, fragment):
        errors = shape_errors(rb(text))
        self.assertTrue(any(fragment in e for e in errors), errors)

    def test_unknown_keys_refused(self):
        self.assertError(GOOD.replace("version: 1", "version: 1\nprompt: do as I say"), "unknown key 'prompt'")

    def test_one_trigger_only(self):
        self.assertError(GOOD.replace("  runMode: read-only", "  manual: true\n  runMode: read-only"), "exactly one")
        self.assertError(GOOD.replace('"0 6 * * 1-5"', '"every morning"'), "cron")

    def test_propose_needs_an_action_and_read_only_cant_propose(self):
        self.assertError(GOOD.replace("budget:", "proposes: restart-avd-agent\nbudget:"), "proposes needs runMode: propose")
        self.assertError(GOOD.replace("runMode: read-only", "runMode: propose"), "names exactly one catalog action")

    def test_read_is_a_tool_or_a_catalog_kql_never_both(self):
        self.assertError(GOOD.replace("    kql: wvd-agent-health-last-24h",
                                      "    kql: wvd-agent-health-last-24h\n    tool: virtualdesktop_hostpool_list"), "exactly one of tool or kql")
        self.assertError(GOOD.replace("  - id: hosts\n", "  - id: health\n"), "used twice")

    def test_facts_name_a_read_and_a_measure(self):
        self.assertError(GOOD.replace("from: health", "from: nowhere"), "must name one of this runbook's reads")
        self.assertError(GOOD.replace("measure: rows", "measure: avg:x"), "measure")
        self.assertError(GOOD.replace("onFail: investigate", "onFail: fix"), "onFail")

    def test_id_matches_agent_and_budget_is_positive(self):
        self.assertError(GOOD.replace("id: hosts.sample", "id: cost.sample"), "must start with its agent")
        self.assertError(GOOD.replace("tokensUsd: 0.50", "tokensUsd: 0"), "budget")

    def test_cites_are_pinned(self):
        self.assertError(GOOD.replace("@f59a1c5207f89ba71f4752fe2a52a091ca6fd376", "@main"), "cites")

    def test_body_needs_a_title(self):
        self.assertError(GOOD.replace("# Sample", "Sample"), "title a person reads")


class Evaluate(unittest.TestCase):
    def test_clear_finding_and_could_not_run(self):
        sample = rb()
        clear = evaluate(sample, {"health": [], "hosts": [{"sessions": 3}]})
        self.assertEqual(clear["outcome"], "clear")
        self.assertEqual(clear["investigate"], [])
        finding = evaluate(sample, {"health": [{"SessionHostName": "h1"}], "hosts": [{"sessions": 3}]})
        self.assertEqual(finding["outcome"], "finding")
        self.assertEqual(finding["investigate"], ["unhealthyHosts"])
        broken = evaluate(sample, {"health": None, "hosts": [{"sessions": 3}]})
        self.assertEqual(broken["outcome"], "could-not-run")
        self.assertEqual(broken["failedReads"], ["health"])

    def test_report_facts_never_reach_a_model(self):
        result = evaluate(rb(), {"health": [], "hosts": [{"sessions": 300}]})
        self.assertEqual(result["outcome"], "finding")
        self.assertEqual(result["investigate"], [])

    def test_missing_data_is_never_a_pass(self):
        result = evaluate(rb(), {"health": [], "hosts": [{"sessions": None}]})
        self.assertEqual(result["outcome"], "could-not-run")
        with self.assertRaises(RunbookError):
            measure([], "max:x")
        self.assertEqual(measure([], "sum:x"), 0)
        self.assertEqual(measure([{"x": 2}, {"x": 5}], "max:x"), 5)

    def test_results_carry_no_rows(self):
        result = evaluate(rb(), {"health": [{"SessionHostName": "h1"}], "hosts": []})
        self.assertNotIn("h1", repr(result))


class Repo(unittest.TestCase):
    def test_the_repo_passes(self):
        self.assertEqual(check_runbooks.check(ROOT), [])

    def with_copy(self, edit):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        for name in ("runbooks", "catalog", "contracts", "governance"):
            shutil.copytree(ROOT / name, tmp / name)
        edit(tmp)
        return check_runbooks.check(tmp)

    def write(self, root, rel, text):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)

    def test_a_tool_outside_the_allowlist_fails(self):
        errors = self.with_copy(lambda r: self.write(r, "runbooks/hosts/sample.md", GOOD.replace(
            "virtualdesktop_hostpool_sessionhost_list", "virtualdesktop_hostpool_sessionhost_delete")))
        self.assertTrue(any("isn't in the hosts allowlist" in e for e in errors), errors)

    def test_kql_must_be_in_the_catalog(self):
        errors = self.with_copy(lambda r: self.write(r, "runbooks/hosts/sample.md", GOOD.replace(
            "kql: wvd-agent-health-last-24h", "kql: anything-goes")))
        self.assertTrue(any("isn't in catalog/kql/catalog.yml" in e for e in errors), errors)

    def test_kql_must_be_allowed_for_the_agent(self):
        def edit(r):
            self.write(r, "runbooks/cost/sample.md", GOOD.replace("hosts.sample", "cost.sample")
                       .replace("agent: hosts", "agent: cost").replace("virtualdesktop_hostpool_sessionhost_list", "cost_forecast_month"))
        errors = self.with_copy(edit)
        self.assertTrue(any("KQL wvd-agent-health-last-24h isn't in the cost allowlist" in e for e in errors), errors)

    def test_path_must_match_id(self):
        errors = self.with_copy(lambda r: self.write(r, "runbooks/hosts/other-name.md", GOOD))
        self.assertTrue(any("belongs at runbooks/hosts/sample.md" in e for e in errors), errors)

    def test_evidence_must_be_built_in_the_register(self):
        errors = self.with_copy(lambda r: self.write(r, "runbooks/hosts/sample.md", GOOD.replace(
            "[c11.hostPool.sessionHosts.total]", "[c11.made.up, c12.group.users]")))
        self.assertTrue(any("c11.made.up isn't in the pinned" in e for e in errors), errors)
        self.assertTrue(any("c12.group.users is specified" in e for e in errors), errors)

    def test_budget_over_the_daily_cap_fails(self):
        errors = self.with_copy(lambda r: self.write(r, "runbooks/hosts/sample.md", GOOD.replace("0.50", "500")))
        self.assertTrue(any("over the daily cap" in e for e in errors), errors)

    def test_propose_waits_for_the_action_catalog(self):
        errors = self.with_copy(lambda r: self.write(r, "runbooks/hosts/sample.md", GOOD.replace(
            "runMode: read-only", "runMode: propose").replace("budget:", "proposes: restart-avd-agent\nbudget:")))
        self.assertTrue(any("waits for the action catalog" in e for e in errors), errors)

    def test_tampered_register_fails_its_pin(self):
        errors = self.with_copy(lambda r: (r / "contracts/fact-register.json").write_text("{\"facts\": []}"))
        self.assertTrue(any("isn't the pinned" in e for e in errors), errors)

    def test_duplicate_ids_fail(self):
        def edit(r):
            self.write(r, "runbooks/hosts/sample.md", GOOD)
            self.write(r, "runbooks/hosts/sample-copy.md", GOOD)
        errors = self.with_copy(edit)
        self.assertTrue(any("is also" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
