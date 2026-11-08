# Azure CLI MCP Server (`Azure/azure-api-mcp`) adapter

> Personal project. Research and learning only, not for production, provided as is. Not affiliated with, endorsed by or supported by the author's employer, Liquidware or Microsoft.

**Status:** Designed (`adapter.yml`). **Default:** Off. **Replaces in core:** Nothing (`addsTo: investigate`).

## What it is

[Azure/azure-api-mcp](https://github.com/Azure/azure-api-mcp) (MIT) is Microsoft's Go-based MCP server that wraps the `az` CLI behind a `--readonly` mode and managed or workload identity sign-in. It is the Azure read tool used by open-source investigators such as HolmesGPT ([docs/prior-art.md](../../docs/prior-art.md)).

## Why core uses `microsoft/mcp` instead, and why this is an optional adapter

| Dimension | Core: Azure MCP Server (`microsoft/mcp`) | Adapter: Azure CLI MCP Server (`Azure/azure-api-mcp`) |
|---|---|---|
| **Maintainer & license** | Microsoft (`microsoft/mcp`), MIT | Microsoft (`Azure/azure-api-mcp`), MIT |
| **Tool surface** | Typed, per-operation tools (`virtualdesktop_hostpool_list`, `virtualdesktop_hostpool_host_list`, `virtualdesktop_hostpool_host_user-list`) in `--mode all --read-only --namespace virtualdesktop` | Single free-form `az` CLI wrapper tool protected by `--readonly` prefix/verb matching |
| **Allowlist enforcement** | Deterministic per-tool allowlist (`HOSTS_ALLOWED_TOOLS`) enforced in code before sending tools to Claude | Cannot restrict to `az desktopvirtualization` by tool name alone because one tool accepts arbitrary `az` subcommands |
| **Prompt caching** | Fixed tool catalog cached across turns (`cache_control: {"type": "ephemeral"}`) | Free-form CLI help and larger multi-service command outputs increase token consumption |
| **Best fit** | Deployed `avd-swarm` agents (`hosts`, `cost`, `intune`) and scheduled/alert runbooks | Interactive operator troubleshooting in Cloud Shell or an IDE across adjacent resources (NICs, route tables, DNS zones) not yet exposed as typed `microsoft/mcp` tools |

Notice also that **neither MCP server is used for test harnesses (`Invoke-Spike.ps1`), collectors (`avd-current-state`) or deployments (`avd-landing-zone`)**: those know the exact queries and assertions ahead of time and run deterministic PowerShell and Bicep at `$0` token cost.

## Cost and metering

- **Software & ARM reads:** `$0` (MIT open-source binary; ARM `GET` calls are free).
- **Compute when deployed:** `$0` standing cost when run locally in Cloud Shell/IDE or on Azure Container Apps Consumption with `minReplicas: 0` (`0.5 vCPU`, `1 GiB`), which bills only Active vCPU-seconds and Active Memory GiB-seconds above the monthly free grant.
- **Model tokens:** Every CLI output returned to the model bills input tokens through `gateway/avd_gateway/meter.py`.

## Enabling locally for interactive operator use

Generate a locked-down, zero-secret `.mcp.json` that pairs `microsoft/mcp` (`--mode all --read-only`) with `az-api-mcp` (`--readonly`):

```bash
python3 scripts/mcp_config.py --all-avd-namespaces --include-az-cli-mcp --output .mcp.json
python3 scripts/mcp_config.py --check .mcp.json
```

## Security and kill switch

1. **Mandatory `--readonly`:** Both `scripts/mcp_config.py` and the adapter contract refuse any `az-cli-mcp` configuration missing `--readonly`.
2. **RBAC is the real boundary:** Even with `--readonly` on the server, the identity running `az-api-mcp` must hold only `Reader`, `Desktop Virtualization Reader`, `Monitoring Reader`, or `Log Analytics Reader` on the target scope—never `Contributor`, `Owner`, or any `Desktop Virtualization * Operator` / `* Contributor` role.
3. **Kill switch:** Checked at the core dispatcher before any tool call. `halt-agents` and `halt-all` refuse every `az-cli-mcp` call immediately; `writes: none` holds in every switch position.
