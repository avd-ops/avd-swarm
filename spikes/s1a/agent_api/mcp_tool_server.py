# avd-swarm published tool layer MCP server (core-and-adapters.md section 3.5 WP4; governance.md section 2.3 layer 5; V1-3).
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Governed MCP endpoint exposing the swarm's read tools and `propose_change` (never a direct write):

1. Entra app-role and operator authentication (`resolve_mcp_caller`), default deny.
2. Per-caller tool and KQL allowlists loaded from `governance/mcp-callers.yml` and `governance/allowlists/*.yml`.
3. Kill-switch flag check before every request (`check_switch_for_mcp_caller`):
   - `halt-all` or an unreadable flag refuses every caller.
   - `halt-agents` refuses every caller that is not a person (`runner`, `claude-code`, `sre-agent`, `foundry`; V1-3),
     and refuses `propose_change` for all callers.
   - `halt-actions` allows reads and marks `propose_change` drafts `approvable: false`.
4. `propose_change` evaluates the plan through `gate/avd_gate/apply.py::check_plan` and returns the redacted gate
   verdict (`planId`, `planHash`, `action`, `status`, `approvable`, `reasons`) without ever executing an Azure write.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Awaitable, Callable

import yaml

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "gate") not in sys.path:
    sys.path.insert(0, str(ROOT / "gate"))
from avd_gate import apply as gate_apply  # noqa: E402

VALID_POSITIONS = ("run", "halt-actions", "halt-agents", "halt-all")
FORBIDDEN_WRITE_VERBS = ("delete", "create", "update", "restart", "stop", "start", "deallocate", "write")


def load_mcp_policy(root: Path | None = None) -> dict[str, Any]:
    """Loads `governance/mcp-callers.yml` and resolves each caller's allowed read tools and KQL IDs."""
    base = root or ROOT
    raw = yaml.safe_load((base / "governance/mcp-callers.yml").read_text(encoding="utf-8")) or {}
    callers_cfg = raw.get("callers") if isinstance(raw.get("callers"), dict) else {}

    allowlists: dict[str, dict[str, set[str]]] = {}
    for path in sorted((base / "governance/allowlists").glob("*.yml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        allowlists[path.stem] = {
            "tools": set(data.get("tools") or []),
            "kql": set(data.get("kql") or []),
        }

    resolved_callers: dict[str, dict[str, Any]] = {}
    for caller_id, cfg in callers_cfg.items():
        if not isinstance(cfg, dict):
            continue
        tools: set[str] = set()
        kql_ids: set[str] = set()
        for agent_name in cfg.get("allowlists") or []:
            agent_allow = allowlists.get(agent_name, {})
            tools |= agent_allow.get("tools", set())
            kql_ids |= agent_allow.get("kql", set())
        resolved_callers[caller_id] = {
            "id": caller_id,
            "isPerson": bool(cfg.get("isPerson") is True),
            "requireScope": cfg.get("requireScope", ""),
            "appRole": cfg.get("appRole", ""),
            "adapter": cfg.get("adapter", ""),
            "allowlists": list(cfg.get("allowlists") or []),
            "tools": sorted(tools),
            "kql": sorted(kql_ids),
            "allowPropose": bool(cfg.get("allowPropose") is True),
            "allowedPositions": list(cfg.get("allowedPositions") or []),
        }
    return {"callers": resolved_callers}


def parse_mcp_callers(text: str, policy: dict[str, Any] | None = None) -> dict[str, str]:
    """Parses `MCP_CALLERS` (`<oid>=<caller_id>,<oid>=<caller_id>`). Refuses unknown caller IDs at startup."""
    pol = policy or load_mcp_policy()
    known = set(pol["callers"]) - {"person"}
    out: dict[str, str] = {}
    for part in (p.strip() for p in text.split(",") if p.strip()):
        oid, sep, caller_id = part.partition("=")
        oid, caller_id = oid.strip(), caller_id.strip()
        if not sep or not oid or caller_id not in known:
            raise ValueError(f"MCP_CALLERS entry {part!r}: want <object id>=<one of {sorted(known)}>")
        out[oid] = caller_id
    return out


def resolve_mcp_caller(
    claims: dict[str, Any],
    operator_oid: str,
    oid_map: dict[str, str],
    policy: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Resolves verified Entra JWT claims to a registered MCP caller profile, or None (default deny)."""
    pol = policy or load_mcp_policy()
    callers = pol["callers"]
    oid = str(claims.get("oid") or "").strip()
    if not oid:
        return None

    person_cfg = callers.get("person")
    if person_cfg and oid == operator_oid:
        scopes = set(str(claims.get("scp") or "").split())
        if person_cfg["requireScope"] in scopes:
            return person_cfg

    caller_id = oid_map.get(oid)
    if not caller_id or caller_id not in callers:
        return None
    caller_cfg = callers[caller_id]
    roles = set(claims.get("roles") or [])
    if caller_cfg["appRole"] and caller_cfg["appRole"] in roles:
        return caller_cfg
    return None


def check_switch_for_mcp_caller(
    caller: dict[str, Any],
    position: str,
    tool_name: str = "",
) -> tuple[bool, str]:
    """Checks the kill-switch flag for an MCP caller and tool (governance.md section 2.3 layer 5; V1-3)."""
    pos = str(position or "").strip()
    if pos not in VALID_POSITIONS:
        return False, "kill-switch: flag unreadable or invalid, refusing MCP call"
    if pos == "halt-all":
        return False, "kill-switch: halt-all refuses all MCP callers"
    if pos == "halt-agents":
        if not caller.get("isPerson"):
            return False, f"kill-switch: halt-agents refuses non-person MCP caller {caller.get('id')!r} (V1-3)"
        if tool_name == "propose_change":
            return False, "kill-switch: halt-agents refuses propose_change"
    if pos not in set(caller.get("allowedPositions") or []):
        return False, f"kill-switch: position {pos!r} refuses caller {caller.get('id')!r}"
    return True, "ok"


def list_tools_for_caller(caller: dict[str, Any], position: str) -> list[dict[str, Any]]:
    """Returns the MCP tool definitions exposed to `caller` at switch `position` (never direct writes)."""
    allowed, _ = check_switch_for_mcp_caller(caller, position)
    if not allowed:
        return []
    tools: list[dict[str, Any]] = []
    for name in caller.get("tools") or []:
        if any(name.endswith(f"_{verb}") for verb in FORBIDDEN_WRITE_VERBS):
            continue
        if name == "kql_catalog_run":
            tools.append(
                {
                    "name": "kql_catalog_run",
                    "description": "Run a read-only KQL query from catalog/kql/catalog.yml by ID (inline KQL is refused).",
                    "inputSchema": {
                        "type": "object",
                        "required": ["queryId"],
                        "properties": {"queryId": {"type": "string", "enum": list(caller.get("kql") or [])}},
                    },
                }
            )
        else:
            tools.append(
                {
                    "name": name,
                    "description": f"Read-only tool {name} (allowlisted for {caller['id']}).",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            )
    if caller.get("allowPropose") and position in ("run", "halt-actions"):
        tools.append(
            {
                "name": "propose_change",
                "description": "Draft a gated change plan for human approval in the approve environment (never executes a write).",
                "inputSchema": {
                    "type": "object",
                    "required": ["plan"],
                    "properties": {"plan": {"type": "object"}},
                },
            }
        )
    return tools


async def handle_mcp_jsonrpc(
    payload: dict[str, Any],
    *,
    caller: dict[str, Any] | None,
    read_flag: Callable[[], str],
    execute_read_tool: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None,
    execute_kql_query: Callable[[str], Awaitable[Any]] | None = None,
    root: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    """Handles a single MCP JSON-RPC 2.0 request with Entra caller check, kill-switch check, and default-deny allowlist."""
    req_id = payload.get("id")
    if caller is None:
        return 403, {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32003, "message": "Forbidden: caller not authorized by governance/mcp-callers.yml"},
        }

    try:
        position = str(read_flag()).strip()
    except Exception:
        position = "unreadable"

    method = str(payload.get("method") or "")
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    tool_name = str(params.get("name") or "") if method == "tools/call" else ""

    ok, reason = check_switch_for_mcp_caller(caller, position, tool_name=tool_name)
    if not ok:
        return 403, {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32001, "message": reason},
        }

    if method == "initialize":
        return 200, {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "serverInfo": {"name": "avd-swarm-tool-layer", "version": "1.0.0"},
                "capabilities": {"tools": {"listChanged": False}},
            },
        }

    if method == "tools/list":
        return 200, {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": list_tools_for_caller(caller, position)},
        }

    if method == "tools/call":
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if tool_name == "propose_change":
            if not caller.get("allowPropose"):
                return 403, {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32002, "message": f"Caller {caller['id']!r} is not allowed to call propose_change"},
                }
            raw_plan = arguments.get("plan")
            if not isinstance(raw_plan, dict):
                return 400, {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": "propose_change requires a plan object"},
                }
            checked = gate_apply.check_plan(raw_plan, root=root)
            checked["approvable"] = position == "run" and checked["status"] == "ready"
            checked["proposedByCaller"] = caller["id"]
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "json", "json": checked}]},
            }

        if tool_name not in set(caller.get("tools") or []):
            return 403, {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32002,
                    "message": f"Tool {tool_name!r} is not in caller {caller['id']!r} allowlist (default deny)",
                },
            }

        if tool_name == "kql_catalog_run":
            if "query" in arguments or "kql" in arguments:
                return 400, {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": "Inline KQL is never allowed; pass queryId from catalog/kql/catalog.yml"},
                }
            query_id = str(arguments.get("queryId") or "").strip()
            if query_id not in set(caller.get("kql") or []):
                return 403, {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32002, "message": f"KQL queryId {query_id!r} is not in caller {caller['id']!r} allowlist"},
                }
            rows = await execute_kql_query(query_id) if execute_kql_query else []
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "json", "json": {"queryId": query_id, "rows": rows}}]},
            }

        result_data = await execute_read_tool(tool_name, arguments) if execute_read_tool else {"tool": tool_name, "ok": True}
        return 200, {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"content": [{"type": "json", "json": result_data}]},
        }

    return 400, {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unsupported MCP method {method!r}"},
    }


def verify_mcp_switch_drill(root: Path | None = None) -> dict[str, Any]:
    """Verifies that every registered MCP connector is refused at the switch positions that forbid it (V1-3)."""
    pol = load_mcp_policy(root)
    callers = pol["callers"]
    matrix: dict[str, dict[str, bool]] = {}
    for cid, caller in sorted(callers.items()):
        matrix[cid] = {}
        for pos in (*VALID_POSITIONS, "unreadable"):
            ok, _ = check_switch_for_mcp_caller(caller, pos, tool_name="virtualdesktop_hostpool_list")
            matrix[cid][pos] = ok

    passed = (
        all(matrix[c]["run"] is True for c in callers)
        and all(matrix[c]["halt-actions"] is True for c in callers)
        and matrix["person"]["halt-agents"] is True
        and all(matrix[c]["halt-agents"] is False for c in callers if c != "person")
        and all(matrix[c]["halt-all"] is False for c in callers)
        and all(matrix[c]["unreadable"] is False for c in callers)
    )
    return {"passed": passed, "matrix": matrix}
