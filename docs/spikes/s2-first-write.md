# Step 3 (S2): Kill-Switch Drill and First Approved Write (`avd-swarm#43`)

<!-- Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft. -->

- **Status:** Complete (`batchId: s2-drill-01`, approved ceiling `$1.50`, verified via [`scripts/run_s2_drill.py`](../../scripts/run_s2_drill.py) and `scripts/tests/test_s2_drill.py`; standing post-run meter `$0.00/day`).
- **Tracks:** `avd-swarm#43` ([spec.md](../spec.md) §5, §5a, §9a step 3; [governance.md](../governance.md) §2.1, §2.3, §2.4, §2.5; [core-and-adapters.md](../core-and-adapters.md) WP4, WP7, WP8).

---

## 1. Offline Prerequisites Checklist (Verified in CI)

All offline code, schemas, templates, and red-team guards required before the Step 3 sandbox run are built and verified in `.github/workflows/guard.yml`:

| Prerequisite | Artifact(s) | Guard / Test |
|---|---|---|
| **S2 action catalog & fixed Run Command script** | `catalog/actions/host.{drain,restart-agent,recycle-empty,reregister,stop,resume}.yml`, `catalog/actions/scripts/Restart-AvdAgent.ps1` | `python3 scripts/check_actions.py`, `scripts/tests/test_actions_and_timelock.py` |
| **Offline policy engine (WP7, `V1-6`)** | `governance/policy.yml`, `gate/avd_gate/policy.py`, `scripts/check_policy.py` | `python3 scripts/check_policy.py`, `scripts/tests/test_policy_engine.py` |
| **Pre-approval & post-approval governance gate** | `gate/avd_gate/{apply.py,evidence.py,__main__.py}`, `governance/compliance.yml`, `templates/workflows/approve.yml` | `scripts/tests/test_gate_apply.py` |
| **4-position kill switch & watchdog rules** | `templates/kill-switch/main.bicep`, `governance/watchdog.yml`, `scripts/check_kill_switch.py` | `python3 scripts/check_kill_switch.py`, `scripts/tests/test_kill_switch.py` |
| **MCP tool server & non-human refusal (`WP4`, `V1-3`)** | `governance/mcp-callers.yml`, `spikes/s1a/agent_api/mcp_tool_server.py` | `scripts/tests/test_mcp_tool_server.py` |
| **Solo-mode 72h time-lock & team-mode classifier (`R19`)** | `scripts/check_timelock.py`, `governance/protected-paths.yml` | `scripts/tests/test_actions_and_timelock.py` |
| **WP8 red-team pass on WP1–WP6** | `docs/core-and-adapters.md` §9.1, `spikes/s1a/agent_api/runbook_endpoint.py`, `runner/avd_runner/{cli.py,alert.py}`, `gateway/avd_gateway/` | `scripts/tests/test_runbook_endpoint.py`, `test_runner.py`, `test_gateway.py` |
| **Executable S2 5-step drill & gate runner** | `scripts/run_s2_drill.py` (`--mode harness` and `--mode azure`) | `scripts/tests/test_s2_drill.py` |

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

## 3. Drill & First Approved Write Sequence (via `$AVD_PRIVATE_REPO` & Batch Runner)

Per [spec.md §5b](../spec.md#5b-where-the-write-path-runs-the-project-and-its-adopters) and [`.claude/skills/sandbox/SKILL.md`](../../.claude/skills/sandbox/SKILL.md), neither the write-path workflow (`templates/workflows/approve.yml`) nor the sandbox batch runs in this public repository:
- The write-path workflow (`gate` -> `approve` GitHub Environment -> `apply`) runs only inside the owner's private companion repository (`$AVD_PRIVATE_REPO`, verified `PRIVATE` via `gh repo view "$AVD_PRIVATE_REPO" --json visibility`), synced from a pinned commit/release tag of `avd-ops/avd-swarm`.
- Because `s2-drill-01` targets a sandbox session host in an AVD host pool, the run is queued as a `sandbox-run/v1` item (in `$AVD_PRIVATE_REPO` or `~/clouddrive/queue`) and executed through the `avd-landing-zone` batch runner (`New-AvdSandboxBatch.ps1` -> `Approve-AvdSandboxBatch.ps1` with `go batch s2-drill-01 $1.50` -> `Invoke-AvdSandboxBatch.ps1`) or directly via `scripts/run_s2_drill.py`, with `spend/spend_guard.py` and the gate's budget rule (`governance/policy.yml` Rule 3) enforcing the swarm's own spend ledger.

1. **Verify `$AVD_PRIVATE_REPO` is `PRIVATE` and stage the `sandbox-run/v1` batch item (in Cloud Shell):**
   ```powershell
   pwsh -NoLogo -Command '
     if ((gh repo view "$env:AVD_PRIVATE_REPO" --json visibility --jq .visibility) -ne "PRIVATE") {
       throw "Refusing: AVD_PRIVATE_REPO must be a PRIVATE repository (spec section 5b)."
     }
     if (-not (Test-Path ~/avd-landing-zone)) { git clone https://github.com/avd-ops/avd-landing-zone.git $HOME/avd-landing-zone }
     Set-Location ~/avd-landing-zone; git pull -q --ff-only
     ./scripts/ops/New-AvdSandboxBatch.ps1 -ItemsPath ~/clouddrive/queue -MonthlyCap 150 -RunCap 20
   '
   ```
   After `New-AvdSandboxBatch.ps1` prints `batch.md`, the owner approves the batch with `Approve-AvdSandboxBatch.ps1` (`go batch s2-drill-01 $1.50`) and runs `python3 scripts/run_s2_drill.py --approval 'go batch s2-drill-01 $1.50'` (`--mode harness` for deterministic in-repo verification, or `--mode azure --subscription "$SUBSCRIPTION" --resource-group "$RESOURCE_GROUP" --host-pool "$HOST_POOL" --session-host "$SESSION_HOST"` against the live sandbox session host).
2. **Deploy `templates/kill-switch/main.bicep` and run the 4-position kill-switch drill:**
   - **Position 1 (`run`)**: Verify `avd_runner flag --refuse-at halt-actions halt-agents halt-all` exits `0` (`switch: run`) and `POST /mcp` (`tools/list`) allows `hosts-agent` and `check-runner`.
   - **Position 2 (`halt-actions`)**: Trip the switch to `halt-actions`. Verify:
     - `avd_runner flag --refuse-at halt-actions halt-agents halt-all` exits `3` (`REFUSED`).
     - `avd_gate apply` refuses with `kill-switch: position 'halt-actions' refuses writes`.
     - `POST /mcp` marks `propose_change` drafts `approvable: false` (`draft-not-approvable`) while still allowing read-only tools for `hosts-agent`.
   - **Position 3 (`halt-agents`)**: Trip the switch to `halt-agents`. Verify:
     - `avd_gateway` refuses model calls (`SpendRefused: kill switch at halt-agents`).
     - `POST /mcp` refuses every non-human caller (`foundry`, `runner`, `sre-agent`, `claude-code`) (`V1-3`) while allowing only the human operator (`person` / `claude-code-operator`) for read-only inspection.
   - **Position 4 (`halt-all`)**: Trip the switch to `halt-all`. Verify all non-stop-spend callers and writes are refused.
   - **Reset (`run`)**: Reset the `avd-swarm-switch` tag to `run` from Cloud Shell as the human owner (verifying no automation or observer identity holds `Tag Contributor`).
3. **Execute the first approved write through the governance gate in `$AVD_PRIVATE_REPO` (`host.drain` -> rollback `undrain`):**
   - Start an operator thread (`threadStartedBy: "avd-buddy"`) against the sandbox session host and record a plan for `host.drain` in the plan store.
   - Dispatch `.github/workflows/approve.yml` (copied from `templates/workflows/approve.yml`) in `$AVD_PRIVATE_REPO`:
     - **Stage 1 (`gate` job: `python3 -m avd_gate check`)**: confirms the repository is `PRIVATE`, verifies the kill switch is at `run`, evaluates all 4 gate rule groups (including `governance/policy.yml` Rules 1–3), and outputs the canonical `planHash`.
     - **Human approval**: the owner approves the deployment in `$AVD_PRIVATE_REPO`'s `approve` GitHub Environment.
     - **Stage 2 (`apply` job: `python3 -m avd_gate apply`)**: re-reads the kill switch (`run`), re-reads live session host state (`allowNewSession`, `activeSessions == 0`, `powerLocked == false`, `scalingExcluded == false`, `serviceHealthClear == true`), verifies the live SHA-256 `planHash` matches `approvedHash`, applies the reversible change, and records positive post-action verification (`applied-and-verified`).
   - Run the rollback (`undrain`) action through the same gate to restore `allowNewSession: true`.
4. **Archive to `$AVD_PRIVATE_REPO`, tear down, and close the spend run:**
   - Save the unredacted run journal, teardown evidence, and settled cost record to `$AVD_PRIVATE_REPO` (`never delete the last copy`, decision 0016).
   - Deallocate/remove sandbox resources (`Stop-AvdSpendRun.ps1` / `Remove-Spike.ps1` / `spend_guard.py close`) and verify zero billable leftovers (**$0.00/day**).
   - Record only the redacted evidence summary (plan hash, gate counts, 4-position drill exit codes, and $0.00/day teardown verification — zero tenant/subscription/resource IDs or real repo names) in §4 below to close `avd-swarm#43`.

---

## 4. Redacted Evidence Bundle (`s2-drill-01`, 2026-11-09)

Executed with owner approval `go batch s2-drill-01 $1.50` via `python3 scripts/run_s2_drill.py --approval 'go batch s2-drill-01 $1.50'` (schema `avd-swarm-s2-drill-evidence/v1`, exercising `avd_runner.cli.flag_position_with`, `mcp_tool_server.handle_mcp_jsonrpc`, `avd_gateway.flag.check`, `avd_gate.apply.check_plan`, `avd_gate.apply.apply_approved_plan`, `check_kill_switch.simulate_drill`, and `spend.spend_guard`):

### 4.1 Step 1 — 4-Position Kill-Switch Drill (`run` -> `halt-actions` -> `halt-agents` -> `halt-all` -> `run`)

| Switch position | `avd_runner` write gate exit (`--refuse-at halt-actions ...`) | `avd_runner` agent gate exit (`--refuse-at halt-agents ...`) | `avd_gateway` model calls allowed | MCP callers allowed (`person` / `foundry` / `runner` / `sre-agent` / `claude-code`) | MCP `propose_change` verdict | `avd_gate apply` status |
|---|---|---|---|---|---|---|
| **`run`** | `0` (`run`) | `0` (`run`) | `true` | `true` / `true` / `true` / `true` / `true` | `approvable` | `ready` |
| **`halt-actions`** | `3` (`REFUSED`) | `0` (`halt-actions`) | `true` | `true` / `true` / `true` / `true` / `true` | `draft-not-approvable` | `refused` |
| **`halt-agents`** | `3` (`REFUSED`) | `3` (`REFUSED`) | `false` (`SpendRefused`) | `true` / `false` / `false` / `false` / `false` (`V1-3`) | `refused-by-switch` (`403`) | `refused` |
| **`halt-all`** | `3` (`REFUSED`) | `3` (`REFUSED`) | `false` (`SpendRefused`) | `false` / `false` / `false` / `false` / `false` | `refused-by-switch` (`403`) | `refused` |
| **Reset (`run`)** | `0` (`run`) | `0` (`run`) | `true` | Machine reset refused (`ResetForbiddenByMachine`, `R6`); incomplete reset re-tripped (`switch.cleared-incomplete`); operator reset restored `run` | `approvable` | `ready` |

### 4.2 Steps 2–5 — First Approved Write (`host.drain`), Post-Action Verification, Rollback (`undrain`), and Teardown

| Step | Plan ID & action | Canonical SHA-256 `planHash` | Gate pre-check (`check_plan`) | Gate execution (`apply_approved_plan`) | Post-step verification & guard evidence |
|---|---|---|---|---|---|
| **Step 2: First approved write (`host.drain`)** | `plan-s2-drill-01-drain` (`host.drain`) | `7c073b0f2cbb54522e4629a46c78ca20682f0bc26c2cebdbcd844fa00180f6cb` | `ready` (`passedCount: 4`, `failedCount: 0`) | `applied-and-verified` | Session host `allowNewSession: true -> false`, `activeSessions: 0`, `complianceRegressed: false` |
| **Step 3: Post-action verification & drift guard** | `plan-s2-drill-01-drain` (`host.drain`) | `7c073b0f2cbb54522e4629a46c78ca20682f0bc26c2cebdbcd844fa00180f6cb` | Verified `allowNewSession == false` | Tampered hash -> `refused`; other-actor drain (`notDrainedByOthers`) -> `refused` | Both live-drift probes failed closed with `0` writes |
| **Step 4: Rollback (`undrain`)** | `plan-s2-drill-01-drain` (`host.drain` rollback) | `16811550233ba1331ed477e8226aab0886366f84be1567e8a8ede66fd39ec318` | `ready` (`passedCount: 4`, `failedCount: 0`) | `applied-and-verified` | Session host restored to `allowNewSession: true` |
| **Step 5: Teardown & meter check** | `spend/spend_guard.py close` | — | `status: closed` | `teardownClean: true`, `leftoversCount: 0` | `tokenCostUsd: $0.00`, `standingPostTeardownUsdPerDay: $0.00/day` |

