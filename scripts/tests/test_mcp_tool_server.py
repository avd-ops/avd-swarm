# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Tests for the published MCP tool layer (core-and-adapters.md WP4; governance.md section 2.3 layer 5; V1-3)."""

import asyncio
import sys
import unittest
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "spikes/s1a/agent_api"))
sys.path.insert(0, str(ROOT / "scripts"))
import check_timelock  # noqa: E402
import mcp_tool_server  # noqa: E402


def valid_plan() -> dict:
    return {
        "planId": "plan-20261108-mcp01",
        "action": "host.drain",
        "targetSurface": "avd",
        "evidence": [{"threadStartedBy": "avd-buddy", "factId": "c11.sessionHosts.status@hp-3fa2c1d0"}],
        "why": {"kind": "fix", "decision": "dec-0001"},
        "rollback": {"restoreAllowNewSession": True},
        "preconditionState": {
            "targetDigest": "host-digest-01",
            "allowNewSession": True,
            "heartbeatFresh": True,
            "activeSessions": 0,
            "recentConnectionsInWindow": False,
            "powerLocked": False,
            "scalingExcluded": False,
            "powerState": "running",
            "availableHostsAfterAction": 3,
            "requiredAvailableHostsFloor": 2,
            "inFlightPlanOnPool": None,
            "serviceHealthClear": True,
        },
    }


class McpToolServerTests(unittest.TestCase):
    def setUp(self):
        self.policy = mcp_tool_server.load_mcp_policy(ROOT)
        self.oid_map = mcp_tool_server.parse_mcp_callers(
            "oid-runner=runner,oid-claude=claude-code,oid-sre=sre-agent,oid-foundry=foundry",
            self.policy,
        )

    def test_entra_caller_resolution_and_default_deny(self):
        person = mcp_tool_server.resolve_mcp_caller(
            {"oid": "oid-op", "scp": "Mcp.Tools.ReadWrite"},
            "oid-op",
            self.oid_map,
            self.policy,
        )
        self.assertIsNotNone(person)
        self.assertEqual(person["id"], "person")
        self.assertTrue(person["isPerson"])

        sre = mcp_tool_server.resolve_mcp_caller(
            {"oid": "oid-sre", "roles": ["mcp.sre-agent"]},
            "oid-op",
            self.oid_map,
            self.policy,
        )
        self.assertIsNotNone(sre)
        self.assertEqual(sre["id"], "sre-agent")
        self.assertEqual(sre["allowlists"], ["hosts"])
        self.assertNotIn("cost_forecast_month", sre["tools"])

        # Wrong role or unknown OID is default-denied
        self.assertIsNone(
            mcp_tool_server.resolve_mcp_caller(
                {"oid": "oid-sre", "roles": ["mcp.runner"]},
                "oid-op",
                self.oid_map,
                self.policy,
            )
        )
        self.assertIsNone(
            mcp_tool_server.resolve_mcp_caller(
                {"oid": "oid-unknown", "roles": ["mcp.runner"]},
                "oid-op",
                self.oid_map,
                self.policy,
            )
        )

    def test_v1_3_kill_switch_drill_across_all_callers_and_positions(self):
        drill = mcp_tool_server.verify_mcp_switch_drill(ROOT)
        self.assertTrue(drill["passed"], drill)
        matrix = drill["matrix"]
        for caller_id in ("runner", "claude-code", "sre-agent", "foundry"):
            self.assertTrue(matrix[caller_id]["run"])
            self.assertTrue(matrix[caller_id]["halt-actions"])
            self.assertFalse(matrix[caller_id]["halt-agents"], caller_id)
            self.assertFalse(matrix[caller_id]["halt-all"], caller_id)
            self.assertFalse(matrix[caller_id]["unreadable"], caller_id)
        self.assertTrue(matrix["person"]["halt-agents"])
        self.assertFalse(matrix["person"]["halt-all"])

    def test_jsonrpc_tools_list_call_kql_and_propose_change(self):
        sre_caller = self.policy["callers"]["sre-agent"]
        runner_caller = self.policy["callers"]["runner"]

        # 1. initialize and tools/list
        status, init_res = asyncio.run(
            mcp_tool_server.handle_mcp_jsonrpc(
                {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                caller=sre_caller,
                read_flag=lambda: "run",
                root=ROOT,
            )
        )
        self.assertEqual(status, 200)
        self.assertEqual(init_res["result"]["serverInfo"]["name"], "avd-swarm-tool-layer")

        status, list_res = asyncio.run(
            mcp_tool_server.handle_mcp_jsonrpc(
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                caller=sre_caller,
                read_flag=lambda: "run",
                root=ROOT,
            )
        )
        self.assertEqual(status, 200)
        tool_names = {t["name"] for t in list_res["result"]["tools"]}
        self.assertIn("virtualdesktop_hostpool_list", tool_names)
        self.assertIn("propose_change", tool_names)
        self.assertNotIn("cost_forecast_month", tool_names)

        # 2. Non-allowlisted tool call is refused (403)
        status, bad_tool = asyncio.run(
            mcp_tool_server.handle_mcp_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "cost_forecast_month", "arguments": {}},
                },
                caller=sre_caller,
                read_flag=lambda: "run",
                root=ROOT,
            )
        )
        self.assertEqual(status, 403)
        self.assertIn("not in caller 'sre-agent' allowlist", bad_tool["error"]["message"])

        # 3. Inline KQL is refused (400); catalog KQL ID in allowlist succeeds (200)
        status, inline_kql = asyncio.run(
            mcp_tool_server.handle_mcp_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "kql_catalog_run", "arguments": {"query": "WVDAgentHealthStatus | take 1"}},
                },
                caller=sre_caller,
                read_flag=lambda: "run",
                root=ROOT,
            )
        )
        self.assertEqual(status, 400)
        self.assertIn("Inline KQL is never allowed", inline_kql["error"]["message"])

        async def fake_kql(qid: str):
            return [{"queryId": qid, "count": 0}]

        status, ok_kql = asyncio.run(
            mcp_tool_server.handle_mcp_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {"name": "kql_catalog_run", "arguments": {"queryId": "wvd-agent-health-latest-7d"}},
                },
                caller=sre_caller,
                read_flag=lambda: "run",
                execute_kql_query=fake_kql,
                root=ROOT,
            )
        )
        self.assertEqual(status, 200)
        self.assertEqual(ok_kql["result"]["content"][0]["json"]["queryId"], "wvd-agent-health-latest-7d")

        # 4. propose_change gates the plan without executing a write; runner cannot call propose_change
        status, prop_run = asyncio.run(
            mcp_tool_server.handle_mcp_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "tools/call",
                    "params": {"name": "propose_change", "arguments": {"plan": valid_plan()}},
                },
                caller=sre_caller,
                read_flag=lambda: "run",
                root=ROOT,
            )
        )
        self.assertEqual(status, 200)
        gated = prop_run["result"]["content"][0]["json"]
        self.assertEqual(gated["status"], "ready")
        self.assertTrue(gated["approvable"])

        status, prop_halt_actions = asyncio.run(
            mcp_tool_server.handle_mcp_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {"name": "propose_change", "arguments": {"plan": valid_plan()}},
                },
                caller=sre_caller,
                read_flag=lambda: "halt-actions",
                root=ROOT,
            )
        )
        self.assertEqual(status, 200)
        self.assertFalse(prop_halt_actions["result"]["content"][0]["json"]["approvable"])

        status, runner_prop = asyncio.run(
            mcp_tool_server.handle_mcp_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "tools/call",
                    "params": {"name": "propose_change", "arguments": {"plan": valid_plan()}},
                },
                caller=runner_caller,
                read_flag=lambda: "run",
                root=ROOT,
            )
        )
        self.assertEqual(status, 403)

    def test_mcp_callers_timelock_classification(self):
        protected_policy = yaml.safe_load((ROOT / "governance/protected-paths.yml").read_text(encoding="utf-8"))
        mcp_yaml = (ROOT / "governance/mcp-callers.yml").read_text(encoding="utf-8")

        tighter = mcp_yaml.replace("    allowPropose: true\n", "    allowPropose: false\n", 1)
        res_low = check_timelock.classify_change("governance/mcp-callers.yml", mcp_yaml, tighter, protected_policy)
        self.assertEqual(res_low["effect"], "lowers")

        looser = mcp_yaml.replace("    allowPropose: false\n", "    allowPropose: true\n")
        res_high = check_timelock.classify_change("governance/mcp-callers.yml", mcp_yaml, looser, protected_policy)
        self.assertEqual(res_high["effect"], "raises")


if __name__ == "__main__":
    unittest.main()
