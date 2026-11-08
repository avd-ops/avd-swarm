# avd-swarm S1a spike helpers. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.

$script:SpikeResourceGroup = 'rg-avdswarm-spike'
$script:ExemptedAssignment = 'avdlz-allowed-locations'

function Write-SpikeStep {
    param([Parameter(Mandatory)][string] $Message)
    Write-Host "==> $Message"
}

function Invoke-SpikeAz {
    <#
    .SYNOPSIS
    Runs az with the given arguments and returns its JSON output as objects.
    On failure, throws with the step, the exit code and everything az wrote to stderr (lesson 0012).
    #>
    param(
        [Parameter(Mandatory)][string] $Step,
        [Parameter(Mandatory)][string[]] $Arguments,
        [switch] $AllowNotFound
    )
    $stderrFile = New-TemporaryFile
    try {
        $output = & az @Arguments --only-show-errors -o json 2> $stderrFile.FullName
        $exitCode = $LASTEXITCODE
        $stderr = Get-Content -Raw -LiteralPath $stderrFile.FullName
    }
    finally {
        Remove-Item -LiteralPath $stderrFile.FullName -ErrorAction SilentlyContinue
    }
    if ($exitCode -ne 0) {
        if ($AllowNotFound -and $stderr -match '(ResourceNotFound|NotFound|\b404\b|PolicyAssignmentNotFound|does not exist or one of its queried reference-property objects are not present)') { return $null }
        throw "[$Step] az exited $exitCode`n$stderr"
    }
    $text = ($output -join "`n").Trim()
    if ($text) { $text | ConvertFrom-Json -Depth 50 }
}

function Get-SpikeContext {
    <# The signed-in subscription and user, printed so you can confirm the target before any write. #>
    $account = Invoke-SpikeAz -Step 'account' -Arguments @('account', 'show')
    $user = Invoke-SpikeAz -Step 'signed-in-user' -Arguments @('ad', 'signed-in-user', 'show', '--query', '{id:id, upn:userPrincipalName}')
    [pscustomobject]@{
        SubscriptionId   = $account.id
        SubscriptionName = $account.name
        TenantId         = $account.tenantId
        UserId           = $user.id
        UserPrincipal    = $user.upn
    }
}

function Get-SpikeRoleId {
    <# A built-in role's ID by its current name, falling back to its former name (Foundry renamed its roles, IDs unchanged). #>
    param([Parameter(Mandatory)][string[]] $Names)
    foreach ($name in $Names) {
        $role = @(Invoke-SpikeAz -Step "role-$name" -Arguments @('role', 'definition', 'list', '--name', $name, '--query', '[].name'))
        if (@($role | Where-Object { $_ }).Count -eq 1) { return $role[0] }
    }
    throw "No single built-in role matched any of: $($Names -join ', ')"
}

function Invoke-SpikeGraph {
    <# A Microsoft Graph call through az rest, with a JSON body written to a file; full error on failure. #>
    param(
        [Parameter(Mandatory)][string] $Step,
        [Parameter(Mandatory)][ValidateSet('get', 'post', 'patch', 'delete')][string] $Method,
        [Parameter(Mandatory)][string] $Path,
        [object] $Body,
        [switch] $AllowNotFound
    )
    $arguments = @('rest', '--method', $Method, '--url', "https://graph.microsoft.com/v1.0$Path")
    $bodyFile = $null
    if ($null -ne $Body) {
        $bodyFile = New-TemporaryFile
        Set-Content -LiteralPath $bodyFile.FullName -Value ($Body | ConvertTo-Json -Depth 20 -Compress)
        $arguments += @('--headers', 'Content-Type=application/json', '--body', "@$($bodyFile.FullName)")
    }
    try { Invoke-SpikeAz -Step $Step -Arguments $arguments -AllowNotFound:$AllowNotFound }
    finally { if ($bodyFile) { Remove-Item -LiteralPath $bodyFile.FullName -ErrorAction SilentlyContinue } }
}

# Entra Agent ID needs delegated Graph scopes the Azure CLI's token doesn't carry, so the fallback's
# blueprint and agent identity calls go through Microsoft Graph PowerShell, consented once by the admin running it.
$script:AgentGraphScopes = @(
    'AgentIdentityBlueprint.Create', 'AgentIdentityBlueprint.AddRemoveCreds.All', 'AgentIdentityBlueprint.ReadWrite.All',
    'AgentIdentityBlueprintPrincipal.Create', 'AgentIdentityBlueprint.DeleteRestore.All', 'AgentIdentity.DeleteRestore.All',
    'AppRoleAssignment.ReadWrite.All', 'Application.Read.All'
)

function Connect-SpikeAgentGraph {
    <# Signs in to Graph PowerShell with the Agent ID scopes (device code in Cloud Shell) and proves it with a real request (lesson 0007). #>
    param([Parameter(Mandatory)][string] $TenantId)
    if (-not (Get-Module -ListAvailable Microsoft.Graph.Authentication)) {
        Write-SpikeStep 'Installing Microsoft.Graph.Authentication for your user (a minute)'
        Install-Module Microsoft.Graph.Authentication -Scope CurrentUser -Force
    }
    $context = Get-MgContext
    $missing = @($script:AgentGraphScopes | Where-Object { -not $context -or $_ -notin $context.Scopes })
    if ($missing) {
        Write-SpikeStep "Signing in to Microsoft Graph PowerShell for Entra Agent ID (scopes: $($script:AgentGraphScopes -join ', ')). Follow the device code prompt; as an admin, consent for the organization."
        # Connect-MgGraph writes the device-code prompt to the output stream; send it to Write-Host so | Select-Object -Last 1 in Invoke-Spike.ps1 does not hide it (lesson 0007).
        Connect-MgGraph -TenantId $TenantId -Scopes $script:AgentGraphScopes -UseDeviceCode -NoWelcome |
            ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
    }
    $me = Invoke-MgGraphRequest -Method GET -Uri 'https://graph.microsoft.com/v1.0/me?$select=userPrincipalName' -OutputType PSObject
    $granted = (Get-MgContext).Scopes
    Write-Host "Graph PowerShell signed in as $($me.userPrincipalName); scopes: $($granted -join ', ')"
    $stillMissing = @($script:AgentGraphScopes | Where-Object { $_ -notin $granted })
    if ($stillMissing) { Write-Warning "Not granted: $($stillMissing -join ', '). The step that needs one will fail and show Graph's error." }
}

function Invoke-SpikeAgentGraph {
    <# One Graph call through Graph PowerShell, with the OData-Version header Agent ID needs; full error on failure (lesson 0012). #>
    param(
        [Parameter(Mandatory)][string] $Step,
        [Parameter(Mandatory)][ValidateSet('GET', 'POST', 'PATCH', 'DELETE')][string] $Method,
        # Starts with /v1.0/ or /beta/
        [Parameter(Mandatory)][string] $Path,
        [object] $Body,
        [switch] $AllowNotFound
    )
    $request = @{ Method = $Method; Uri = "https://graph.microsoft.com$Path"; Headers = @{ 'OData-Version' = '4.0' }; OutputType = 'PSObject' }
    if ($null -ne $Body) { $request.Body = ($Body | ConvertTo-Json -Depth 20 -Compress); $request.ContentType = 'application/json' }
    try { Invoke-MgGraphRequest @request }
    catch {
        $detail = if ($_.ErrorDetails) { $_.ErrorDetails.Message } else { '' }
        if ($AllowNotFound -and "$($_.Exception.Message) $detail" -match '(NotFound|\b404\b|does not exist)') { return $null }
        throw "[$Step] Graph $Method $Path failed: $($_.Exception.Message)`n$detail"
    }
}

function Get-SpikeOperatorToken {
    <# Gets a delegated access token for the MCP server's Entra app, signing in with --use-device-code if Cloud Shell's MSI proxy rejects custom api:// audiences. #>
    param(
        [Parameter(Mandatory)][string] $AppId,
        [Parameter(Mandatory)][string] $TenantId,
        [string] $SubscriptionId
    )
    $scope = "api://$AppId/Mcp.Tools.ReadWrite"
    $stderrFile = New-TemporaryFile
    try {
        $token = (& az account get-access-token --scope $scope --query accessToken -o tsv 2> $stderrFile.FullName | Out-String).Trim()
        $exitCode = $LASTEXITCODE
        $stderr = Get-Content -Raw -LiteralPath $stderrFile.FullName
    }
    finally {
        Remove-Item -LiteralPath $stderrFile.FullName -ErrorAction SilentlyContinue
    }
    if ($exitCode -ne 0 -or -not $token) {
        if ($stderr -match 'not a supported MSI token audience') {
            Write-SpikeStep 'Cloud Shell MSI proxy rejects custom api:// scopes; running az logout + az login --use-device-code so MCP and agent API tokens work'
            $null = & az logout 2>$null
            & az login --tenant $TenantId --use-device-code --only-show-errors -o none 2>&1 |
                ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
            if ($LASTEXITCODE -ne 0) { throw '[operator-token] az login --use-device-code failed.' }
            if ($SubscriptionId) {
                $null = & az account set --subscription $SubscriptionId
            }
            $token = (& az account get-access-token --scope $scope --query accessToken -o tsv 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -ne 0 -or -not $token) {
                throw "[operator-token] az account get-access-token failed after az login: $token"
            }
            return $token
        }
        throw "[operator-token] az account get-access-token for $scope failed ($exitCode): $stderr"
    }
    return $token
}

function Invoke-SpikeDeployment {
    <# Starts a resource group Bicep deployment with --no-wait and polls every 15 seconds with a printed heartbeat so Cloud Shell stays alive. #>
    param(
        [Parameter(Mandatory)][string] $ResourceGroup,
        [Parameter(Mandatory)][string] $Name,
        [Parameter(Mandatory)][string] $TemplateFile,
        [Parameter(Mandatory)][string[]] $Parameters
    )
    $null = Invoke-SpikeAz -Step 'deploy-start' -Arguments (@('deployment', 'group', 'create', '--no-wait',
            '-g', $ResourceGroup, '-n', $Name, '-f', $TemplateFile, '-p') + $Parameters)
    $started = [datetime]::UtcNow
    while ($true) {
        Start-Sleep -Seconds 15
        $elapsed = ([datetime]::UtcNow - $started).ToString('mm\m\ ss\s')
        $deployment = Invoke-SpikeAz -Step 'deploy-poll' -Arguments @('deployment', 'group', 'show', '-g', $ResourceGroup, '-n', $Name)
        $state = $deployment.properties.provisioningState
        $ops = @(Invoke-SpikeAz -Step 'deploy-ops' -AllowNotFound -Arguments @('deployment', 'operation', 'group', 'list',
                '-g', $ResourceGroup, '-n', $Name, '--query', '[?properties.targetResource.resourceName!=null].{name:properties.targetResource.resourceName, state:properties.provisioningState}'))
        $doneCount = @($ops | Where-Object { $_.state -eq 'Succeeded' }).Count
        $runningNames = @($ops | Where-Object { $_.state -notin @('Succeeded', 'Failed', 'Canceled') } | ForEach-Object name)
        $runningNote = if ($runningNames.Count -gt 0) { "; in progress: $($runningNames -join ', ')" } else { '' }
        Write-Host "  [$elapsed] Bicep deploy ${Name}: $state ($doneCount/$($ops.Count) resources succeeded$runningNote)"
        if ($state -eq 'Succeeded') { return $deployment }
        if ($state -in @('Failed', 'Canceled')) {
            $failed = @(Invoke-SpikeAz -Step 'deploy-failed-ops' -AllowNotFound -Arguments @('deployment', 'operation', 'group', 'list',
                    '-g', $ResourceGroup, '-n', $Name, '--query', "[?properties.provisioningState=='Failed'].{resource:properties.targetResource.resourceName, error:properties.statusMessage}"))
            throw "[deploy] Bicep deployment $Name ${state}: $($failed | ConvertTo-Json -Depth 10 -Compress)"
        }
    }
}

function Remove-SpikeResourceGroup {
    <# Starts asynchronous resource group deletion and polls every 15 seconds with a printed heartbeat so Cloud Shell does not time out. #>
    param([Parameter(Mandatory)][string] $ResourceGroup)
    $null = Invoke-SpikeAz -Step 'delete-rg' -Arguments @('group', 'delete', '-n', $ResourceGroup, '--yes', '--no-wait')
    $started = [datetime]::UtcNow
    while ($true) {
        Start-Sleep -Seconds 15
        $elapsed = ([datetime]::UtcNow - $started).ToString('mm\m\ ss\s')
        $rg = Invoke-SpikeAz -Step 'poll-delete-rg' -AllowNotFound -Arguments @('group', 'show', '-n', $ResourceGroup)
        if (-not $rg) {
            Write-Host "  [$elapsed] Resource group $ResourceGroup deleted."
            return
        }
        $remaining = @(Invoke-SpikeAz -Step 'poll-rg-resources' -AllowNotFound -Arguments @('resource', 'list', '-g', $ResourceGroup, '--query', '[].name'))
        $names = @($remaining | Where-Object { $_ })
        $preview = if ($names.Count -gt 0) { ": $($names -join ', ')" } else { '' }
        Write-Host "  [$elapsed] Deleting $ResourceGroup ($($names.Count) resource(s) remaining$preview)..."
    }
}

Export-ModuleMember -Function Write-SpikeStep, Invoke-SpikeAz, Get-SpikeContext, Get-SpikeRoleId, Invoke-SpikeGraph, Connect-SpikeAgentGraph, Invoke-SpikeAgentGraph, Get-SpikeOperatorToken, Invoke-SpikeDeployment, Remove-SpikeResourceGroup -Variable SpikeResourceGroup, ExemptedAssignment
