# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Phase 2 tests for avd-assistant 4-stage router and server hardening (docs/assistant-spec.md)."""

import hashlib
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from buddy import assistant_router as ar  # noqa: E402
from buddy import server as buddy_server  # noqa: E402
from buddy import transcript_store as ts  # noqa: E402


class ScopeGateAndWindowTests(unittest.TestCase):
    def test_off_topic_refused_with_vendor_neutral_referral(self):
        ok, msg = ar.classify_scope("Write a poem about Monday")
        self.assertFalse(ok)
        self.assertIn("general-purpose assistant", msg or "")
        for vendor in ("Claude", "ChatGPT", "Gemini"):
            self.assertNotIn(vendor, msg or "")

    def test_in_scope_paraphrase_and_context_not_refused(self):
        ok1, _ = ar.classify_scope("My profile is slow on Mondays")
        ok2, _ = ar.classify_scope("Write a poem about Monday", has_recent_in_scope=True)
        self.assertTrue(ok1)
        self.assertTrue(ok2)

    def test_input_over_1000_chars_refused(self):
        ok, _ = ar.classify_scope("x" * 1001)
        self.assertFalse(ok)

    def test_sliding_window_bounded_and_tagged_by_mode(self):
        turns = [
            {
                "operatorText": f"q{i}",
                "answer": f"a{i}",
                "assistantMode": "red_team" if i % 2 else "guru",
                "aside": "ignored aside",
                "modeBlock": {"docs": [{"summary": "HUGE RETRIEVED BODY"}]},
            }
            for i in range(12)
        ]
        win = ar.build_stage3_window(turns, limit=8)
        self.assertEqual(len(win), 16)  # 8 user + 8 assistant
        serialized = json.dumps(win)
        self.assertNotIn("ignored aside", serialized)
        self.assertNotIn("HUGE RETRIEVED BODY", serialized)
        self.assertTrue(any(item["content"].startswith("[red_team]") for item in win if item["role"] == "assistant"))


class Stage1ModeTests(unittest.TestCase):
    def test_snippet_sha256_gate(self):
        bad = "Remove-AzResourceGroup -Name rg-prod -Force"
        good = "Get-AzWvdSessionHost -ResourceGroupName rg-avd -HostPoolName hp-01"
        allowlist = {hashlib.sha256(good.encode("utf-8")).hexdigest()}
        res = ar.format_code_blocks([bad, good], allowlist)
        self.assertFalse(res[0]["copyable"])
        self.assertTrue(res[0]["unverified"])
        self.assertEqual(res[0]["label"], "unverified, not for pasting")
        self.assertTrue(res[1]["copyable"])
        self.assertFalse(res[1]["unverified"])

    def test_red_team_static_audit_has_no_pass_state(self):
        audit = ar.audit_plan_static(buddy_server.DEFAULT_FIXTURE_PLAN)
        self.assertIn("This is not a gate result.", audit["footer"])
        self.assertEqual(audit["risks"], [])

    def test_chief_of_staff_triage_order(self):
        triage_cfg = ar.load_yaml_safe(ROOT / "governance/assistant-triage.yml")
        items = ar.rank_triage_items(
            {
                "switchPosition": "halt-actions",
                "switchMeaning": "Write path halted.",
                "pendingPlans": 2,
                "spendAlerts": ["Daily token warning"],
                "overdueRuns": ["run-01"],
                "findings": [{"severity": "high", "title": "NSG open", "plane": "azure"}],
            },
            triage_cfg,
        )
        kinds = [i["kind"] for i in items]
        self.assertEqual(
            kinds,
            ["switch-not-run", "pending-operator-approval", "spend-alerts", "overdue-teardowns", "findings-by-severity"],
        )

    def test_no_model_sdk_imports_in_buddy(self):
        forbidden = ("import anthropic", "from anthropic", "import openai", "from openai", "azure.ai")
        for py_file in (ROOT / "buddy").glob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for needle in forbidden:
                self.assertNotIn(needle, text, f"Forbidden SDK import {needle!r} in {py_file.name}")


class AssistantServerHardeningTests(unittest.TestCase):
    def setUp(self):
        self.state = buddy_server.BuddyState(sim_mode=True)
        handler = buddy_server.make_handler(self.state)
        self.httpd = buddy_server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def _post(self, path: str, body: dict) -> tuple[int, dict]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_client_model_or_effort_refused_400(self):
        code, data = self._post("/api/turns", {"question": "status?", "mode": "guru", "idempotencyKey": "k1", "model": "opus"})
        self.assertEqual(code, 400)
        self.assertEqual(data["error"], "client-model-override-forbidden")

    def test_missing_idempotency_key_with_mode_refused_400(self):
        code, data = self._post("/api/turns", {"question": "status?", "mode": "chief_of_staff"})
        self.assertEqual(code, 400)
        self.assertEqual(data["error"], "missing-idempotency-key")

    def test_idempotency_key_deduplicates_turns(self):
        c1, d1 = self._post("/api/turns", {"question": "What needs attention?", "mode": "chief_of_staff", "idempotencyKey": "idem-1"})
        c2, d2 = self._post("/api/turns", {"question": "What needs attention?", "mode": "chief_of_staff", "idempotencyKey": "idem-1"})
        self.assertEqual(c1, 200)
        self.assertEqual(c2, 200)
        self.assertEqual(d1["turnId"], d2["turnId"])
        self.assertEqual(d1["turn"]["assistantMode"], "chief_of_staff")
        self.assertIsNone(d1["turn"]["aside"])

    def test_redacted_transcript_store_and_feedback_stay_tenant_only(self):
        fake_sub = "-".join(["11111111", "2222", "3333", "4444", "555555555555"])
        _, d1 = self._post(
            "/api/turns",
            {
                "question": f"Check hp-sales-01 and 10.20.1.5 for user@example.com in /subscriptions/{fake_sub}",
                "mode": "buddy",
                "idempotencyKey": "idem-redact-1",
            },
        )
        fb_code, fb_data = self._post(
            "/api/feedback",
            {"turnId": d1["turnId"], "rating": "down", "reason": "tone"},
        )
        self.assertEqual(fb_code, 200)
        self.assertTrue(fb_data["tenantOnly"])
        exported = self.state.transcript_store.export_jsonl()
        self.assertNotIn("10.20.1.5", exported)
        self.assertNotIn("user@example.com", exported)
        self.assertNotIn(fake_sub, exported)
        self.assertNotIn("hp-sales-01", exported)
        self.assertIn("<ip>", exported)
        self.assertIn("<operator>", exported)
        self.assertIn("<resource-id>", exported)

    def test_transcript_store_prunes_records_older_than_90_days(self):
        store = ts.TranscriptStore()
        now = 1_800_000_000.0
        store.append_turn({"id": "old-1", "operatorText": "old", "answer": "old"}, now_ts=now - (91 * 86400))
        store.append_turn({"id": "new-1", "operatorText": "new", "answer": "new"}, now_ts=now)
        self.assertEqual(len(store.records), 1)
        self.assertEqual(store.records[0]["turnId"], "new-1")

    def test_bicep_hardening_guards_f7_f8_f9(self):
        bicep = (ROOT / "templates/buddy/main.bicep").read_text(encoding="utf-8")
        for needle in (
            "external: true",
            "allowInsecure: false",
            "unauthenticatedClientAction: 'RedirectToLoginPage'",
            "enabled: false",
            "concurrentRequests: '10'",
            "minReplicas: 0",
            "APPINSIGHTS_DISABLED",
            "Microsoft.Consumption/budgets",
        ):
            self.assertIn(needle, bicep)

    def test_load_docs_from_sources_and_microsoft_learn_mechanical_queries(self):
        import io
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sb = tmp_path / "avd-second-brain"
            (sb / "docs/knowledge/troubleshooting").mkdir(parents=True)
            (sb / "docs/knowledge/troubleshooting/known-errors.yaml").write_text(
                "errors:\n"
                "- id: fslogix-kerberos-0x52e\n"
                "  symptom: FSLogix profile mount fails with 0x0000052e\n"
                "  cause: Missing Cloud Kerberos ticket\n"
                "  fix: Grant SMB Share Contributor\n"
                "  source: https://learn.microsoft.com/azure/virtual-desktop/fslogix-containers-azure-files\n",
                encoding="utf-8",
            )
            assess = tmp_path / "avd-assess"
            (assess / "methods").mkdir(parents=True)
            (assess / "index.json").write_text(
                json.dumps(
                    {
                        "references": [
                            {
                                "id": "rdp-shortpath",
                                "title": "RDP Shortpath for AVD",
                                "url": "https://learn.microsoft.com/azure/virtual-desktop/rdp-shortpath",
                                "notes": "UDP 3390 direct transport.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            docs = ar.load_docs_from_sources(sb, assess)
            idx = ar.BrainIndex(docs=docs)
            self.assertTrue(any(h["docId"] == "brain/known-errors/fslogix-kerberos-0x52e" for h in idx.search("FSLogix 0x0000052e")))
            self.assertTrue(any(h["docId"] == "assess/ref:rdp-shortpath" for h in idx.search("RDP Shortpath UDP")))

            captured = {}

            class _FakeResp:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def read(self):
                    return json.dumps(
                        {
                            "results": [
                                {
                                    "title": "Troubleshoot FSLogix",
                                    "url": "https://learn.microsoft.com/en-us/fslogix/troubleshooting",
                                    "description": "Resolve 0x0000052e mount errors.",
                                }
                            ]
                        }
                    ).encode("utf-8")

            def fake_opener(req, timeout=2.5):
                captured["url"] = req.full_url
                return _FakeResp()

            learn_hits = ar.query_microsoft_learn(
                "FSLogix 0x0000052e 12345678-1234-1234-1234-1234567890ab 10.0.0.5 user@contoso.com",
                opener=fake_opener,
            )
            self.assertEqual(len(learn_hits), 1)
            self.assertNotIn("12345678", captured["url"])
            self.assertNotIn("10.0.0.5", captured["url"])
            self.assertNotIn("contoso.com", captured["url"])

            # End-to-end Guru mode with mocked Learn opener and F5 no-synthesis check on Learn-only match
            state = buddy_server.BuddyState(
                second_brain_dir=sb,
                assess_dir=assess,
                learn_search=True,
                learn_opener=fake_opener,
            )
            srv = buddy_server.ThreadingHTTPServer(("127.0.0.1", 0), buddy_server.make_handler(state))
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                base = f"http://127.0.0.1:{srv.server_address[1]}"
                req = urllib.request.Request(
                    f"{base}/api/turns",
                    data=json.dumps({"question": "FSLogix 0x0000052e", "mode": "guru", "idempotencyKey": "guru-1"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req) as resp:
                    turn_data = json.loads(resp.read().decode("utf-8"))["turn"]
                self.assertIn("0 tokens", turn_data["sourceBadge"])
                self.assertEqual(turn_data["modeBlock"]["canSynthesize"], True)
                self.assertTrue(any(l["url"] == "https://learn.microsoft.com/en-us/fslogix/troubleshooting" for l in turn_data["learnLinks"]))

                req2 = urllib.request.Request(
                    f"{base}/api/turns",
                    data=json.dumps({"question": "quantum computing entanglement", "mode": "guru", "idempotencyKey": "guru-2"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req2) as resp2:
                    turn2 = json.loads(resp2.read().decode("utf-8"))["turn"]
                self.assertIn("0 tokens", turn2["sourceBadge"])
                self.assertEqual(turn2["modeBlock"]["canSynthesize"], False)
                self.assertIn("Microsoft Learn", turn2["sourceBadge"])
            finally:
                srv.shutdown()
                srv.server_close()


if __name__ == "__main__":
    unittest.main()
