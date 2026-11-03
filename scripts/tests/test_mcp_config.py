"""Unit tests for scripts/mcp_config.py."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.mcp_config import (
    ALLOWED_NAMESPACES,
    build_mcp_config,
    main,
    validate_mcp_config,
)


class McpConfigTests(unittest.TestCase):
    def test_default_config_is_read_only_virtualdesktop_only(self) -> None:
        cfg = build_mcp_config()
        self.assertEqual(list(cfg["mcpServers"].keys()), ["azure-mcp"])
        args = cfg["mcpServers"]["azure-mcp"]["args"]
        self.assertIn("--read-only", args)
        self.assertIn("--mode", args)
        self.assertIn("all", args)
        self.assertEqual(args[-2:], ["--namespace", "virtualdesktop"])
        self.assertEqual(validate_mcp_config(cfg), [])

    def test_include_az_cli_mcp_adds_readonly_flag(self) -> None:
        cfg = build_mcp_config(
            namespaces=["virtualdesktop", "monitor"],
            include_az_cli_mcp=True,
        )
        self.assertIn("az-cli-mcp", cfg["mcpServers"])
        cli_args = cfg["mcpServers"]["az-cli-mcp"]["args"]
        self.assertEqual(cli_args, ["az-api-mcp", "--readonly"])
        self.assertEqual(validate_mcp_config(cfg), [])

    def test_rejects_unknown_namespace(self) -> None:
        with self.assertRaises(ValueError):
            build_mcp_config(namespaces=["virtualdesktop", "keyvault"])

    def test_validator_rejects_missing_read_only_or_secrets(self) -> None:
        fake_guid = "-".join(["0" * 8, "0" * 4, "0" * 4, "0" * 4, "0" * 12])
        bad = {
            "mcpServers": {
                "azure-mcp": {
                    "command": "npx",
                    "args": ["-y", "@azure/mcp@latest", "server", "start", "--mode", "all"],
                    "env": {
                        "AZURE_CLIENT_SECRET": "super-secret",
                        "AZURE_TENANT_ID": fake_guid,
                    },
                }
            }
        }
        problems = validate_mcp_config(bad)
        self.assertTrue(any("read-only" in p for p in problems))
        self.assertTrue(any("--namespace" in p for p in problems))
        self.assertTrue(any("secret" in p.lower() for p in problems))
        self.assertTrue(any("GUID" in p for p in problems))

    def test_cli_write_and_check_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / ".mcp.json"
            rc_write = main(["--all-avd-namespaces", "--include-az-cli-mcp", "--output", str(out)])
            self.assertEqual(rc_write, 0)
            doc = json.loads(out.read_text(encoding="utf-8"))
            for ns in ALLOWED_NAMESPACES:
                self.assertIn(ns, doc["mcpServers"]["azure-mcp"]["args"])
            rc_check = main(["--check", str(out)])
            self.assertEqual(rc_check, 0)


if __name__ == "__main__":
    unittest.main()
