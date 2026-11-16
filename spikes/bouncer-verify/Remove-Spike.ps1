# avd-swarm bouncer verification spike: idempotent teardown of Entra + Azure resources.
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
<#
.SYNOPSIS
Deletes the disposable Entra ID objects (from entra-state.json) and the disposable Azure resource group,
then prints a JSON leftovers summary for spend_guard.py close.
#>
[CmdletBinding()]
param(
    [string] $ResourceGroup = 'rg-avdswarm-bouncer-verify',
    [string] $StateDir = (Join-Path $HOME 'clouddrive/avdswarm-bouncer-verify')
)
$ErrorActionPreference = 'Stop'

$leftovers = [System.Collections.Generic.List[string]]::new()
$entraStatePath = Join-Path $StateDir 'entra-state.json'

function Invoke-GraphDelete {
    param([string] $Path)
    $uri = "https://graph.microsoft.com/v1.0/$($Path.TrimStart('/'))"
    az rest --method DELETE --uri $uri --only-show-errors 2>&1 | Out-Null
}

if (Test-Path $entraStatePath) {
    $es = Get-Content $entraStatePath -Raw | ConvertFrom-Json
    if ($es.caPolicyId) {
        try { Invoke-GraphDelete "identity/conditionalAccess/policies/$($es.caPolicyId)" } catch { $leftovers.Add("caPolicy:$($es.caPolicyId)") }
    }
    foreach ($raId in @($es.roleAssignmentIds)) {
        if ($raId) {
            try { Invoke-GraphDelete "roleManagement/directory/roleAssignments/$raId" } catch { $leftovers.Add("roleAssignment:$raId") }
        }
    }
    if ($es.customRoleId) {
        try { Invoke-GraphDelete "roleManagement/directory/roleDefinitions/$($es.customRoleId)" } catch { $leftovers.Add("customRole:$($es.customRoleId)") }
    }
    foreach ($gid in @($es.inAuGroupId, $es.outsideAuGroupId)) {
        if ($gid) {
            try { Invoke-GraphDelete "groups/$gid" } catch { $leftovers.Add("group:$gid") }
        }
    }
    if ($es.auId) {
        try { Invoke-GraphDelete "directory/administrativeUnits/$($es.auId)" } catch { $leftovers.Add("au:$($es.auId)") }
    }
    if ($es.createdTestUserId) {
        try { Invoke-GraphDelete "users/$($es.createdTestUserId)" } catch { $leftovers.Add("user:$($es.createdTestUserId)") }
    }
}

$rgExists = az group exists --name $ResourceGroup -o tsv 2>$null
if ($rgExists -eq 'true') {
    az group delete --name $ResourceGroup --yes --only-show-errors | Out-Null
    $stillThere = az group exists --name $ResourceGroup -o tsv 2>$null
    if ($stillThere -eq 'true') {
        $leftovers.Add("resourceGroup:$ResourceGroup")
    }
}

[pscustomobject]@{
    resourceGroup = $ResourceGroup
    clean         = ($leftovers.Count -eq 0)
    leftovers     = @($leftovers)
} | ConvertTo-Json -Compress
