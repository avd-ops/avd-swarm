# Step 3 (S2): Kill-Switch Drill and First Approved Write (`avd-swarm#43`)

<!-- Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft. -->

- **Status:** Offline prerequisites complete; waiting on the owner's `go batch s2-drill-01 $1.50` before any Azure or sandbox command runs.
- **Tracks:** `avd-swarm#43` ([spec.md](../spec.md) §5, §5a, §9a step 3; [governance.md](../governance.md) §2.1, §2.3, §2.4, §2.5; [core-and-adapters.md](../core-and-adapters.md) WP4, WP7, WP8).

---

## 1. Offline Prerequisites Checklist (Verified in CI)

All offline code, schemas, templates, and red-team guards required before the Step 3 sandbox run are built and verified in `.github/workflows/guard.yml`:

| Prerequisite | Artifact(s) | Guard / Test |
|---|---|---|
| **S2 action catalog & fixed Run Command script** | `catalog/actions/host.{drain,restart-agent,recycle-empty,reregister,stop,resume}.yml`, `catalog/actions/scripts/Restart-AvdAgent.ps1` | `python3 scripts/check_actions.py`, `scripts/tests/test_actions_and_timelock.py` |
| **Offline policy engine (WP7, `V1-6`)** | `governance/policy.yml`, `gate/avd_gate/policy.py`, `scripts/check_policy.py` | `python3 scripts/check_policy.py`, `scripts/tests/test_policy_engine.py` |
| **Pre-approval & post-approval governance gate** | `gate/avd_gate/{apply.py,evidence.py,cli.py}`, `governance/compliance.yml`, `templates/workflows/approve.yml` | `scripts/tests/test_gate_apply.py` |
| **4-position kill switch & watchdog rules** | `templates/kill-switch/main.bicep`, `governance/watchdog.yml`, `scripts/check_kill_switch.py` | `python3 scripts/check_kill_switch.py`, `scripts/tests/test_kill_switch.py` |
| **MCP tool server & non-human refusal (`WP4`, `V1-3`)** | `governance/mcp-callers.yml`, `spikes/s1a/agent_api/mcp_tool_server.py` | `scripts/tests/test_mcp_tool_server.py` |
| **Solo-mode 72h time-lock & team-mode classifier (`R19`)** | `scripts/check_timelock.py`, `governance/protected-paths.yml` | `scripts/tests/test_actions_and_timelock.py` |
| **WP8 red-team pass on WP1–WP6** | `docs/core-and-adapters.md` §9.1, `spikes/s1a/agent_api/runbook_endpoint.py`, `runner/avd_runner/{cli.py,alert.py}`, `gateway/avd_gateway/` | `scripts/tests/test_runbook_endpoint.py`, `test_runner.py`, `test_gateway.py` |

---

## 2. Written Cost Estimate (`docs/spend.md` Format)

Region: **North Central US** (`northcentralus`), reusing the S1a sandbox estate for a bounded **2-hour** drill window (`max-hours: 2`). Nothing bills standing compute after teardown (`Stop-Spike.ps1` / `spend_guard.py close`).

| Line item | Meter / SKU | Quantity & duration (2h window) | Retail unit price (USD) | Estimated run cost (USD) | Per-day cost if not torn down (USD/day) |
|---|---|---|---|---|---|
| **1. Kill switch Logic App (`templates/kill-switch/main.bicep`)** | Azure Logic Apps Consumption (`Standard Action` / `Built-in Action`) | ~40 action executions across 4 position transitions (`run` -> `halt-actions` -> `halt-agents` -> `halt-all` -> reset) | $0.000025 / action (first 4,000 built-in actions/month free) | **$0.01** | **$0.00** (idle; Consumption bills per execution only) |
| **2. Plan store & audit storage (`StorageV2 Standard_LRS`)** | Azure Table + Queue + Blob LRS (`avd-swarm` plan store, findings, spend counter) | < 10 MB stored, < 500 transactions during drill | $0.045 / GB-month, $0.00036 / 10k ops | **$0.01** | **$0.01** |
| **3. S1a Agent API & MCP endpoint (`Microsoft.App/containerApps`)** | Container Apps Consumption (0.5 vCPU, 1.0 GiB, minReplicas 0) | ~0.5 active hours during drill & MCP refusal checks | Covered by monthly free grant (180,000 vCPU-s, 360,000 GiB-s); list ~$0.09/hr active | **$0.05** | **$0.00** (scales to 0 when idle) |
| **4. Session host target (`Standard_D2s_v5` in sandbox host pool)** | Existing sandbox session host started only for the `host.drain` + `host.restart-agent` verification window, then deallocated | 1 VM × 2 hours (`Standard_D2s_v5` Linux/Windows compute + 128 GiB Standard SSD OS disk) | Compute ~$0.096 / hr; OS disk ~$0.32 / day | **$0.43** | **$2.62** (if VM left running; **$0.32/day** deallocated; **$0.00/day** after `Stop-Spike.ps1`) |
| **5. Model tokens (`gateway/avd_gateway`)** | Anthropic API (`claude-sonnet-5-5` / `claude-haiku-5-5`) metered by `avd_gateway` | 1 operator-started thread (`avd-buddy`) proposing `host.drain` and `host.restart-agent` + 1 manual runbook check | Pinned in `governance/model-prices.yml`; hard-capped at `perRunUsd: $1.00` | **$1.00** (hard ceiling; typical < $0.25) | **$0.00** (no unattended model calls once closed) |
| **Total estimated batch ceiling** | | **2-hour bounded run (`s2-drill-01`)** | | **$1.50** | **$2.63/day** if left running; **$0.00/day** after teardown |

- **Spend guard check (`spend/spend_guard.py decide_open`):**
  - Estimated run ceiling: **$1.50** (inside `perRunCapUsd: $20.00` in `governance/policy.yml`).
  - Monthly cap: **$150.00** (`governance/policy.yml`).
  - Unpriced lines: **0**.
  - Required owner approval phrase before any Azure command runs:
    `go batch s2-drill-01 $1.50`

---

## 3. Drill & First Approved Write Sequence (After Owner's `go batch s2-drill-01 $1.50`)

1. **Open the spend run (`spend/spend_guard.py open`):**
   ```bash
   python3 spend/spend_guard.py --sandbox lab open \
     --estimate 1.50 --run-cap 20.00 --monthly-cap 150.00 \
     --month-to-date 0.00 --max-hours 2 --resource-group rg-avd-swarm-lab
   ```
2. **Deploy `templates/kill-switch/main.bicep` and run the 4-position kill-switch drill:**
   - **Position 1 (`run`)**: Verify `avd_runner flag --refuse-at halt-actions halt-agents halt-all` exits `0` (`switch: run`) and `POST /mcp` (`tools/list`) allows `hosts-agent` and `check-runner`.
   - **Position 2 (`halt-actions`)**: Trip the switch to `halt-actions`. Verify:
     - `avd_runner flag --refuse-at halt-actions halt-agents halt-all` exits `3` (`REFUSED`).
     - `avd_gate apply` refuses with `kill-switch: position 'halt-actions' refuses writes`.
     - `POST /mcp` refuses `propose_change` while still allowing read-only tools for `hosts-agent`.
   - **Position 3 (`halt-agents`)**: Trip the switch to `halt-agents`. Verify:
     - `avd_gateway` refuses model calls (`SpendRefused: kill switch at halt-agents`).
     - `POST /mcp` refuses every non-human caller (`hosts-agent`, `check-runner`, `sre-agent-adapter`) (`V1-3`) while allowing only the human operator (`claude-code-operator`) for read-only inspection.
   - **Position 4 (`halt-all`)**: Trip the switch to `halt-all`. Verify all non-stop-spend callers and writes are refused.
   - **Reset (`run`)**: Reset the `avd-swarm-switch` tag to `run` from Cloud Shell as the human owner (verifying no automation or observer identity holds `Tag Contributor`).
3. **Execute the first approved write through the governance gate (`host.drain` -> `host.restart-agent`):**
   - Start an operator thread (`threadStartedBy: "avd-buddy"`) against the sandbox session host and record a plan for `host.drain` (and/or `host.restart-agent`).
   - Run Stage 1 (`gate` job: `python3 -m avd_gate check`), confirming all 4 gate rule groups (including `governance/policy.yml` Rules 1–3) pass and emit the canonical `planHash`.
   - Approve the deployment in the private repo's `approve` GitHub Environment.
   - Run Stage 2 (`apply` job: `python3 -m avd_gate apply`), re-reading the kill switch (`run`), re-reading live session host state (`allowNewSession`, `activeSessions == 0`, `powerLocked == false`, `scalingExcluded == false`, `serviceHealthClear == true`), verifying the live SHA-256 `planHash` matches `approvedHash`, applying the reversible change, and recording positive post-action verification (`applied-and-verified`).
4. **Teardown & close the spend run (`spend/spend_guard.py close`):**
   - Restore host drain state / deallocate or remove sandbox resources via `Stop-Spike.ps1`.
   - Verify zero billable leftovers and close the run record with `spend_guard.py close`.
