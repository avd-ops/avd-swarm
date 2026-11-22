# `w365-agents` Adapter (Windows 365 for Agents CUA Gateway)

**Status:** Designed (off by default). See [avd-ops ADR 0022](https://github.com/avd-ops/avd-ops/blob/main/docs/knowledge/decisions/0022-cua-external-executor-plumbing.md) and [cua-executor-spec.md](https://github.com/avd-ops/avd-ops/blob/main/docs/cua-executor-spec.md).

This optional adapter wraps Microsoft's `mcp_W365ComputerUse` endpoint behind a default-deny MCP policy gateway (`scripts/cua/w365_cua_gateway.py` + `scripts/cua/policy.yml`) and provides a zero-Azure-spend auth probe (`scripts/cua/q1_auth_probe.py`) to verify `StartSession` before creating any billing policy or Cloud PC pool.

## Files

- `scripts/cua/policy.yml` — default-deny MCP tool policy (`allow`: `StartSession`, `EndSession`, `screenshot`, `get_accessibility_tree`, `browser_navigate`, `click`, `type_text`; `deny`: shell, Python, JS eval, clipboard, `kill_process`).
- `scripts/cua/w365_cua_gateway.py` — evaluates tool calls, enforces `WARDEN_HALT=1` (`EndSession`-only when halted), logs JSONL decisions with SHA-256 screenshot hashes, and forwards allowed calls to `mcp_W365ComputerUse`.
- `scripts/cua/q1_auth_probe.py` — `$0` Azure `StartSession` probe reading `AZURE_TENANT_ID` and `W365_AGENT_TOKEN` from environment variables.

Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
