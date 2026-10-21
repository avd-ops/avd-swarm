# Governance, the external observer and the kill switch

- **Status:** Proposed ([decision 0002](decisions/0002-governance-and-kill-switch.md))
- **Part of:** [spec.md](spec.md) §6a. Modeled on the second brain's kill switch (brain §4.5.1, EX-0004), with one addition: an observer that avd-swarm doesn't control.

## 1. Why

Approval of each plan (spec §5) stops a bad change from running. It doesn't stop the platform itself from misbehaving: agents looping and burning tokens, a write identity used outside an approved plan, a flood of plans that wears the approver down, or a compromised component. The failsafe mechanism catches those, from outside the part that's failing, and fails closed.

It guards against two threats:
- **The system turned against itself:** prompt injection through logs or tool output, a compromised identity or component, or tampering with the guardrails, so that avd-swarm proposes or makes changes nobody intended.
- **Runaway failure:** loops, floods of plans or actions, cascading changes, or a cost spiral, with no attacker involved.
- **Runaway spend**, its own threat because the sandboxes are paid for out of pocket: a run that costs more than its estimate, a teardown that doesn't finish, or resources nobody owns left running. Detail in `spend.md` (spec §7a).

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

**Limits on the toolkit, written before the spike** (red team V1-6): it evaluates `governance/policy.yml` and returns allow or deny, nothing more. Its own approval, approver quorum, kill switch and MCP gateway are never used, so its approval can never stand in for the GitHub Environment and its in-process switch can never stand in for §2.3's layers. It is pinned by version and hash, installed from a lock file or vendored, and the `approve` job runs it with no network. If it can't run offline in the Actions job, the gate stays our own code.

| Check | Example rule |
|---|---|
| Action allowlist | Only actions in the catalog, at the level the catalog allows |
| Scope | Targets inside the registered resource groups and the AVD groups; never tenant-wide objects |
| Blast radius | At most one host per pool at a time; at most N users per group change |
| Rate | At most N plans per hour and N writes per day, per action and overall |
| Time windows | No host actions in business hours unless the plan says why |
| Evidence freshness | Refuse a brain proposal (C9) whose only evidence is stale: an avd-current-state facts snapshot (brain contract C11) older than 7 days, or a forensic window (C12) that ended more than 90 days ago or that the factory's quality gate marked `low-confidence`. Limits start there. Facts are a reason to propose; targets are always resolved live ([spec](spec.md) §6d) |
| Budget | Refuse a plan whose spend block fails the spend gate ([spend.md](spend.md) §3.2): over the per-run cap, over the remaining monthly cap, any unpriced line, or no cap set |
| Switch position | Refuse everything the current position forbids (§2.3) |
| Observer verdict | When the policy requires it, wait for the observer to allow the plan (§2.2) |
| Recovery | Recovery actions write only into the subscriptions, regions and resource groups `governance/recovery-scope.yml` lists for the landing zone, into new names, with test users only, and every one but the configuration snapshot needs the observer's verdict ([recovery.md](recovery.md) §4, §6) |
| Compliance | Refuse a plan whose action may lower a Defender for Cloud control on its targets unless the policy allows it, and every such plan while the score is below its floor (§2.5) |

Answers aren't plans, so the gate doesn't see them. Their limits, how many specialists one question may fan out to and the claim check's settings ([spec](spec.md) §4a), will live in `governance/answers.yml`, protected like the policy: an agent can't widen its own fan-out or loosen the check that marks its claims.

A refusal tells the operator which rule refused and why. Changing a rule is a PR; a PR an agent authored that touches `governance/` fails CI, and a person's change that raises capability waits out the time-lock (§2.6).

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

**Audit trail (R12).** Every event is also written to immutable blob storage with a one-year time-based retention policy that no swarm identity can delete or shorten. After the year, lifecycle management moves events to the archive tier; it never deletes them (never delete the last copy, avd-second-brain decision 0016).

**Transports.** Event Grid custom topic (default), a webhook, or both. The observer registry (`governance/observers.yml`, protected) lists each observer, its transport, the events it gets, and whether its verdicts are required.

**Failing closed.** If an observer with required verdicts stops acknowledging events or heartbeats for 15 minutes, the switch moves to `halt-actions` until it is back. Reads keep working.

**Observers in v1** (red team V1-18). v1 has no observer outside avd-swarm's trust boundary: the watchdog runs in the switch's own Logic App, and the person's channel is a notification. No v1 plan needs a verdict (drain and restart touch one host and no identity), so the 15-minute silence rule and required verdicts are exercised first in step 4's red team. A budget's action group that calls `trip` is registered as an observer with its own managed identity, and the trip endpoint refuses unauthenticated calls (V1-37).

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
5. **Front end and published tools:** at `halt-all`, avd-buddy's API is stopped, and so is the tool layer's Container App when it is published as an MCP server (the Logic App's `containerApps/stop/action` covers both). At `halt-agents` and above, the published tool layer checks the flag on every call and refuses every caller that isn't a person: Azure SRE Agent, Foundry's Toolbox, Claude Code (red team V1-3). The drill checks that an MCP call from each registered connector is refused at each position.
6. **Model calls, on every provider** (R21). Removing roles and disabling Foundry agents doesn't stop agents that call Claude through Anthropic's API (spec §6): that path signs in with a managed identity's Entra token for an audience app, which needs no Azure role, and Conditional Access for workload identities doesn't apply to managed identities. So at `halt-agents` and above:
   - **the model client checks the flag before every call** and refuses when the position forbids agents or the flag can't be read. It is the only way agent code reaches a model (spend.md §3.7), so this is also where the token meter sits;
   - **the switch stops the compute that calls the model:** its Logic App stops the agents' Container App (a custom role with only `Microsoft.App/containerApps/stop/action`), which ends in-flight conversations, as well as disabling the Foundry agents;
   - **the person's trip block cuts the provider credential:** it archives the Anthropic federation rule the agents' identity uses, so no process can exchange a new token. The rule is created with a 10-minute token lifetime (`token_lifetime_seconds: 600`), so an access token issued before the trip stops working within 10 minutes. The Logic App can't do this itself: the Anthropic Admin API needs an `org:admin` credential, the kind of standing capability this design never gives a machine (R5). The watchdog pages the person to run it;
   - **the provider cap stays as the outer wall:** a dedicated Anthropic workspace with a monthly spend limit and token rate limits (spend.md §3.7), which no swarm identity can raise.

   The brain's later model calls (Tier 3's Critic and Concierge agents) go through the same client, which avd-swarm owns and the brain consumes as a pinned package, never a copy. In v1 the brain's only model call is Tier 1's Diagnose, which runs as Claude Code in GitHub Actions with its own client, so that workflow's first step reads the flag and exits at `halt-agents` or above or when it can't read it; its spend is capped by the action's max turns and a workspace limit (red team V1-8; brain contract C10).

**Stopping spend is never blocked** ([spend.md](spend.md) §3.3). Every position, `halt-all` included, still allows Stop, Lock and teardown plans for resources inside a run, still approved by a person. Because `halt-actions` and above delete the `approve` credential, those plans run through a separate `stop-spend` environment and identity. Its custom role can only deallocate, stop and delete. Azure RBAC conditions don't apply to those control-plane actions, so the role can't be limited by tag (red team V1-14): it is limited by **scope**. A run deploys into its own run resource groups, the stop-spend role is assigned on those groups only, and the run store lives in its own resource group with a `CanNotDelete` lock. Anything tagged `avd-evidence: keep` is protected by an Azure Policy `denyAction` on `delete`, which no swarm identity can change (no swarm identity holds policy write). The tags stay as evidence for the leftovers check. The switch never removes the stop-spend identity, and the watchdog trips `halt-all` if it writes outside the run resource groups.

**Who can trip it:** a person (a button in avd-buddy, a Cloud Shell block, or a GitHub workflow), any registered observer, and the watchdog. **No agent can trip or reset it.** An agent can recommend a trip, and a person or an observer decides.

**Who can reset it:** only a person, by running a self-contained Cloud Shell block as themselves (R6). It recreates the credentials and roles with the person's own rights, creates a new Anthropic federation rule (the agents' app then reads its ID from a variable) and starts the agents' Container App, and then clears the flag. No standing identity can recreate credentials, because that identity would be the best target in the system. A flag cleared while any layer is still missing trips the switch again at the previous position.

**Drill (R13).** Once a month, in a test environment, a workflow trips each position, checks that every layer took effect (including that an agent running on Anthropic's API makes no further model call), and resumes. A failed drill blocks deployments of S2 and later.

**Independence:** the switch runs as its own Consumption Logic App with its own managed identity, holding custom roles that can only take capability away (R5): tag write on the swarm's resource group, `federatedIdentityCredentials/delete` on the `approve` identity, `roleAssignments/delete` on the AVD resource groups with a delegation condition that limits it to the agent identities' principal IDs (a role can't be scoped to particular assignments, and without the condition the switch could remove the landing zone's deploy identity or the operator; red team V1-15), and `containerApps/stop/action` on the agents' Container App. It can't create a credential or grant a role. It doesn't depend on Foundry, the agents, avd-buddy or any private endpoint. A failed trip pages the owner. The drill checks that a trip removes no assignment held by a principal that isn't a swarm agent.

### 2.4 Watchdog rules

Deterministic triggers, evaluated by Azure Monitor alerts and the switch's Logic App, never by a model:

| Trigger | Position |
|---|---|
| A write by the `approve` identity with no matching approved plan (Activity Log) | `halt-all` |
| A write by any swarm identity outside the registered scope | `halt-all` |
| A role assignment to Azure SRE Agent's identity, or any write by it (spec §3a) | `halt-all`, and page the person |
| Daily token spend cap reached, from the token meter (spend.md §3.7), never from Cost Management's delayed data | `halt-agents` |
| Model usage metered, or reported by the provider for the swarm's workspace, after `halt-agents` or above took effect | `halt-all`, and page the person to cut the provider credential |
| More than N plans proposed in an hour, or N rejections in a row | `halt-actions` |
| Two failed verifications or rollbacks within 24 hours | `halt-actions` |
| A required observer silent for 15 minutes | `halt-actions` (clears itself when it's back) |
| A protected path (`governance/`, the observer registry, the action catalog's protected fields, workflows) changed on `main` other than through the solo-mode or team-mode path (§2.6): a PR, the required checks green, and the time-lock when the change raises capability | `halt-actions` |
| A write-path workflow (an environment, `id-token: write`) ran in a public repository (spec §5b) | `halt-all` |
| `spend.cap.reached`: month to date reached the monthly cap | `halt-actions`, except plans that stop spend |
| `spend.teardown.failed`: a run's teardown didn't finish | `halt-actions` for new runs in that sandbox |
| One operator's reads spike far above their usual rate (a stolen avd-buddy session) | `halt-agents` (R15) |
| The switch flag cleared while a layer is still missing | Trips again at the previous position |
| A swarm identity creates or changes a policy exemption, a policy assignment, a Defender exemption or a Defender plan setting (Activity Log) | `halt-all` |
| Two `compliance.regressed` events within 7 days | `halt-actions` |
| A write by the recovery test identity outside a run resource group in a registered recovery scope ([recovery.md](recovery.md) §6.3) | `halt-all` |
| A recovery test past its time box with resources still billing | as `spend.teardown.failed` |

### 2.5 Compliance gate (Defender for Cloud)

A change avd-swarm makes must not leave the estate less compliant than it found it, and avd-swarm must never raise the score without fixing anything. Defender for Cloud is the measure, so the number people already watch is the number the gate enforces. Like the rest of the gate, this is code with its settings in Git (`governance/compliance.yml`, a protected path); no model decides.

**What it reads.** Azure Resource Graph's `securityresources` table, with the Reader role the jobs already hold:
- `microsoft.security/assessments`: each recommendation's status (`Healthy`, `Unhealthy`, `NotApplicable`) per resource. This is what the gate compares.
- `microsoft.security/securescores`: the subscription's secure score, for the floor.
- `microsoft.security/regulatorycompliancestandards/.../regulatorycomplianceassessments`: pass and fail counts per standard and control, for reports and C10.

The Microsoft cloud security benchmark and the secure score come with Foundational CSPM, which is free and on by default, so the gate costs nothing in the personal sandbox. Other standards (CIS, NIST, ISO 27001, PCI) need Defender CSPM, a paid plan; `compliance.yml` lists the standards the gate reads, and adding a paid one needs a cost estimate first (spend.md). Source: [Defender for Cloud Resource Graph samples](https://learn.microsoft.com/azure/defender-for-cloud/resource-graph-samples), [CSPM plans](https://learn.microsoft.com/azure/defender-for-cloud/concept-cloud-security-posture-management).

**Defender is slow; Azure Policy is the fast check.** Defender refreshes assessments on its own schedule, often hours after a change, which is too late to verify a plan. Most benchmark recommendations come from the benchmark's Azure Policy initiative, so the verify step uses policy states (`Microsoft.PolicyInsights/policyStates`) with an on-demand evaluation of the targets' resource group, and Defender confirms on its next refresh. The write identity's custom role gains `Microsoft.PolicyInsights/policyStates/triggerEvaluation/action`, nothing else.

**Three checks, at the points the gate already runs:**

| When | Check | Result |
|---|---|---|
| Gate, before approval | Snapshot every assessment on the plan's targets. Each action in the catalog declares its compliance effect: `none`, `improves <controls>` or `may-lower <controls>`. A `may-lower` plan needs the policy to allow that control and an observer verdict (§2.2). The plan shows the snapshot, so the approver sees what it touches | Refused with the rule and the control that refused it |
| Gate, score floor | While the secure score is below `floor` in `compliance.yml`, refuse every `may-lower` plan; `none` and `improves` plans still pass, and so do plans that stop spend | Refused, naming the floor and the score |
| Verify, after the job | Trigger policy evaluation, wait up to `verifyMinutes` (default 30), and compare with the snapshot. A resource that went from compliant to non-compliant on a control the plan didn't declare is a failed verification: roll back and emit `compliance.regressed` | Verified, or rolled back. A timeout marks the plan `compliance-pending`; the daily read job finishes the comparison against Defender and reports a late regression the same way |

**No gaming the score.** An exemption, a disabled policy or a switched-off Defender plan raises the score without fixing anything, so no swarm identity holds `Microsoft.Authorization/policyExemptions/write`, `policyAssignments/write`, or any `Microsoft.Security` write. A person's exemption is fine but counted on its own: reports and C10 split a score change into fixes and exemptions, so an exemption is never credited as a fix.

**Events to the observer** (§2.2): `compliance.snapshot` (plan ID, counts per control), `compliance.regressed` (plan ID, control, resource ID), and `compliance.exempted` (any exemption by anyone, from the Activity Log). Watchdog rules are in §2.4.

**Pre-approved exceptions and remediation.** A plan whose effect is `improves` is still a plan a person approves; this section only adds a reason to refuse, never a way around approval. Remediation actions (for example turning on a diagnostic setting a recommendation asks for) join the catalog like any other action, with their own verification.

### 2.6 Solo mode and team mode (R19)

avd-swarm has one maintainer and may never have a second, and so may an adopter. GitHub doesn't let an author approve their own PR, so a rule that needs "the owner's review" of the owner's own change, or two code owners, either blocks every change or gets bypassed. This section replaces those assumptions with the brain's answer (brain operations-spec §9.3): in **solo mode, time and evidence stand in for the second person.** It is weaker than a second reviewer. It protects against a rash change and a hijacked session, which gets 72 hours to be noticed, not against a determined owner, and each adopter accepts that knowingly by running in solo mode.

The mode comes from how many people are in the adopter's approvers team: one is solo mode, two or more is team mode. The project repo is always solo mode.

| Decision | Team mode | Solo mode |
|---|---|---|
| A plan | One approver who isn't the requester (the requester is the swarm's dispatch App) | Same: one approver |
| A plan using Run Command, or touching identities or more than one host | Two approvers, from environments that share no reviewers | One approver, and the plan shows the script's hash, the targets' current state and the observer's verdict |
| A guardrail change that **lowers** capability (a tighter limit, a removed action, a stricter rule) | A code owner's review | The owner merges once the guard is green |
| A guardrail change that **raises** capability: a new action, a looser limit or precondition, a new observer exemption, a workflow change, a write role | Two code owners | A **72-hour time-lock**: the required check `swarm-guard/timelock` stays pending until 72 hours after the PR's last commit, the guard posts its deterministic summary of what changed, and a pinned issue stays open for the whole window. A new commit restarts the clock |
| Resuming after the kill switch | Two people | The owner, running the reset block (§2.3) |

Branch protection in solo mode: PRs required, status checks required (the guard and the time-lock), no required approval count (GitHub can't count the owner's own review), and no bypass.

**Which changes raise capability** is decided by the guard, not the author: it compares each protected field before and after (old value to new value) and marks the change raising when any value moves toward more capability. When it can't tell, it counts as raising.

**AI reviews are advisory, in both modes.** No review by an app, a bot or a model satisfies a review requirement here, Claude Code Review and Copilot included. The guard counts only a person's approving review, and only where team mode asks for one.

**What CODEOWNERS is for.** A `CODEOWNERS` file names the owner on every protected path, so GitHub requests their review and shows it. It is not what keeps a protected path safe in solo mode; the guard's agent check, the time-lock and the watchdog's merged-around-the-rules trigger (§2.4) are.

The time-lock check is built in step 3 of the build order (spec §9a), with the adopter repo it guards. Until then, no protected path holds anything that can act: there is no write path, catalog or observer to loosen.

## 3. Relationship to the second brain

There is **one kill switch, this one** ([decision 0003](decisions/0003-swarm-acts-brain-thinks.md)). The brain's own switch and watchdog (brain EX-0004) move here, since the brain no longer acts. The brain reads the position through contract C10 and obeys it: at `halt-actions` it stops proposing playbook runs, at `halt-agents` and `halt-all` it stops its model calls, which go through the same flag-checking, metered model client as the swarm's agents (§2.3 layer 6). It keeps sensing and recording at every position, so a halted estate still has its evidence. The brain's watchdog triggers that are about acting (a write with no matching plan, a write outside the landing zone, failed verifications) are rules in §2.4. The cost safeties (EX-0001, EX-0002) stay outside the switch, as [spend.md](spend.md) §3.3 already requires.

**Where the brain's old text differs, this section wins** (second red team AX-11). The brain's operations-spec §4.5.1 now points here instead of describing EX-0004. The differences, settled this way: four positions, not the brain's three (`halt-agents` is new); the daily token cap trips `halt-agents`, read from the token meter, not `halt-all` from Cost Management; the watchdog's identity holds no Managed Identity Contributor (R5); and only a person resumes, from Cloud Shell, with no standing resume workflow or identity (R6).

## 4. Open questions

- ~~**GQ1** Which events may carry conversation text?~~ **Decided 2026-10-17:** none. Observers get change-workflow and security events only.
- **GQ4** Do plans that touch users carry user principal names to the observer, or pseudonyms?
- **GQ2** Is a 15-minute observer silence right for a personally paid tenant where nights are quiet?
- **GQ5** Which standards beyond the Microsoft cloud security benchmark does the pilot estate need? Each one needs Defender CSPM, a paid plan, so it comes with a cost estimate first (§2.5).
- ~~**GQ3**~~ Withdrawn: tenant-wide work is out of scope here (spec §8).
