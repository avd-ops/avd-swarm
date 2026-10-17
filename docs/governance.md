# Governance, the external observer and the kill switch

- **Status:** Proposed ([decision 0002](decisions/0002-governance-and-kill-switch.md))
- **Part of:** [spec.md](spec.md) §6a. Modeled on the second brain's kill switch (brain §4.5.1, EX-0004), with one addition: an observer that avd-swarm doesn't control.

## 1. Why

Approval of each plan (spec §5) stops a bad change from running. It doesn't stop the platform itself from misbehaving: agents looping and burning tokens, a write identity used outside an approved plan, a flood of plans that wears the approver down, or a compromised component. The failsafe mechanism catches those, from outside the part that's failing, and fails closed.

It guards against two threats:
- **The system turned against itself:** prompt injection through logs or tool output, a compromised identity or component, or tampering with the guardrails, so that avd-swarm proposes or makes changes nobody intended.
- **Runaway failure:** loops, floods of plans or actions, cascading changes, or a cost spiral, with no attacker involved.
- **Runaway spend**, its own threat because the sandboxes are billed to personal and work cards: a run that costs more than its estimate, a teardown that doesn't finish, or resources nobody owns left running. Detail in `spend.md` (spec §7a).

The observer watches the change workflow and the security signals for both. It doesn't need the conversation to do that, so it never receives conversation text.

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

Write the policy in Rego or Cedar and evaluate it through Microsoft's Agent Governance Toolkit (MIT), so the same policy refuses early inside the agents and decides with authority in the job. The toolkit's own kill switch runs inside the agent process, so it doesn't replace §2.3. Borrow Azure SRE Agent's allow, ask and deny rule format.

| Check | Example rule |
|---|---|
| Action allowlist | Only actions in the catalog, at the level the catalog allows |
| Scope | Targets inside the registered resource groups and the AVD groups; never tenant-wide objects |
| Blast radius | At most one host per pool at a time; at most N users per group change |
| Rate | At most N plans per hour and N writes per day, per action and overall |
| Time windows | No host actions in business hours unless the plan says why |
| Budget | Refuse a plan whose spend block fails the spend gate ([spend.md](spend.md) §3.2): over the per-run cap, over the remaining monthly cap, any unpriced line, or no cap set |
| Switch position | Refuse everything the current position forbids (§2.3) |
| Observer verdict | When the policy requires it, wait for the observer to allow the plan (§2.2) |

A refusal tells the operator which rule refused and why. Changing a rule is a PR; a PR an agent authored that touches `governance/` fails CI.

### 2.2 External observer hook

An observer is any system outside avd-swarm's trust boundary that watches what the platform does and can stop it. avd-swarm doesn't run it, can't change its configuration, and can't silence it.

**What avd-swarm sends.** Change-workflow and security events only, each signed (CloudEvents, JSON), sent before the next step starts. **No conversation text, no prompts, no model output**, so a prompt injection can't reach the observer either.

| Group | Events | What they carry |
|---|---|---|
| Change workflow | `plan.proposed`, `plan.gated` (each rule's result), `plan.approved`, `plan.rejected`, `plan.expired`, `action.started`, `action.verified`, `action.failed`, `action.rolled-back` | Plan ID and hash, action, targets as resource IDs, parameters, who approved, timings, outcome |
| Security | `identity.token-issued` (write identity), `write.observed` (any write by a swarm identity, from the Activity Log), `role.changed`, `credential.changed`, `guardrail.changed` (`governance/`, prompts, allowlists, the action catalog), `gate.refused` | Identity, scope, the matching plan ID or "none" |
| Activity counters | `agents.activity` every 5 minutes | Tool calls, plans proposed and token use per agent, as counts only |
| Liveness | `switch.changed`, `heartbeat` (every 5 minutes) | Switch position, component health |

The counters let an observer see a runaway (an agent calling tools a thousand times an hour) without seeing what it was asked.

**What an observer can do back.** Two calls, both authenticated with the observer's own identity:
- `verdict(planId, allow | deny, reason)`: required for plans the policy marks `observer: required` (by default: anything touching identities, groups or more than one host). No verdict before the plan expires counts as deny.
- `trip(position, reason)`: moves the kill switch (§2.3). An observer can trip; it can never reset.

**Corroborate, don't trust (R9).** A compromised avd-swarm can sign false events, so an observer treats them as claims. It checks them against Azure's own records, the Activity Log and the Entra audit and sign-in logs for every swarm identity, which avd-swarm can't edit. The watchdog's `halt-all` triggers read only those records.

**Audit trail (R12).** Every event is also written to immutable blob storage with a one-year time-based retention policy that no swarm identity can delete or shorten.

**Transports.** Event Grid custom topic (default), a webhook, or both. The observer registry (`governance/observers.yml`, protected) lists each observer, its transport, the events it gets, and whether its verdicts are required.

**Failing closed.** If an observer with required verdicts stops acknowledging events or heartbeats for 15 minutes, the switch moves to `halt-actions` until it is back. Reads keep working.

**Possible observers**, in the order worth building:
1. The **watchdog** (§2.4), the minimum and always on.
2. A **second-opinion model** on a different provider or account from the agents, reviewing each structured plan against the policy and recent plans. It never sees the conversation, so text that steered the agents can't steer it. It holds no tools, only `verdict` and `trip`.
3. A **person's channel**: events to email, Teams or a phone notification, with trip links.
4. A **third-party monitor or SIEM** (Sentinel, a SOAR playbook) that can call `trip`.

**Rejected:** Copilot Studio's external security webhook, the nearest shipped equivalent. It sends the chat history and fails open after one second, the opposite of both choices here.

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
4. **Conditional Access:** at `halt-agents` and above, a policy blocks the agents' Entra Agent IDs from getting tokens at all. Turning that policy on needs a permission that can edit any Conditional Access policy, so the switch's Logic App never holds it (R5): this layer is added by the person's Cloud Shell trip block, and by the watchdog only through an alert to the person.
5. **Front end:** at `halt-all`, avd-buddy's API is stopped.

**Stopping spend is never blocked** ([spend.md](spend.md) §3.3). Every position, `halt-all` included, still allows Stop, Lock and teardown plans for resources inside a run, still approved by a person. Because `halt-actions` and above delete the `approve` credential, those plans run through a separate `stop-spend` environment and identity. Its custom role can only deallocate, stop and delete, and only resources tagged with a run ID. The switch never removes it, and the watchdog trips `halt-all` if it writes anything else.

**Who can trip it:** a person (a button in avd-buddy, a Cloud Shell block, or a GitHub workflow), any registered observer, and the watchdog. **No agent can trip or reset it.** An agent can recommend a trip, and a person or an observer decides.

**Who can reset it:** only a person, by running a self-contained Cloud Shell block as themselves (R6). It recreates the credentials and roles with the person's own rights and then clears the flag. No standing identity can recreate credentials, because that identity would be the best target in the system. A flag cleared while any layer is still missing trips the switch again at the previous position.

**Drill (R13).** Once a month, in a test environment, a workflow trips each position, checks that every layer took effect, and resumes. A failed drill blocks deployments of S2 and later.

**Independence:** the switch runs as its own Consumption Logic App with its own managed identity, holding custom roles that can only take capability away (R5): tag write on the swarm's resource group, `federatedIdentityCredentials/delete` on the `approve` identity, and `roleAssignments/delete` scoped to the agents' assignments. It can't create a credential or grant a role. It doesn't depend on Foundry, the agents, avd-buddy or any private endpoint. A failed trip pages the owner.

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
| `spend.cap.reached`: month to date reached the monthly cap | `halt-actions`, except plans that stop spend |
| `spend.teardown.failed`: a run's teardown didn't finish | `halt-actions` for new runs in that sandbox |
| One operator's reads spike far above their usual rate (a stolen avd-buddy session) | `halt-agents` (R15) |
| The switch flag cleared while a layer is still missing | Trips again at the previous position |

## 3. Relationship to the second brain

When a brain is deployed alongside avd-swarm, each switch also listens to the other: a brain trip to `halt-actions` or `halt-all` moves avd-swarm to `halt-actions`, so no brain playbook can be proposed while the brain is stopped. avd-swarm tripping doesn't stop the brain's cost safeties (EX-0001, EX-0002). Each resume clears only its own switch; while the brain is still halted, avd-swarm stays at `halt-actions` and avd-buddy says why (R16).

## 4. Open questions

- ~~**GQ1** Which events may carry conversation text?~~ **Decided 2026-10-17:** none. Observers get change-workflow and security events only.
- **GQ4** Do plans that touch users carry user principal names to the observer, or pseudonyms?
- **GQ2** Is a 15-minute observer silence right for a personally paid tenant where nights are quiet?
- **GQ3** Should tenant-swarm share one kill switch with avd-swarm, or have its own?
