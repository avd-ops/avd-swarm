# Bouncer §6 Sandbox Verification Spike

> [!NOTE]
> Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.

Disposable sandbox verification harness for the six unverified assumptions in [`docs/bouncer.md`](../../docs/bouncer.md) §6 before implementing production/adopter templates.

## What it tests

| §6 # | Assumption | How the spike tests it | Pass criteria |
|---|---|---|---|
| 1 & 2 | Managed identity with **no** Graph app role, holding `Directory Readers` + AU-scoped custom role (`microsoft.directory/groups/members/update`) in a restricted management AU, can `POST/DELETE /groups/{id}/members/$ref` | Short-lived ACI (`verify_imds_tokens.py`) acquires IMDS app-only token for `id-bouncer-verify-quarantine` and adds/removes test user in `grp-bouncer-verify-in-au`, plus negative test on `grp-bouncer-verify-out-au` | In-AU `POST` & `DELETE` return `204`; out-of-AU `POST` returns `403` |
| 3 | AU-scoped `User Administrator` permits `revokeSignInSessions` from an app-only token | ACI acquires IMDS app-only token for `id-bouncer-verify-revoke` and calls `POST /users/{id}/revokeSignInSessions` (plus optional out-of-AU negative user) | In-AU returns `200`; out-of-AU returns `403` |
| 4 | Measured latency from `avd-quarantine` membership to refused AVD connection, with and without `Remove-AzWvdUserSession` | Interactive timing with `-TestCaPropagation -TestUserObjectId <id>` | Recorded elapsed seconds in `results.json` |
| 5 | `Microsoft.DesktopVirtualization/hostPools/sessionHosts/write` accepts a body containing only `allowNewSession` | ACI acquires IMDS ARM token for `id-bouncer-verify-drain` (custom role with `hostPools/read`, `sessionHosts/read`, `sessionHosts/write`) and sends `PATCH` with `{"properties":{"allowNewSession":false}}`, then restores | `PATCH` returns `200` and restores original state |
| 6 | `alertsmanagementresources` schema and lag as a watchdog corroboration join source | Deploys disposable Log Analytics workspace + 1-min Scheduled Query Rule (`sq-bouncer-verify-synthetic-*`), polls `az graph query` | Alert appears in `alertsmanagementresources`; `essentialKeys` and `observedLagSec` captured |

## Prerequisites

- Lab Entra ID tenant with **Entra ID P1** (required for restricted management AUs) and **Privileged Role Administrator** + **Conditional Access Administrator** (if `-TestCaPropagation` is used) + **Owner** on a disposable sandbox subscription.
- Never run against a production tenant or subscription. Never pass a break-glass account to `-TestUserObjectId`.

## Run in Azure Cloud Shell (PowerShell)

First, check the retail cost estimate (no resources deployed):

```powershell
& ~/avd-swarm/spikes/bouncer-verify/Get-SpikeEstimate.ps1
```

Then run with your monthly cap (requires `spend_guard.py open` approval):

```powershell
if (Test-Path ~/avd-swarm) { git -C ~/avd-swarm pull -q } else { git clone -q https://github.com/avd-ops/avd-swarm ~/avd-swarm }
& ~/avd-swarm/spikes/bouncer-verify/Invoke-Spike.ps1 -MonthlyCap 50
```

Optional parameters for Assumption 4 & 5 against an existing lab host pool and test user:

```powershell
& ~/avd-swarm/spikes/bouncer-verify/Invoke-Spike.ps1 -MonthlyCap 50 `
  -HostPoolResourceId '/subscriptions/<subId>/resourceGroups/<rg>/providers/Microsoft.DesktopVirtualization/hostPools/<hp>' `
  -SessionHostName '<host.fqdn>' `
  -TestUserObjectId '<lab-test-user-object-id>' `
  -TestCaPropagation
```

Teardown only (if `-KeepOnFailure` was used or a session disconnected):

```powershell
& ~/avd-swarm/spikes/bouncer-verify/Invoke-Spike.ps1 -TeardownOnly
```
