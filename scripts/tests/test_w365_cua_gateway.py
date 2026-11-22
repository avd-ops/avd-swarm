"""Unit tests for the optional w365-agents CUA MCP gateway and Q1 auth probe."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

CUA_DIR = Path(__file__).resolve().parents[2] / "scripts" / "cua"
if str(CUA_DIR) not in sys.path:
    sys.path.insert(0, str(CUA_DIR))

import q1_auth_probe  # noqa: E402
import w365_cua_gateway  # noqa: E402


class W365CuaGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = w365_cua_gateway.load_policy()

    def test_denies_shell_python_js_clipboard_and_unknown_tools(self) -> None:
        for denied in (
            "execute_shell_command",
            "execute_python_code",
            "browser_eval_js",
            "clipboard_read",
            "clipboard_write",
            "kill_process",
            "unlisted_tool",
        ):
            ok, reason = w365_cua_gateway.evaluate_tool_call(denied, {}, self.policy)
            self.assertFalse(ok, (denied, reason))

    def test_allows_safe_cua_tools_and_enforces_url_allowlist(self) -> None:
        for allowed in ("StartSession", "EndSession", "screenshot", "get_accessibility_tree", "click", "type_text"):
            ok, reason = w365_cua_gateway.evaluate_tool_call(allowed, {}, self.policy)
            self.assertTrue(ok, (allowed, reason))

        ok_url, _ = w365_cua_gateway.evaluate_tool_call(
            "browser_navigate", {"url": "https://learn.microsoft.com/en-us/windows-365/"}, self.policy
        )
        self.assertTrue(ok_url)

        bad_url, _ = w365_cua_gateway.evaluate_tool_call(
            "browser_navigate", {"url": "https://example.com/"}, self.policy
        )
        self.assertFalse(bad_url)

    def test_halted_gateway_permits_only_end_session(self) -> None:
        ok_click, _ = w365_cua_gateway.evaluate_tool_call("click", {}, self.policy, halted=True)
        self.assertFalse(ok_click)
        ok_end, _ = w365_cua_gateway.evaluate_tool_call("EndSession", {}, self.policy, halted=True)
        self.assertTrue(ok_end)

    def test_log_decision_records_sha256_hash_without_raw_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "cua.jsonl"
            entry = w365_cua_gateway.log_decision(log_file, "screenshot", True, "allowed", b"fake-png-bytes")
            self.assertTrue(entry["screenshotHash"].startswith("sha256:"))
            lines = log_file.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            parsed = json.loads(lines[0])
            self.assertEqual(parsed["tool"], "screenshot")
            self.assertNotIn("fake-png-bytes", lines[0])

    def test_q1_probe_requires_env_vars(self) -> None:
        with self.assertRaises(ValueError):
            q1_auth_probe.run_probe(tenant_id="", token="tok")
        with self.assertRaises(ValueError):
            q1_auth_probe.run_probe(tenant_id="tenant-placeholder", token="")


if __name__ == "__main__":
    unittest.main()
