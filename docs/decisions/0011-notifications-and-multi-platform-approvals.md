# 0011. Smart notification router and two-phase multi-platform approvals

- **Status:** Proposed
- **Date:** 2026-11-16
- **Updates:** [0002](0002-governance-and-kill-switch.md), [0010](0010-avd-buddy-container-app-hosting.md), [governance.md](../governance.md) §2.2, [buddy-app.md](../buddy-app.md) §7.

## Context

Today, `avd-swarm` relies on GitHub Actions notifications for paused `approve` environment runs, with `avd-buddy` Web Push designed as an opt-in, empty-payload convenience ([buddy-app.md](../buddy-app.md) §7) and "a person's channel" sketched as a future observer ([governance.md](../governance.md) §2.2). There is no built-in routing, deduplication, escalation chain, or multi-platform approval experience across mobile push, Microsoft Teams, email, and SMS/voice.

At the same time, any notification and approval layer must preserve `avd-swarm`'s core security invariants: no standing Azure write credentials outside the GitHub Environment `approve` OIDC identity, no estate names or parameters on external channels (R17), and continued delivery of `halt-all` and `stop-spend` alerts even when `avd-buddy` is stopped.

## Decision

- **Add a built-in, event-driven Notification & Escalation Router** ([notifications.md](../notifications.md)) subscribed to `avd-swarm`'s Event Grid / observer stream, hosted on **Azure Logic Apps Consumption or Azure Functions Consumption** with Table Storage for escalation timers.
- **Support four v1 channels** (`avd-buddy` Web Push, Microsoft Teams Adaptive Cards, Email, and SMS/Voice) using **tiered urgency (`critical`, `actionable`, `info`), deduplication, reserved emergency quotas, immediate delivery failover, and timed escalation** across a primary + fallback contact list in `governance/notifications.yml` (protected path).
- **Enforce strict R17 redaction** on all external channels: only `planId`, short `planHash` prefix, `action` name, urgency, expiry, and `planId`-only deep links (with Easy Auth before any HTML/unfurl metadata) leave the Entra/GitHub trust boundary.
- **Adopt a two-phase approval model:**
  - **Phase 1 (v1 — Option 1):** Deep-link to `avd-buddy` / GitHub Environment `approve`, Entra-authenticated `POST` Kill Switch `trip` (`halt-actions`, never `GET`), and optional signed external **co-signatures** (bound to `fullPlanHash`, `signerOid`, and `expiresAt`) checked by `avd_gate` without granting external channels GitHub write permissions.
  - **Phase 2 (later — Option 2):** Direct ChatOps approval via **per-user GitHub OAuth** or a GitHub **Custom Deployment Protection Rule** (never a shared bot token), preserving `avd_gate apply`'s live state-hash re-check.

## Consequences

- Good: PagerDuty-style alerting and escalation without third-party SaaS licensing or standing idle compute costs (pennies/month idle; SMS/voice metered only on escalation).
- Good: Notifications for `halt-all` and `stop-spend` remain operational when `avd-buddy`'s Container App is stopped.
- Good: Phase 1 ships without expanding GitHub token scope or trusting external chat platforms with direct deployment-approval authority.
- Tradeoff: Operators must follow the deep link to GitHub (or `avd-buddy` + GitHub) to complete final approval until Phase 2's ChatOps proxy is built.
