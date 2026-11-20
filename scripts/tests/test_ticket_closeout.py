import io
import json
import unittest

from runner.avd_runner.ticket_closeout import (
    MockTicketAdapter,
    closeout_ticket,
    create_ticket_adapter,
    format_closeout_note,
    normalize_ticket_ref,
)


class _FakeResp:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8") if payload is not None else b""

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class TicketCloseoutTests(unittest.TestCase):
    def test_closeout_mock_resolves_on_succeeded(self):
        adapter = MockTicketAdapter([{"id": "AVD-7", "status": "awaiting_approval"}])
        record = {
            "planId": "plan-001",
            "planHash": "a" * 64,
            "action": "host.drain",
            "passedCount": 1,
            "failedCount": 0,
            "status": "succeeded",
        }
        res = closeout_ticket(record, {"provider": "mock", "ticketId": "AVD-7", "correlationId": "c-7"}, adapter=adapter)
        self.assertEqual(res["status"], "resolved")
        ticket = adapter.get_ticket("AVD-7")
        self.assertEqual(ticket["status"], "resolved")
        self.assertEqual(len(ticket["comments"]), 1)
        self.assertFalse(ticket["comments"][0]["internal"])
        self.assertIn("passed=1, failed=0", ticket["comments"][0]["body"])

    def test_closeout_mock_returns_to_triaging_on_failure(self):
        adapter = MockTicketAdapter([{"id": "AVD-8", "status": "awaiting_approval"}])
        record = {
            "planId": "plan-002",
            "planHash": "b" * 64,
            "action": "host.restart-agent",
            "passedCount": 0,
            "failedCount": 1,
            "status": "failed",
        }
        res = closeout_ticket(record, {"provider": "mock", "ticketId": "AVD-8"}, adapter=adapter)
        self.assertEqual(res["status"], "triaging")
        ticket = adapter.get_ticket("AVD-8")
        self.assertTrue(ticket["comments"][0]["internal"])

    def test_servicenow_is_reserved_for_avd_servicenow(self):
        with self.assertRaises(NotImplementedError):
            create_ticket_adapter("servicenow")

    def test_jira_jsm_closeout_posts_comment_and_transition(self):
        calls = []

        def fake_opener(req):
            calls.append((req.get_method(), req.full_url, json.loads(req.data.decode("utf-8")) if req.data else None))
            if req.full_url.endswith("/comment"):
                return _FakeResp({"id": "1001"})
            if req.full_url.endswith("/transitions") and req.get_method() == "GET":
                return _FakeResp({"transitions": [{"id": "41", "name": "Resolved", "to": {"name": "Resolved"}}]})
            if req.full_url.endswith("/transitions") and req.get_method() == "POST":
                return _FakeResp(None)
            raise AssertionError(f"Unexpected URL: {req.full_url}")

        adapter = create_ticket_adapter(
            "jira-jsm",
            base_url="https://contoso.atlassian.net",
            email="bot@contoso.com",
            api_token="fake-token",
            opener=fake_opener,
        )
        record = {"planId": "plan-9", "planHash": "c" * 64, "action": "host.drain", "passedCount": 1, "failedCount": 0, "status": "succeeded"}
        res = closeout_ticket(record, {"provider": "jira-jsm", "ticketId": "AVD-9"}, adapter=adapter)
        self.assertEqual(res["status"], "resolved")
        self.assertEqual([c[0] for c in calls], ["POST", "GET", "POST"])

    def test_rejects_invalid_ticket_id(self):
        with self.assertRaises(ValueError):
            normalize_ticket_ref({"provider": "mock", "ticketId": "bad ticket id!"})
        note = format_closeout_note({"planId": "p1", "planHash": "d" * 64, "action": "host.drain", "passedCount": 1, "failedCount": 0, "status": "succeeded"}, {"provider": "mock", "ticketId": "AVD-1"})
        self.assertIn("AVD-1", note)


if __name__ == "__main__":
    unittest.main()
