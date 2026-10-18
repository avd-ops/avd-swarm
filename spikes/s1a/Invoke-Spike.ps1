# avd-swarm S1a spike, start to finish. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
<#
.SYNOPSIS
Runs the whole S1a spike in one command: price, open the spend run, deploy, Q1-Q6, keep the
evidence, tear down and close the run.

.DESCRIPTION
Run it in Azure Cloud Shell (PowerShell), signed in to the sandbox subscription. It stops twice
for you: once to confirm the estimate before anything is deployed, and once before teardown so you
can do the Q5 portal steps while the agent still exists (-NoPause skips that).

It resumes: progress lives in ~/clouddrive/avdswarm-spike/progress.json, so after a Cloud Shell
disconnect, run the same command again and it picks up at the first step that hasn't finished.
If a step fails, it tears down and closes the run unless you pass -KeepOnFailure.
Everything it prints is also saved as a transcript next to the evidence.

.EXAMPLE
& ~/avd-swarm/spikes/s1a/Invoke-Spike.ps1
.EXAMPLE
& ~/avd-swarm/spikes/s1a/Invoke-Spike.ps1 -TeardownOnly
.EXAMPLE
& ~/avd-swarm/spikes/s1a/Invoke-Spike.ps1 -Provider Anthropic
The fallback while Foundry has no Claude quota: Anthropic's API from a Container App, and Entra Agent ID
through Graph instead of Foundry. No API key: after the deploy it shows the values for a workload identity
federation rule in the Claude Console, asks for the rule's IDs, and checks the token exchange before any question.
#>
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', '', Justification = 'Used inside the step script blocks.')]
[CmdletBinding()]
param(
    [string] $Sandbox = 'personal',
    [double] $RunCap = 20,
    [double] $MonthlyCap = 150,
    [string] $Funding = 'personal-card',
    # Comma-separated MCP tool names for the Hosts agent. Default: the read-only host pool and session host tools found in Q3.
    [string] $AllowedTools,
    [ValidateSet('python_3_13', 'python_3_14')][string] $Runtime = 'python_3_13',
    # Open the run even if a line is unpriced (the estimate's note says which).
    [switch] $AllowUnpriced,
    # Don't wait before teardown (skips the Q5 portal steps).
    [switch] $NoPause,
    # Leave the resources up when a step fails, to look at them. Run -TeardownOnly after.
    [switch] $KeepOnFailure,
    [switch] $TeardownOnly,
    # Foundry: Claude in Foundry with hosted agents. Anthropic: the fallback (see the third example).
    [ValidateSet('Foundry', 'Anthropic')][string] $Provider = 'Foundry'
)
$ErrorActionPreference = 'Stop'

$Repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)          # ~/avd-swarm
$TenantSwarm = Join-Path (Split-Path -Parent $Repo) 'tenant-swarm'      # ~/tenant-swarm, for the spend scripts
$Kit = $PSScriptRoot
$Spike = Join-Path $HOME 'clouddrive/avdswarm-spike'
$Ledger = Join-Path $HOME 'clouddrive/spend-ledger'
$Venv = Join-Path $HOME 'avdswarm-spike-venv'
$Py = Join-Path $Venv 'bin/python'
$ProgressFile = Join-Path $Spike 'progress.json'
New-Item -ItemType Directory -Force $Spike | Out-Null

function Write-Phase([string] $Message) { Write-Host "`n======== $Message ========" -ForegroundColor Cyan }

function Get-SpikeProgress {
    if (Test-Path $ProgressFile) { return Get-Content -Raw $ProgressFile | ConvertFrom-Json -AsHashtable }
    @{ done = @(); results = @{} }
}
function Save-SpikeProgress($Progress) { $Progress | ConvertTo-Json -Depth 6 | Set-Content $ProgressFile }

function Get-LastJson {
    # The kit's scripts end with one JSON line; everything before it is progress for the reader.
    param([object[]] $Lines, [string] $Step)
    $json = @($Lines | ForEach-Object { "$_" } | Where-Object { $_ -match '^\{.*\}$' } |
            ForEach-Object { $_ | ConvertFrom-Json } | Where-Object { -not $Step -or $_.step -eq $Step })
    if ($json.Count -eq 0) { throw "No '$Step' result line in the output above." }
    $json[-1]
}

function Invoke-Native {
    # Runs a Python tool, shows its output as it comes, and fails with the exit code.
    param([string] $Step, [string[]] $Arguments, [int[]] $OkExitCodes = @(0))
    Write-Host "--> $Step"
    $lines = & $Py @Arguments 2>&1 | ForEach-Object { $line = "$_"; Write-Host $line; $line }
    if ($LASTEXITCODE -notin $OkExitCodes) { throw "$Step failed with exit code $LASTEXITCODE (output above)." }
    , @($lines)
}

function Invoke-NativeAz {
    # az with the full error on failure (lesson 0012).
    param([string[]] $Arguments)
    $out = & az @Arguments --only-show-errors -o json 2>&1
    if ($LASTEXITCODE) { throw "az $($Arguments[0..1] -join ' ') failed: $($out -join "`n")" }
    $out
}

function Wait-SpikeAgentsHealthy {
    # The agents app reinstalls on each revision; wait until it answers with the expected allowlist (at most 10 minutes).
    param([string] $Url, [string] $Allowed)
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $health = Invoke-RestMethod -Uri "$Url/healthz" -TimeoutSec 10
            if (($health.allowedTools -join ',') -eq $Allowed) { Write-Host "Agents app is up with allowlist: $Allowed"; return }
        }
        catch { Write-Verbose "not up yet: $($_.Exception.Message)" }
        Start-Sleep -Seconds 10
    }
    throw "The agents app didn't come back with the new allowlist in 10 minutes ($Url/healthz)."
}

function Invoke-Step {
    param([string] $Name, [scriptblock] $Body)
    $progress = Get-SpikeProgress
    if ($Name -in $progress.done) { Write-Host "(already done: $Name)"; return }
    Write-Phase $Name
    & $Body $progress
    $progress = Get-SpikeProgress
    $progress.done = @($progress.done) + $Name
    Save-SpikeProgress $progress
}

function Save-SpikeResult([string] $Key, $Value) {
    $progress = Get-SpikeProgress
    $progress.results[$Key] = $Value
    Save-SpikeProgress $progress
}

function Invoke-Teardown {
    Write-Phase 'Teardown: delete everything and close the spend run'
    $runId = if (Test-Path "$Spike/run-id") { Get-Content "$Spike/run-id" } else { $null }
    if (-not $runId) { Write-Host 'No spend run was opened, so nothing was deployed.'; return }
    $env:AZURE_SUBSCRIPTION_ID = az account show --query id -o tsv
    $runProvider = if (Test-Path "$Spike/provider") { Get-Content "$Spike/provider" } else { $Provider }
    & (Join-Path $Kit 'scripts/Remove-Spike.ps1') -RunId $runId -Provider $runProvider
    if ($LASTEXITCODE) { throw 'Teardown left something behind (listed above). Clean it up and run -TeardownOnly again.' }
    & (Join-Path $TenantSwarm 'scripts/spend/Stop-SpendRun.ps1') -Sandbox $Sandbox -RunId $runId -LedgerPath $Ledger
    Save-SpikeResult 'teardown' 'clean'
    Write-Host "`nRun $runId closed. Delete the report-only Conditional Access policy if you made one."
    Write-Host "Three days from now the daily check settles the actual cost; commit $Ledger to tenant-swarm's ledger branch."
    Move-Item $ProgressFile "$Spike/progress-$runId.json" -Force
    Remove-Item "$Spike/run-id", "$Spike/provider" -Force -ErrorAction SilentlyContinue
}

Start-Transcript -Path (Join-Path $Spike "transcript-$([datetime]::UtcNow.ToString('yyyyMMdd-HHmmss')).txt") | Out-Null
try {
    # ---------- Prerequisites ----------
    Write-Phase 'Checking the repos, the subscription and the tools'
    gh auth status *> $null
    if ($LASTEXITCODE) { gh auth login --hostname github.com --git-protocol https --web }
    gh auth setup-git
    if (Test-Path $TenantSwarm) { git -C $TenantSwarm pull -q } else { gh repo clone nickprignano/tenant-swarm $TenantSwarm -- -q }
    if ($LASTEXITCODE) { throw 'Could not get tenant-swarm (the spend scripts). Check gh auth status.' }
    $account = az account show -o json | ConvertFrom-Json
    Write-Host "Subscription: $($account.name)  User: $($account.user.name)"

    if ($TeardownOnly) { Invoke-Teardown; return }
    if ((Test-Path "$Spike/provider") -and (Get-Content "$Spike/provider") -ne $Provider) {
        $Provider = Get-Content "$Spike/provider"
        Write-Host "Resuming the open run with -Provider $Provider, the provider it was opened with."
    }

    # The deployment registers the MCP server's Entra app, and Q2 assigns its app role to the agents.
    # Both need an Entra role; check before the spend run opens, not halfway through the deploy.
    if ('deploy' -notin (Get-SpikeProgress).done) {
        $needed = 'Global Administrator', 'Application Administrator', 'Cloud Application Administrator'
        $roles = @(az rest --method get --url 'https://graph.microsoft.com/v1.0/me/transitiveMemberOf/microsoft.graph.directoryRole?$select=displayName' `
                --query 'value[].displayName' -o tsv 2>&1)
        if ($LASTEXITCODE) { throw "Could not read your Entra roles: $($roles -join ' ')" }
        if (-not ($roles | Where-Object { $_ -in $needed })) {
            throw "$($account.user.name) has none of: $($needed -join ', ') (has: $(if ($roles) { $roles -join ', ' } else { 'no Entra roles' })). " +
                'The spike creates an Entra app for the MCP server and assigns its app role to the agents. ' +
                'Sign in to Cloud Shell as an account with one of those roles, or assign Cloud Application Administrator to this one for the run. Nothing was deployed.'
        }
        Write-Host "Entra role for the app registration: $(@($roles | Where-Object { $_ -in $needed }) -join ', ')"
    }

    # Each Claude deployment needs 50 (thousand tokens per minute; Start-Spike.ps1 -Capacity) of the subscription's quota in eastus2,
    # and new subscriptions can start at 0. Check before the spend run opens.
    if ($Provider -eq 'Foundry' -and 'deploy' -notin (Get-SpikeProgress).done) {
        Write-Host 'Reading the subscription''s Claude quota in eastus2 ...'
        $usage = @(az cognitiveservices usage list -l eastus2 -o json --only-show-errors 2>&1)
        if ($LASTEXITCODE) { throw "Could not read the Foundry quota in eastus2: $($usage -join ' ')" }
        $claudeQuota = @(($usage -join "`n") | ConvertFrom-Json | Where-Object { $_.name.localizedValue -match 'Claude' })
        foreach ($q in $claudeQuota) { Write-Host "  $($q.name.localizedValue): $($q.currentValue) of $($q.limit) used ($($q.name.value))" }
        $short = foreach ($m in 'claude-haiku-5-5', 'claude-opus-5-5', 'claude-sonnet-5-5') {
            # claude-haiku-5-5 -> 'Claude Haiku 5.5'
            $label = ($m -replace '^claude-(\w+)-(\d+)-(\d+)$', 'Claude $1 $2.$3')
            $lines = @($claudeQuota | Where-Object { $_.name.localizedValue -match "$([regex]::Escape($label))$" })
            if (-not $lines) { Write-Warning "No quota line found for $label; the deploy will tell." }
            elseif (-not ($lines | Where-Object { ($_.limit - $_.currentValue) -ge 50 })) { $label }
        }
        if ($short) {
            throw "Less than 50 thousand TPM of Claude quota left in eastus2 for: $(@($short) -join ', '). Request Tokens Per Minute quota for them in the Foundry portal (Quota page, eastus2, Global Standard; 50 thousand TPM each), then rerun, or run the fallback with -Provider Anthropic. Nothing was deployed and no spend run was opened."
        }
    }

    $progress = Get-SpikeProgress
    if ($progress.done.Count -eq 0) {
        $answer = Read-Host "Run the S1a spike in this subscription (sandbox '$Sandbox', run cap $RunCap)? [y/N]"
        if ($answer -notmatch '^(y|yes)$') { Write-Host 'Stopped; nothing was done.'; return }
    }
    else {
        Write-Host "Resuming; done so far: $($progress.done -join ', ')"
    }

    if (-not (Test-Path $Py)) {
        Write-Host 'Creating the Python environment (a minute or two)...'
        python3 -m venv $Venv
    }
    & (Join-Path $Venv 'bin/pip') install -q -r (Join-Path $Kit 'tools/requirements.txt')
    if ($LASTEXITCODE) { throw 'pip install failed (output above).' }

    # ---------- Price it and open the spend run ----------
    Invoke-Step 'price-and-open-run' {
        $estimateArgs = if ($Provider -eq 'Anthropic') { @{ Provider = 'Anthropic'; Region = 'northcentralus' } } else { @{} }
        $estimate = & (Join-Path $Kit 'scripts/Get-SpikeEstimate.ps1') @estimateArgs | Select-Object -Last 1 | ConvertFrom-Json
        Write-Host "`nAzure lines: $($estimate.manualAmount) ($($estimate.unpricedLines) unpriced)`n$($estimate.manualNote)"
        if ($estimate.unpricedLines -gt 0 -and -not $AllowUnpriced) {
            throw "$($estimate.unpricedLines) line(s) unpriced (warnings above). Nothing is deployed. Rerun with -AllowUnpriced to open the run anyway."
        }
        $answer = Read-Host "Open the spend run with these Azure lines plus the token estimate, run cap ${RunCap}? [y/N]"
        if ($answer -notmatch '^(y|yes)$') { throw 'Stopped at the estimate; nothing is deployed.' }
        $env:AZURE_SUBSCRIPTION_ID = $account.id
        $spendArgs = @{
            Sandbox = $Sandbox; Kind = 'other'; Purpose = "avd-swarm S1a spike ($Provider)"; Hours = 6
            ManualAmount = $estimate.manualAmount; ManualNote = $estimate.manualNote
            RunCap = $RunCap; MonthlyCap = $MonthlyCap; Funding = $Funding; LedgerPath = $Ledger
        }
        if ($AllowUnpriced) { $spendArgs.AllowUnpriced = $true }
        & (Join-Path $TenantSwarm 'scripts/spend/Start-SpendRun.ps1') @spendArgs
        $run = Get-ChildItem (Join-Path $Ledger $Sandbox) -Recurse -Filter *.json | Sort-Object LastWriteTime |
            Select-Object -Last 1 | Get-Content -Raw | ConvertFrom-Json
        if ($run.status -ne 'open') { throw "The spend run was refused: $($run.runId) is $($run.status). Nothing is deployed." }
        $run.runId | Set-Content "$Spike/run-id"
        $Provider | Set-Content "$Spike/provider"
        Write-Host "Run $($run.runId) is open."
    }
    $RunId = Get-Content "$Spike/run-id"

    # ---------- Deploy, Q1 ----------
    Invoke-Step 'deploy' {
        if ($Provider -eq 'Anthropic') {
            Write-Host 'Deploying the resource group, the MCP server, the agents app and the host pool, and creating the Agent ID blueprint; 5 to 15 minutes.'
            & (Join-Path $Kit 'scripts/Start-Spike.ps1') -RunId $RunId -Provider Anthropic -Location northcentralus |
                Select-Object -Last 1 | Set-Content "$Spike/state.json"
        }
        else {
            Write-Host 'Deploying the resource group, Foundry, the MCP server and the host pool; this takes 5 to 10 minutes.'
            & (Join-Path $Kit 'scripts/Start-Spike.ps1') -RunId $RunId | Select-Object -Last 1 | Set-Content "$Spike/state.json"
        }
    }
    $s = Get-Content "$Spike/state.json" | ConvertFrom-Json
    if ($Provider -eq 'Anthropic') {
        Save-SpikeResult 'Q1' 'not tested: no Claude quota in Foundry; fallback to Anthropic''s API'

        # ---------- Anthropic workload identity federation: Claude without an API key ----------
        Invoke-Step 'anthropic-federation' {
            Write-Host @"
Claude Console (platform.claude.com) > Settings > Workload identity > Connect workload > Microsoft Entra.
Use a workspace just for the spike, with a spend limit (about `$20). Enter:
  Token issuer     v2.0 (login.microsoftonline.com): https://login.microsoftonline.com/$($s.tenantId)/v2.0
  Service account  a new one, for example avdswarm-spike
  Rule audience    $($s.claudeAudienceAppId)
  Rule claims      oid = $($s.agentsIdentityPrincipalId)   tid = $($s.tenantId)
Then Issuers tab: edit the issuer and set the maximum JWT lifetime to 86400 seconds (Azure managed identity
tokens live up to 24 hours; the wizard's 7500 rejects them).
None of these IDs is a secret; they're saved in $Spike/anthropic.json, not in any repo.
"@
            $federation = [ordered]@{
                ANTHROPIC_ORGANIZATION_ID    = Read-Host 'Organization ID (Console > Settings > Organization, a GUID)'
                ANTHROPIC_FEDERATION_RULE_ID = Read-Host 'Federation rule ID'
                ANTHROPIC_SERVICE_ACCOUNT_ID = Read-Host 'Service account ID (svac_...)'
                ANTHROPIC_WORKSPACE_ID       = Read-Host 'Workspace ID (wrkspc_..., or Enter if the rule has one workspace)'
            }
            if ($federation.ANTHROPIC_ORGANIZATION_ID -notmatch '^[0-9a-fA-F-]{36}$' -or -not $federation.ANTHROPIC_FEDERATION_RULE_ID) {
                throw 'The organization ID is a GUID and the rule ID is required. Run the same command again to re-enter them.'
            }
            $federation | ConvertTo-Json | Set-Content "$Spike/anthropic.json"
            Write-Host 'Setting them on the agents app (a new revision; it reinstalls, a minute or two).'
            $null = Invoke-NativeAz (@('containerapp', 'update', '-g', $s.resourceGroup, '-n', $s.agentsAppName, '--set-env-vars') +
                @($federation.GetEnumerator() | Where-Object Value | ForEach-Object { "$($_.Key)=$($_.Value)" }))
            for ($i = 0; $i -lt 60; $i++) {
                try { if ((Invoke-RestMethod -Uri "$($s.agentsUrl)/healthz" -TimeoutSec 10).claudeConfigured) { break } }
                catch { Write-Verbose "not up yet: $($_.Exception.Message)" }
                Start-Sleep -Seconds 10
            }
            $operatorToken = az account get-access-token --scope "api://$($s.mcpAppId)/Mcp.Tools.ReadWrite" --query accessToken -o tsv
            for ($try = 1; ; $try++) {
                Write-Host 'Checking the token exchange with Anthropic (no model call, no tokens spent)'
                $check = Invoke-WebRequest -Uri "$($s.agentsUrl)/claude-check" -Method Post -SkipHttpErrorCheck -Headers @{ Authorization = "Bearer $operatorToken" }
                Write-Host "  $($check.StatusCode): $($check.Content)"
                if ($check.StatusCode -eq 200) { break }
                if ($try -ge 3) { throw 'The Anthropic token exchange still fails (above). Console > Workload identity > History shows the deny reason.' }
                Write-Host 'A 401 is usually the rule not matching the claims above, or the issuer''s maximum JWT lifetime still under 86400. Console > Workload identity > History shows the reason.'
                $null = Read-Host 'Fix it in the Console, then press Enter to check again'
            }
            Save-SpikeResult 'Federation' 'confirmed: the managed identity''s Entra token exchanged for an Anthropic access token, no API key'
        }
    }

    if ($Provider -eq 'Foundry') {
        Invoke-Step 'q1-models' {
            $deploymentArgs = @($s.compareDeployments | ForEach-Object { '--deployment', $_ })
            $null = Invoke-Native 'Q1: call each Claude deployment' (@((Join-Path $Kit 'tools/test_models.py'), '--resource', $s.foundryName) + $deploymentArgs)
            Save-SpikeResult 'Q1' 'confirmed'
        }
    }

    # ---------- Q3: the MCP server's tools ----------
    Invoke-Step 'q3-tools' {
        Write-Host 'The first call can take a minute while the Container App scales up from zero.'
        $lines = Invoke-Native 'Q3: list the MCP tools' @((Join-Path $Kit 'tools/list_mcp_tools.py'), '--url', $s.mcpUrl, '--app-id', $s.mcpAppId)
        $lines | Set-Content "$Spike/mcp-tools.txt"
        $tools = (Get-LastJson $lines 'list-mcp-tools').tools
        $picked = if ($AllowedTools) { $AllowedTools }
        else {
            @($tools | Where-Object { $_.readOnly -eq $true -and $_.name -match 'host.?pool|session.?host' } | ForEach-Object name) -join ','
        }
        if (-not $picked) { throw 'No read-only host pool or session host tool found in the list above. Rerun with -AllowedTools <names>.' }
        $picked | Set-Content "$Spike/allowed-tools"
        Write-Host "Hosts agent allowlist: $picked"
        Save-SpikeResult 'Q3' "confirmed: $($tools.Count) tools, all read-only"
    }
    $allowed = Get-Content "$Spike/allowed-tools"

    if ($Provider -eq 'Anthropic') {
        $evalArgs = @('--agent-url', $s.agentsUrl, '--app-id', $s.mcpAppId, '--identity', $s.agentIdentityAppId)

        Invoke-Step 'q2-identity' {
            Write-Host "Setting the Hosts allowlist on the agents app (a new revision; it reinstalls, a minute or two)."
            $null = Invoke-NativeAz @('containerapp', 'update', '-g', $s.resourceGroup, '-n', $s.agentsAppName,
                '--set-env-vars', "HOSTS_ALLOWED_TOOLS=$allowed")
            Wait-SpikeAgentsHealthy $s.agentsUrl $allowed
            & (Join-Path $Kit 'scripts/Connect-SpikeAgent.ps1') -AgentPrincipalId $s.agentIdentityId -McpAppSpId $s.mcpAppSpId -McpAppRoleId $s.mcpAppRoleId
            Write-Host 'Waiting a minute for the app role to reach the token service...'
            Start-Sleep -Seconds 60
            $null = Invoke-Native 'Q2: first question, end to end, with the agent identity' (@((Join-Path $Kit 'tools/run_evals.py')) + $evalArgs +
                @('--model', $s.hostsDeployment, '--out', "$Spike/evals.jsonl", '--only', 'q01'))
            Save-SpikeResult 'Q2' 'fallback: answered q01 with an Agent ID created without Foundry; check the answer and mcpTokenClaims in evals.jsonl'
        }

        Invoke-Step 'q4-probes' {
            $null = Invoke-Native 'Q4: allowlist probes' (@((Join-Path $Kit 'tools/run_evals.py')) + $evalArgs +
                @('--model', $s.hostsDeployment, '--out', "$Spike/evals.jsonl", '--only', 'p01,p02'))
        }

        # Q6: the ten questions with the Hosts agent on each model; one service, the model chosen per request.
        foreach ($hostsModel in @($s.compareDeployments)) {
            Invoke-Step "q6-$hostsModel" {
                $lines = Invoke-Native "Q6: the ten eval questions on $hostsModel" (@((Join-Path $Kit 'tools/run_evals.py')) + $evalArgs +
                    @('--model', $hostsModel, '--out', "$Spike/evals-$hostsModel.jsonl", '--only', 'q01,q02,q03,q04,q05,q06,q07,q08,q09,q10')) -OkExitCodes 0, 2
                if ($LASTEXITCODE -eq 2) { throw 'STOP: past the 2M input token stop rule.' }
                Save-SpikeResult "Q6-$hostsModel" (Get-LastJson $lines 'run-evals')
            }
        }
        $a = [pscustomobject]@{ identityPrincipalId = $s.agentIdentityId }
    }
    else {
        # ---------- Q2: the hosted agent ----------
        Invoke-Step 'q2-agent' {
            Write-Host 'Building and starting the hosted agent; the remote build takes several minutes.'
            $lines = Invoke-Native 'Q2: deploy the hosted agent' @((Join-Path $Kit 'tools/deploy_agent.py'),
                '--project-endpoint', $s.projectEndpoint, '--resource', $s.foundryName, '--coordinator', $s.coordinatorDeployment,
                '--hosts', $s.hostsDeployment, '--mcp-url', $s.mcpUrl, '--mcp-app-id', $s.mcpAppId, '--allowed-tools', $allowed,
                '--runtime', $Runtime)
            Get-LastJson $lines 'deploy-agent' | ConvertTo-Json -Compress | Set-Content "$Spike/agent.json"
        }
        $a = Get-Content "$Spike/agent.json" | ConvertFrom-Json

        Invoke-Step 'q2-connect' {
            & (Join-Path $Kit 'scripts/Connect-SpikeAgent.ps1') -AgentPrincipalId $a.identityPrincipalId -McpAppSpId $s.mcpAppSpId -McpAppRoleId $s.mcpAppRoleId
            $null = Invoke-Native 'Q2: first question, end to end' @((Join-Path $Kit 'tools/run_evals.py'),
                '--project-endpoint', $s.projectEndpoint, '--out', "$Spike/evals.jsonl", '--only', 'q01')
            Save-SpikeResult 'Q2' 'answered q01; check the answer above'
        }

        # ---------- Q4: allowlist probes ----------
        Invoke-Step 'q4-probes' {
            $null = Invoke-Native 'Q4: allowlist probes' @((Join-Path $Kit 'tools/run_evals.py'),
                '--project-endpoint', $s.projectEndpoint, '--out', "$Spike/evals.jsonl", '--only', 'p01,p02')
        }

        # ---------- Q6: eval questions and token counts ----------
        Invoke-Step 'q6-evals' {
            $lines = Invoke-Native 'Q6: the ten eval questions' @((Join-Path $Kit 'tools/run_evals.py'),
                '--project-endpoint', $s.projectEndpoint, '--out', "$Spike/evals.jsonl",
                '--only', 'q01,q02,q03,q04,q05,q06,q07,q08,q09,q10') -OkExitCodes 0, 2
            if ($LASTEXITCODE -eq 2) { throw 'STOP: past the 2M input token stop rule.' }
            Save-SpikeResult "Q6-$($s.hostsDeployment)" (Get-LastJson $lines 'run-evals')
        }

        # The same ten questions with the Hosts agent on each other model: one agent per model, so they compare side by side.
        foreach ($hostsDeployment in @($s.compareDeployments | Where-Object { $_ -ne $s.hostsDeployment })) {
            $agentName = "avdswarm-spike-$($hostsDeployment -replace '^claude-', '' -replace '[^a-z0-9]+', '-')"
            Invoke-Step "q6-$hostsDeployment" {
                Write-Host "Building a second agent, $agentName, with the Hosts agent on $hostsDeployment; several minutes."
                $lines = Invoke-Native "Q6: deploy $agentName" @((Join-Path $Kit 'tools/deploy_agent.py'),
                    '--project-endpoint', $s.projectEndpoint, '--resource', $s.foundryName, '--coordinator', $s.coordinatorDeployment,
                    '--hosts', $hostsDeployment, '--mcp-url', $s.mcpUrl, '--mcp-app-id', $s.mcpAppId, '--allowed-tools', $allowed,
                    '--runtime', $Runtime, '--agent-name', $agentName)
                $other = Get-LastJson $lines 'deploy-agent'
                $other | ConvertTo-Json -Compress | Set-Content "$Spike/agent-$hostsDeployment.json"
                & (Join-Path $Kit 'scripts/Connect-SpikeAgent.ps1') -AgentPrincipalId $other.identityPrincipalId -McpAppSpId $s.mcpAppSpId -McpAppRoleId $s.mcpAppRoleId
                $lines = Invoke-Native "Q6: the ten eval questions on $hostsDeployment" @((Join-Path $Kit 'tools/run_evals.py'),
                    '--project-endpoint', $s.projectEndpoint, '--agent-name', $agentName, '--out', "$Spike/evals-$hostsDeployment.jsonl",
                    '--only', 'q01,q02,q03,q04,q05,q06,q07,q08,q09,q10') -OkExitCodes 0, 2
                if ($LASTEXITCODE -eq 2) { throw 'STOP: past the 2M input token stop rule.' }
                Save-SpikeResult "Q6-$hostsDeployment" (Get-LastJson $lines 'run-evals')
            }
        }

        Invoke-Step 'q6-metrics' {
            Write-Host 'Waiting 5 minutes for Azure Monitor to catch up with the token metrics...'
            Start-Sleep -Seconds 300
            & (Join-Path $Kit 'scripts/Get-SpikeToken.ps1') -FoundryName $s.foundryName | Select-Object -Last 1 | Set-Content "$Spike/tokens.json"
        }
    }

    # ---------- Q6 summary: the same ten questions per Hosts model ----------
    Write-Phase 'Q6: per model (caching on, Hosts effort medium; read the answers before trusting the checks)'
    (Get-SpikeProgress).results.GetEnumerator() | Where-Object { $_.Key -like 'Q6-*' } | ForEach-Object {
        $r = $_.Value
        [pscustomobject]@{
            hosts          = $_.Key -replace '^Q6-', ''
            checksPassed   = "$($r.expectMet)/$($r.questions)"
            inputPerQ      = [math]::Round($r.inputTokens / [math]::Max(1, $r.questions))
            outputPerQ     = [math]::Round($r.outputTokens / [math]::Max(1, $r.questions))
            costUsd        = $r.costUsd
        }
    } | Format-Table -AutoSize | Out-String | Write-Host

    # ---------- Q5: portal steps while the agent still exists ----------
    if (-not $NoPause -and $Provider -eq 'Anthropic') {
        Write-Phase 'Q5: identity evidence (portal steps, optional)'
        Write-Host @"
The agent identity's object ID is $($s.agentIdentityId); its blueprint is $($s.blueprintAppId) (both in $Spike/state.json).
1. evals.jsonl: each answer's evidence.mcpTokenClaims shows the token the Hosts agent called the MCP server with.
2. Entra admin center > Agent ID > Agent identities: open avdswarm-spike-hosts-$RunId, then its sign-in logs.
3. Optional: a report-only Conditional Access policy targeting it, then ask one more question and check the Report-only tab:
   & `$Py $Kit/tools/run_evals.py --agent-url $($s.agentsUrl) --app-id $($s.mcpAppId) --identity $($s.agentIdentityAppId) --model $($s.hostsDeployment) --out $Spike/evals.jsonl --only q01
   Delete the policy after.
"@
        $null = Read-Host 'Press Enter to tear down (or Ctrl+C to keep it up; run -TeardownOnly later)'
    }
    elseif (-not $NoPause) {
        Write-Phase 'Q5: identity evidence (portal steps, optional)'
        Write-Host @"
The agent identity's object ID is $($a.identityPrincipalId) (also in $Spike/agent.json).
1. Foundry portal > project 'spike' > Agents > avdswarm-spike > Logs: find the 'identity claims:' line.
2. Entra admin center > Agent ID (or Enterprise apps, filtered to agent identities): open that identity's sign-in logs.
3. Optional: a report-only Conditional Access policy targeting it, then ask one question in the Foundry playground
   and check the Report-only tab. Delete the policy after.
"@
        $null = Read-Host 'Press Enter to tear down (or Ctrl+C to keep it up; run -TeardownOnly later)'
    }

    Invoke-Teardown
    Write-Phase 'Done'
    Write-Host "Evidence is in $Spike (state.json, mcp-tools.txt, agent*.json, evals*.jsonl, tokens.json, progress-*.json, transcript-*.txt)."
    Write-Host 'Paste the transcript or those files in the thread and the results go into docs/spikes/s1a.md.'
}
catch {
    Write-Host "`nFAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ($_.ScriptStackTrace)
    if ((Test-Path "$Spike/run-id") -and -not $KeepOnFailure -and -not $TeardownOnly) {
        Write-Host 'Tearing down so nothing keeps billing. Pass -KeepOnFailure next time to leave it up.'
        try { Invoke-Teardown } catch { Write-Host "Teardown failed too: $($_.Exception.Message). Run -TeardownOnly." -ForegroundColor Red }
    }
    elseif (Test-Path "$Spike/run-id") {
        Write-Host 'Resources are still up. Fix and rerun to resume, or run -TeardownOnly.'
    }
    exit 1
}
finally {
    Stop-Transcript | Out-Null
}
