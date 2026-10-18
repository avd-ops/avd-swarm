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

Export-ModuleMember -Function Write-SpikeStep, Invoke-SpikeAz, Get-SpikeContext, Get-SpikeRoleId, Invoke-SpikeGraph -Variable SpikeResourceGroup, ExemptedAssignment
