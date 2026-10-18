# avd-swarm S1a spike: price the Azure lines. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
<#
.SYNOPSIS
Prices the spike's Azure lines from the retail prices API and prints the -ManualAmount and
-ManualNote for tenant-swarm's Start-SpendRun.ps1.

.DESCRIPTION
Each line must match exactly one meter (avd-landing-zone decision 0010). A line that matches none
or several is reported with the meters it saw and left unpriced; the run then needs a decision,
never a guess. Tokens are priced from Anthropic's list prices, which Claude on Foundry bills at.
#>
[CmdletBinding()]
param(
    [string] $Region = 'eastus2',
    [double] $Hours = 6,
    [double] $TokenUpperBound = 20.00
)
$ErrorActionPreference = 'Stop'

function Get-RetailMeter {
    param([string] $Filter)
    $uri = "https://prices.azure.com/api/retail/prices?`$filter=$([uri]::EscapeDataString($Filter))"
    $items = [System.Collections.Generic.List[object]]::new()
    while ($uri) {
        $page = Invoke-RestMethod -Uri $uri -Method Get
        $page.Items | ForEach-Object { $items.Add($_) }
        $uri = if ($page.Items.Count -gt 0) { $page.NextPageLink } else { $null }  # end on empty pages (lesson 0008)
    }
    $items | Where-Object { $_.type -eq 'Consumption' -and $_.unitPrice -gt 0 }
}

$lines = @(
    @{ Name = 'Container Apps vCPU (MCP server)'; Quantity = 0.5 * $Hours * 3600; Unit = 'vCPU-seconds'
       Filter = "serviceName eq 'Azure Container Apps' and armRegionName eq '$Region'"; Match = { $_.meterName -match 'vCPU' -and $_.meterName -match 'Active' -and $_.skuName -match 'Consumption|Standard' } }
    @{ Name = 'Container Apps memory (MCP server)'; Quantity = 1 * $Hours * 3600; Unit = 'GiB-seconds'
       Filter = "serviceName eq 'Azure Container Apps' and armRegionName eq '$Region'"; Match = { $_.meterName -match 'Memory' -and $_.meterName -match 'Active' -and $_.skuName -match 'Consumption|Standard' } }
    @{ Name = 'Log Analytics ingestion'; Quantity = 0.5; Unit = 'GB'
       Filter = "serviceName eq 'Log Analytics' and armRegionName eq '$Region'"; Match = { $_.meterName -match 'Data Ingestion' -and $_.meterName -notmatch 'Basic|Auxiliary|Commitment' } }
    @{ Name = 'Foundry hosted agent compute'; Quantity = $Hours; Unit = 'hours of one 0.5 vCPU session'
       Filter = "armRegionName eq '$Region' and contains(productName, 'Foundry')"; Match = { $_.meterName -match 'Hosted' -or $_.productName -match 'Hosted Agent' } }
)

$total = $TokenUpperBound
$note = [System.Collections.Generic.List[string]]::new()
$note.Add("Tokens upper bound $('{0:N2}' -f $TokenUpperBound) (Opus 5.5 4/20, Haiku 5.5 0.10/0.50 per 1M; docs/spikes/s1a.md section 3)")
$unpriced = 0
foreach ($line in $lines) {
    Write-Host "Pricing $($line.Name) in $Region..."
    $seen = @(Get-RetailMeter -Filter $line.Filter)
    $matched = @($seen | Where-Object $line.Match)
    if ($matched.Count -ne 1) {
        $unpriced++
        Write-Warning "$($line.Name): $($matched.Count) meters matched (of $($seen.Count) seen); left unpriced. Matched:"
        $matched | Select-Object productName, skuName, meterName, unitOfMeasure, unitPrice | Format-Table -AutoSize | Out-String | Write-Host
        $note.Add("$($line.Name): UNPRICED ($($matched.Count) meters matched)")
        continue
    }
    $meter = $matched[0]
    # Convert the quantity to the meter's unit: per-second meters, hourly meters, or per-GB.
    $perUnit = switch -Regex ($meter.unitOfMeasure) {
        'Second' { 1; break }
        'Hour'   { if ($line.Unit -match 'seconds') { 1 / 3600 } else { 1 }; break }
        default  { 1 }
    }
    $scale = if ($meter.unitOfMeasure -match '^(\d+)') { [double]$Matches[1] } else { 1 }
    $amount = [math]::Round($line.Quantity * $perUnit / $scale * $meter.unitPrice, 2)
    $total += $amount
    Write-Host ("  {0}: {1} {2} at {3} per {4} = {5:N2}" -f $line.Name, $line.Quantity, $line.Unit, $meter.unitPrice, $meter.unitOfMeasure, $amount)
    $note.Add("$($line.Name): $('{0:N2}' -f $amount) ($($meter.meterName), $($meter.unitPrice) per $($meter.unitOfMeasure))")
}

[pscustomobject]@{
    step          = 'spike-estimate'
    manualAmount  = [math]::Round($total, 2)
    unpricedLines = $unpriced
    manualNote    = ($note -join '; ')
} | ConvertTo-Json -Compress
if ($unpriced -gt 0) {
    Write-Warning "$unpriced line(s) unpriced. Decide before opening the run: price them by hand with a note, or pass -AllowUnpriced to Start-SpendRun.ps1."
}
