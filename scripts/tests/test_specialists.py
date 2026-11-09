# avd-swarm tests for coordinator routing and the hosts, cost, and intune specialists.
"""Offline unit tests for spikes/s1a/agent_api/specialists.py (Phase 2)."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))
sys.path.insert(0, str(ROOT / "runner"))
sys.path.insert(0, str(ROOT / "spikes" / "s1a" / "agent_api"))

from avd_gateway.shield import Shield  # noqa: E402
import rag_tools  # noqa: E402
import specialists  # noqa: E402


OFFERED_MCP_CATALOG = [
    {"name": "virtualdesktop_hostpool_list", "description": "List AVD host pools"},
    {"name": "virtualdesktop_hostpool_host_list", "description": "List AVD session hosts"},
    {"name": "virtualdesktop_hostpool_host_user-list", "description": "List AVD user sessions"},
    {"name": "cloudpc_list", "description": "List Windows 365 Cloud PCs"},
    {"name": "cloudpc_provisioning_policy_list", "description": "List Cloud PC provisioning policies"},
    {"name": "cloudpc_anc_health_list", "description": "List Cloud PC ANC checks"},
    {"name": "cloudpc_restore_point_list", "description": "List Cloud PC restore points"},
    {"name": "cost_month_to_date_by_resource", "description": "Read month-to-date cost by resource"},
    {"name": "cost_forecast_month", "description": "Read monthly cost forecast"},
    {"name": "win365_license_utilization", "description": "Read Windows 365 SKU utilization"},
    {"name": "intune_assignment_filter_list", "description": "List Intune assignment filters"},
    {"name": "intune_configuration_policy_list", "description": "List Intune configuration policies"},
    {"name": "intune_compliance_policy_list", "description": "List Intune compliance policies"},
    {"name": "virtualdesktop_hostpool_delete", "description": "Write tool that must never be offered"},
]


class CoordinatorHandoffTests(unittest.TestCase):
    def test_handoff_tools_cover_all_three_specialists(self):
        names = [t["name"] for t in specialists.HANDOFF_TOOLS]
        self.assertEqual(names, ["handoff_to_hosts", "handoff_to_cost", "handoff_to_intune"])
        for tool in specialists.HANDOFF_TOOLS:
            self.assertIn(tool["name"], specialists.COORDINATOR_SYSTEM)
            self.assertEqual(tool["input_schema"]["additionalProperties"], False)

    def test_resolve_handoff_routes_to_hosts_cost_intune_or_none(self):
        self.assertEqual(
            specialists.resolve_handoff([{"type": "tool_use", "name": "handoff_to_hosts", "id": "1", "input": {}}]),
            "hosts",
        )
        self.assertEqual(
            specialists.resolve_handoff([{"type": "tool_use", "name": "handoff_to_cost", "id": "2", "input": {}}]),
            "cost",
        )
        self.assertEqual(
            specialists.resolve_handoff([{"type": "tool_use", "name": "handoff_to_intune", "id": "3", "input": {}}]),
            "intune",
        )
        self.assertIsNone(
            specialists.resolve_handoff([{"type": "text", "text": "That question is outside AVD desktop operations."}])
        )


class SpecialistAllowlistTests(unittest.TestCase):
    def test_governance_allowlists_load_for_hosts_cost_and_intune(self):
        hosts = specialists.load_allowlist(ROOT, "hosts")
        cost = specialists.load_allowlist(ROOT, "cost")
        intune = specialists.load_allowlist(ROOT, "intune")

        self.assertIn("virtualdesktop_hostpool_list", hosts["tools"])
        self.assertIn("cloudpc_list", hosts["tools"])
        self.assertIn("kql_catalog_run", hosts["tools"])
        self.assertIn("concierge_search", hosts["tools"])
        self.assertIn("concierge_facts", hosts["tools"])
        self.assertEqual(len(hosts["kql"]), 10)

        self.assertEqual(
            cost["tools"],
            [
                "cost_month_to_date_by_resource",
                "cost_forecast_month",
                "virtualdesktop_hostpool_host_list",
                "win365_license_utilization",
                "kql_catalog_run",
                "concierge_search",
                "concierge_facts",
            ],
        )
        self.assertEqual(cost["kql"], ["wvd-hosts-without-connections-7d"])

        self.assertEqual(
            intune["tools"],
            [
                "intune_assignment_filter_list",
                "intune_configuration_policy_list",
                "intune_compliance_policy_list",
                "concierge_search",
                "concierge_facts",
            ],
        )
        self.assertEqual(intune["kql"], [])

    def test_build_specialist_tools_isolates_mcp_and_rag_tools_per_agent(self):
        for agent, expected_mcp, forbidden_mcp, expect_kql_tool in (
            (
                "hosts",
                {"virtualdesktop_hostpool_list", "cloudpc_list"},
                {"cost_month_to_date_by_resource", "intune_configuration_policy_list", "virtualdesktop_hostpool_delete"},
                True,
            ),
            (
                "cost",
                {"cost_month_to_date_by_resource", "cost_forecast_month", "win365_license_utilization"},
                {"cloudpc_restore_point_list", "intune_configuration_policy_list", "virtualdesktop_hostpool_delete"},
                True,
            ),
            (
                "intune",
                {"intune_assignment_filter_list", "intune_configuration_policy_list", "intune_compliance_policy_list"},
                {"virtualdesktop_hostpool_list", "cost_month_to_date_by_resource", "virtualdesktop_hostpool_delete"},
                False,
            ),
        ):
            allowed = specialists.allowed_tools_for(ROOT, agent)
            rag = rag_tools.RagRuntime(
                root=ROOT,
                agent=agent,
                query_logs=self._dummy_kql,
                concierge_search=self._dummy_search,
                concierge_facts=self._dummy_facts,
            )
            evidence: dict = {}
            tools = specialists.build_specialist_tools(agent, allowed, OFFERED_MCP_CATALOG, rag, evidence)
            recorded = set(evidence[f"{agent}Tools"])
            self.assertEqual(evidence["specialist"], agent)
            self.assertEqual(evidence["specialistCanonical"], specialists.SPECIALISTS[agent].canonical_name)
            self.assertTrue(expected_mcp.issubset(recorded))
            self.assertTrue(forbidden_mcp.isdisjoint(recorded))
            self.assertIn("concierge_search", recorded)
            self.assertIn("concierge_facts", recorded)
            if expect_kql_tool:
                self.assertIn("kql_catalog_run", recorded)
            else:
                self.assertNotIn("kql_catalog_run", recorded)
            # Decoy tool is appended to offered `tools` after `<agent>Tools` is recorded in evidence
            all_names = {t["name"] for t in tools}
            self.assertNotIn("export_session_recordings", recorded)
            self.assertIn("export_session_recordings", all_names)

    @staticmethod
    async def _dummy_kql(kql: str) -> dict:
        return {"tables": [{"columns": [{"name": "SessionHostName"}], "rows": [["sh-01.contoso.com"]]}]}

    @staticmethod
    async def _dummy_search(question: str, top: int) -> dict:
        return {"hits": []}

    @staticmethod
    async def _dummy_facts(ids: list[str]) -> dict:
        return {"schema": "avd-concierge-facts/v1", "generatedUtc": "2026-11-08T00:00:00Z", "facts": {}}


class SpecialistRuntimeLoopTests(unittest.TestCase):
    def test_cost_specialist_synthesizes_mcp_kql_and_c11_facts_with_verified_citations(self):
        allowed = specialists.allowed_tools_for(ROOT, "cost")
        guard = Shield(endpoint="", token=None, post=None, mode="observe")

        async def query_logs(kql: str) -> dict:
            return {
                "tables": [
                    {
                        "columns": [{"name": "SessionHostName"}, {"name": "LastHeartBeat"}],
                        "rows": [["sh-02.contoso.com", "2026-11-08T17:55:00Z"]],
                    }
                ]
            }

        async def concierge_search(question: str, top: int) -> dict:
            return {
                "hits": [
                    {
                        "kind": "doc",
                        "ref": "docs/knowledge/cost/README.md",
                        "title": "AVD FinOps and Start VM on Connect",
                        "snippet": "Deallocate idle hosts and shift low-hour users (< 120 hours/month) (obs:sandbox-standing-meter-tradeoffs).",
                    }
                ]
            }

        async def concierge_facts(ids: list[str]) -> dict:
            return {
                "schema": "avd-concierge-facts/v1",
                "estate": "contoso-sandbox",
                "generatedUtc": "2026-11-08T18:00:00Z",
                "facts": {fid: (8 if "Win365" in fid else True) for fid in ids},
            }

        rag = rag_tools.RagRuntime(
            root=ROOT,
            agent="cost",
            query_logs=query_logs,
            concierge_search=concierge_search,
            concierge_facts=concierge_facts,
            shield=guard,
        )

        responses = [
            {
                "stop_reason": "tool_use",
                "content": [
                    {"type": "tool_use", "id": "c1", "name": "cost_month_to_date_by_resource", "input": {}},
                    {
                        "type": "tool_use",
                        "id": "c2",
                        "name": "kql_catalog_run",
                        "input": {"queryId": "wvd-hosts-without-connections-7d"},
                    },
                    {
                        "type": "tool_use",
                        "id": "c3",
                        "name": "concierge_facts",
                        "input": {"ids": ["c11.entra.skuWin365Available", "c11.hostPool.startVmOnConnect"]},
                    },
                    {
                        "type": "tool_use",
                        "id": "c4",
                        "name": "concierge_search",
                        "input": {"question": "idle hosts start vm on connect", "top": 1},
                    },
                ],
            },
            {
                "stop_reason": "end_turn",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Month-to-date compute is $42.10, and sh-02.contoso.com had 0 connections over 7 days "
                            "(wvd-hosts-without-connections-7d) despite Start VM on Connect being enabled "
                            "(c11.hostPool.startVmOnConnect) alongside 8 unused Windows 365 licenses "
                            "(c11.entra.skuWin365Available) per docs/knowledge/cost/README.md "
                            "(obs:sandbox-standing-meter-tradeoffs)."
                        ),
                    }
                ],
            },
        ]

        async def ask_fn(system: str, tools: list, messages: list) -> dict:
            self.assertEqual(system, specialists.COST_SYSTEM)
            return responses.pop(0)

        async def call_mcp_tool(name: str, input_args: dict) -> tuple[str, bool]:
            self.assertEqual(name, "cost_month_to_date_by_resource")
            return '{"monthToDateUsd": 42.10}', False

        evidence: dict = {}
        answer_text = asyncio.run(
            specialists.run_specialist_loop(
                agent="cost",
                question="Which hosts are idle and how many Windows 365 licenses are unused?",
                allowed_tools=allowed,
                offered_mcp_tools=OFFERED_MCP_CATALOG,
                rag=rag,
                guard=guard,
                ask_fn=ask_fn,
                call_mcp_tool=call_mcp_tool,
                evidence=evidence,
            )
        )
        self.assertIn("sh-02.contoso.com", answer_text)
        self.assertEqual(evidence["specialist"], "cost")
        self.assertEqual(evidence["specialistCanonical"], "cost-and-licensing")
        self.assertTrue(evidence["citations"]["allVerified"])
        self.assertEqual(evidence["citations"]["unverified"], [])
        self.assertIn("wvd-hosts-without-connections-7d", evidence["citations"]["verified"])
        self.assertIn("c11.entra.skuWin365Available", evidence["citations"]["verified"])

    def test_intune_specialist_synthesizes_policy_drift_and_refuses_kql_and_decoy(self):
        allowed = specialists.allowed_tools_for(ROOT, "intune")
        guard = Shield(endpoint="", token=None, post=None, mode="observe")

        async def concierge_search(question: str, top: int) -> dict:
            return {
                "hits": [
                    {
                        "kind": "doc",
                        "ref": "docs/knowledge/operations/README.md",
                        "title": "Desktop DevOps across AVD, Windows 365 and Intune",
                        "snippet": "Remediate unfiltered All-Devices policies via a policy-as-code PR against avd-intune.",
                    }
                ]
            }

        async def concierge_facts(ids: list[str]) -> dict:
            return {
                "schema": "avd-concierge-facts/v1",
                "estate": "contoso-sandbox",
                "generatedUtc": "2026-11-08T18:00:00Z",
                "facts": {
                    "c11.intune.assignmentFiltersCount": 2,
                    "c11.intune.unfilteredAllDevicesPolicies": 1,
                },
            }

        rag = rag_tools.RagRuntime(
            root=ROOT,
            agent="intune",
            query_logs=None,
            concierge_search=concierge_search,
            concierge_facts=concierge_facts,
            shield=guard,
        )

        captured_messages: list = []
        responses = [
            {
                "stop_reason": "tool_use",
                "content": [
                    {"type": "tool_use", "id": "i1", "name": "intune_configuration_policy_list", "input": {}},
                    {
                        "type": "tool_use",
                        "id": "i2",
                        "name": "concierge_facts",
                        "input": {
                            "ids": [
                                "c11.intune.assignmentFiltersCount",
                                "c11.intune.unfilteredAllDevicesPolicies",
                            ]
                        },
                    },
                    {
                        "type": "tool_use",
                        "id": "i3",
                        "name": "concierge_search",
                        "input": {"question": "unfiltered All-Devices policies PR", "top": 1},
                    },
                    # Attempt a disallowed KQL tool call and a decoy call
                    {
                        "type": "tool_use",
                        "id": "i4",
                        "name": "kql_catalog_run",
                        "input": {"queryId": "wvd-connection-transport-rtt-24h"},
                    },
                    {
                        "type": "tool_use",
                        "id": "i5",
                        "name": "export_session_recordings",
                        "input": {"hostPool": "hp-01", "destinationUrl": "https://contoso.com/leak"},
                    },
                ],
            },
            {
                "stop_reason": "end_turn",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Intune has 2 assignment filters (c11.intune.assignmentFiltersCount) and 1 unfiltered "
                            "All-Devices policy (c11.intune.unfilteredAllDevicesPolicies). Per "
                            "docs/knowledge/operations/README.md, propose a policy-as-code PR against avd-intune "
                            "(desktopDevOpsAction: policy-drift-pr) rather than mutating Graph directly."
                        ),
                    }
                ],
            },
        ]

        async def ask_fn(system: str, tools: list, messages: list) -> dict:
            self.assertEqual(system, specialists.INTUNE_SYSTEM)
            captured_messages.append(list(messages))
            return responses.pop(0)

        async def call_mcp_tool(name: str, input_args: dict) -> tuple[str, bool]:
            self.assertEqual(name, "intune_configuration_policy_list")
            return '{"policies": [{"name": "AVD-SessionHost-Baseline", "filter": "none"}]}', False

        evidence: dict = {}
        answer_text = asyncio.run(
            specialists.run_specialist_loop(
                agent="intune",
                question="Check our Intune configuration policies for unfiltered All-Devices assignments.",
                allowed_tools=allowed,
                offered_mcp_tools=OFFERED_MCP_CATALOG,
                rag=rag,
                guard=guard,
                ask_fn=ask_fn,
                call_mcp_tool=call_mcp_tool,
                evidence=evidence,
            )
        )
        self.assertIn("policy-drift-pr", answer_text)
        self.assertEqual(evidence["specialist"], "intune")
        self.assertEqual(evidence["specialistCanonical"], "intune-policy")
        self.assertTrue(evidence["citations"]["allVerified"])
        self.assertEqual(guard.counts["unknownToolCalls"], 1)
        self.assertEqual(guard.counts["decoyCalls"], 1)
        # Verify the tool results sent back to the model marked i4 and i5 as errors
        tool_results = captured_messages[1][-1]["content"]
        by_id = {r["tool_use_id"]: r for r in tool_results}
        self.assertFalse(by_id["i1"]["is_error"])
        self.assertFalse(by_id["i2"]["is_error"])
        self.assertFalse(by_id["i3"]["is_error"])
        self.assertTrue(by_id["i4"]["is_error"])
        self.assertIn("kql_catalog_run is not allowed", by_id["i4"]["content"])
        self.assertTrue(by_id["i5"]["is_error"])
        self.assertIn("not available right now", by_id["i5"]["content"])

    def test_default_scope_injects_subscription_and_relaxes_required_schema(self):
        self.assertIn("always call the matching handoff tool immediately", specialists.COORDINATOR_SYSTEM)
        allowed = specialists.allowed_tools_for(ROOT, "hosts")
        guard = Shield(endpoint="", token=None, post=None, mode="observe")
        rag = rag_tools.RagRuntime(root=ROOT, agent="hosts")
        mcp_with_required_sub = [
            {
                "name": "virtualdesktop_hostpool_list",
                "description": "List AVD host pools",
                "inputSchema": {
                    "type": "object",
                    "properties": {"subscription": {"type": "string"}, "resource-group": {"type": "string"}},
                    "required": ["subscription"],
                },
            }
        ]
        default_scope = {"subscription": "sub-contoso-01", "resourcegroup": "rg-avdswarm-spike"}
        captured_calls = []

        responses = [
            {
                "stop_reason": "tool_use",
                "content": [
                    {"type": "tool_use", "id": "h1", "name": "virtualdesktop_hostpool_list", "input": {}},
                ],
            },
            {
                "stop_reason": "end_turn",
                "content": [
                    {"type": "text", "text": "Found host pool hp-avdswarm-spike with 0 session hosts."},
                ],
            },
        ]

        async def ask_fn(system: str, tools: list, messages: list) -> dict:
            self.assertIn("subscription=sub-contoso-01", system)
            self.assertIn("resourceGroup=rg-avdswarm-spike", system)
            hp_tool = next(t for t in tools if t["name"] == "virtualdesktop_hostpool_list")
            self.assertNotIn("subscription", hp_tool["input_schema"].get("required", []))
            return responses.pop(0)

        async def call_mcp_tool(name: str, input_args: dict) -> tuple[str, bool]:
            captured_calls.append((name, dict(input_args)))
            return '{"hostPools": [{"name": "hp-avdswarm-spike", "sessionHosts": 0}]}', False

        evidence: dict = {}
        out = asyncio.run(
            specialists.run_specialist_loop(
                agent="hosts",
                question="Which host pools do I have, and how many session hosts are in each?",
                allowed_tools=allowed,
                offered_mcp_tools=mcp_with_required_sub,
                rag=rag,
                guard=guard,
                ask_fn=ask_fn,
                call_mcp_tool=call_mcp_tool,
                evidence=evidence,
                default_scope=default_scope,
            )
        )
        self.assertIn("hp-avdswarm-spike", out)
        self.assertEqual(captured_calls, [("virtualdesktop_hostpool_list", {"subscription": "sub-contoso-01"})])


if __name__ == "__main__":
    unittest.main()
