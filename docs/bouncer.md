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
| **AVD-scoped Azure & Entra containment** (AVD quarantine group, AVD session logoff, host drain/stop/isolate, rogue-host unregister, host-pool registration token deletion, AVD RG RBAC cleanup) | **`avd-swarm`** | **Yes** (this spec). |
| **Tenant-wide Entra / Azure SOAR** (disabling arbitrary users, blocking non-AVD service principals, stopping non-AVD subscriptions, revoking subscription-level role-assignment permissions) | Entra ID Protection + Sentinel / Defender XDR SOAR (or a dedicated security repo) | **No.** `avd-swarm` never holds tenant-wide `User.ReadWrite.All` or subscription-wide write roles. |

---

## 2. Two-Tier Execution Gate & Latency Budget

`avd-swarm` allows no unapproved standing write path ([governance.md](governance.md), [redteam.md](redteam.md) R1/R2). To enable fast containment without breaking that invariant—or handing an attacker a self-DoS lever—actions are split into two tiers:

### Tier 1: Pre-Approved Containment Envelope (Deterministic, Reversible, Local-Breaker Capped)
- **Pre-approved envelope (no standing unapproved write):** A person approves a `containment-envelope` plan once per estate in the `approve` environment (pinning target `avd-quarantine` group, target host pools, allowed alert IDs, and caps), hash-pinned and expiring every **30 days** (requiring human re-approval). Every Tier 1 execution runs through the `approve.yml` workflow template—re-reading the alert record, re-running the gate, and acting—with bouncer identities federated to that envelope's environment so kill-switch Layer 2 (`federatedIdentityCredentials/delete`) cuts them.
  - *Simpler v1 fallback:* If the envelope pattern is too heavy for initial rollout, cut Tier 1 to `bouncer.host-drain` only (an Azure RBAC write the kill switch can strip directly) and keep all Graph writes in Tier 2.
- **No LLM in the loop:** Triggered only by deterministic Azure Monitor alert rules over a dedicated, non-agent-readable query set (`catalog/bouncer/`, protected path), never by an agent's free-form judgment.
- **Observe-only calibration first:** Heuristic signals—specifically IP mismatch—must run in **observe-only mode** first to measure false-positive rates (matching [security-tooling.md](security-tooling.md) §3) and require corroboration by a signal the attacker cannot choose (sign-in risk event or new device/client), excluding **Windows Cloud Login** rows from client-IP comparison.
- **Bouncer-local circuit breaker (no self-DoS):** Bounded by `maxAutoContainmentsPerHour` (default `3` estate-wide; catalog limits `perHour: 2`, `perHostPerDay: 2`, `oneHostPerPoolAtATime` also apply, with the stricter limit winning). Exceeding the cap or hitting `circuitBreakerFailures24h: 2` **pauses Tier 1 locally and pages the owner**—it does **not** trip `halt-actions`, so normal swarm operations and Tier 2 approvals remain available. (On a 3rd rogue host in one pool, page the owner and open a Tier 2 batch plan.)

### Tier 2: Human-Approved `containment` Plan Class
- Uses a `containment` plan class through the `approve` environment and deterministic governance gate: same hash pinning and gate checks, shorter expiry, immediate approver page, and **no time-lock exemption**.
- Required for session revocation (`revokeSignInSessions`), rogue host removal (`sessionhosts/delete`), registration token deletion, VM stop/NSG isolation, RBAC cleanup, and **all containment rollbacks** (each rollback requires a `why` block citing evidence of resolution).
- When `avd-swarm` is at `halt-actions`, containment falls back to **self-contained Cloud Shell blocks** run by the operator (shipped alongside the spec, lesson 0015 style).

### Latency Budget (Detect-to-Act)
Because log ingestion and alert evaluation introduce inherent delay, any containment SLA must budget for platform lag:

| Signal / Source | Ingestion + Eval Lag | Notes |
|---|---|---|
| Azure Resource / Activity Logs | 3–20 min | Standard Log Analytics ingestion latency. |
| Entra Sign-In & Audit Logs | Up to 15 min | After diagnostic settings setup. |
| 1-min Log Search Alert Rules | +1–5 min | 1-min rules reject `union`, `search`, `take`, `ingestion_time()`; false fires possible on data >30 min late. |
| `avd-quarantine` CA Propagation | Up to 2 hr (optimized) / 24 hr max | Group membership is not a CAE critical event; established RDP sessions stay live until logged off. |

---

## 3. v1 Containment Actions

**Query separation (`catalog/bouncer/`):** Existing `catalog/kql/` queries return counts or host names only (never UPNs or IPs). Bouncer alerts use a separate `catalog/bouncer/` query set (protected path), run solely by the alert rule's identity, never readable by agents and never printed in job summaries. Alerts pass resource ID or object ID, and the job re-resolves live.

| Action ID | Tier | Trigger (Alert / Signal) | Effect | Least-Privilege Identity & Scope | Rollback (Tier 2 Plan) |
|---|---|---|---|---|---|
| `user.quarantine-and-logoff` | **Tier 1** *(after observe calibration)* | Corroborated IP mismatch (+ risk event or new device/client, excluding Windows Cloud Login) or CA-bypass sign-in | **Atomic pair:** (1) adds user to `avd-quarantine` (`isAssignableToRole: false`) targeted by CA blocking AVD cloud apps (prevents *new* connections after propagation); (2) logs off active AVD sessions (`Remove-AzWvdUserSession`) so live sessions cannot persist. | **Entra:** `avd-quarantine` sits in a **restricted management AU** (`au-avd-quarantine`, Entra ID P1 required; blocks PIM/access reviews on it). `bouncer-quarantine` holds tenant-wide **Directory Readers** (required for SPs using AU roles) + an AU-scoped **custom Entra role** with only `microsoft.directory/groups/members/update` (never `Groups Administrator` or `GroupMember.ReadWrite.All`).<br>**AVD:** `usersessions/delete` on host-pool RGs. *(If sandbox test shows no-app-role Graph write fails, move Graph write to Tier 2 `approve`.)* | Remove user from `avd-quarantine` (Tier 2 plan with resolution evidence). |
| `user.revoke-signin-sessions` | **Tier 2** | Follow-up to `user.quarantine-and-logoff` for confirmed token theft | Calls `revokeSignInSessions` to invalidate refresh tokens across sessions. | `approve` identity with named user-scoped permission (e.g., AU-scoped User Administrator or custom role, verified in sandbox). | User re-authenticates after removal from `avd-quarantine`. |
| `bouncer.host-drain` | **Tier 1** | `wvd-agent-health-latest-7d` or suspect host anomaly (never silent hosts) | Sets `AllowNewSession = false`. Declares **all nine §5a precondition keys** (`noActiveSessions: false`, `notDrainedByOthers: true`, `restoreDrainStateFound: true`, recording prior drain state). Routed through 72-hr time-lock as capability-raising. | Dedicated `bouncer-host-drain` custom Azure role: `hostpools/read`, `hostpools/sessionhosts/read`, `hostpools/sessionhosts/write` only, scoped to host-pool RGs. Job refuses any body field other than `allowNewSession`. | `host.undrain` (Tier 2 plan restoring recorded drain state only; never `host.resume`). |
| `host.unregister-and-invalidate-token` | **Tier 2** (`containment`) | `resourceId` join against Resource Graph finds a session host VM outside the landing zone's host-pool RGs (identity-based, never hostname regex) | **Paired action:** (1) `sessionhosts/delete` on the rogue host; (2) `hostPools/write` sending **only** `registrationInfo` with `registrationTokenOperation: Delete` (refuses all other fields). *(Note: session-host-configuration pools expose no retrievable token.)* | `approve` identity, human-approved `containment` plan only. Tier 1 alerts only page on rogue registration. | Re-issue short-lived registration token on next approved deployment. |
| `host.stop` / `host.nsg-isolate` | **Tier 2** (`containment`) | Escalation when compromised host has active sessions or must be network-isolated | Deallocates the VM (`host.stop`) or applies a deny-all NSG isolation rule. | `approve-host-stop` / `approve-nsg-isolate`, human-approved `containment` plan only. | Start VM / remove isolation rule (`restoreStateFound: true`). |
| `rbac.remove-unauthorized` | **Tier 2** (`containment`) | Unauthorized role assignment detected on an AVD RG | **Cleanup (not full containment):** deletes the unauthorized role assignment. Removing an attacker's upstream Owner/UAA right is tenant/subscription work outside `avd-swarm`'s lane (page owner). | `approve-rbac-cleanup` with `roleAssignments/delete` on AVD RGs and an ABAC delegation condition using an **allow-list of role definition IDs** (never a principal deny-list). | Re-apply approved assignment via landing-zone PR. |

---

## 4. Guardrails, Corroboration, and Failure Modes

### 4.1 Protected Principals (Scope-Enforced)
Protection is enforced by **Azure/Entra scope**, not only by a config file a hijacked bouncer could ignore:
- Break-glass accounts are excluded from the `avd-quarantine` Conditional Access policy and never placed in groups writable by bouncer identities.
- `bouncer-host-drain` is scoped strictly to host-pool resources; `rbac.remove-unauthorized` uses an ABAC role-definition allow-list.
- `governance/bouncer.yml` (protected path) serves as the monthly drill's expected-exclusion reference.

### 4.2 Corroboration (R9)
The watchdog (`governance/watchdog.yml`) adds `alerts-management` (Resource Graph `alertsmanagementresources`) and `entra-audit` as corroboration sources. Every Tier 1 write carries an `alertId` correlation tag and is joined against Alerts Management and Activity/Entra Audit Logs with a **30-minute grace window** for ingestion lag.

### 4.3 Rogue or Hijacked Bouncer Containment
1. **Kill-switch coverage (fixing B2):** Add all `bouncer-*` principal IDs to the kill-switch Logic App's `roleAssignments/delete` delegation condition and `watchdog.yml`'s strip set (never in `preserveIdentities`). Because Tier 1 runs via federated credentials on the envelope environment, kill-switch Layer 2 deletes their credentials. If any AU role assignment is standing, the person's Cloud Shell trip block removes it (verified in the monthly R13 drill).
2. **Watchdog rules (`governance/watchdog.yml`):**
   - `bouncer.protected-principal-targeted` → **`halt-all`** + page owner.
   - `bouncer.write-without-signal`: Activity Log or Entra Audit write by any `bouncer-*` identity with no matching Alerts Management firing record (after 30-min grace window) → **`halt-all`** + page owner.
   - `bouncer.unexpected-target`: any `bouncer-quarantine` member **removal**, owner change, group delete/create, or write outside `avd-quarantine`; or any `bouncer-host-drain` write touching `assignedUser` or fields other than `allowNewSession` → **`halt-all`** + page owner.

### 4.4 Absent, Silent, or Failed Bouncer
1. **Silent / disabled bouncer (`bouncer.silent`):** Give the bouncer a scheduled heartbeat at **`<= 15` minutes** frequency (rules >15 min report `Unknown` health, and rules failing for a week auto-disable). Silence >15 minutes pages the owner and marks Tier 1 unavailable; it trips `halt-actions` **only** if the adopter registers the bouncer as a required observer.
2. **Tampered controls (`bouncer.control-missing`):** If the `avd-quarantine` CA policy or `au-avd-quarantine` role is disabled/removed outside an approved change or kill-switch event, page the owner and mark Tier 1 degraded (or `halt-actions` per adopter policy).
3. **Failed Tier 1 execution (`bouncer.action-failed` / `circuitBreakerFailures24h`):** Retries once with backoff; on second failure, pages the owner with the alert correlation ID and **auto-opens a Tier 2 `containment` plan**. Two failures in 24 hours trip the **bouncer-local circuit breaker** (pausing Tier 1, keeping the swarm running).
4. **Monthly drill (R13):** Verifies synthetic alert → Tier 1 containment → Tier 2 rollback, kill-switch stripping of `bouncer-*` credentials/roles, and `bouncer.silent` / `bouncer.action-failed` paging.

---

## 5. Paired Cross-Repo Contract Notes

When scheduled, the following sibling repositories change together:

- **`avd-entra` / `avd-landing-zone`:** Provision `au-avd-quarantine` (restricted management AU, Entra ID P1), `avd-quarantine` (`isAssignableToRole: false`), custom AU role (`microsoft.directory/groups/members/update`), and the Conditional Access policy targeting **Azure Virtual Desktop** and **Windows Cloud Login** while **excluding Azure Virtual Desktop Azure Resource Manager Provider** and break-glass accounts.
- **`avd-assess`:** Keep endpoint-protection posture in `dimensions.yaml` (`agents.av-edr-present`, `agents.coexistence`, `agents.edr-onboarding`, `enableDefenderForCloud`) and add containment-readiness checks.
- **`avd-second-brain`:** Add C8 incident categories (`token-replay`, `unrecognized-host`, `leaked-reg-token`, `unauthorized-avd-rbac`) and C10 containment outcome tracking.
- **`avd-intune`:** Remains separate for endpoint security policy delivery (Defender AV, ASR rules, FSLogix/AVD exclusions).

---

## 6. Assumptions to Verify Live Before Step 3

Before implementing this spec in a sandbox:
1. An app-only Graph token for a managed identity with **no** Graph app role, holding `Directory Readers` + an AU-scoped directory role, is honored for `POST /groups/{id}/members/$ref` and `DELETE .../members/$ref`.
2. A custom Entra role with only `microsoft.directory/groups/members/update` is assignable at AU scope to a service principal and suffices for member updates.
3. Whether AU-scoped User Administrator (or a custom role) permits `revokeSignInSessions` from an app-only token.
4. Measured latency from `avd-quarantine` membership to a refused AVD connection, with and without `Remove-AzWvdUserSession`.
5. Whether `sessionhosts/write` accepts a body containing only `allowNewSession`, and whether it can be further constrained.
6. `alertsmanagementresources` schema and lag as a watchdog corroboration join source.
