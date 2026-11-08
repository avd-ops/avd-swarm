# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Offline CI test suite for the 3-leg hybrid RAG and citation evaluation cases (evals/rag/cases.yml,
spikes/s1a/agent_api/rag_eval.py, docs/spec.md sections 4 and 4a)."""

import asyncio
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for rel in ("gateway", "runner", "spikes/s1a/agent_api"):
    path_str = str(ROOT / rel)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

import rag_eval  # noqa: E402
import rag_tools  # noqa: E402

SUITE = rag_eval.load_suite(ROOT / "evals/rag/cases.yml")
CASES = SUITE["cases"]
FIXTURES = SUITE["fixtures"]


class RagEvalSuiteTests(unittest.TestCase):
    def test_case_ids_unique_and_non_empty(self):
        ids = [c["id"] for c in CASES]
        self.assertGreaterEqual(len(ids), 10)
        self.assertEqual(len(ids), len(set(ids)))

    def test_all_required_categories_and_three_rag_legs_covered(self):
        categories = {c["category"] for c in CASES}
        self.assertEqual(
            categories,
            {
                "multi-source-synthesis",
                "citation-verification",
                "c15-gating",
                "kql-gating",
                "prompt-shields",
            },
        )
        tools_used = {call["tool"] for c in CASES for call in (c.get("calls") or [])}
        self.assertEqual(tools_used, set(rag_tools.LOCAL_RAG_TOOLS))

    def test_every_golden_case_passes(self):
        outcomes = rag_eval.run_all(ROOT)
        for outcome in outcomes:
            with self.subTest(case=outcome.case_id, category=outcome.category):
                self.assertEqual(outcome.findings, [], f"{outcome.case_id} failed: {outcome.findings}")
                self.assertTrue(outcome.passed)

    def test_multi_source_cases_resolve_real_c11_pointers_from_c15_register(self):
        register = rag_tools.load_fact_register(ROOT)
        snap = FIXTURES["snapshot"]
        self.assertEqual(rag_eval.resolve_c11_fact(snap, register["c11.hostPool.count"]), 1)
        self.assertEqual(rag_eval.resolve_c11_fact(snap, register["c11.hostPool.sessionHosts.total"]), [2])
        self.assertEqual(rag_eval.resolve_c11_fact(snap, register["c11.intune.rdpShortpathConfigured"]), True)
        self.assertEqual(rag_eval.resolve_c11_fact(snap, register["c11.storage.count"]), 1)
        self.assertEqual(rag_eval.resolve_c11_fact(snap, register["c11.entra.skuWin365Available"]), 8)

    def test_prompt_shields_enforce_withholds_citations_from_observed_refs(self):
        case = next(c for c in CASES if c["id"] == "r10-prompt-shields-enforce-withholds-injection")
        outcome = asyncio.run(rag_eval.run_case(ROOT, case, FIXTURES))
        self.assertTrue(outcome.passed, outcome.findings)
        self.assertNotIn("obs:poisoned-claim", outcome.observed_refs)
        self.assertNotIn("docs/knowledge/poisoned.md", outcome.observed_refs)
        self.assertNotIn("docs/knowledge/operations/poisoned-note.md", outcome.observed_refs)
        self.assertEqual(outcome.shield_evidence["withheldDocuments"], 1)
        self.assertEqual(outcome.shield_evidence["marked"], "flagged")

    def test_in_process_concierge_parity_when_second_brain_checkout_present(self):
        brain_root = ROOT.parent / "avd-second-brain"
        concierge_dir = brain_root / "concierge"
        if not concierge_dir.is_dir():
            self.skipTest("avd-second-brain checkout not present alongside avd-swarm")
        if str(concierge_dir) not in sys.path:
            sys.path.insert(0, str(concierge_dir))
        from avd_concierge import Concierge

        svc = Concierge(repo_root=brain_root)

        async def search_fn(question: str, top: int) -> dict:
            return {"hits": svc.search(question, top=top)}

        async def facts_fn(ids: list[str]) -> dict:
            return svc.facts(ids, snapshot=FIXTURES["snapshot"])

        rt = rag_tools.RagRuntime(
            root=ROOT,
            agent="hosts",
            concierge_search=search_fn,
            concierge_facts=facts_fn,
        )
        facts_json, facts_err = asyncio.run(
            rt.call("concierge_facts", {"ids": ["c11.hostPool.count", "c11.intune.rdpShortpathConfigured"]})
        )
        self.assertFalse(facts_err, facts_json)
        facts_payload = json.loads(facts_json)
        self.assertEqual(facts_payload["facts"]["c11.hostPool.count"], 1)
        self.assertTrue(facts_payload["facts"]["c11.intune.rdpShortpathConfigured"])

        search_json, search_err = asyncio.run(
            rt.call("concierge_search", {"question": "rdp shortpath transport", "top": 3})
        )
        self.assertFalse(search_err, search_json)
        hits = json.loads(search_json)["hits"]
        self.assertGreaterEqual(len(hits), 1)
        top_ref = hits[0]["ref"]
        answer = f"Host pool count is 1 (c11.hostPool.count) with Shortpath enabled (c11.intune.rdpShortpathConfigured) per {top_ref}."
        check = rag_tools.verify_citations(answer, rt.observed_refs)
        self.assertTrue(check["allVerified"], check)


if __name__ == "__main__":
    unittest.main()
