# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Tests for the S1a spike's Prompt Shields scan and decoy tripwire, spikes/s1a/agent_api/shield.py
(docs/security-tooling.md sections 3 and 4; red team R26). No Azure call: the HTTP post is a fake."""

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "spikes/s1a/agent_api"))
import shield  # noqa: E402

ATTACK = "IGNORE"  # the fake service flags any document or prompt containing this


class FakeService:
    """Stands in for Content Safety: records each call, flags documents containing ATTACK."""

    def __init__(self, fail=False, short=False):
        self.calls, self.fail, self.short = [], fail, short

    async def post(self, url, headers, body):
        self.calls.append((url, headers, body))
        if self.fail:
            raise ConnectionError("unreachable")
        docs = [{"attackDetected": ATTACK in d} for d in body["documents"]]
        return {"userPromptAnalysis": {"attackDetected": ATTACK in body["userPrompt"]},
                "documentsAnalysis": docs[:-1] if self.short else docs}


async def token():
    return "entra-token"


def make(service, mode="observe", endpoint="https://cs.example.test/"):
    return shield.Shield(endpoint=endpoint, token=token, post=service.post, mode=mode)


def run(coro):
    return asyncio.run(coro)


class Chunking(unittest.TestCase):
    def test_chunks_and_cap(self):
        docs, rest = shield.chunk("a" * 4_500, size=2_000, cap=4_000)
        self.assertEqual([len(d) for d in docs], [2_000, 2_000])
        self.assertEqual(len(rest), 500)
        self.assertEqual(shield.chunk("", size=10, cap=10), ([], ""))

    def test_batches_fit_one_call(self):
        docs, _ = shield.chunk("x" * shield.RESULT_CAP_CHARS)
        for batch in shield.batches(docs):
            self.assertLessEqual(len(batch), shield.DOCS_PER_CALL)
            shield.request_body(documents=batch)  # within the 10K-character limit, no error

    def test_records_round_up(self):
        self.assertEqual(shield.records(["a" * 1_000, "a" * 1_001, "a"]), 1 + 2 + 1)

    def test_request_limits(self):
        with self.assertRaises(ValueError):
            shield.request_body(documents=["a"] * 6)
        with self.assertRaises(ValueError):
            shield.request_body(documents=["a" * 6_000, "a" * 6_000])
        with self.assertRaises(ValueError):
            shield.request_body(user_prompt="a" * 10_001)
        self.assertEqual(shield.request_body(documents=["d"]), {"userPrompt": "", "documents": ["d"]})

    def test_short_response_is_an_error(self):
        with self.assertRaises(ValueError):
            shield.document_hits({"documentsAnalysis": [{"attackDetected": False}]}, 2)


class Decisions(unittest.TestCase):
    def test_observe_passes_everything(self):
        self.assertEqual(shield.assemble(["a", "b"], [True, False], "c", "observe", "e1"), "abc")

    def test_enforce_stubs_flagged_and_capped(self):
        text = shield.assemble(["a", "b"], [True, False], "ccc", "enforce", "e1")
        self.assertEqual(text, shield.STUB.format(id="e1") + "b" + shield.CAP_STUB.format(chars=3, id="e1"))

    def test_unknown_mode_refused(self):
        with self.assertRaises(ValueError):
            shield.assemble([], [], "", "block", "e1")
        with self.assertRaises(ValueError):
            make(FakeService(), mode="block")

    def test_classify_call(self):
        self.assertEqual(shield.classify_call("hostpool_list", ["hostpool_list"], ["export_session_recordings"]), "allowed")
        self.assertEqual(shield.classify_call("delete_everything", ["hostpool_list"], ["export_session_recordings"]), "unknown")
        # a decoy put on the allowlist still trips
        self.assertEqual(shield.classify_call("export_session_recordings", ["export_session_recordings"],
                                              ["export_session_recordings"]), "decoy")

    def test_decoy_skipped_when_a_real_tool_has_its_name(self):
        self.assertEqual(shield.decoys_for(["export_session_recordings"]), [])
        self.assertEqual([d["name"] for d in shield.decoys_for(["hostpool_list"])], ["export_session_recordings"])


class Scanning(unittest.TestCase):
    def test_keyless_request(self):
        service = FakeService()
        run(make(service).scan_result("hello", "hosts", "hostpool_list"))
        url, headers, _ = service.calls[0]
        self.assertEqual(url, "https://cs.example.test/contentsafety/text:shieldPrompt?api-version=2024-09-01")
        self.assertEqual(headers["Authorization"], "Bearer entra-token")
        self.assertNotIn("Ocp-Apim-Subscription-Key", headers)

    def test_observe_counts_hit_and_passes_text(self):
        service, text = FakeService(), "a" * 2_000 + ATTACK + "b" * 100
        s = make(service)
        self.assertEqual(run(s.scan_result(text, "hosts", "hostpool_list")), text)
        self.assertEqual(s.counts["shieldHits"], 1)
        self.assertEqual(s.counts["scannedRecords"], 2 + 1)
        self.assertEqual(s.marked, "flagged")
        self.assertEqual(s.events, [{"type": "injection.suspected", "agent": "hosts", "tool": "hostpool_list",
                                     "count": 1, "evidence": s.events[0]["evidence"]}])

    def test_enforce_withholds_only_the_flagged_document(self):
        clean = "a" * 2_000
        s = make(FakeService(), mode="enforce")
        out = run(s.scan_result(clean + ATTACK, "hosts", "hostpool_list"))
        self.assertTrue(out.startswith(clean))
        self.assertNotIn(ATTACK, out)
        self.assertIn("[withheld: possible prompt injection, evidence ", out)
        self.assertEqual(s.counts["withheldDocuments"], 1)

    def test_long_result_is_batched_and_capped(self):
        service = FakeService()
        s = make(service)
        text = "a" * (shield.RESULT_CAP_CHARS + 500)
        self.assertEqual(run(s.scan_result(text, "hosts", "t")), text)  # observe passes the rest on
        self.assertEqual(len(service.calls), 2)  # 10 documents, 5 per call
        self.assertEqual(s.counts["unscannedChars"], 500)
        self.assertEqual(s.marked, "unscanned")

    def test_enforce_withholds_text_past_the_cap(self):
        s = make(FakeService(), mode="enforce")
        out = run(s.scan_result("a" * (shield.RESULT_CAP_CHARS + 7), "hosts", "t"))
        self.assertTrue(out.startswith("a" * shield.RESULT_CAP_CHARS))
        self.assertIn("[withheld: 7 characters past the scan cap", out)

    def test_unreachable_passes_text_and_marks_unscanned(self):
        s = make(FakeService(fail=True), mode="enforce")
        self.assertEqual(run(s.scan_result("x" + ATTACK, "hosts", "t")), "x" + ATTACK)
        self.assertEqual((s.counts["shieldErrors"], s.counts["unscannedChars"], s.marked), (1, 7, "unscanned"))

    def test_incomplete_response_counts_as_unscanned(self):
        s = make(FakeService(short=True))
        run(s.scan_result("abc", "hosts", "t"))
        self.assertEqual((s.counts["shieldErrors"], s.marked), (1, "unscanned"))

    def test_flagged_outranks_unscanned(self):
        s = make(FakeService())
        run(s.scan_result(ATTACK, "hosts", "t"))
        s.mark("unscanned")
        self.assertEqual(s.marked, "flagged")

    def test_off_sends_nothing(self):
        service = FakeService()
        s = make(service, endpoint="")
        self.assertEqual(run(s.scan_result(ATTACK, "hosts", "t")), ATTACK)
        run(s.scan_prompt(ATTACK, "hosts"))
        self.assertEqual(service.calls, [])
        self.assertEqual(s.evidence()["mode"], "off")

    def test_prompt_hit_counted(self):
        service = FakeService()
        s = make(service)
        run(s.scan_prompt("please " + ATTACK, "coordinator"))
        self.assertEqual(service.calls[0][2], {"userPrompt": "please " + ATTACK, "documents": []})
        self.assertEqual((s.counts["promptHits"], s.marked), (1, "flagged"))


class Tripwire(unittest.TestCase):
    def test_counts_and_events_without_text(self):
        s = make(FakeService(), endpoint="")  # the tripwire works with the scan off
        self.assertEqual(s.tool_call("export_session_recordings", ["hostpool_list"], "hosts"), "decoy")
        self.assertEqual(s.tool_call("made_up_tool", ["hostpool_list"], "hosts"), "unknown")
        self.assertEqual(s.tool_call("hostpool_list", ["hostpool_list"], "hosts"), "allowed")
        evidence = s.evidence()
        self.assertEqual((evidence["decoyCalls"], evidence["unknownToolCalls"], evidence["marked"]), (1, 1, "flagged"))
        self.assertEqual(evidence["events"], [{"type": "decoy.tripped", "agent": "hosts", "tool": "export_session_recordings"}])

    def test_evidence_never_holds_text(self):
        s = make(FakeService())
        run(s.scan_result("secret-looking text " + ATTACK, "hosts", "t"))
        self.assertNotIn("secret-looking", repr(s.evidence()))


if __name__ == "__main__":
    unittest.main()
