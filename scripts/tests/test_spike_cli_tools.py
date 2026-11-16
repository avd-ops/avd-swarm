# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Offline tests for Windows az.cmd resolution in spikes/s1a/tools/{list_mcp_tools,run_evals}.py."""

from __future__ import annotations

import importlib
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
for rel in ("spikes/s1a/tools", "runner", "scripts", "spend"):
    path_str = str(ROOT / rel)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


def _install_tool_stubs() -> dict[str, types.ModuleType | None]:
    saved: dict[str, types.ModuleType | None] = {}
    needed = {
        "httpx": {"AsyncClient": object, "Client": object},
        "mcp": {"ClientSession": object},
        "mcp.client": {},
        "mcp.client.streamable_http": {"streamable_http_client": object},
    }
    for name, attrs in needed.items():
        try:
            importlib.import_module(name)
        except ImportError:
            saved[name] = sys.modules.get(name)
            mod = types.ModuleType(name)
            for k, v in attrs.items():
                setattr(mod, k, v)
            sys.modules[name] = mod
    return saved


def _restore_stubs(saved: dict[str, types.ModuleType | None]) -> None:
    for name, orig in saved.items():
        if orig is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = orig


class SpikeCliToolsTests(unittest.TestCase):
    def setUp(self):
        self._saved = _install_tool_stubs()
        self.list_mcp_tools = importlib.import_module("list_mcp_tools")
        self.run_evals = importlib.import_module("run_evals")

    def tearDown(self):
        _restore_stubs(self._saved)

    def test_cli_token_resolves_az_via_shutil_which(self):
        az_cmd = r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd"
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="tok-123\n", stderr="")

        for mod in (self.list_mcp_tools, self.run_evals):
            with self.subTest(module=mod.__name__):
                with (
                    mock.patch.object(mod.shutil, "which", return_value=az_cmd) as which_mock,
                    mock.patch.object(mod.subprocess, "run", return_value=completed) as run_mock,
                ):
                    token = mod.cli_token("api://app-1/Mcp.Tools.ReadWrite")
                self.assertEqual(token, "tok-123")
                which_mock.assert_called_once_with("az")
                called_cmd = run_mock.call_args.args[0]
                self.assertEqual(called_cmd[0], az_cmd)

    def test_runner_drill_and_spend_guard_resolve_az_via_shutil_which(self):
        from avd_runner import cli as runner_cli
        import run_s2_drill
        import spend_guard

        az_cmd = r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd"
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout='{"ok": true}', stderr="")
        drill = run_s2_drill._AzureBackend(
            subscription="sub-1",
            resource_group="rg-1",
            host_pool="hp-1",
            session_host="sh-1",
            target_digest="sha256:0000",
        )

        targets = (
            ("avd_runner.cli", runner_cli, lambda: runner_cli.az("account", "show")),
            ("run_s2_drill", run_s2_drill, lambda: drill._az_json("account", "show")),
            ("spend_guard", spend_guard, lambda: spend_guard.az("account", "show")),
        )
        for name, mod, call in targets:
            with self.subTest(module=name):
                with (
                    mock.patch.object(mod.shutil, "which", return_value=az_cmd) as which_mock,
                    mock.patch.object(mod.subprocess, "run", return_value=completed) as run_mock,
                ):
                    call()
                which_mock.assert_called_once_with("az")
                self.assertEqual(run_mock.call_args.args[0][0], az_cmd)

                with (
                    mock.patch.object(mod.shutil, "which", return_value=None),
                    mock.patch.object(mod.subprocess, "run", side_effect=FileNotFoundError),
                ):
                    with self.assertRaisesRegex(RuntimeError, "Azure CLI"):
                        call()


if __name__ == "__main__":
    unittest.main()
