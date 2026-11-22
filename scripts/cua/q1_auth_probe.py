"""Zero-Azure-spend Q1 auth probe for adapters/w365-agents: calls StartSession on mcp_W365ComputerUse."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from w365_cua_gateway import forward_mcp_call


def run_probe(tenant_id: str | None = None, token: str | None = None) -> dict[str, Any]:
    tid = (tenant_id or os.environ.get("AZURE_TENANT_ID", "")).strip()
    tok = (token or os.environ.get("W365_AGENT_TOKEN", "")).strip()
    if not tid:
        raise ValueError("AZURE_TENANT_ID environment variable is required")
    if not tok:
        raise ValueError("W365_AGENT_TOKEN environment variable is required")
    resp = forward_mcp_call(tenant_id=tid, bearer_token=tok, tool="StartSession", arguments={})
    result = resp.get("result") if isinstance(resp, dict) else None
    has_session = isinstance(result, dict) and bool(result.get("sessionId"))
    return {"ok": has_session, "hasSessionId": has_session}


def main() -> int:
    try:
        outcome = run_probe()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(outcome))
    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
