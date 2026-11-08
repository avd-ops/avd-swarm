# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Offline tests for the S1a hybrid RAG tool layer (spikes/s1a/agent_api/rag_tools.py): KQL catalog execution by ID,
Concierge KB search and C11 fact lookups by C15 register ID, and deterministic citation verification (spec section 4a)."""

import asyncio
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runner"))
sys.path.insert(0, str(ROOT / "spikes/s1a/agent_api"))
import rag_tools  # noqa: E402


def run_now(coro):
    return asyncio.run(coro)


def fake_la_table(columns, rows):
    return {
        "tables": [
            {
                "name": "PrimaryResult",
                "columns": [{"name": c, "type": "string"} for c in columns],
                "rows": rows,
            }
        ]
    }


class RagToolsTests(unittest.TestCase):
    def make_runtime(self):
        executed_kql = []

        async def query_logs(kql: str) -> dict:
            executed_kql.append(kql)
            return fake_la_table(
                ["SessionHostName", "Connections", "UdpShortpathConnections", "RttP50Ms", "RttP95Ms"],
                [["sh-01.contoso.com", 12, 11, 18.5, 42.0]],
            )

        async def search_fn(question: str, top: int) -> dict:
            return {
                "hits": [
                    {
                        "score": 3,
                        "inTitle": 1,
                        "kind": "doc",
                        "ref": "docs/knowledge/networking/README.md",
                        "title": "RDP Shortpath and UDP transport",
                        "snippet": "Prefer UDP Shortpath (ref:avd-rdp-shortpath, obs:udp-shortpath-cuts-rtt-and-Reconnects).",
                    },
                    {
                        "score": 2,
                        "inTitle": 0,
                        "kind": "rule",
                        "ref": "knowledge/rules/avd-network-rules.json@v1",
                        "title": "AVD network latency thresholds",
                        "snippet": "active, used by: avd-assess",
                    },
                ][:top]
            }

        async def facts_fn(ids: list[str]) -> dict:
            sample = {
                "c11.hostPool.count": 1,
                "c11.hostPool.type": ["Pooled"],
                "c11.sessionHost.count": 2,
            }
            return {
                "estate": "sandbox",
                "generatedUtc": "2026-11-08T18:00:00Z",
                "facts": {fid: sample.get(fid) for fid in ids},
            }

        rt = rag_tools.RagRuntime(
            root=ROOT,
            agent="hosts",
            query_logs=query_logs,
            concierge_search=search_fn,
            concierge_facts=facts_fn,
        )
        return rt, executed_kql

    def test_tool_definitions_follow_allowed_tools(self):
        rt, _ = self.make_runtime()
        defs = rt.tools_for(["virtualdesktop_hostpool_list", "kql_catalog_run", "concierge_search"])
        self.assertEqual([d["name"] for d in defs], ["kql_catalog_run", "concierge_search"])
        all_defs = rt.tools_for(["kql_catalog_run", "concierge_search", "concierge_facts"])
        self.assertEqual([d["name"] for d in all_defs], ["kql_catalog_run", "concierge_search", "concierge_facts"])

    def test_kql_catalog_run_executes_catalog_query_and_refuses_inline_kql(self):
        rt, executed = self.make_runtime()
        text, is_error = run_now(rt.call("kql_catalog_run", {"queryId": "wvd-connection-transport-rtt-24h"}))
        self.assertFalse(is_error)
        payload = json.loads(text)
        self.assertEqual(payload["queryId"], "wvd-connection-transport-rtt-24h")
        self.assertEqual(payload["rowCount"], 1)
        self.assertEqual(payload["rows"][0]["RttP95Ms"], 42.0)
        self.assertEqual(len(executed), 1)
        self.assertIn("WVDConnectionNetworkData", executed[0])
        self.assertIn('column_ifexists("UdpUse", "")', executed[0])
        self.assertIn("wvd-connection-transport-rtt-24h", rt.observed_refs)

        for bad in (
            {"queryId": "wvd-connection-transport-rtt-24h", "kql": "WVDConnections | take 1"},
            {"query": "WVDConnections"},
            {"queryId": "unknown-query-id"},
        ):
            err_text, err_flag = run_now(rt.call("kql_catalog_run", bad))
            self.assertTrue(err_flag, bad)
            self.assertIn("error", json.loads(err_text))

    def test_concierge_facts_allows_built_c11_and_refuses_unknown_or_unbuilt_ids(self):
        rt, _ = self.make_runtime()
        text, is_error = run_now(rt.call("concierge_facts", {"ids": ["c11.hostPool.count", "c11.hostPool.type"]}))
        self.assertFalse(is_error)
        payload = json.loads(text)
        self.assertEqual(payload["facts"]["c11.hostPool.count"], 1)
        self.assertEqual(payload["facts"]["c11.hostPool.type"], ["Pooled"])
        self.assertIn("c11.hostPool.count", rt.observed_refs)

        for bad_ids in (
            ["c11.unknown.nonExistent"],
            ["c11.hostPool.appAttachPackages"],
            ["proposed:c11.sessionHost.defenderRunningMode"],
            ["c06.finding.severity"],
        ):
            err_text, err_flag = run_now(rt.call("concierge_facts", {"ids": bad_ids}))
            self.assertTrue(err_flag, bad_ids)
            self.assertIn("error", json.loads(err_text))

    def test_concierge_search_and_verify_citations_partition_verified_vs_unverified(self):
        rt, _ = self.make_runtime()
        run_now(rt.call("concierge_search", {"question": "rdp shortpath latency", "top": 2}))
        run_now(rt.call("concierge_facts", {"ids": ["c11.hostPool.count"]}))
        run_now(rt.call("kql_catalog_run", {"queryId": "wvd-connection-transport-rtt-24h"}))

        verified_answer = (
            "The estate has 1 host pool (c11.hostPool.count) with p95 RTT of 42 ms "
            "(wvd-connection-transport-rtt-24h). Per docs/knowledge/networking/README.md and "
            "knowledge/rules/avd-network-rules.json@v1 (ref:avd-rdp-shortpath), UDP Shortpath is active."
        )
        check_ok = rag_tools.verify_citations(verified_answer, rt.observed_refs)
        self.assertTrue(check_ok["allVerified"])
        self.assertEqual(check_ok["unverified"], [])
        self.assertIn("c11.hostPool.count", check_ok["verified"])
        self.assertIn("wvd-connection-transport-rtt-24h", check_ok["verified"])
        self.assertIn("docs/knowledge/networking/README.md", check_ok["verified"])

        hallucinated_answer = (
            verified_answer
            + " Also see c11.storage.profileContainers and wvd-logon-checkpoints-p95-24h."
        )
        check_bad = rag_tools.verify_citations(hallucinated_answer, rt.observed_refs)
        self.assertFalse(check_bad["allVerified"])
        self.assertEqual(
            check_bad["unverified"],
            ["c11.storage.profileContainers", "wvd-logon-checkpoints-p95-24h"],
        )


if __name__ == "__main__":
    unittest.main()
