# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Offline startup & route-wiring tests for spikes/s1a/agent_api/main.py.

Catches module-load errors (such as guarded(..., allow_buddy=True) signature mismatches)
and verifies buddy vs operator caller auth on guarded() routes with zero cloud/token spend.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
for rel in ("gateway", "runner", "spikes/s1a/agent_api"):
    path_str = str(ROOT / rel)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


def _install_stubs() -> dict[str, types.ModuleType | None]:
    """Install lightweight stubs only for third-party packages absent in minimal CI."""
    saved: dict[str, types.ModuleType | None] = {}

    def stub(name: str, attrs: dict | None = None) -> types.ModuleType:
        if name not in saved:
            saved[name] = sys.modules.get(name)
        mod = types.ModuleType(name)
        for k, v in (attrs or {}).items():
            setattr(mod, k, v)
        sys.modules[name] = mod
        return mod

    class _PyJWTError(Exception):
        pass

    class _FakeResponse:
        def __init__(self, body, status_code=200):
            self.body = body
            self.status_code = status_code

    class _FakeRoute:
        def __init__(self, path, endpoint, methods=None):
            self.path = path
            self.endpoint = endpoint
            self.methods = list(methods or ["GET"])

    class _FakeStarlette:
        def __init__(self, routes=None):
            self.routes = list(routes or [])

    class _FakeCredential:
        def __init__(self, *args, **kwargs):
            pass

    class _FakeAnthropic:
        def __init__(self, *args, **kwargs):
            pass

    needed = {
        "anthropic": {"AsyncAnthropicFoundry": _FakeAnthropic, "AsyncAnthropic": _FakeAnthropic},
        "httpx": {"AsyncClient": object},
        "jwt": {"PyJWTError": _PyJWTError, "PyJWKClient": lambda *a, **kw: object(), "decode": lambda *a, **kw: {}},
        "uvicorn": {"run": lambda *a, **kw: None},
        "azure": {},
        "azure.identity": {"ManagedIdentityCredential": _FakeCredential},
        "azure.identity.aio": {"ManagedIdentityCredential": _FakeCredential},
        "mcp": {"ClientSession": object},
        "mcp.client": {},
        "mcp.client.streamable_http": {"streamablehttp_client": object},
        "starlette": {},
        "starlette.applications": {"Starlette": _FakeStarlette},
        "starlette.requests": {"Request": object},
        "starlette.responses": {"JSONResponse": _FakeResponse},
        "starlette.routing": {"Route": _FakeRoute},
    }
    for mod_name, attrs in needed.items():
        try:
            importlib.import_module(mod_name)
        except ImportError:
            stub(mod_name, attrs)
    return saved


def _restore_stubs(saved: dict[str, types.ModuleType | None]) -> None:
    for name, orig in saved.items():
        if orig is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = orig


class AgentApiStartupTests(unittest.TestCase):
    def setUp(self):
        self._saved_modules = _install_stubs()
        self._saved_main = sys.modules.pop("main", None)
        env = {
            "AZURE_TENANT_ID": "tenant-test",
            "BLUEPRINT_APP_ID": "blueprint-test",
            "UAMI_CLIENT_ID": "uami-test",
            "MCP_APP_ID": "mcp-test",
            "MCP_URL": "https://mcp.example.invalid",
            "OPERATOR_OID": "operator-oid-1",
            "BUDDY_CALLER_OID": "buddy-uami-oid-1",
            "MODEL_PROVIDER": "anthropic",
            "MODEL_PRICES_FILE": str(ROOT / "governance" / "model-prices.yml"),
        }
        self._env_patch = mock.patch.dict(os.environ, env, clear=False)
        self._env_patch.start()
        self.main = importlib.import_module("main")

    def tearDown(self):
        sys.modules.pop("main", None)
        if self._saved_main is not None:
            sys.modules["main"] = self._saved_main
        self._env_patch.stop()
        _restore_stubs(self._saved_modules)

    def test_app_constructs_with_expected_routes(self):
        paths = {route.path for route in self.main.app.routes}
        self.assertEqual(
            paths,
            {
                "/healthz",
                "/setup",
                "/claude-check",
                "/ask",
                "/ask/{job_id}",
                "/stop",
                "/runbook",
                "/runbook/findings",
                "/mcp",
            },
        )

    def test_healthz_answers_ok(self):
        resp = asyncio.run(self.main.health(object()))
        self.assertEqual(resp.status_code, 200)
        body = resp.body if isinstance(resp.body, dict) else json.loads(resp.body)
        self.assertTrue(body["ok"])
        self.assertEqual(body["modelProvider"], "anthropic")

    def test_guarded_allows_buddy_only_when_allow_buddy_is_true(self):
        async def ok_handler(_request):
            return self.main.JSONResponse({"ok": True}, status_code=200)

        operator_only = self.main.guarded(ok_handler)
        buddy_allowed = self.main.guarded(ok_handler, allow_buddy=True)

        buddy_claims = {"oid": "buddy-uami-oid-1", "idtyp": "app"}
        operator_claims = {"oid": "operator-oid-1", "scp": "Mcp.Tools.ReadWrite"}
        stranger_claims = {"oid": "stranger-oid-9", "idtyp": "app"}

        with mock.patch.object(self.main, "verified_claims", return_value=buddy_claims):
            self.assertEqual(asyncio.run(operator_only(object())).status_code, 401)
            self.assertEqual(asyncio.run(buddy_allowed(object())).status_code, 200)

        with mock.patch.object(self.main, "verified_claims", return_value=operator_claims):
            self.assertEqual(asyncio.run(operator_only(object())).status_code, 200)
            self.assertEqual(asyncio.run(buddy_allowed(object())).status_code, 200)

        with mock.patch.object(self.main, "verified_claims", return_value=stranger_claims):
            self.assertEqual(asyncio.run(operator_only(object())).status_code, 401)
            self.assertEqual(asyncio.run(buddy_allowed(object())).status_code, 401)


if __name__ == "__main__":
    unittest.main()
