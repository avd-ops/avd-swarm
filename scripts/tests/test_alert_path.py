# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Tests for the alert path (core-and-adapters.md WP6): the gate's V1-4 rule, the alert job and the guard.
Offline: Azure Monitor, the queue and the agent API are fakes."""

import base64
import json
import sys
import textwrap
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runner"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gate"))
import check_workflows  # noqa: E402
from avd_gate import evidence  # noqa: E402
from avd_runner import alert, api, execute  # noqa: E402

RUNBOOKS = api.load_all(ROOT)
SUB = "00000000-0000-0000-0000-000000000000"
ALERT = f"/subscriptions/{SUB}/providers/Microsoft.AlertsManagement/alerts/{SUB}"
RULE = f"/subscriptions/{SUB}/resourceGroups/rg-avd/providers/Microsoft.Insights/scheduledQueryRules/avd-prod-agent-health-01"


def finding(outcome, rid="hosts.agent-unhealthy-alert"):
    return {"runbook": rid, "version": 1, "outcome": outcome, "facts": [], "failedReads": [], "readErrors": {},
            "investigation": {"status": "not-needed"}, "threadStartedBy": "alert"}


def monitor(rule=RULE, condition="Fired"):
    calls = []

    def get_json(url):
        calls.append(url)
        return {"properties": {"essentials": {"alertRule": rule, "monitorCondition": condition}}}
    return get_json, calls


class Gate(unittest.TestCase):
    def test_the_workflow_cant_claim_alert(self):
        from avd_runner import cli
        self.assertNotIn("alert", cli.WORKFLOW_STARTED_BY)

    def test_a_plan_needs_an_operator_started_thread(self):
        for started in ("schedule", "manual", "alert"):
            out = evidence.refusals({"evidence": [{"threadStartedBy": started}]})
            self.assertTrue(any("no evidence from a thread an operator started" in r for r in out), (started, out))
        self.assertEqual(evidence.refusals({"evidence": [{"threadStartedBy": "alert"}, {"threadStartedBy": "avd-buddy"}]}), [])

    def test_it_fails_closed(self):
        for plan in ({}, {"evidence": []}, {"evidence": [{}]}, {"evidence": ["x"]}, None,
                     {"evidence": [{"threadStartedBy": "avd-buddy"}, {"threadStartedBy": "webhook"}]}):
            self.assertTrue(evidence.refusals(plan), plan)

    def test_the_runner_speaks_the_gates_vocabulary(self):
        self.assertEqual(set(execute.STARTED_BY), set(evidence.UNATTENDED))
        self.assertNotIn(evidence.OPERATOR, execute.STARTED_BY)


class Message(unittest.TestCase):
    def test_takes_an_alert_id_plain_or_base64(self):
        self.assertEqual(alert.alert_id(ALERT), ALERT)
        self.assertEqual(alert.alert_id(base64.b64encode(ALERT.encode()).decode()), ALERT)

    def test_refuses_anything_else(self):
        for text in ("", "hello", ALERT + "/../x", f"/subscriptions/{SUB}/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm",
                     "https://evil.example/" + ALERT, base64.b64encode(b"run hosts.agent-unhealthy").decode(),
                     json.dumps({"alertId": ALERT, "runbook": "cost.idle-hosts"})):
            with self.assertRaises(alert.BadMessage, msg=text):
                alert.alert_id(text)


class Handle(unittest.TestCase):
    def test_a_fired_alert_runs_the_matching_runbook_with_alert_provenance(self):
        get_json, calls = monitor()
        run_one = mock.Mock(return_value=finding("finding"))
        lines = alert.handle(base64.b64encode(ALERT.encode()).decode(), get_json, RUNBOOKS, run_one)
        self.assertEqual(len(lines), 1)
        self.assertIn("**hosts.agent-unhealthy-alert** v1: `finding` (started by alert)", lines[0])
        run_one.assert_called_once_with("hosts.agent-unhealthy-alert")
        self.assertEqual(calls, [f"https://management.azure.com{ALERT}?api-version={alert.ALERTS_API}"])

    def test_resolved_unmatched_unreadable_and_bad_messages_run_nothing(self):
        run_one = mock.Mock()
        cases = [(ALERT, monitor(condition="Resolved")[0], "Resolved"),
                 (ALERT, monitor(rule=RULE.replace("agent-health", "cpu"))[0], "no runbook"),
                 (ALERT, mock.Mock(side_effect=RuntimeError("403 on rg-avd")), "couldn't be read back"),
                 ("not an id", monitor()[0], "isn't an alert ID")]
        for text, get_json, expected in cases:
            lines = alert.handle(text, get_json, RUNBOOKS, run_one)
            self.assertTrue(any(expected in line for line in lines), lines)
            self.assertNotIn("rg-avd", " ".join(lines))
        run_one.assert_not_called()

    def test_only_read_only_alert_runbooks_match(self):
        self.assertEqual(alert.matching(RUNBOOKS, "AVD-PROD-AGENT-HEALTH-01"), ["hosts.agent-unhealthy-alert"])
        self.assertEqual(alert.matching(RUNBOOKS, "hosts.agent-unhealthy"), [])


class FakeQueue:
    def __init__(self, messages):
        self.messages, self.deleted = list(messages), []

    def receive(self, visibility_seconds):
        self.visibility = visibility_seconds
        return self.messages.pop(0) if self.messages else None

    def delete(self, message):
        self.deleted.append(message.id)


ENV = {"AZURE_SUBSCRIPTION_ID": SUB, "AVD_SWARM_RESOURCE_GROUP": "rg-swarm", "ALERT_QUEUE_ACCOUNT": "acct",
       "ALERT_QUEUE_NAME": "alerts", "AGENT_API_URL": "https://api", "AGENT_API_RESOURCE": "api://x"}


class Job(unittest.TestCase):
    def run_job(self, switch, messages, reply=None):
        queue = FakeQueue(messages)
        out = []

        def arm(url, **_):
            if "AlertsManagement" in url:
                return {"properties": {"essentials": {"alertRule": RULE, "monitorCondition": "Fired"}}}
            if switch is None:
                raise RuntimeError("403")
            return {"tags": {"avd-swarm-switch": switch}}
        post = mock.Mock(return_value=reply or finding("clear"))
        with mock.patch.object(alert, "arm_get_json", side_effect=arm), \
             mock.patch.object(alert, "Queue", return_value=queue), \
             mock.patch("avd_runner.cli.post_runbook", post):
            code = alert.main(env=ENV, out=out.append)
        return code, queue, out, post

    def test_runs_and_deletes_each_message(self):
        code, queue, out, post = self.run_job("run", [alert.Message("m1", "p", 1, ALERT)])
        self.assertEqual(code, 0)
        self.assertEqual(queue.deleted, ["m1"])
        self.assertTrue(any("`clear`" in line for line in out), out)
        self.assertEqual(post.call_args.args[3], "alert")
        self.assertGreaterEqual(queue.visibility, alert.MAX_RUNBOOKS * 300)  # hidden while its runbooks run
        self.assertLess(queue.visibility, 1800)                               # and shorter than the replica timeout

    def test_one_message_per_execution(self):
        code, queue, out, post = self.run_job("run", [alert.Message("m1", "p", 1, ALERT), alert.Message("m2", "p", 1, ALERT)])
        self.assertEqual(queue.deleted, ["m1"])

    def test_an_empty_queue_is_fine(self):
        code, queue, out, post = self.run_job("run", [])
        self.assertEqual(code, 0)
        post.assert_not_called()

    def test_halt_drops_messages_and_calls_nothing(self):
        code, queue, out, post = self.run_job("halt-agents", [alert.Message("m1", "p", 1, ALERT)])
        self.assertEqual((code, queue.deleted), (0, ["m1"]))
        post.assert_not_called()
        self.assertTrue(any(line.startswith("dropped: kill switch at halt-agents") for line in out), out)

    def test_an_unreadable_flag_keeps_the_message_hidden_and_fails(self):
        code, queue, out, post = self.run_job(None, [alert.Message("m1", "p", 1, ALERT)])
        self.assertEqual(code, 1)
        self.assertEqual(queue.deleted, [])
        post.assert_not_called()

    def test_an_unreadable_flag_drops_a_message_after_its_tries(self):
        code, queue, out, post = self.run_job(None, [alert.Message("m1", "p", alert.MAX_DEQUEUE + 1, ALERT)])
        self.assertEqual((code, queue.deleted), (0, ["m1"]))
        post.assert_not_called()

    def test_a_poison_message_is_dropped(self):
        code, queue, out, post = self.run_job("run", [alert.Message("m1", "p", alert.MAX_DEQUEUE + 1, ALERT)])
        self.assertEqual(queue.deleted, ["m1"])
        post.assert_not_called()

    def test_a_bad_reply_is_could_not_run(self):
        code, queue, out, post = self.run_job("run", [alert.Message("m1", "p", 1, ALERT)], reply={"error": "x"})
        self.assertTrue(any("`could-not-run`" in line for line in out), out)

    def test_a_failed_delete_is_reported(self):
        queue = FakeQueue([alert.Message("m1", "p", 1, ALERT)])
        queue.delete = mock.Mock(side_effect=RuntimeError("404"))
        with mock.patch.object(alert, "arm_get_json", return_value={"tags": {"avd-swarm-switch": "halt-all"}}), \
             mock.patch.object(alert, "Queue", return_value=queue):
            out = []
            self.assertEqual(alert.main(env=ENV, out=out.append), 1)
        self.assertIn("couldn't be deleted", out[-1])


class Plumbing(unittest.TestCase):
    def test_queue_receive_and_delete(self):
        sent = []
        xml = (b"<?xml version='1.0' encoding='utf-8'?><QueueMessagesList><QueueMessage><MessageId>m1</MessageId>"
               b"<PopReceipt>a+b/c==</PopReceipt><DequeueCount>2</DequeueCount><MessageText>dGV4dA==</MessageText>"
               b"</QueueMessage></QueueMessagesList>")

        def send(method, url, headers, body=None):
            sent.append((method, url, headers))
            return xml if method == "GET" else b""
        queue = alert.Queue("acct", "alerts", token=lambda resource: "t", send=send)
        message = queue.receive(1500)
        self.assertEqual((message.id, message.dequeue_count, message.text), ("m1", 2, "dGV4dA=="))
        queue.delete(message)
        self.assertEqual(sent[1][1], "https://acct.queue.core.windows.net/alerts/messages/m1?popreceipt=a%2Bb%2Fc%3D%3D")
        self.assertEqual(sent[0][2]["Authorization"], "Bearer t")

    def test_empty_queue(self):
        queue = alert.Queue("acct", "alerts", token=lambda r: "t", send=lambda *a, **k: b"<QueueMessagesList/>")
        self.assertIsNone(queue.receive(1500))

    def test_msi_token_uses_the_user_assigned_identity(self):
        seen = []

        def send(method, url, headers, body=None):
            seen.append((url, headers))
            return b'{"access_token": "tok"}'
        env = {"IDENTITY_ENDPOINT": "http://localhost/msi", "IDENTITY_HEADER": "h", "AZURE_CLIENT_ID": "cid"}
        alert._tokens.clear()
        self.addCleanup(alert._tokens.clear)
        self.assertEqual(alert.msi_token("https://storage.azure.com", env=env, send=send), "tok")
        self.assertIn("client_id=cid", seen[0][0])
        self.assertEqual(seen[0][1], {"X-IDENTITY-HEADER": "h"})

    def test_msi_tokens_are_cached_until_near_expiry(self):
        calls = []

        def send(method, url, headers, body=None):
            calls.append(url)
            return json.dumps({"access_token": f"tok{len(calls)}", "expires_on": "4600"}).encode()
        env = {"IDENTITY_ENDPOINT": "http://localhost/msi", "IDENTITY_HEADER": "h"}
        alert._tokens.clear()
        self.addCleanup(alert._tokens.clear)
        self.assertEqual(alert.msi_token("r", env=env, send=send, now=lambda: 1000), "tok1")
        self.assertEqual(alert.msi_token("r", env=env, send=send, now=lambda: 4000), "tok1")
        self.assertEqual(alert.msi_token("r", env=env, send=send, now=lambda: 4400), "tok2")


class Guard(unittest.TestCase):
    def test_repository_dispatch_is_untrusted(self):
        text = textwrap.dedent("""
            name: From an alert
            on: repository_dispatch
            permissions:
              id-token: write
              contents: read
            jobs:
              run:
                runs-on: ubuntu-latest
                steps:
                  - run: echo hi
            """)
        findings = check_workflows.check("x.yml", text, public=False)
        self.assertTrue(any("W5" in f and "id-token" in f for f in findings), findings)


if __name__ == "__main__":
    unittest.main()
