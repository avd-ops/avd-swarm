# AVD-Scoped Bouncer Specification (`avd-swarm`)

- **Status:** Proposed (not yet scheduled; write-path implementation follows build order step 3 in [README.md](../README.md))
- **Scope:** AVD-scoped Azure and Entra ID active containment ("bouncer")
- **Related:** [governance.md](governance.md), [security-tooling.md](security-tooling.md), [redteam.md](redteam.md), [spec.md](spec.md), [catalog/actions/README.md](../catalog/actions/README.md)

> [!NOTE]
> Personal research project, not for production, provided as is, and not affiliated with the author's employer, Liquidware, or Microsoft.

## 1. Purpose and Boundaries

`avd-swarm` already detects AVD-plane anomalies through its KQL hunts ([`catalog/kql/`](../catalog/kql/catalog.yml)) and protects its own agents with a deterministic watchdog and kill switch ([governance.md](governance.md)). It does **not** currently take active containment actions against suspicious AVD users, hosts, tokens, or role assignments.

This specification defines an **AVD-scoped bouncer** that pairs deterministic detection with least-privilege containment, while keeping strict boundaries:

| Layer | Owner | In `avd-swarm`? |
|---|---|---|
| **Session-host OS processes, services, malware** | Microsoft Defender for Endpoint / Defender XDR or 3rd-party EDR | **No.** Defender XDR tables (`DeviceLogonEvents`, etc.) stay out of `avd-swarm` ([security-tooling.md](security-tooling.md) §6.3). Endpoint policies are delivered via [`avd-intune`](https://github.com/avd-ops/avd-intune). |
| **AVD-scoped Azure & Entra containment** (AVD quarantine group, AVD session logoff, host drain/stop/isolate, host-pool registration token invalidation, AVD RG RBAC cleanup) | **`avd-swarm`** | **Yes** (this spec). |
| **Tenant-wide Entra / Azure SOAR** (disabling arbitrary users, blocking non-AVD service principals, stopping non-AVD subscriptions) | Entra ID Protection + Sentinel / Defender XDR SOAR (or a dedicated security repo) | **No.** `avd-swarm` never holds tenant-wide `User.ReadWrite.All` or subscription-wide write roles. |

---

## 2. Two-Tier Execution Gate

To contain fast-moving threats without giving an LLM autonomous write access or risking a self-DoS storm, actions are split into two tiers:

### Tier 1: Deterministic Auto-Containment (Reversible, Low Blast Radius, Rate-Capped)
- **No LLM in the loop:** Triggered only by deterministic KQL hunt / Azure Monitor alert rules, never by an agent's free-form judgment.
- **Observe-only calibration first:** Any heuristic signal with known legitimate variance—specifically `wvd-connection-ip-mismatch-24h.kql` (split-tunnel VPNs, IPv4/IPv6 dual-stack, mobile handoffs, proxy auth vs. UDP Shortpath)—must run in **observe-only mode** first to establish a false-positive baseline (matching the `observe` → `enforce` pattern in [security-tooling.md](security-tooling.md) §3) and require **corroboration** (e.g., IP mismatch plus new country/ASN or anomalous client) before Tier 1 auto-containment is enabled.
- **Reversible & low blast radius:** Must not delete infrastructure or hold broad RBAC/Graph write permissions.
- **Strict rate cap:** Bounded by `maxAutoContainmentsPerHour` (proposed default: `3` per hour across the estate, `1` per host pool per 15 minutes). Exceeding the cap **trips the kill switch to `halt-actions`** and pages the owner so a false-positive storm or adversary-triggered alert flood cannot lock out the estate.
- **Kill-switch bound:** Tier 1 identities check the `avd-swarm-switch` tag before every step and refuse to run at `halt-actions` or above.

### Tier 2: Human-Approved Containment (Higher Blast Radius / Broad RBAC)
- Proposed as a standard `avd-swarm` plan through the `approve` environment and deterministic governance gate ([governance.md](governance.md) §2.1).
- Required whenever containment terminates active user sessions on a host, blocks network traffic to a host, removes RBAC assignments, or requires an Azure RBAC permission broader than the single containment action (such as `hostPools/write`).

---

## 3. v1 Containment Actions

| Action ID | Tier | Trigger (KQL / Signal) | Effect | Least-Privilege Identity & Scope | Rollback |
|---|---|---|---|---|---|
| `user.quarantine-avd` | **Tier 1** *(after observe calibration)* | Corroborated `wvd-connection-ip-mismatch-24h.kql` (suspected token replay) or `wvd-signins-without-ca-24h.kql` (CA bypass) | Adds the user to a dedicated `avd-quarantine` Entra security group (`isAssignableToRole: false`) targeted by a Conditional Access policy blocking AVD cloud apps. **Note:** blocks new sign-ins/refreshes; pair with `user.logoff-avd-sessions` to terminate already-open sessions. | **R7 pattern:** `avd-quarantine` sits inside a dedicated Entra Administrative Unit (`au-avd-quarantine`). `bouncer-quarantine` holds **Groups Administrator** scoped to `au-avd-quarantine` only, with **zero** tenant-wide Graph write permissions (`GroupMember.ReadWrite.All` is never granted). | Remove user from `avd-quarantine`. |
| `host.drain` (`bouncer.host-drain`) | **Tier 1** | `wvd-unrecognized-session-hosts-7d.kql` (unrecognized host) or `wvd-agent-health-latest-7d.kql` anomaly | Sets `AllowNewSession = false` on the session host using a dedicated `bouncer.host-drain` manifest (omits `noActiveSessions` and `restoreDrainStateFound` blockers so a suspect host is drained immediately) and **auto-opens a Tier 2 `host.stop` / `host.nsg-isolate` plan** if active sessions exist. | Dedicated `bouncer-host-drain` identity with `Desktop Virtualization Session Host Contributor` (or drain-only custom role) scoped to AVD host-pool resource groups. | `host.resume` (`AllowNewSession = true`). |
| `user.logoff-avd-sessions` | **Tier 2** *(or Tier 1 after calibration)* | Paired with `user.quarantine-avd` when an active AVD session exists for the quarantined user | Disconnects/logs off the user's active AVD sessions (`Remove-AzWvdUserSession`) so an attacker with a live session cannot persist past `avd-quarantine` CA enforcement. | `approve-user-logoff` (or `bouncer-session-logoff`) scoped to AVD host-pool resource groups (`Microsoft.DesktopVirtualization/hostPools/sessionHosts/userSessions/delete`). | User signs in again once removed from `avd-quarantine`. |
| `hostpool.invalidate-reg-token` | **Tier 2** | `wvd-unrecognized-session-hosts-7d.kql` or unexpected registration token retrieval in `wvd-control-plane-changes-7d.kql` | Clears/invalidates the active registration token on the affected host pool. **Kept in Tier 2** because Azure RBAC has no token-delete-only action (`hostPools/write` could also mint new tokens or alter `customRdpProperty`). | `approve-regtoken` identity scoped to AVD host-pool resource groups, human-approved plan only. | Re-issue a short-lived registration token on the next approved host deployment. |
| `host.stop` / `host.nsg-isolate` | **Tier 2** | Escalation from `bouncer.host-drain` when active sessions exist or host is confirmed rogue/compromised | Deallocates the VM (`host.stop`) or applies a deny-all NSG isolation rule on the host's NIC/subnet-exception. | `approve-host-stop` / `approve-nsg-isolate` identity, human-approved plan only. | Start VM / remove isolation rule (`restoreStateFound: true`). |
| `rbac.remove-unauthorized` | **Tier 2** | `wvd-control-plane-changes-7d.kql` / `write.outside-scope` detecting an unapproved role assignment on an AVD RG | Deletes the unauthorized role assignment on the AVD resource group. | `approve-rbac-cleanup` identity with `roleAssignments/delete` on AVD RGs and an ABAC delegation condition **excluding** landing-zone deploy, operator, and break-glass principals. | Re-apply approved role assignment via landing-zone PR. |

---

## 4. Guardrails, Corroboration, and the Rogue/Hijacked Bouncer Scenario

### 4.1 Protected Principals (Never Targeted)
- Break-glass / emergency-access Entra accounts
- Landing-zone deployment identities, `approve-*` identities, `stop-spend` identities, and the kill-switch Logic App identity
- Any group or principal listed in a protected `governance/bouncer.yml` allowlist (protected path under `governance/protected-paths.yml`)

### 4.2 Corroboration (R9)
Tier 1 bouncer actions must never be verified solely by the bouncer's own logs. The watchdog corroborates every Tier 1 write by joining:
- **Azure Activity Log / Entra Audit Logs** (who actually wrote to the host pool or `avd-quarantine` group), against
- **Azure Monitor alert firing records** (whether the deterministic KQL alert rule actually fired for that target and timestamp).

### 4.3 Rogue or Hijacked Bouncer Containment
If a bouncer identity, Logic App, or function is compromised or malfunctions:
1. **No LLM / no broad blast radius:** Bouncer identities have no model access, no `approve` federated credentials, no `hostPools/write`, and no tenant-wide Graph write permissions. A hijacked `bouncer-quarantine` can at worst add non-break-glass users to `avd-quarantine` up to `maxAutoContainmentsPerHour` (3/hr).
2. **Covered by the kill switch:** At `halt-actions` and above, the kill switch Logic App disables/stops the bouncer compute and removes the bouncer identities' Azure RBAC role assignments (via its existing `roleAssignments/delete` delegation condition), and Conditional Access / AU role removal cuts `bouncer-quarantine`. Only a person in Cloud Shell can reset the switch.
3. **Watchdog rules (`governance/watchdog.yml`):**
   - `bouncer.rate-cap-exceeded`: more than `maxAutoContainmentsPerHour` Tier 1 actions in an hour → **`halt-actions`** + page owner.
   - `bouncer.protected-principal-targeted`: any bouncer attempt against a break-glass, deploy, `approve-*`, or `stop-spend` principal → **`halt-all`** + page owner.
   - `bouncer.write-without-signal`: an Activity Log or Entra Audit write by any `bouncer-*` identity with no matching Azure Monitor alert firing record → **`halt-all`** + page owner.
   - `bouncer.unexpected-target`: any write by `bouncer-quarantine` to a group other than `avd-quarantine` or outside `au-avd-quarantine` → **`halt-all`** + page owner.

### 4.4 Absent, Silent, or Failed Bouncer
If the bouncer is disabled, misconfigured, or fails during an active alert, it must **never fail silently**:
1. **Silent / disabled bouncer (`bouncer.silent`):** Mirroring `observer.silent` in `governance/watchdog.yml`, if the bouncer's scheduled health check / alert-rule evaluation is silent for more than 15 minutes (or the bouncer Logic App/function is disabled), the watchdog trips **`halt-actions`** and pages the owner.
2. **Tampered containment controls (`bouncer.control-missing`):** If the `avd-quarantine` Conditional Access policy is disabled/deleted or `bouncer-quarantine`'s AU-scoped role assignment is removed outside an approved deployment/kill-switch event, the watchdog trips **`halt-actions`** and pages the owner (prevents an attacker from disabling quarantine before replaying tokens).
3. **Failed Tier 1 execution (`bouncer.action-failed`):** If a Tier 1 containment action fails (Graph/ARM error, throttling, or missing permission), it retries once with short backoff; on second failure, it **pages the owner immediately** with the alert correlation ID and **auto-opens a Tier 2 fallback plan** so a person can execute containment directly. Two failed Tier 1 executions within 24 hours (`bouncer.failures-24h`) trip **`halt-actions`** (mirroring `verify.failures-24h`).
4. **Periodic drill (R13):** Mirroring the monthly kill-switch drill ([governance.md](governance.md) §2.3), a test-environment drill verifies that a synthetic KQL alert triggers Tier 1 containment, rolls back cleanly, and that disabling the bouncer triggers `bouncer.silent` / `bouncer.action-failed` paging.

---

## 5. Paired Cross-Repo Contract Notes

When this specification is scheduled for implementation, the following sibling repositories change together:

- **`avd-entra` / `avd-landing-zone`:** Provision the `au-avd-quarantine` Administrative Unit, the `avd-quarantine` security group (`isAssignableToRole: false`) inside it, AU-scoped `Groups Administrator` for `bouncer-quarantine`, and the Conditional Access policy blocking AVD cloud apps for `avd-quarantine` members (excluding break-glass accounts).
- **`avd-assess`:** Keep endpoint-protection posture in `dimensions.yaml` (`agents.av-edr-present`, `agents.coexistence`, `agents.edr-onboarding`, `enableDefenderForCloud`) and add containment-readiness checks (verifying `au-avd-quarantine`, the CA policy, and bouncer rate-cap config exist).
- **`avd-second-brain`:** Add C8 incident categories for `token-replay`, `unrecognized-host`, `leaked-reg-token`, and `unauthorized-avd-rbac`, mapping each to Tier 1 vs. Tier 2 bouncer plans and recording containment outcomes via C10.
- **`avd-intune`:** Remains separate for endpoint security policy delivery (Defender AV, ASR rules, FSLogix/AVD exclusions) across `avd-multisession`, `avd-personal`, and `win365-cloudpc`.
