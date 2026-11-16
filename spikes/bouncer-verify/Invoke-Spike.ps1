# avd-swarm bouncer verification spike: tests docs/bouncer.md section 6 assumptions in a disposable sandbox.
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
<#
.SYNOPSIS
Verifies the six unverified assumptions in docs/bouncer.md section 6 using disposable Azure/Entra
resources and spend_guard.py. Run in Azure Cloud Shell (PowerShell) against a lab tenant only.
#>
[CmdletBinding()]
param(
    [string] $Sandbox = 'lab',
    [string] $Region = 'eastus2',
    [string] $ResourceGroup = 'rg-avdswarm-bouncer-verify',
    [double] $RunCap = 5.0,
    [double] $MonthlyCap,
    [Nullable[double]] $MonthToDate,
    [string] $LedgerPath = (Join-Path $HOME 'clouddrive/avdswarm-spend'),
    [switch] $AllowUnpriced,
    # Existing lab AVD host pool + session host for Assumption 5 & 4 (optional; skips live host write if omitted).
    [string] $HostPoolResourceId = '',
    [string] $SessionHostName = '',
    # Lab test user object ID (never a break-glass account) for AU quarantine, revokeSignInSessions, and CA tests.
    [string] $TestUserObjectId = '',
    [string] $OutsideAuUserObjectId = '',
    # Create a temporary Conditional Access policy targeting Azure Virtual Desktop & Windows Cloud Login for Assumption 4.
    [switch] $TestCaPropagation,
    [switch] $KeepOnFailure,
    [switch] $TeardownOnly
)
$ErrorActionPreference = 'Stop'

$SpikeRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $SpikeRoot '../..')).Path
$StateDir = Join-Path $HOME 'clouddrive/avdswarm-bouncer-verify'
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

if ($TeardownOnly) {
    $teardown = & (Join-Path $SpikeRoot 'Remove-Spike.ps1') -ResourceGroup $ResourceGroup -StateDir $StateDir
    Write-Host $teardown
    $runIdPath = Join-Path $StateDir 'run-id'
    if (Test-Path $runIdPath) {
        $rid = (Get-Content $runIdPath -Raw).Trim()
        python3 (Join-Path $RepoRoot 'spend/spend_guard.py') --ledger $LedgerPath --sandbox $Sandbox close --run-id $rid --teardown $teardown
    }
    return
}

if (-not $PSBoundParameters.ContainsKey('MonthlyCap')) {
    $MonthlyCap = [double](Read-Host 'Monthly sandbox cap (in subscription billing currency)')
}

# 1. Price & open spend guard run
$estimateJson = & (Join-Path $SpikeRoot 'Get-SpikeEstimate.ps1') -Region $Region | Select-Object -Last 1
$estimate = $estimateJson | ConvertFrom-Json
Write-Host "Estimate: `$$($estimate.manualAmount) ($($estimate.manualNote))"

$openArgs = @(
    (Join-Path $RepoRoot 'spend/spend_guard.py'),
    '--ledger', $LedgerPath,
    '--sandbox', $Sandbox,
    'open',
    '--purpose', 'avd-swarm bouncer section 6 verification',
    '--resource-group', $ResourceGroup,
    '--hours', '2',
    '--estimate', "$($estimate.manualAmount)",
    '--estimate-note', "$($estimate.manualNote)",
    '--unpriced-lines', "$($estimate.unpricedLines)",
    '--run-cap', "$RunCap",
    '--monthly-cap', "$MonthlyCap"
)
if ($null -ne $MonthToDate) { $openArgs += @('--month-to-date', "$MonthToDate") }
if ($AllowUnpriced) { $openArgs += '--allow-unpriced' }

$openOut = python3 @openArgs
$openOut | Write-Host
$runObj = ($openOut | Select-Object -Last 1) | ConvertFrom-Json
if ($runObj.status -ne 'open') {
    throw "Spend guard refused run $($runObj.runId): $($runObj.reasons -join '; ')"
}
$runId = $runObj.runId
$runId | Set-Content (Join-Path $StateDir 'run-id')

function Invoke-GraphJson {
    param([string] $Method, [string] $Path, [hashtable] $Body = $null)
    $uri = "https://graph.microsoft.com/v1.0/$($Path.TrimStart('/'))"
    if ($null -ne $Body) {
        $tmp = New-TemporaryFile
        ($Body | ConvertTo-Json -Depth 10 -Compress) | Set-Content -Path $tmp -Encoding utf8
        try {
            $raw = az rest --method $Method --uri $uri --headers 'Content-Type=application/json' --body "@$($tmp.FullName)" --only-show-errors
            if ($LASTEXITCODE) { throw "Graph $Method $Path failed (exit $LASTEXITCODE)." }
        }
        finally {
            Remove-Item -Force $tmp -ErrorAction SilentlyContinue
        }
    }
    else {
        $raw = az rest --method $Method --uri $uri --only-show-errors
        if ($LASTEXITCODE) { throw "Graph $Method $Path failed (exit $LASTEXITCODE)." }
    }
    if ($raw) { return ($raw | ConvertFrom-Json) }
    return $null
}

$results = [ordered]@{
    runId     = $runId
    timestamp = (Get-Date).ToUniversalTime().ToString('o')
}
$failed = $false

try {
    # 2. Deploy disposable Azure resources
    az group create --name $ResourceGroup --location $Region --tags "avd-swarm-run=$runId" --only-show-errors | Out-Null
    if ($LASTEXITCODE) { throw "az group create failed (exit $LASTEXITCODE)." }
    $deployStart = Get-Date
    $depRaw = az deployment group create `
        --resource-group $ResourceGroup `
        --template-file (Join-Path $SpikeRoot 'infra/main.bicep') `
        --parameters runId=$runId `
        --query 'properties.outputs' -o json --only-show-errors
    if ($LASTEXITCODE) { throw "az deployment group create failed (exit $LASTEXITCODE)." }
    $dep = $depRaw | ConvertFrom-Json

    $qPrincipal = $dep.quarantinePrincipalId.value
    $qClient = $dep.quarantineClientId.value
    $qId = $dep.quarantineIdentityId.value
    $rPrincipal = $dep.revokePrincipalId.value
    $rClient = $dep.revokeClientId.value
    $rId = $dep.revokeIdentityId.value
    $dClient = $dep.drainClientId.value
    $dId = $dep.drainIdentityId.value

    # 3. Provision disposable Entra objects (restricted management AU, groups, custom role, assignments)
    $suffix = ($runId -split '-')[-1]
    $entraState = [ordered]@{
        auId              = $null
        inAuGroupId       = $null
        outsideAuGroupId  = $null
        customRoleId      = $null
        roleAssignmentIds = @()
        caPolicyId        = $null
        createdTestUserId = $null
    }
    $entraStatePath = Join-Path $StateDir 'entra-state.json'

    $au = Invoke-GraphJson -Method POST -Path 'directory/administrativeUnits' -Body @{
        displayName                   = "au-bouncer-verify-$suffix"
        description                   = 'Disposable restricted management AU for avd-swarm bouncer verification'
        isMemberManagementRestricted  = $true
    }
    $entraState.auId = $au.id
    $entraState | ConvertTo-Json -Depth 5 | Set-Content $entraStatePath

    $inGroup = Invoke-GraphJson -Method POST -Path 'groups' -Body @{
        displayName        = "grp-bouncer-verify-in-au-$suffix"
        mailEnabled        = $false
        mailNickname       = "bouncerverif$suffix"
        securityEnabled    = $true
        isAssignableToRole = $false
    }
    $entraState.inAuGroupId = $inGroup.id

    $outGroup = Invoke-GraphJson -Method POST -Path 'groups' -Body @{
        displayName        = "grp-bouncer-verify-out-au-$suffix"
        mailEnabled        = $false
        mailNickname       = "bouncerout$suffix"
        securityEnabled    = $true
        isAssignableToRole = $false
    }
    $entraState.outsideAuGroupId = $outGroup.id
    $entraState | ConvertTo-Json -Depth 5 | Set-Content $entraStatePath

    # Add in-AU group to the restricted management AU
    Invoke-GraphJson -Method POST -Path "directory/administrativeUnits/$($au.id)/members/`$ref" -Body @{
        '@odata.id' = "https://graph.microsoft.com/v1.0/directoryObjects/$($inGroup.id)"
    } | Out-Null

    if (-not $TestUserObjectId) {
        $me = Invoke-GraphJson -Method GET -Path 'me?$select=id,userPrincipalName'
        Write-Warning "No -TestUserObjectId provided; using signed-in user $($me.id) ONLY for AU group add/remove test (skipping revokeSignInSessions if it is your active session)."
        $TestUserObjectId = $me.id
    }
    else {
        Invoke-GraphJson -Method POST -Path "directory/administrativeUnits/$($au.id)/members/`$ref" -Body @{
            '@odata.id' = "https://graph.microsoft.com/v1.0/directoryObjects/$TestUserObjectId"
        } | Out-Null
    }

    # Custom Entra role with ONLY microsoft.directory/groups/members/update
    $customRole = Invoke-GraphJson -Method POST -Path 'roleManagement/directory/roleDefinitions' -Body @{
        displayName     = "avd-bouncer-verify-group-members-$suffix"
        description     = 'Disposable custom role for bouncer section 6 verification'
        isEnabled       = $true
        rolePermissions = @(
            @{
                allowedResourceActions = @('microsoft.directory/groups/members/update')
            }
        )
    }
    $entraState.customRoleId = $customRole.id
    $entraState | ConvertTo-Json -Depth 5 | Set-Content $entraStatePath

    # Lookup Directory Readers & User Administrator dynamically (no hardcoded GUIDs)
    $dirReaders = (Invoke-GraphJson -Method GET -Path "roleManagement/directory/roleDefinitions?`$filter=displayName eq 'Directory Readers'").value[0]
    $userAdmin = (Invoke-GraphJson -Method GET -Path "roleManagement/directory/roleDefinitions?`$filter=displayName eq 'User Administrator'").value[0]

    $ra1 = Invoke-GraphJson -Method POST -Path 'roleManagement/directory/roleAssignments' -Body @{
        principalId      = $qPrincipal
        roleDefinitionId = $dirReaders.id
        directoryScopeId = '/'
    }
    $ra2 = Invoke-GraphJson -Method POST -Path 'roleManagement/directory/roleAssignments' -Body @{
        principalId      = $qPrincipal
        roleDefinitionId = $customRole.id
        directoryScopeId = "/administrativeUnits/$($au.id)"
    }
    $ra3 = Invoke-GraphJson -Method POST -Path 'roleManagement/directory/roleAssignments' -Body @{
        principalId      = $rPrincipal
        roleDefinitionId = $userAdmin.id
        directoryScopeId = "/administrativeUnits/$($au.id)"
    }
    $entraState.roleAssignmentIds = @($ra1.id, $ra2.id, $ra3.id)
    $entraState | ConvertTo-Json -Depth 5 | Set-Content $entraStatePath

    Write-Host 'Waiting 30s for Entra role assignment propagation...'
    Start-Sleep -Seconds 30

    # 4. Run ACI with the 3 UAMIs assigned to test Assumptions 1, 2, 3, and 5 via real IMDS tokens
    $pyB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes((Get-Content (Join-Path $SpikeRoot 'scripts/verify_imds_tokens.py') -Raw)))
    $aciName = "aci-bouncer-verify-$suffix"
    $cmd = "echo $pyB64 | base64 -d > /tmp/verify.py && python3 /tmp/verify.py"

    az container create `
        --resource-group $ResourceGroup `
        --name $aciName `
        --image 'mcr.microsoft.com/azure-cli:latest' `
        --os-type Linux `
        --cpu 1 `
        --memory 1 `
        --restart-policy Never `
        --assign-identity $qId $rId $dId `
        --environment-variables `
            QUARANTINE_CLIENT_ID=$qClient `
            REVOKE_CLIENT_ID=$rClient `
            DRAIN_CLIENT_ID=$dClient `
            IN_AU_GROUP_ID=$($inGroup.id) `
            OUTSIDE_AU_GROUP_ID=$($outGroup.id) `
            TEST_USER_ID=$TestUserObjectId `
            OUTSIDE_AU_USER_ID=$OutsideAuUserObjectId `
            HOST_POOL_RESOURCE_ID=$HostPoolResourceId `
            SESSION_HOST_NAME=$SessionHostName `
        --command-line "/bin/sh -c `"$cmd`"" `
        --only-show-errors | Out-Null

    $logs = az container logs --resource-group $ResourceGroup --name $aciName --only-show-errors
    az container delete --resource-group $ResourceGroup --name $aciName --yes --only-show-errors | Out-Null

    $markerLine = ($logs -split "`n" | Where-Object { $_ -match '^BOUNCER_VERIFY_RESULTS=' } | Select-Object -Last 1)
    if ($markerLine) {
        $results.imdsTests = ($markerLine -replace '^BOUNCER_VERIFY_RESULTS=', '') | ConvertFrom-Json
    }
    else {
        $results.imdsTests = @{ error = 'Missing BOUNCER_VERIFY_RESULTS marker'; rawLogs = ($logs | Out-String) }
    }

    # 5. Assumption 6: Poll alertsmanagementresources for the synthetic 1-min alert rule
    Write-Host 'Polling Resource Graph (alertsmanagementresources) for synthetic alert (up to 10 min)...'
    $alertFound = $null
    $deadline = (Get-Date).AddMinutes(10)
    while ((Get-Date) -lt $deadline) {
        $q = "alertsmanagementresources | where resourceGroup =~ '$ResourceGroup' | project id, name, type, properties"
        $graphResp = az graph query -q $q --only-show-errors -o json | ConvertFrom-Json
        if ($graphResp.count -gt 0) {
            $alertFound = $graphResp.data[0]
            break
        }
        Start-Sleep -Seconds 30
    }
    if ($alertFound) {
        $lagSec = [math]::Round(((Get-Date) - $deployStart).TotalSeconds, 1)
        $results.assumption_6_alertsmanagementresources = [ordered]@{
            passed         = $true
            observedLagSec = $lagSec
            essentialKeys  = @($alertFound.properties.essentials.PSObject.Properties.Name)
            sampleEssentials = $alertFound.properties.essentials
        }
    }
    else {
        $results.assumption_6_alertsmanagementresources = [ordered]@{
            passed = $false
            reason = 'No alert appeared in alertsmanagementresources within 10 minutes.'
        }
    }

    # 6. Assumption 4: Optional CA propagation latency check
    if ($TestCaPropagation -and $TestUserObjectId) {
        Write-Host 'Assumption 4: Add test user to quarantine group and measure time to refused AVD connection.'
        $t0 = Get-Date
        Invoke-GraphJson -Method POST -Path "groups/$($inGroup.id)/members/`$ref" -Body @{
            '@odata.id' = "https://graph.microsoft.com/v1.0/directoryObjects/$TestUserObjectId"
        } | Out-Null
        Read-Host 'Attempt AVD sign-in now; press Enter once connection is refused (or Ctrl+C to abort)'
        $tRefused = Get-Date
        $results.assumption_4_ca_propagation = [ordered]@{
            groupAddUtc       = $t0.ToUniversalTime().ToString('o')
            refusedUtc        = $tRefused.ToUniversalTime().ToString('o')
            elapsedSeconds    = [math]::Round(($tRefused - $t0).TotalSeconds, 1)
        }
    }
    else {
        $results.assumption_4_ca_propagation = @{ skipped = $true; reason = 'Pass -TestCaPropagation and -TestUserObjectId to measure interactively.' }
    }

    $resultsPath = Join-Path $StateDir 'results.json'
    $results | ConvertTo-Json -Depth 10 | Set-Content $resultsPath
    Write-Host "Saved verification results to $resultsPath"
    $results | ConvertTo-Json -Depth 8 | Write-Host
}
catch {
    $failed = $true
    Write-Error $_
}
finally {
    if (-not ($failed -and $KeepOnFailure)) {
        $teardown = & (Join-Path $SpikeRoot 'Remove-Spike.ps1') -ResourceGroup $ResourceGroup -StateDir $StateDir
        Write-Host $teardown
        python3 (Join-Path $RepoRoot 'spend/spend_guard.py') --ledger $LedgerPath --sandbox $Sandbox close --run-id $runId --teardown $teardown
    }
    else {
        Write-Warning "Keeping resources (-KeepOnFailure). Run -TeardownOnly before the 2-hour spend window expires."
    }
}
