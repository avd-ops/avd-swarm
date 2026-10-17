# Governance, the external observer and the kill switch

- **Status:** Proposed ([decision 0002](decisions/0002-governance-and-kill-switch.md))
- **Part of:** [spec.md](spec.md) §6a. Modeled on the second brain's kill switch (brain §4.5.1, EX-0004), with one addition: an observer that avd-swarm doesn't control.

## 1. Why

Approval of each plan (spec §5) stops a bad change from running. It doesn't stop the platform itself from misbehaving: agents looping and burning tokens, a write identity used outside an approved plan, a flood of plans that wears the approver down, or a compromised component. The failsafe mechanism catches those, from outside the part that's failing, and fails closed.

## 2. Three parts

```
                ┌──────────────────────── avd-swarm ─────────────────────────┐
 operator ──►   │ avd-buddy → coordinator → specialists → propose_change      │
                │                                              │              │
                │                    ┌─────── Governance gate ─▼────────┐     │
                │                    │ policy checks on every plan      │     │
                │                    │ observer verdict (if required)   │     │
                │                    │ switch position                  │     │
                │                    └──────────────┬───────────────────┘     │
                │                         approve environment → job → verify │
                └───────┬──────────────────────────────────────────▲─────────┘
                        │ event stream (every step)                │ trip / veto
                        ▼                                          │
                 External observer(s) ─────────────────────► Kill switch
                 (outside avd-swarm's trust boundary)     (separate identity)
                        ▲                                          ▲
                 Watchdog (deterministic rules) ───────────────────┘
                 Person (avd-buddy button, Cloud Shell, GitHub) ───┘
```

### 2.1 Governance gate

Every plan passes the gate before it is shown for approval, and again in the job before it runs. The gate is deterministic code with the policy in Git (`governance/policy.yml`, a protected path). No model is involved.

| Check | Example rule |
|---|---|
| Action allowlist | Only actions in the catalog, at the level the catalog allows |
| Scope | Targets inside the registered resource groups and the AVD groups; never tenant-wide objects |
| Blast radius | At most one host per pool at a time; at most N users per group change |
| Rate | At most N plans per hour and N writes per day, per action and overall |
| Time windows | No host actions in business hours unless the plan says why |
| Budget | Refuse plans whose cost estimate crosses the remaining budget |
| Switch position | Refuse everything the current position forbids (§2.3) |
| Observer verdict | When the policy requires it, wait for the observer to allow the plan (§2.2) |

A refusal tells the operator which rule refused and why. Changing a rule is a PR; a PR an agent authored that touches `governance/` fails CI.

### 2.2 External observer hook

An observer is any system outside avd-swarm's trust boundary that watches what the platform does and can stop it. avd-swarm doesn't run it, can't change its configuration, and can't silence it.

**What avd-swarm sends.** Every step goes out as a signed event (CloudEvents, JSON) to each registered observer, before the next step starts:
- `conversation.turn`: operator, agent, tools called, token counts. No message text unless the observer is set to receive it.
- `plan.proposed`, `plan.gated` (with each rule's result), `plan.approved`, `plan.rejected`, `plan.expired`
- `action.started`, `action.verified`, `action.failed`, `action.rolled-back`
- `switch.changed`, `heartbeat` (every 5 minutes)

**What an observer can do back.** Two calls, both authenticated with the observer's own identity:
- `verdict(planId, allow | deny, reason)`: required for plans the policy marks `observer: required` (by default: anything touching identities, groups or more than one host). No verdict before the plan expires counts as deny.
- `trip(position, reason)`: moves the kill switch (§2.3). An observer can trip; it can never reset.

**Transports.** Event Grid custom topic (default), a webhook, or both. The observer registry (`governance/observers.yml`, protected) lists each observer, its transport, the events it gets, and whether its verdicts are required.

**Failing closed.** If an observer with required verdicts stops acknowledging events or heartbeats for 15 minutes, the switch moves to `halt-actions` until it is back. Reads keep working.

**Possible observers**, in the order worth building:
1. The **watchdog** (§2.4), the minimum and always on.
2. A **second-opinion model** on a different provider or account from the agents, reviewing each plan against the conversation that produced it. It holds no tools, only `verdict` and `trip`.
3. A **person's channel**: events to email, Teams or a phone notification, with trip links.
4. A **third-party monitor or SIEM** (Sentinel, a SOAR playbook) that can call `trip`.

### 2.3 Kill switch

| Position | Effect | Reads | Agents | Writes |
|---|---|---|---|---|
| `run` | Normal | Yes | Yes | By approval |
| `halt-actions` | No changes. Plans can be drafted but not approved | Yes | Yes | No |
| `halt-agents` | Agents disabled; avd-buddy shows status and Cloud Shell blocks only | Yes, by people | No | No |
| `halt-all` | Everything stopped; avd-buddy shows a static notice | No | No | No |

**How it enforces, in layers,** so that no single broken part keeps it from working:
1. **Flag:** a tag on the swarm's resource group, read by the gate, the agents' tool layer and every job before each step.
2. **Credentials:** at `halt-actions` and above, the federated credential of the `approve` identity is deleted, so Azure issues no write token whatever the code does.
3. **Roles:** at `halt-agents` and above, the agents' role assignments are removed and the Foundry agents disabled.
4. **Front end:** at `halt-all`, avd-buddy's API is stopped.

**Who can trip it:** a person (a button in avd-buddy, a Cloud Shell block, or a GitHub workflow), any registered observer, and the watchdog. **No agent can trip or reset it.** An agent can recommend a trip, and a person or an observer decides.

**Who can reset it:** only a person, through `swarm-resume.yml` in the `resume` GitHub Environment, which recreates the credentials and roles and clears the flag. A reset outside that workflow trips the switch again at the previous position.

**Independence:** the switch runs as its own Consumption Logic App with its own managed identity, holding exactly the roles it needs (Tag Contributor on the swarm's resource group, Managed Identity Contributor on the `approve` identity, User Access Administrator scoped to the agents' role assignments). It doesn't depend on Foundry, the agents, avd-buddy or any private endpoint. A failed trip pages the owner.

### 2.4 Watchdog rules

Deterministic triggers, evaluated by Azure Monitor alerts and the switch's Logic App, never by a model:

| Trigger | Position |
|---|---|
| A write by the `approve` identity with no matching approved plan (Activity Log) | `halt-all` |
| A write by any swarm identity outside the registered scope | `halt-all` |
| Daily token or cost cap reached | `halt-agents` |
| More than N plans proposed in an hour, or N rejections in a row | `halt-actions` |
| Two failed verifications or rollbacks within 24 hours | `halt-actions` |
| A required observer silent for 15 minutes | `halt-actions` (clears itself when it's back) |
| `governance/` or the observer registry changed on `main` without the owner's review | `halt-actions` |
| The switch flag changed outside `swarm-resume.yml` | Trips again at the previous position |

## 3. Relationship to the second brain

When a brain is deployed alongside avd-swarm, each switch also listens to the other: a brain trip to `halt-actions` or `halt-all` moves avd-swarm to `halt-actions`, so no brain playbook can be proposed while the brain is stopped. avd-swarm tripping doesn't stop the brain's cost safeties (EX-0001, EX-0002).

## 4. Open questions

- **GQ1** Which events may carry conversation text to an observer, and is the second-opinion observer allowed to see user names?
- **GQ2** Is a 15-minute observer silence right for a personally paid tenant where nights are quiet?
- **GQ3** Should tenant-swarm share one kill switch with avd-swarm, or have its own?
