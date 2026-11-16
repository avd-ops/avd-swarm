# avd-swarm bouncer verification spike: price the Azure lines.
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
<#
.SYNOPSIS
Prices the bouncer verification spike's disposable Azure resources from the retail prices API
and prints JSON for spend_guard.py open.
#>
[CmdletBinding()]
param(
    [string] $Region = 'eastus2',
    [double] $AciMinutes = 15,
    [double] $LogAnalyticsGb = 0.1
)
$ErrorActionPreference = 'Stop'

function Get-RetailMeter {
    param([string] $Filter)
    $uri = "https://prices.azure.com/api/retail/prices?`$filter=$([uri]::EscapeDataString($Filter))"
    $items = [System.Collections.Generic.List[object]]::new()
    while ($uri) {
        $page = Invoke-RestMethod -Uri $uri -Method Get
        $page.Items | ForEach-Object { $items.Add($_) }
        $uri = if ($page.Items.Count -gt 0) { $page.NextPageLink } else { $null }
    }
    $items | Where-Object { $_.type -eq 'Consumption' -and $_.unitPrice -gt 0 }
}

$aciHours = [math]::Round($AciMinutes / 60, 4)
$lines = @(
    @{
        Name     = 'Container Instances vCPU (1 vCPU)'
        Quantity = 1 * $aciHours
        Unit     = 'vCPU-hours'
        Filter   = "serviceName eq 'Container Instances' and armRegionName eq '$Region'"
        Match    = { $_.meterName -match 'vCPU Duration' -and $_.skuName -eq 'Standard' }
    }
    @{
        Name     = 'Container Instances memory (1 GiB)'
        Quantity = 1 * $aciHours
        Unit     = 'GiB-hours'
        Filter   = "serviceName eq 'Container Instances' and armRegionName eq '$Region'"
        Match    = { $_.meterName -match 'Memory Duration' -and $_.skuName -eq 'Standard' }
    }
    @{
        Name     = 'Log Analytics ingestion'
        Quantity = $LogAnalyticsGb
        Unit     = 'GB'
        Filter   = "serviceName eq 'Log Analytics' and armRegionName eq '$Region'"
        Match    = { $_.meterName -match 'Data Ingestion' -and $_.meterName -notmatch 'Basic|Auxiliary|Commitment' }
    }
    @{
        Name     = 'Azure Monitor Scheduled Query Rule (1 rule)'
        Quantity = 1
        Unit     = 'rules'
        Filter   = "serviceName eq 'Azure Monitor' and armRegionName eq '$Region'"
        Match    = { $_.meterName -match 'Log Search' -or $_.meterName -match 'Scheduled Query' }
    }
)

$total = 0.0
$note = [System.Collections.Generic.List[string]]::new()
$unpriced = 0

foreach ($line in $lines) {
    $meters = @(Get-RetailMeter -Filter $line.Filter | Where-Object $line.Match)
    if ($meters.Count -eq 1) {
        $m = $meters[0]
        $cost = [math]::Round($line.Quantity * [double]$m.unitPrice, 4)
        $total += $cost
        $note.Add("$($line.Name): `$$('{0:N4}' -f $cost) ($($line.Quantity) $($line.Unit) @ `$$($m.unitPrice)/$($m.unitOfMeasure))")
    }
    else {
        $unpriced += 1
        $note.Add("$($line.Name): UNPRICED ($($meters.Count) matching meters)")
    }
}

$total = [math]::Max(0.10, [math]::Round($total, 2))

[pscustomobject]@{
    region        = $Region
    manualAmount  = $total
    manualNote    = ($note -join '; ')
    unpricedLines = $unpriced
} | ConvertTo-Json -Compress
