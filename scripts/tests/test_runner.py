# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Tests for the check runner, runner/avd_runner (core-and-adapters.md section 3.3; red team P-2, P-6; R17).
Offline: reads, the agent, az and the agent API are fakes."""

import io
import json
import shutil
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runner"))
sys.path.insert(0, str(ROOT / "scripts"))
import check_runbooks  # noqa: E402
from avd_runner import api, cli, execute, report  # noqa: E402
from avd_runner.runbook import parse  # noqa: E402
from avd_gateway.prices import SpendRefused  # noqa: E402

RUNBOOKS = api.load_all(ROOT)
HEALTH = RUNBOOKS["hosts.agent-unhealthy"]
SECRET_HOST = "avdlz-sh-secret-0"


class FakeReads:
    def __init__(self, kql=None, tools=None, fail=()):
        self.kql_rows, self.tool_rows, self.fail = kql or {}, tools or {}, set(fail)

    def tool(self, name):
        if name in self.fail:
            raise ConnectionError(f"{name} failed for {SECRET_HOST}")
        return self.tool_rows.get(name, [])

    def kql(self, query_id):
        if query_id in self.fail:
            raise TimeoutError(f"{query_id} timed out on {SECRET_HOST}")
        return self.kql_rows.get(query_id, [])


HEALTHY = FakeReads(kql={"wvd-agent-health-latest-7d": [{"SessionHostName": SECRET_HOST, "Problem": 0}]})
SICK = FakeReads(kql={"wvd-agent-health-latest-7d": [{"SessionHostName": SECRET_HOST, "Problem": 1}]})


class Execute(unittest.TestCase):
    def test_clear_run_never_calls_the_agent(self):
        agent = mock.Mock()
        finding = execute.run(HEALTH, HEALTHY, agent, "schedule")
        self.assertEqual(finding["outcome"], "clear")
        agent.assert_not_called()
        self.assertEqual(finding["investigation"], {"status": "not-needed"})

    def test_finding_asks_the_agent_with_the_runbook_budget(self):
        agent = mock.Mock(return_value={"claims": [{"claim": "x"}], "unanswered": []})
        finding = execute.run(HEALTH, SICK, agent, "schedule")
        self.assertEqual(finding["outcome"], "finding")
        self.assertEqual(agent.call_args.args[3], 0.5)
        self.assertEqual(finding["investigation"]["status"], "answered")

    def test_a_refused_investigation_leaves_the_finding(self):
        def refused(*_):
            raise execute.Refused("kill switch at halt-agents: no model calls")
        finding = execute.run(HEALTH, SICK, refused, "schedule")
        self.assertEqual(finding["outcome"], "finding")
        self.assertEqual(finding["investigation"]["status"], "refused")

    def test_the_gateways_own_refusal_is_refused_not_failed(self):
        def capped(*_):
            raise SpendRefused("daily cap reached")
        finding = execute.run(HEALTH, SICK, capped, "schedule")
        self.assertEqual(finding["investigation"], {"status": "refused", "reason": "daily cap reached"})

    def test_a_failed_read_is_could_not_run_and_carries_no_message(self):
        reads = FakeReads(fail={"wvd-agent-health-latest-7d"})
        finding = execute.run(HEALTH, reads, mock.Mock(), "manual")
        self.assertEqual(finding["outcome"], "could-not-run")
        self.assertEqual(finding["readErrors"], {"health": "TimeoutError"})
        self.assertNotIn(SECRET_HOST, json.dumps(finding))

    def test_findings_carry_provenance_and_no_rows(self):
        finding = execute.run(HEALTH, SICK, None, "schedule")
        self.assertEqual(finding["threadStartedBy"], "schedule")
        self.assertEqual(finding["investigation"]["status"], "not-available")
        self.assertNotIn(SECRET_HOST, json.dumps(finding))
        for started_by in ("buddy", "avd-buddy"):  # the runner never claims an operator started it
            with self.assertRaises(ValueError):
                execute.run(HEALTH, SICK, None, started_by)


class Report(unittest.TestCase):
    def test_issue_on_change(self):
        cases = [(None, "clear", "none"), (None, "finding", "open"), (None, "could-not-run", "open"),
                 ("finding", "finding", "none"), ("finding", "could-not-run", "comment"), ("finding", "clear", "close"),
                 ("could-not-run", "clear", "close"), ("unknown", "finding", "comment"), ("unknown", "clear", "close")]
        for previous, outcome, action in cases:
            self.assertEqual(report.issue_action(previous, outcome), action, (previous, outcome))

    def test_summary_has_values_not_names(self):
        finding = execute.run(HEALTH, SICK, mock.Mock(return_value={"claims": [{"claim": SECRET_HOST}]}), "schedule")
        text = report.summary(finding)
        self.assertIn("unhealthyHosts", text)
        self.assertIn("1 claims", text)
        self.assertNotIn(SECRET_HOST, text)

    def test_checks_block_counts_only(self):
        findings = [execute.run(HEALTH, r, None, "schedule") for r in (HEALTHY, SICK, SICK)]
        block = report.checks_block(findings)
        self.assertEqual(block["runs"], 3)
        self.assertEqual(block["runbooks"], [{"id": "hosts.agent-unhealthy", "clear": 1, "finding": 2, "could-not-run": 0}])
        self.assertNotIn(SECRET_HOST, json.dumps(block))


class Api(unittest.TestCase):
    def test_runs_a_known_read_only_runbook(self):
        finding = api.handle({"runbook": "hosts.agent-unhealthy", "startedBy": "schedule"}, RUNBOOKS, HEALTHY, None)
        self.assertEqual(finding["outcome"], "clear")

    def test_refuses_what_it_shouldnt_run(self):
        for body in ({"runbook": "hosts.unknown", "startedBy": "schedule"}, {"runbook": "hosts.agent-unhealthy"},
                     {"runbook": "hosts.agent-unhealthy", "startedBy": "schedule", "kql": "WVDConnections | take 10"},
                     ["hosts.agent-unhealthy"]):
            with self.assertRaises(api.BadRequest, msg=repr(body)):
                api.handle(body, RUNBOOKS, HEALTHY, None)
        with self.assertRaises(api.BadRequest):
            api.handle({"runbook": "cost.idle-hosts", "startedBy": "manual"}, RUNBOOKS, HEALTHY, None, allowed_agents={"hosts"})


class Cli(unittest.TestCase):
    def test_select_by_cron(self):
        self.assertEqual(cli.select(ROOT, "0 6 * * 1-5"), ["hosts.agent-unhealthy"])
        self.assertEqual(cli.select(ROOT, "0  7 * * 1"), ["cost.idle-hosts"])
        self.assertEqual(cli.select(ROOT, "0 0 * * *"), [])

    def test_flag_refuses_at_halt_agents_and_when_unreadable(self):
        refuse = ["halt-agents", "halt-all"]
        with mock.patch.object(cli, "arm_get_json", return_value={"tags": {"avd-swarm-switch": "run"}}):
            self.assertEqual(cli.flag_position("s", "rg", "avd-swarm-switch", refuse), (0, "run"))
        with mock.patch.object(cli, "arm_get_json", return_value={"tags": {"avd-swarm-switch": "halt-agents"}}):
            self.assertEqual(cli.flag_position("s", "rg", "avd-swarm-switch", refuse)[0], cli.REFUSED)
        with mock.patch.object(cli, "arm_get_json", side_effect=RuntimeError("az rest failed (1)")):
            self.assertEqual(cli.flag_position("s", "rg", "avd-swarm-switch", refuse)[0], cli.UNREADABLE)

    def test_flag_command_exit_code(self):
        with mock.patch.object(cli, "arm_get_json", return_value={"tags": {"avd-swarm-switch": "halt-all"}}):
            self.assertEqual(cli.main(["flag", "--subscription", "s", "--resource-group", "rg"]), cli.REFUSED)
        with mock.patch.object(cli, "arm_get_json", return_value={}):  # a missing tag fails the job, visibly
            self.assertEqual(cli.main(["flag", "--subscription", "s", "--resource-group", "rg"]), cli.UNREADABLE)

    def test_unknown_runbook_ids_stop_before_any_call(self):
        for bad in ("*", "../x", 'a"b', "hosts.unknown"):
            self.assertEqual(cli.main(["title", "--id", bad]), cli.UNKNOWN_RUNBOOK)
            self.assertEqual(cli.main(["run", "--id", bad, "--out", "/nonexistent/f.json"]), cli.UNKNOWN_RUNBOOK)

    def test_a_reply_that_isnt_a_finding_is_could_not_run(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        env = {"AGENT_API_URL": "https://api", "AGENT_API_RESOURCE": "api://x"}
        for reply in ({"error": "boom"}, {"runbook": "hosts.agent-unhealthy", "outcome": "error"},
                      {"runbook": "cost.idle-hosts", "outcome": "clear"}, ["clear"]):
            with mock.patch.dict("os.environ", env), mock.patch.object(cli, "post_runbook", return_value=reply):
                self.assertEqual(cli.main(["run", "--id", "hosts.agent-unhealthy", "--out", str(tmp / "f.json")]), 0)
            self.assertEqual(json.loads((tmp / "f.json").read_text())["outcome"], "could-not-run", reply)

    def test_http_errors_keep_their_status(self):
        error = urllib.error.HTTPError("https://api/runbook", 403, "Forbidden", {}, None)
        self.assertEqual(cli.unreachable("hosts.agent-unhealthy", "schedule", error)["readErrors"],
                         {"agent-api": "HTTPError 403 Forbidden"})

    def test_unreachable_agent_api_is_could_not_run(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        with mock.patch.dict("os.environ", {"AGENT_API_URL": "", "AGENT_API_RESOURCE": ""}):
            self.assertEqual(cli.main(["run", "--id", "hosts.agent-unhealthy", "--out", str(tmp / "f.json")]), 0)
        finding = json.loads((tmp / "f.json").read_text())
        self.assertEqual(finding["outcome"], "could-not-run")
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(cli.main(["issue", "--finding", str(tmp / "f.json"), "--summary", str(tmp / "s.md")]), 0)
        self.assertEqual(out.getvalue().split(), ["open", "could-not-run"])

    def test_token_is_never_printed(self):
        with mock.patch.object(cli, "az", return_value="eyJ.secret.token\n"), \
             mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b'{"outcome": "clear"}'
            self.assertEqual(cli.post_runbook("https://api", "api://x", "hosts.agent-unhealthy", "schedule"), {"outcome": "clear"})
            request = urlopen.call_args.args[0]
            self.assertEqual(request.get_header("Authorization"), "Bearer eyJ.secret.token")
            self.assertEqual(json.loads(request.data), {"runbook": "hosts.agent-unhealthy", "startedBy": "schedule"})


class Template(unittest.TestCase):
    def test_template_crons_match_runbooks(self):
        self.assertEqual(check_runbooks.template_crons(ROOT), {"0 6 * * 1-5", "15 6 * * 1-5", "0 7 * * 1"})

    def test_a_runbook_with_no_cron_fails(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        for name in ("runbooks", "catalog", "contracts", "governance", "templates"):
            shutil.copytree(ROOT / name, tmp / name)
        path = tmp / "runbooks/hosts/connection-errors.md"
        path.write_text(path.read_text().replace('"15 6 * * 1-5"', '"30 6 * * 1-5"'))
        errors = check_runbooks.check(tmp)
        self.assertTrue(any('no cron for "30 6 * * 1-5"' in e for e in errors), errors)
        self.assertTrue(any('cron "15 6 * * 1-5" starts no runbook' in e for e in errors), errors)

    def test_the_adopters_live_copy_is_checked_too(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        for name in ("runbooks", "catalog", "contracts", "governance", "templates"):
            shutil.copytree(ROOT / name, tmp / name)
        live = tmp / ".github/workflows/runbooks.yml"
        live.parent.mkdir(parents=True)
        live.write_text((ROOT / "templates/workflows/runbooks.yml").read_text().replace('"0 7 * * 1"', '"0 8 * * 1"'))
        errors = check_runbooks.check(tmp)
        self.assertTrue(any(e.startswith(".github/workflows/runbooks.yml: no cron for \"0 7 * * 1\"") for e in errors), errors)

    def test_a_duplicate_runbook_id_stops_start_up(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        shutil.copytree(ROOT / "runbooks", tmp / "runbooks")
        shutil.copy(tmp / "runbooks/cost/idle-hosts.md", tmp / "runbooks/cost/idle-hosts-copy.md")
        with self.assertRaises(ValueError):
            api.load_all(tmp)

    def test_runbooks_parse(self):
        self.assertEqual(parse("x", (ROOT / "runbooks/cost/idle-hosts.md").read_text()).id, "cost.idle-hosts")


if __name__ == "__main__":
    unittest.main()
