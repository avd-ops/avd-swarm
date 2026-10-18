# avd-swarm S1a spike: let the hosted agent's identity call the MCP server. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
<#
.SYNOPSIS
Gives the hosted agent's identity (deploy_agent.py's identityPrincipalId) the MCP server's app
role, so it can get a token for the server. Its result is Q5 evidence either way: whether an
agent identity can hold an app role at all.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string] $AgentPrincipalId,
    [Parameter(Mandatory)][string] $McpAppSpId,
    [Parameter(Mandatory)][string] $McpAppRoleId
)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Spike.psm1') -Force

Write-SpikeStep "Reading the agent identity $AgentPrincipalId from Graph"
$agent = Invoke-SpikeAz -Step 'get-agent-identity' -Arguments @('rest', '--method', 'get', '--url',
    "https://graph.microsoft.com/v1.0/servicePrincipals/$($AgentPrincipalId)?`$select=id,appId,displayName,servicePrincipalType,tags")
Write-Host ($agent | ConvertTo-Json -Compress)

Write-SpikeStep 'Assigning the MCP app role to it'
$body = @{ principalId = $AgentPrincipalId; resourceId = $McpAppSpId; appRoleId = $McpAppRoleId } | ConvertTo-Json -Compress
$bodyFile = New-TemporaryFile
Set-Content -LiteralPath $bodyFile.FullName -Value $body
try {
    if ($PSCmdlet.ShouldProcess($AgentPrincipalId, 'assign MCP app role')) {
        $assignment = Invoke-SpikeAz -Step 'assign-app-role' -Arguments @('rest', '--method', 'post', '--url',
            "https://graph.microsoft.com/v1.0/servicePrincipals/$AgentPrincipalId/appRoleAssignments", '--body', "@$($bodyFile.FullName)")
        Write-Host ($assignment | ConvertTo-Json -Compress)
        Write-Host 'RESULT Q5 app role: assigned to the agent identity'
    }
}
finally {
    Remove-Item -LiteralPath $bodyFile.FullName -ErrorAction SilentlyContinue
}
