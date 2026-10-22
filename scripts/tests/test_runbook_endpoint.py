# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Tests for the S1a agent API's runbook endpoint (spikes/s1a/agent_api/runbook_endpoint.py): who may start what,
the reads it hands to the runner, and the investigation's prompt. Offline: the MCP server, Log Analytics and
Claude are fakes, and nothing here imports the spike's web stack."""

import asyncio
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runner"))
sys.path.insert(0, str(ROOT / "spikes/s1a/agent_api"))
import runbook_endpoint as rbe  # noqa: E402
from avd_runner import api, execute  # noqa: E402

RUNBOOKS = api.load_all(ROOT)
OPERATOR = "operator-oid"
RUNNER = "runner-oid"
ALERT_JOB = "alert-job-oid"
CALLERS = rbe.parse_callers(f"{RUNNER}=schedule|manual,{ALERT_JOB}=alert")
TOOL = "virtualdesktop_hostpool_sessionhost_list"
HOST_POOL = "/subscriptions/x/resourceGroups/rg/providers/Microsoft.DesktopVirtualization/hostPools/hp"
OPERATOR_CLAIMS = {"oid": OPERATOR, "scp": "Mcp.Tools.ReadWrite"}


def run_now(coroutine):
    return asyncio.run(coroutine)


def la_body(rows, columns=("SessionHostName", "Problem")):
    return {"tables": [{"name": "PrimaryResult", "columns": [{"name": c, "type": "string"} for c in columns],
                        "rows": rows}]}


class Fakes:
    def __init__(self, health=None, tool_text="[]", tool_error=None, answer=None):
        self.health = la_body([["host-a", 0]]) if health is None else health
        self.tool_text, self.tool_error = tool_text, tool_error
        self.answer = answer or {"claims": [{"fact": "unhealthyHosts", "claim": "one host is silent", "read": "health"}],
                                 "unanswered": []}
        self.tool_calls, self.queries, self.prompts = [], [], []

    async def call_tool(self, name, args):
        self.tool_calls.append((name, args))
        if self.tool_error:
            raise self.tool_error
        return self.tool_text

    async def query_logs(self, kql):
        self.queries.append(kql)
        return self.health

    async def investigate(self, instructions, rows, budget):
        self.prompts.append((instructions, rows, budget))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def deps(self, tools=(TOOL,)):
        return rbe.Deps(scope={"hostPoolId": HOST_POOL}, allowed_tools=list(tools), call_tool=self.call_tool,
                        query_logs=self.query_logs, investigate=self.investigate,
                        queries=rbe.catalog_queries(ROOT), log=lambda line: None)


def call(body, claims=OPERATOR_CLAIMS, fakes=None, tools=(TOOL,)):
    fakes = fakes or Fakes()
    deps = fakes.deps(tools)
    status, result = rbe.handle(body, claims, OPERATOR, CALLERS, RUNBOOKS, deps, run_now)
    return status, result, fakes, deps


class Callers(unittest.TestCase):
    def test_each_caller_starts_only_its_own_runs(self):
        runner = {"oid": RUNNER, "roles": ["runbook.run"]}
        alert_job = {"oid": ALERT_JOB, "roles": ["runbook.run"]}
        self.assertEqual(rbe.allowed_started_by(OPERATOR_CLAIMS, OPERATOR, CALLERS), {"manual"})
        self.assertEqual(rbe.allowed_started_by(runner, OPERATOR, CALLERS), {"schedule", "manual"})
        self.assertEqual(rbe.allowed_started_by(alert_job, OPERATOR, CALLERS), {"alert"})

    def test_a_role_without_a_listed_object_id_or_a_listed_id_without_the_role_starts_nothing(self):
        self.assertEqual(rbe.allowed_started_by({"oid": "someone", "roles": ["runbook.run"]}, OPERATOR, CALLERS), set())
        self.assertEqual(rbe.allowed_started_by({"oid": RUNNER, "roles": []}, OPERATOR, CALLERS), set())
        self.assertEqual(rbe.allowed_started_by({"oid": OPERATOR, "scp": "User.Read"}, OPERATOR, CALLERS), set())

    def test_refused_callers_get_403_and_nothing_is_read(self):
        status, _, fakes, _ = call({"runbook": "hosts.agent-unhealthy", "startedBy": "manual"}, claims={"oid": "x"})
        self.assertEqual(status, 403)
        self.assertEqual(fakes.queries, [])

    def test_the_alert_job_cannot_claim_a_schedule_nor_the_operator_an_alert(self):
        status, result, fakes, _ = call({"runbook": "hosts.agent-unhealthy", "startedBy": "schedule"},
                                        claims={"oid": ALERT_JOB, "roles": ["runbook.run"]})
        self.assertEqual((status, fakes.queries), (403, []))
        self.assertIn("alert", result["error"])
        status, _, _, _ = call({"runbook": "hosts.agent-unhealthy-alert", "startedBy": "alert"})
        self.assertEqual(status, 403)

    def test_malformed_caller_lists_fail_at_start(self):
        for text in ("x", f"{RUNNER}=", f"{RUNNER}=schedule|admin", "=alert"):
            with self.assertRaises(ValueError, msg=text):
                rbe.parse_callers(text)
        self.assertEqual(rbe.parse_callers(""), {})


class Requests(unittest.TestCase):
    def test_a_bad_body_or_unknown_runbook_is_400_without_echoing_it(self):
        for body in ([], {"runbook": "hosts.agent-unhealthy"}, {"runbook": "<script>", "startedBy": "manual"},
                     {"runbook": "hosts.agent-unhealthy", "startedBy": "manual", "extra": 1}):
            status, result, fakes, _ = call(body)
            self.assertEqual(status, 400, body)
            self.assertNotIn("<script>", json.dumps(result))
            self.assertEqual(fakes.queries, [])


class Reads(unittest.TestCase):
    def test_a_healthy_estate_is_clear_and_nothing_reaches_the_model(self):
        status, finding, fakes, deps = call({"runbook": "hosts.agent-unhealthy", "startedBy": "manual"})
        self.assertEqual((status, finding["outcome"]), (200, "clear"))
        self.assertEqual(finding["threadStartedBy"], "manual")
        self.assertEqual(fakes.prompts, [])
        self.assertEqual(fakes.tool_calls, [(TOOL, {"hostpool-resource-id": HOST_POOL})])
        self.assertIn("WVDAgentHealthStatus", fakes.queries[0])  # the catalog's query text, from the deployed commit
        self.assertEqual(deps.findings, [finding])

    def test_a_tool_outside_the_allowlist_is_never_called(self):
        status, finding, fakes, _ = call({"runbook": "hosts.agent-unhealthy", "startedBy": "manual"}, tools=())
        self.assertEqual(status, 200)
        self.assertEqual(fakes.tool_calls, [])
        self.assertEqual(finding["readErrors"], {"hosts": "PermissionError"})
        self.assertEqual(finding["outcome"], "clear")  # no fact depends on hosts: it is for the agent only

    def test_a_runbook_for_an_agent_this_api_doesnt_deploy_is_refused(self):
        from avd_runner.runbook import Runbook
        other = dict(RUNBOOKS)
        rb = RUNBOOKS["hosts.agent-unhealthy"]
        other["cost.example"] = Runbook(rb.path, {**rb.meta, "id": "cost.example", "agent": "cost"}, rb.body)
        fakes = Fakes()
        status, _ = rbe.handle({"runbook": "cost.example", "startedBy": "manual"}, OPERATOR_CLAIMS, OPERATOR, CALLERS,
                               other, fakes.deps(), run_now)
        self.assertEqual((status, fakes.queries), (400, []))

    def test_a_call_past_the_deadline_fails_its_read(self):
        fakes = Fakes()

        async def slow(kql):
            await asyncio.sleep(5)
        fakes.query_logs = slow
        original = rbe.DEADLINE_SECONDS
        rbe.DEADLINE_SECONDS = 0.05
        try:
            _, finding, _, _ = call({"runbook": "hosts.agent-unhealthy", "startedBy": "manual"}, fakes=fakes)
        finally:
            rbe.DEADLINE_SECONDS = original
        self.assertEqual(finding["outcome"], "could-not-run")
        self.assertEqual(finding["readErrors"]["health"], "TimeoutError")

    def test_no_reports_is_a_finding_and_a_failed_query_could_not_run(self):
        _, finding, fakes, _ = call({"runbook": "hosts.agent-unhealthy", "startedBy": "manual"},
                                    fakes=Fakes(health=la_body([])))
        self.assertEqual(finding["outcome"], "finding")
        self.assertEqual(fakes.prompts, [])  # reportingHosts is onFail: report
        _, finding, _, _ = call({"runbook": "hosts.agent-unhealthy", "startedBy": "manual"}, fakes=Fakes(health={}))
        self.assertEqual((finding["outcome"], finding["readErrors"]), ("could-not-run", {"health": "TypeError"}))

    def test_tool_results_become_rows(self):
        self.assertEqual(rbe.rows_from_tool_text('[{"name": "a"}]'), [{"name": "a"}])
        self.assertEqual(rbe.rows_from_tool_text('{"sessionHosts": [{"name": "a"}], "count": 1}'), [{"name": "a"}])
        envelope = '{"status": 200, "message": "Success", "results": {"sessionHosts": [{"name": "a"}]}}'
        self.assertEqual(rbe.rows_from_tool_text(envelope), [{"name": "a"}])
        self.assertEqual(rbe.rows_from_tool_text('{"results": {"sessionHosts": []}}'), [])
        for text in ('{"a": [], "b": []}', '"text"', '[1, 2]'):
            with self.assertRaises(TypeError, msg=text):
                rbe.rows_from_tool_text(text)

    def test_every_catalog_query_loads(self):
        queries = rbe.catalog_queries(ROOT)
        self.assertIn("wvd-agent-health-latest-7d", queries)
        self.assertTrue(all(text.strip() for text in queries.values()))


class Investigation(unittest.TestCase):
    def test_an_unhealthy_host_is_investigated_within_the_runbook_budget(self):
        fakes = Fakes(health=la_body([["host-a", 1], ["host-b", 0]]))
        _, finding, _, _ = call({"runbook": "hosts.agent-unhealthy", "startedBy": "manual"}, fakes=fakes)
        self.assertEqual(finding["outcome"], "finding")
        self.assertEqual(finding["investigation"]["status"], "answered")
        instructions, rows, budget = fakes.prompts[0]
        self.assertEqual(budget, float(RUNBOOKS["hosts.agent-unhealthy"].meta["budget"]["tokensUsd"]))
        self.assertIn("unhealthyHosts", instructions)
        self.assertNotIn("host-a", instructions)  # rows travel apart, for the shield to scan as a document
        self.assertEqual(set(json.loads(rows)), {"health", "hosts"})  # every read, the agent-only one included
        self.assertIn("host-a", rows)
        self.assertNotIn("host-a", json.dumps(finding))  # the rows go to the agent, never into the finding

    def test_a_refusal_leaves_the_finding_standing(self):
        fakes = Fakes(health=la_body([["host-a", 1]]), answer=execute.Refused("spend cap"))
        _, finding, _, _ = call({"runbook": "hosts.agent-unhealthy", "startedBy": "manual"}, fakes=fakes)
        self.assertEqual((finding["outcome"], finding["investigation"]["status"]), ("finding", "refused"))


class Memory(unittest.TestCase):
    def test_findings_are_capped(self):
        fakes = Fakes()
        deps = fakes.deps()
        for _ in range(rbe.KEEP + 5):
            rbe.handle({"runbook": "hosts.agent-unhealthy", "startedBy": "manual"}, OPERATOR_CLAIMS, OPERATOR, CALLERS,
                       RUNBOOKS, deps, run_now)
        self.assertEqual(len(deps.findings), rbe.KEEP)


if __name__ == "__main__":
    unittest.main()
