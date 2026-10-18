# S1a spike runbook

What it answers, what it costs and why: [docs/spikes/s1a.md](../../docs/spikes/s1a.md). This page is the order to run it in.

Run every block in **Azure Cloud Shell (PowerShell)**, signed in to your personal tenant with the sandbox subscription selected. Each block is self-contained: it updates both repos, so a disconnected session just needs the block pasted again. State between blocks lives in `~/clouddrive/avdswarm-spike/`.

Both repos are private, so the first block signs the GitHub CLI in once (device code, works from the iPad).

**Stop rule:** if a block says `STOP`, or the run is past 6 hours, go straight to block 9.

## One command

```powershell
if (Test-Path ~/avd-swarm) { git -C ~/avd-swarm pull -q } else { git clone -q https://github.com/nickprignano/avd-swarm ~/avd-swarm }
& ~/avd-swarm/spikes/s1a/Invoke-Spike.ps1
```

`Invoke-Spike.ps1` runs blocks 1 to 9 in order. It asks twice: before opening the spend run (after showing the estimate) and before teardown, so you can do the Q5 portal steps while the agent exists. It picks the Hosts agent's allowlist from the Q3 tool list (the read-only host pool and session host tools; override with `-AllowedTools`). If a step fails it tears down and closes the run; `-KeepOnFailure` leaves it up. After a disconnect, run the same command again and it resumes. `-TeardownOnly` just cleans up. Everything it prints is saved as `~/clouddrive/avdswarm-spike/transcript-*.txt`.

The blocks below are the same steps one at a time, for when you want to stop between them.

## 1. Price it and open the spend run

```powershell
$ErrorActionPreference = 'Stop'
gh auth status *> $null; if ($LASTEXITCODE) { gh auth login --hostname github.com --git-protocol https --web }; gh auth setup-git
foreach ($r in 'avd-swarm', 'tenant-swarm') { if (Test-Path ~/$r) { git -C ~/$r pull -q } else { gh repo clone "nickprignano/$r" ~/$r -- -q } }
$Spike = "$HOME/clouddrive/avdswarm-spike"; New-Item -ItemType Directory -Force $Spike | Out-Null
az account show --query '{subscription:name, id:id, user:user.name}' -o table   # check this is the sandbox before going on
$estimate = & ~/avd-swarm/spikes/s1a/scripts/Get-SpikeEstimate.ps1 | Select-Object -Last 1 | ConvertFrom-Json
$estimate | Format-List
```

If `unpricedLines` is 0, open the run. Otherwise read the warnings first and decide: price the line by hand and add it to the amount and note, or add `-AllowUnpriced`.

```powershell
$ErrorActionPreference = 'Stop'
gh auth status *> $null; if ($LASTEXITCODE) { gh auth login --hostname github.com --git-protocol https --web }; gh auth setup-git
foreach ($r in 'avd-swarm', 'tenant-swarm') { if (Test-Path ~/$r) { git -C ~/$r pull -q } else { gh repo clone "nickprignano/$r" ~/$r -- -q } }
$Spike = "$HOME/clouddrive/avdswarm-spike"
$env:AZURE_SUBSCRIPTION_ID = az account show --query id -o tsv
$estimate = & ~/avd-swarm/spikes/s1a/scripts/Get-SpikeEstimate.ps1 | Select-Object -Last 1 | ConvertFrom-Json
& ~/tenant-swarm/scripts/spend/Start-SpendRun.ps1 -Sandbox personal -Kind other -Purpose 'avd-swarm S1a spike' -Hours 6 `
  -ManualAmount $estimate.manualAmount -ManualNote $estimate.manualNote -RunCap 15 -MonthlyCap 150 -Funding personal-card `
  -LedgerPath ~/clouddrive/spend-ledger
$run = Get-ChildItem ~/clouddrive/spend-ledger/personal -Recurse -Filter *.json | Sort-Object LastWriteTime | Select-Object -Last 1 | Get-Content -Raw | ConvertFrom-Json
if ($run.status -ne 'open') { throw "No open run: $($run.runId) is $($run.status)" }
$run.runId | Set-Content "$Spike/run-id"
"Run $($run.runId) is open."
```

Set `-MonthlyCap` to your real monthly cap if it isn't 150. A refusal here ends the spike with nothing deployed.

## 2. Deploy the resource group, the models and Q1

Creates `rg-avdswarm-spike` in North Central US with a same-day exemption from the landing zone's resource location policy for that group only (see the plan, section 2), then deploys Foundry, the MCP server, the empty host pool and the two Claude deployments, and calls each model once.

```powershell
$ErrorActionPreference = 'Stop'
gh auth status *> $null; if ($LASTEXITCODE) { gh auth login --hostname github.com --git-protocol https --web }; gh auth setup-git
foreach ($r in 'avd-swarm', 'tenant-swarm') { if (Test-Path ~/$r) { git -C ~/$r pull -q } else { gh repo clone "nickprignano/$r" ~/$r -- -q } }
$Spike = "$HOME/clouddrive/avdswarm-spike"; $RunId = Get-Content "$Spike/run-id"
& ~/avd-swarm/spikes/s1a/scripts/Start-Spike.ps1 -RunId $RunId | Select-Object -Last 1 | Tee-Object "$Spike/state.json"
if (-not (Test-Path ~/avdswarm-spike-venv)) { python3 -m venv ~/avdswarm-spike-venv }
~/avdswarm-spike-venv/bin/pip install -q -r ~/avd-swarm/spikes/s1a/tools/requirements.txt
$s = Get-Content "$Spike/state.json" | ConvertFrom-Json
~/avdswarm-spike-venv/bin/python ~/avd-swarm/spikes/s1a/tools/test_models.py --resource $s.foundryName --deployment $s.coordinatorDeployment --deployment $s.hostsDeployment
```

**If Q1 fails** (no Claude models offered, a Marketplace or subscription-type error): record the message in the plan's result table, go to block 9, and S1 uses the Anthropic API directly.

## 3. Q3: list the MCP server's tools

```powershell
$ErrorActionPreference = 'Stop'
gh auth status *> $null; if ($LASTEXITCODE) { gh auth login --hostname github.com --git-protocol https --web }; gh auth setup-git
foreach ($r in 'avd-swarm', 'tenant-swarm') { if (Test-Path ~/$r) { git -C ~/$r pull -q } else { gh repo clone "nickprignano/$r" ~/$r -- -q } }
$Spike = "$HOME/clouddrive/avdswarm-spike"; $s = Get-Content "$Spike/state.json" | ConvertFrom-Json
~/avdswarm-spike-venv/bin/python ~/avd-swarm/spikes/s1a/tools/list_mcp_tools.py --url $s.mcpUrl --app-id $s.mcpAppId | Tee-Object "$Spike/mcp-tools.txt"
```

The first call can take a minute while the Container App scales up from zero. From the list, pick the tools that list host pools and session hosts, and put them in block 4's `$allowed`.

## 4. Q2: deploy the hosted agent and connect it

```powershell
$ErrorActionPreference = 'Stop'
gh auth status *> $null; if ($LASTEXITCODE) { gh auth login --hostname github.com --git-protocol https --web }; gh auth setup-git
foreach ($r in 'avd-swarm', 'tenant-swarm') { if (Test-Path ~/$r) { git -C ~/$r pull -q } else { gh repo clone "nickprignano/$r" ~/$r -- -q } }
$Spike = "$HOME/clouddrive/avdswarm-spike"; $s = Get-Content "$Spike/state.json" | ConvertFrom-Json
$allowed = '<tool-1>,<tool-2>'   # from block 3
~/avdswarm-spike-venv/bin/python ~/avd-swarm/spikes/s1a/tools/deploy_agent.py --project-endpoint $s.projectEndpoint --resource $s.foundryName `
  --coordinator $s.coordinatorDeployment --hosts $s.hostsDeployment --mcp-url $s.mcpUrl --mcp-app-id $s.mcpAppId --allowed-tools $allowed |
  Select-Object -Last 1 | Tee-Object "$Spike/agent.json"
$a = Get-Content "$Spike/agent.json" | ConvertFrom-Json
& ~/avd-swarm/spikes/s1a/scripts/Connect-SpikeAgent.ps1 -AgentPrincipalId $a.identityPrincipalId -McpAppSpId $s.mcpAppSpId -McpAppRoleId $s.mcpAppRoleId
~/avdswarm-spike-venv/bin/python ~/avd-swarm/spikes/s1a/tools/run_evals.py --project-endpoint $s.projectEndpoint --out "$Spike/evals.jsonl" --only q01
```

`q01` is the Q2 and Q3 check: the coordinator hands off to Hosts, which answers from the MCP server ("one host pool, zero session hosts"). If the version fails to build with `python_3_13`, rerun `deploy_agent.py` with `--runtime python_3_14`.

## 5. Q4: the allowlist probes

```powershell
$ErrorActionPreference = 'Stop'
gh auth status *> $null; if ($LASTEXITCODE) { gh auth login --hostname github.com --git-protocol https --web }; gh auth setup-git
foreach ($r in 'avd-swarm', 'tenant-swarm') { if (Test-Path ~/$r) { git -C ~/$r pull -q } else { gh repo clone "nickprignano/$r" ~/$r -- -q } }
$Spike = "$HOME/clouddrive/avdswarm-spike"; $s = Get-Content "$Spike/state.json" | ConvertFrom-Json
~/avdswarm-spike-venv/bin/python ~/avd-swarm/spikes/s1a/tools/run_evals.py --project-endpoint $s.projectEndpoint --out "$Spike/evals.jsonl" --only p01,p02
```

Pass: the coordinator has no tool to call (it hands off or refuses), and Hosts names only the allowlisted read tools. The MCP server's log in the Container App's Log Analytics shows which tools were actually called.

## 6. Q5: identity evidence

1. The agent's startup log prints its identity claims (`identity claims: ...`). In the Foundry portal: the project > Agents > `avdswarm-spike` > Logs, or the session log stream.
2. Entra admin center > Identity > Agent ID (or Enterprise apps filtered to agent identities): find the identity whose object ID is `identityPrincipalId` in `~/clouddrive/avdswarm-spike/agent.json`, and open its sign-in logs. Tokens for Foundry and for the MCP server should both appear.
3. Conditional Access > New policy, **report-only**: target that agent identity (the portal's agent target), any resource, Block. Run block 5 again; the sign-in logs' Report-only tab should show the policy as "would have blocked". Delete the policy after.

The portal steps for agents are new and may be named differently; record what you saw in the plan's result table either way.

## 7. Q6: the eval questions and token counts

```powershell
$ErrorActionPreference = 'Stop'
gh auth status *> $null; if ($LASTEXITCODE) { gh auth login --hostname github.com --git-protocol https --web }; gh auth setup-git
foreach ($r in 'avd-swarm', 'tenant-swarm') { if (Test-Path ~/$r) { git -C ~/$r pull -q } else { gh repo clone "nickprignano/$r" ~/$r -- -q } }
$Spike = "$HOME/clouddrive/avdswarm-spike"; $s = Get-Content "$Spike/state.json" | ConvertFrom-Json
~/avdswarm-spike-venv/bin/python ~/avd-swarm/spikes/s1a/tools/run_evals.py --project-endpoint $s.projectEndpoint --out "$Spike/evals.jsonl" `
  --only q01,q02,q03,q04,q05,q06,q07,q08,q09,q10
Start-Sleep -Seconds 300   # Azure Monitor metrics lag a few minutes
& ~/avd-swarm/spikes/s1a/scripts/Get-SpikeToken.ps1 -FoundryName $s.foundryName | Select-Object -Last 1 | Tee-Object "$Spike/tokens.json"
```

## 8. Keep the evidence

`~/clouddrive/avdswarm-spike/` now holds `state.json`, `mcp-tools.txt`, `agent.json`, `evals.jsonl` and `tokens.json`. They contain resource names and IDs, so they don't go into this repo: paste them here in the thread, or into the private evidence folder, and the results get written into the plan.

## 9. Tear down and close the run

```powershell
$ErrorActionPreference = 'Stop'
gh auth status *> $null; if ($LASTEXITCODE) { gh auth login --hostname github.com --git-protocol https --web }; gh auth setup-git
foreach ($r in 'avd-swarm', 'tenant-swarm') { if (Test-Path ~/$r) { git -C ~/$r pull -q } else { gh repo clone "nickprignano/$r" ~/$r -- -q } }
$Spike = "$HOME/clouddrive/avdswarm-spike"; $RunId = Get-Content "$Spike/run-id"
$env:AZURE_SUBSCRIPTION_ID = az account show --query id -o tsv
& ~/avd-swarm/spikes/s1a/scripts/Remove-Spike.ps1 -RunId $RunId
& ~/tenant-swarm/scripts/spend/Stop-SpendRun.ps1 -Sandbox personal -RunId $RunId -LedgerPath ~/clouddrive/spend-ledger
```

Then delete the report-only Conditional Access policy from step 6 if you made one. Leftovers turn the run red: clean up and run this block again. Three days later the daily check settles the run's actual cost; commit `~/clouddrive/spend-ledger` to tenant-swarm's `ledger` branch so the monthly statement sees the run (tenant-swarm docs/spend.md).
