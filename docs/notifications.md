# Smart Notifications, Escalation & Multi-Platform Approvals

> Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.

**Status:** Accepted ([decision 0011](decisions/0011-notifications-and-multi-platform-approvals.md)). Implemented in [governance/notifications.yml](../governance/notifications.yml), [notifications/](../notifications/), [gate/avd_gate/cosign.py](../gate/avd_gate/cosign.py), and [buddy/server.py](../buddy/server.py).

This specification defines a built-in, PagerDuty-style notification, escalation, and multi-platform approval router for `avd-swarm`. It delivers actionable alerts across mobile push, Microsoft Teams, email, and SMS/voice while preserving every invariant in [governance.md](governance.md) and [spec.md](spec.md) §5: no standing write credentials, no estate names or parameters on external channels (R17), and no bypass of `avd_gate` or the GitHub Environment `approve` gate.

---

## 1. Goals and Non-Goals

### Goals
- **One smart router for all swarm signals:** Route plan approvals, kill-switch transitions, spend alerts, and security findings (`decoy.tripped`, `injection.suspected`) with urgency tiers, deduplication, and timed escalation.
- **Multi-platform delivery in v1:** Support `avd-buddy` Web Push, Microsoft Teams Adaptive Cards, Email, and SMS/Voice.
- **Two-phase approval enablement:**
  - **Phase 1 (v1 — Option 1):** Deep-link approvals (`avd-buddy` + GitHub Actions), a one-tap Kill Switch `trip` button, and optional signed external **co-signatures** (e.g., Teams or ServiceNow).
  - **Phase 2 (later — Option 2):** Direct ChatOps approval via an authenticated bot calling GitHub's Deployment Review API, without bypassing `avd_gate`.
- **Survive `halt-all` and protect `stop-spend`:** Keep critical alerts and `stop-spend` approval notifications running even when `avd-buddy`'s Container App is stopped.
- **Near-zero idle cost:** Scale to zero on Consumption hosting (pennies/month idle; SMS/voice metered only when triggered).

### Non-Goals
- Full shift-rotation / calendar scheduling in Git (v1 uses a single primary + fallback list).
- Sending targets, host names, resource IDs, user UPNs, or chat transcripts over external notification channels.
- Replacing the GitHub Environment `approve` OIDC credential as the sole holder of Azure write roles.

---

## 2. Architecture & Hosting

```
avd-swarm events (plan.gated, switch.changed, budget.alert, security)
        │
        ▼  Event Grid custom topic (governance.md §2.2)
┌──────────────────────────────────────────────────────────┐
│  Notification & Escalation Router                        │
│  (Logic Apps Consumption / Azure Functions Consumption)  │
│  • Deduplicates by planId / incident key                 │
│  • Evaluates urgency tier & quiet hours                  │
│  • Tracks ack / escalation timers in Table Storage       │
│  • Reads webhook/ACS/VAPID secrets via Managed Identity  │
└───┬──────────────┬──────────────┬──────────────┬─────────┘
    │              │              │              │
    ▼              ▼              ▼              ▼
Web Push        Teams          Email         SMS / Voice
("needs you")   (Redacted      (Redacted     (ACS / Twilio,
                Card +         Digest /      critical or
                Deep-Link +    Escalation)   escalated)
                Trip + Co-Sign)
```

- **Why Consumption Logic Apps / Functions instead of `avd-buddy`'s Container App:** At `halt-all`, the kill switch stops `avd-buddy`'s Container App ([governance.md §2.3](governance.md#L113)). Running the router on an independent Event Grid–triggered Consumption host ensures `halt-all` notifications and `stop-spend` approval links still reach the operator.
- **State store:** Lightweight Azure Table Storage tracks open incident keys, last-notified timestamps, escalation step, and acknowledgement state so timers survive scale-to-zero.
- **Delivery resilience:** If a primary channel returns HTTP `4xx`/`5xx` or times out, the router fails over to the next channel **immediately** rather than waiting for the escalation timer. Event Grid dead-letter events and a periodic `router.heartbeat` are monitored externally so silent delivery failures page the operator.

---

## 3. Urgency Tiers, Deduplication & Escalation

Routing rules live in `governance/notifications.yml` (a **protected path**; agent-authored PRs cannot weaken rules or disable critical alerts).

| Tier | Trigger Events | Default Channels | Quiet Hours | Escalation |
|---|---|---|---|---|
| **Critical** | `switch.changed` (`halt-*`), `decoy.tripped`, `injection.suspected`, `budget.alert`, `stop-spend` pending | Web Push + Teams + Email + SMS/Voice | **Bypassed** (pages immediately) | Escalates to fallback contact after `ackTimeoutMinutes` (default: 5 min) |
| **Actionable** | `plan.gated` (passed `gate` job, awaiting `approve` environment) | Web Push + Teams | Respects quiet hours (queues for business hours unless marked `urgent`) | Escalates to Email → SMS/Voice after `escalateAfterMinutes` (default: 15 min) if unacknowledged and plan has not expired |
| **Info** | `action.verified`, `plan.rejected`, `plan.expired` | Teams ops channel / Email digest | Respects quiet hours | None |

### Deduplication, Quotas & On-Call Model (v1)
- **Deduplication:** Plan events are keyed by `(eventType, planId)`. Security-noise events (`injection.suspected`, `decoy.tripped`) are collapsed by `(eventType, agentId)` over a configurable cooldown window (default: 15 minutes) so an agent loop or inbound alert storm cannot flood SMS/Voice.
- **Reserved emergency quota:** `critical` alerts (`switch.changed` to `halt-*`, `budget.alert`, `stop-spend`) use a **separate, reserved rate-limit bucket** that `actionable` or security-noise floods cannot exhaust.
- **Primary + Fallback List:** `governance/notifications.yml` defines a `primary` operator contact and an ordered `fallback` list (no shift-rotation engine in v1).
- **Auto-Cancel on Resolution:** When `plan.approved`, `plan.rejected`, or `plan.expired` arrives, any pending escalation timer for that `planId` is cancelled immediately.

---

## 4. Channels & Redaction (Rule R17)

Every external notification channel sits outside the Entra/GitHub trust boundary and must obey **R17**:

- **Never include:** host names, resource IDs, host pool names, user UPNs, parameters, `why` evidence text, or conversation history.
- **Allowed fields:** `planId`, short `planHash` prefix (first 8 chars), catalog `action` name (e.g., `host.drain`), urgency tier, expiry countdown, and deep links.
- **Deep-link & unfurl safety:** Deep links carry **only `planId`** (e.g., `https://<buddy-host>/plans/<planId>`, never query parameters with targets or actions), and `avd-buddy` enforces Entra Easy Auth before returning any HTML `<meta>` or OpenGraph tags so chat/email link-unfurlers cannot leak metadata.

### v1 Channels
1. **`avd-buddy` Web Push:** Sends only the fixed string `"avd-buddy needs you"` ([buddy-app.md §7](buddy-app.md#L92)). Tapping opens `avd-buddy`, which displays the full plan card after Entra sign-in.
2. **Microsoft Teams (Incoming Webhook / Adaptive Card):** Posts a redacted card with `planId`, `action`, `planHash` prefix, urgency, expiry, and action buttons (`Review in avd-buddy`, `Approve in GitHub`, `Co-sign`, `Trip Kill Switch`).
3. **Email (Azure Communication Services or Logic App):** Sends redacted HTML/text summary with the same deep links; used for escalation and digests.
4. **SMS / Voice (Azure Communication Services or Twilio):** Short redacted alert (`"avd-swarm [CRITICAL]: <planId> (<action>) awaiting action. Check avd-buddy or GitHub."`) used only for `critical` events or unacknowledged `actionable` escalations.

---

## 5. Two-Phase Approval Model

### Phase 1 (v1 — Option 1: Deep-Link + One-Tap Kill Switch + Optional Co-Sign)
In v1, external platforms **never** hold GitHub `actions:write` or Deployment Review permissions:

1. **Deep-Link Approval:** Tapping `Review in avd-buddy` opens the plan card behind Entra Easy Auth; tapping `Approve in GitHub` opens the paused `approve` environment run in GitHub Actions where the operator completes approval and unlocks the OIDC write token.
2. **One-Tap Kill Switch (`trip`):** Every Teams card and email links to an Entra Easy Auth–protected confirmation page (or verified Teams Bot action) that issues an authenticated `POST` to the observer `trip` endpoint ([governance.md §2.2](governance.md#L86)). It **never** uses a `GET` endpoint or URL-embedded token (preventing email Safe Links / pre-fetch self-DoS and link-forward replay), and it can only halt—never reset.
3. **Optional Signed Co-Signature:** External channels (Teams Bot action, ServiceNow Change Request) can post a signed **co-signature** binding `(planId, fullPlanHash, signerOid, channel, issuedAt, expiresAt)` to the plan store.
   - `avd_gate` verifies the full SHA-256 `planHash` (never the 8-char display prefix), checks `expiresAt`, and validates `signerOid` against an authorized approver allowlist and the channel's cryptographic key or Entra token—rejecting static unauthenticated webhooks or stale hashes.
   - When `governance/policy.yml` or an adapter (e.g., `servicenow`) marks an action as requiring a co-signature, `avd_gate` requires this verified co-signature before `apply` proceeds.
   - In solo mode, co-signing is optional by default; the GitHub Environment `approve` gate remains the authoritative unlock for the Azure write token.

### Phase 2 (Later — Option 2: Direct ChatOps Approval Proxy)
Once Phase 1 is proven in practice, an authenticated ChatOps bot (Teams/Slack) can enable one-click approval directly from chat:
- **Per-user identity binding:** The bot authenticates the clicking user via Entra ID and uses **per-user GitHub OAuth** (a user-to-server token so GitHub records the human's own identity and enforces required-reviewer separation) or a GitHub **Custom Deployment Protection Rule**—**never** a shared bot installation token.
- **Pre-checks:** Verifies the kill switch is `run` and `fullPlanHash` matches the current plan.
- **Execution:** Calls GitHub's Deployment Review API (`POST /repos/{owner}/{repo}/actions/runs/{run_id}/pending_deployments`) to approve the `approve` environment run.
- **Unchanged safety floor:** `avd_gate apply` still re-reads the kill switch, re-reads live Azure state, rebuilds the plan hash, and refuses on any drift before writing.

---

## 6. Kill-Switch & `stop-spend` Behavior

| Kill-Switch Position | Router Behavior | Approvals Allowed |
|---|---|---|
| `run` | Full routing & escalation | Standard `approve` + `stop-spend` |
| `halt-actions` / `halt-agents` | Notifies switch change (`critical`); suppresses standard `plan.gated` approvals | `stop-spend` only |
| `halt-all` | `avd-buddy` is stopped; Consumption router stays up, sends `halt-all` page and routes `stop-spend` deep links directly to GitHub Actions | `stop-spend` only |

---

## 7. Security & Cost Guardrails

- **Secrets:** Webhook URLs, VAPID private key, ACS/Twilio credentials, and co-sign verification keys live in Azure Key Vault and are read by the router's managed identity. Nothing is committed to Git.
- **Protected configuration:** `governance/notifications.yml` is a protected path alongside `governance/policy.yml` and `governance/observers.yml`.
- **Cost:** Event Grid + Logic Apps/Functions Consumption + Table Storage cost pennies/month at idle. SMS/Voice incurs per-message/per-minute charges only when `critical` or escalated alerts fire, bounded by deduplication, separate emergency vs. actionable per-hour rate caps in `governance/notifications.yml`, and a provider-side monthly spend cap on ACS/Twilio.
