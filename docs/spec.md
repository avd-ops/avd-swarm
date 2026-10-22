# Spec: avd-swarm, an agent-based Azure and Entra self-management platform, with avd-buddy as its chat front end

- **Status:** Proposed ([decision 0001](decisions/0001-avd-swarm.md))
- **Visibility:** public, MIT. It follows avd-landing-zone's rules: no IDs, no secrets, American English.
- **Operates:** [avd-landing-zone](https://github.com/avd-ops/avd-landing-zone). Decision and lesson numbers without a repo name are that repo's.
- **Connects to:** the second brain ([avd-second-brain](https://github.com/avd-ops/avd-second-brain), [operations spec](https://github.com/avd-ops/avd-second-brain/blob/main/docs/operations-spec.md)), its intelligence layer ([decision 0003](decisions/0003-swarm-acts-brain-thinks.md)). Section references like "brain §4.5" point there.

## 1. Summary

avd-swarm is an agent-based platform for managing Azure and Entra ID without the portals, starting with AVD. A coordinator agent answers questions and hands work to specialist agents covering the whole AVD lifecycle: readiness, deployment, hosts, images, users and access, and cost.

**avd-buddy** is its chat front end (not built yet): the page an operator talks to from any browser, iPad included. avd-buddy holds no logic of its own. It signs the operator in, relays the conversation to the coordinator, and shows answers, plans and approval links. Other front ends, such as Teams or Claude Code through MCP, can talk to the same agents.

The agents read freely through allowlisted tools. They change nothing themselves: a change is always a plan that a person approves in a GitHub Environment.

**Where the value is** ([prior art](prior-art.md)). Chat over Azure and read tools are becoming commodity: Azure Copilot, Azure SRE Agent, the Azure MCP Server. None of them combines per-plan approval outside the chat, no standing write credential, a deterministic gate, an external observer and a layered kill switch. That governed write path is what avd-swarm builds; for reads it builds on Microsoft's open-source pieces (Agent Framework, the Azure MCP Server).

**An open-source core with optional adapters** ([decision 0009](decisions/0009-open-source-core-optional-adapters.md), [core-and-adapters.md](core-and-adapters.md)). Everything avd-swarm needs to do its job is in this repo under MIT: the agents, investigation, runbooks and the check runner, the model and tool gateway, the gate and the kill switch. Paid or closed Microsoft services (Azure SRE Agent, Logic Apps Automation, API Management's AI gateway, Foundry Agent Service hosting) are **adapters** in `adapters/`: off by default, never required by CI, an eval or a v1 exit test, and turned on only after a spend estimate and the owner's go (§3b).

**avd-swarm acts, the second brain thinks** ([decision 0003](decisions/0003-swarm-acts-brain-thinks.md)). The brain senses, remembers, diagnoses, critiques and learns; avd-swarm is the actor and control layer and the only thing that changes an estate. **The brain is the why, avd-swarm is the how** ([decision 0007](decisions/0007-every-plan-says-why.md)): the brain keeps each environment's use cases and the decisions behind it from its birth (brain contract C18), and every plan here says which decision it carries out. They change together:
- The brain's playbooks reach avd-swarm as proposals (C9) and go through the same gate, approval and executor as every other change (§5). avd-swarm's action catalog is what playbooks are built from.
- avd-swarm's one kill switch stops both (governance §3). The brain's Concierge is a read tool here; the front ends are avd-swarm's.
- What avd-swarm learns and does goes back to the brain: insights, incidents and grading scores (C8), action outcomes and the switch position (C10), §6c.
- The brain's docs findings (C13) reach Scribe, which drafts the fixes as PRs, §6e.
- Every plan carries its why (event kind, use cases, the decision it carries out or drafts), and its outcome takes it back to the brain's lifecycle record (C18), §6f.
- avd-landing-zone and avd-current-state stay a self-contained pair: avd-swarm reads and changes a landing zone only through what that pair already offers (its scripts, its parameters by PR), and the pair never depends on avd-swarm or the brain.
- Facts about an estate come through the brain, redacted, never straight from their sources: its current state from avd-current-state (C11), and measured performance and sizing from a Stratusphere UX assessment when a question needs it (C12), §6d.
- avd-swarm still runs without a brain, with its own actions and evals; a brain without avd-swarm only advises.

Tenant-wide administration is out of scope; see §8.

## 2. Goals and non-goals

### Goals
- Answer operator questions about the estate with evidence: cost to date and forecast, host health, sessions, image versions, user assignments, profile issues, drift, failed checks, readiness findings.
- Turn "fix it" into a plan the operator can approve from the same screen.
- Cover the AVD lifecycle end to end: assess (the avd-assess method), inventory (avd-current-state), deploy and operate (avd-landing-zone).
- Work from a phone or tablet with nothing installed.
- Cost close to nothing when idle, and stop itself at a budget.

### Non-goals
- Any change without a person's approval of that exact change. There is no "confirm in chat" button.
- Tenant-wide administration that isn't about AVD (all of Entra, Exchange, Teams, other workloads). It is out of scope here (§8).
- Replacing Cloud Shell blocks for operators who prefer them (lesson 0015).

### The base pair stands alone (invariant)

[avd-landing-zone](https://github.com/avd-ops/avd-landing-zone) (portal-driven Cloud Shell deployment and operation) and [avd-current-state](https://github.com/avd-ops/avd-current-state) (offline fact collection and validation) are a self-contained pair. They work, and keep working, with no second brain and no avd-swarm; avd-landing-zone stays true to its original purpose. avd-swarm and the brain are built on top of them and never become something they need (the owner's rule, 2026-10-20; the brain's operations spec §3.0 says the same).

**Three questions, three repos** (the owner, 2026-10-20): avd-landing-zone is *how it is built and changed from here*, avd-current-state is *what is there now*, and avd-assess (free, MIT, being specified) is *what it should be, and why*. Together those three are the **foundation layer**: what it is, what it should be, and how we build it. The brain and avd-swarm are the intelligence and abstraction layer built on top, how we automate it and make it smart and self-improving: the brain is the why over the estate's whole life, and avd-swarm is the how of operating it. avd-assess is held to the same rule: it stands with the pair and never depends on the brain or avd-swarm.

- **Dependencies point down only.** avd-swarm reads the pair's outputs (the preflight's state line, `-WhatIf`, lessons, run records) and runs the pair's own scripts pinned by commit SHA and file hash (by release tag once the landing zone cuts tags, which is the pair's own decision; red team V1-22). Scripts step 3 needs that the pair doesn't have yet (a restart-agent host script) are a prerequisite PR to the pair, written as Cloud Shell-usable scripts first. No script, template, parameter, portal step or default of the pair calls, imports, deploys or waits on avd-swarm.
- **avd-swarm deploys from here, never from the pair's repos.** Its resource group, identities, workflows and templates live in this repo and the adopter's private repo (§5b), never as a module, folder or workflow of avd-landing-zone.
- **A need in the pair is a feature of the pair.** When avd-swarm needs something from the landing zone (a recovery script, a run record, a read-only status check), it is built there as something an operator can use from Cloud Shell alone, and avd-swarm runs it unchanged. Nothing swarm-specific lands there.
- **The landing zone's default deployment never changes** because avd-swarm exists, and every avd-swarm action has a Cloud Shell block that does the same without it (§5).
- **Formats the pair produces are vendored as byte copies**, so the pair builds and tests with no other repo checked out.

## 3. Architecture

```
iPad / browser
   │  Entra sign-in (Static Web Apps built-in auth, one allowed group)
   ▼
avd-buddy: Static Web App chat page + thin API (Functions)
   │  passes the signed-in operator's identity as a claim, never a token
   ▼
Agent API: Container App (consumption, scales to zero), managed identity
   running an Agent Framework workflow
   coordinator ──► readiness · deploy · hosts · images · users · cost   (handoffs)
   │  model calls through the gateway (gateway/): kill switch flag, caps,
   │    Prompt Shields scan, meter; Claude by Anthropic workload identity
   │    federation (adapter: Foundry hosted agent, §3b)
   │  tool calls, per-agent allowlist
   ▼
Read tools                                     One write path (a person approves each plan)
  Azure MCP Server (--read-only, namespaces       propose_change (an action from the catalog)
    incl. virtualdesktop, allowed_tools per agent)  or a brain playbook proposal (C9)
  ARM MCP server cost read tools (forecast_costs)  → plan store → governance gate
  Graph (read, AVD-related objects only)           → GitHub Environment `approve`
  avd-landing-zone scripts: preflight state        → job with a federated token
    line, -WhatIf, Well-Architected review         → verify → outcome to the brain (C10)
  Brain tools (Concierge, episodes), if deployed
  Custom MCP server, if needed (SQ5)

Check runner (§3c): GitHub Actions schedule, or a Container Apps Job for alerts
   runs runbooks (runbooks/) through the agent API, read-only, never calls a model
```

avd-swarm deploys into its own resource group (`rg-<prefix>-<env>-swarm`) and doesn't require any brain tier.

- **Multi-agent:** the coordinator and specialists are one Agent Framework workflow (handoffs) in the agent API, a Container App that scales to zero (the S1a spike's `agent_api`). Foundry's GA Agent Service has no connected agents, and its portal workflows retire on 2026-12-01; running the same workflow as a Foundry hosted agent is the `model-host: foundry` adapter (§3b).
- **Read tools:** the Azure MCP Server, hosted from Microsoft's Container Apps template with a managed identity, run with `--read-only`, an explicit namespace list, and `allowed_tools` per agent. Its `virtualdesktop` namespace covers host pools, session hosts and user sessions. A custom MCP server (SQ5) only fills what that leaves out. Cost forecasts come from the ARM MCP server's `forecast_costs`, with only its read tools allowed, since that toolset can also create budgets and deploy.
- **MCP, in both directions (answers SQ5 for v1).** *As a client*, avd-swarm uses MCP for reads only: Microsoft's Azure MCP Server as above, and the ARM MCP server's cost read tools. It doesn't use the Microsoft MCP Server for Enterprise, whose Graph reads are tenant-wide; the Users agent keeps a narrow AVD-scoped Graph tool (prior art §1). *As a server*, avd-swarm builds no MCP server in v1 (build order steps 1 to 3): its own tools (`propose_change`, the brain's Concierge, the KQL catalog, the AVD-scoped Graph reads) are plain functions behind one tool layer, where the allowlist, the decoy tripwire, the untrusted-text scan, the token meter and the kill switch flag already sit, and avd-buddy is the only front end. The same tool layer is published as an MCP server, with no change to what it exposes, at build order step 3 (core-and-adapters.md section 3.5, WP4): Entra app-role auth, per-caller allowlists with default deny, the kill switch flag on every call. Its first client is the check runner when it moves out of the agent API's process, its second Claude Code for the owner; an adapter's connector (SRE Agent, if an adopter turns it on) comes after both. It exposes read tools and `propose_change` or `propose_playbook_run` only, never a write, and a plan it proposes is approved in the GitHub Environment like any other.
- **The Prompt Shields scan is in the gateway and applies on every provider** (red team R26): the gateway scans the operator's message and every tool result itself and counts calls to decoy tools and to tools outside the allowlist ([security-tooling.md](security-tooling.md) §3 and §4; `gateway/avd_gateway/shield.py`, observe mode first). Both feed the observer's security events. Traces go to Application Insights.
- **With the `model-host: foundry` adapter on**, Foundry's guardrails (indirect prompt injection and task adherence checks) add to the gateway's scan, never replace it. Foundry's own tool approval (`require_approval`, MCP approval requests) answers inside the conversation, so it is never the write approval; at most it is an extra check in front of `propose_change`.

## 3b. Adapters: paid or closed services, off by default

An adapter is a manifest in `adapters/<name>/adapter.yml` and a page beside it ([adapters/README.md](../adapters/README.md)), checked in CI by `scripts/check_adapters.py`. It declares the core capability it adds to (never one it replaces), its retail meters and standing charges, the identity it runs as with the Azure Policy deny that keeps that identity read-only, how each kill switch position reaches it and what it can't reach, the observer events it emits, `writes: none`, and an empty `exitTests`. The check fails a manifest that is on by default, claims to replace a core capability, writes, lacks the Policy deny or names an exit test.

| Adapter | Kind | Adds to | Status |
|---|---|---|---|
| `sre-agent` | investigator | investigation and scheduled checks (§3a) | designed |
| `logic-apps` | scheduler | the check runner, as a trigger source only | designed |
| `apim` | gateway | the gateway package, in front of it | designed |
| `foundry` | model-host | the agent API's model host | designed (the S1a spike's Foundry path is its acceptance test) |

Turning one on is an adopter's choice, made after a spend estimate in [spend.md](spend.md) format and, in the owner's sandbox, the exact go phrase. The same gate and kill switch apply; the drill (build order step 3) refuses each registered adapter's calls at each halt position.

## 3c. Runbooks and the check runner

**Runbooks as code** (`runbooks/`, [runbooks/README.md](../runbooks/README.md)): a Markdown page a person reads, with a YAML front matter that code reads, naming the trigger, the agent, the reads (each in that agent's allowlist; KQL only by catalog ID from `catalog/kql/`), the facts code checks before a model sees anything, the C15 register IDs it may cite and its token budget. **The steps are fixed; the model only interprets what they return.** A runbook's Markdown reaches the model as untrusted text through the gateway's scan; the agent's prompt stays a protected path. `scripts/check_runbooks.py` checks every runbook in CI. In v1 every runbook is `runMode: read-only`; `propose`, with at most one catalog action, comes with the catalog at step 3 and is a protected field.

**The check runner** runs runbooks on their trigger in the adopter's private repo (§5b): GitHub Actions `schedule` by default, keyless through OIDC with a read-only identity, or a Container Apps Job for alert-triggered runs and for adopters who want checks inside their network. Its first step reads the kill switch flag and stops at `halt-agents` or above. It never calls a model: it asks the agent API to run a runbook, and the agent API's gateway enforces the runbook's budget inside the daily cap. Findings carry `threadStartedBy: schedule` or `manual` (and `alert` from WP6), so the gate refuses a plan whose only evidence is such a run (V1-4). It writes a finding per run, an avd-buddy card, and a GitHub issue only when the outcome changed since the last run. Alert-triggered runs (core-and-adapters.md WP6) go alert rule, action group, Consumption Logic App, storage queue (the alert ID only), Container Apps Job: every hop a managed identity, nothing in Azure holding a GitHub credential.

## 3a. The `sre-agent` adapter: Azure SRE Agent, off by default

**Superseded interim choice.** The owner decided on 2026-10-20 to use Azure SRE Agent for investigations and scheduled health checks while avd-buddy and the agents were built, and dropped that on 2026-10-21 as cost-prohibitive (about $292 a month until deleted; [decision 0009](decisions/0009-open-source-core-optional-adapters.md)). Investigation, scheduled checks and alert-triggered runs are open-source core here (§3c); without SRE Agent, the Hosts agent and the runbooks answer the same questions and v1 is complete.

This section is the design of the `sre-agent` adapter ([adapters/sre-agent](../adapters/sre-agent/README.md), §3b): what an adopter gets if they turn SRE Agent on after a spend estimate, and the controls that keep it read-only. It was red-teamed (V1-1 to V1-5, V1-29, V1-30, V1-33) and costs nothing while off. The design, checked against Microsoft Learn ([user roles](https://learn.microsoft.com/azure/sre-agent/user-roles), [MCP server](https://learn.microsoft.com/azure/sre-agent/mcp-server), [MCP connectors](https://learn.microsoft.com/azure/sre-agent/mcp-connectors)):

- **avd-buddy stays the one front end.** It asks SRE Agent to investigate through a **separate Azure MCP Server instance** (red team V1-4): namespace `sreagent` only, no `--read-only` (starting a thread is a write to SRE Agent), an identity that holds the SRE Agent **Standard User** role and **no Azure RBAC**, and `allowed_tools` limited to starting a thread, a follow-up and reading a thread, never creating a task, uploading knowledge or adding a connector. avd-buddy shows the findings with their sources, labeled as SRE Agent's.
- **SRE Agent comes back as a plan.** It adds avd-swarm's tool layer as an MCP connector with two kinds of tools: AVD reads and `propose_change` (it has no episodes, so never `propose_playbook_run`). That needs the tool layer's MCP server (§3, build order step 3), and it comes after the server's own first two clients. A fix it finds becomes a plan through the gate and the GitHub Environment; nothing runs through SRE Agent's Approve button. The connector is added only if it authenticates with Entra (a managed identity or an app); if it takes only a key or a bearer secret, it isn't added and SRE Agent's findings reach us only through avd-buddy's thread reads (V1-29). The published tool layer obeys the kill switch like every other caller (governance §2.3, V1-3).
- **Evidence says where it came from.** Plan evidence carries `source: sreagent-thread:<id>`, `requestedBy` (the operator claim) and `threadStartedBy` (`avd-buddy`, `schedule` or `manual`), so the gate refuses a plan whose only evidence comes from a thread no operator started in avd-buddy (V1-4). SRE Agent thread text is untrusted text: it goes through the same Prompt Shields scan as tool results and is quoted, never followed (V1-30).
- **Its identity can't be given a write role: Azure enforces it, not a habit** (red team V1-1, critical). SRE Agent always assigns its own managed identity **Monitoring Contributor at subscription scope**, which could change or disable the alert rules the watchdog runs on (governance §2.4). The adopter's setup script removes that assignment and leaves the identity **Reader and Desktop Virtualization Reader only**, keeps the swarm's resource group out of SRE Agent's managed resources, and deploys an **Azure Policy `deny`** on `Microsoft.Authorization/roleAssignments/write` whose principal is SRE Agent's identity and whose role is anything else. The policy assignment is a protected setting (governance §2.1). Watchdog rows: a role assignment to SRE Agent's identity, or any write by it, trips **`halt-all`** and pages, the same as a swarm identity writing outside its scope.
- **Roles.** avd-buddy's sreagent identity is a Standard User (it can't approve, change connectors, hooks or settings). Learn's API reference also lists an "SRE Agent User" role that can approve; check which roles the tenant actually offers before assigning one, and assign none that can approve. The SRE Agent **Administrator** role is held only by a break-glass account, never the operator's daily account, and the setup script stops while the daily account holds it. avd-swarm's write identities hold no SRE Agent role.
- **Writes and on-behalf-of are denied at the global scope** (V1-2). SRE Agent hooks are Stop and PostToolUse only, so a hook can't deny a tool call. Its **tool access policy at global scope** denies every write tool and every on-behalf-of request (only the global scope can deny). The policy is portal configuration, not Git, so it isn't a protected path: RBAC and the Policy deny above are the control the design relies on, and the tool access policy is a second check. A PostToolUse hook is kept only as an event source (`sreagent.tool.called`, counts only). The approver's daily account holds no write role on the AVD scope outside PIM, so an on-behalf-of write through a person's own credentials can't happen quietly either.
- **What the kill switch doesn't reach.** The kill switch stops avd-buddy's calls to SRE Agent and the tool layer's answers to it, but not SRE Agent itself, which keeps running its own investigations. With no write role and the Policy deny, that is read-only activity. The owner accepts this residual or keeps SRE Agent out of any subscription the swarm manages (an owner decision, [v1-review.md](v1-review.md) §8).
- **It adds, it never replaces.** The brain's Tier 1 job (scheduled preflight, detections, episode issues), the Hosts agent and the runbooks with their check runner (§3c) are the baseline every adopter gets; SRE Agent only adds to them. A v1 exit test runs without SRE Agent, and no CI job or eval depends on it, since it is paid and closed (V1-5).
- **Only reviewed public knowledge is uploaded** to it (the brain's knowledge base, avd-swarm's catalog as text), never a vault, C11 or C12.
- **Cost.** SRE Agent is billed in Azure agent units, not per model response, so the metered client can't reserve or book it (V1-33). Its spend is capped by a budget on its resource group (action: notify and `halt-actions`), estimated per month in spend.md format before it is turned on, and never counted as metered token spend. It is never turned on in the sandbox without that estimate and the owner's go.

## 4. Agents

| Agent | Answers | Reads | Proposes |
|---|---|---|---|
| **Coordinator** | Anything; splits a question into typed sub-questions for the specialists and merges only the claims that passed the claim check, with their citations (§4a) | Catalogs only; hands off to specialists in the workflow | Nothing |
| **Readiness** | Is this environment ready for AVD, what's in the current estate, how it performs | Current-state (C11) and forensic (C12) facts through the brain (§6d) | A forensic collection, as a plan (§6d) |
| **Deploy** | What's deployed where, what a redeploy would change, preflight results | Deployment history, what-if, preflight state line, Well-Architected review (a review mode of this agent: [waf-review.md](waf-review.md)) | A PR to avd-landing-zone's parameters, which that repo's own `deploy.yml` runs. Never an `approve` action (red team R8) |
| **Hosts** | Health, sessions, drain state, agent version, failed checks | Resource Graph, KQL (`WVDAgentHealthStatus`, `WVDErrors`, ...), the brain's answers about past episodes (Concierge) | Host actions (drain, restart agent, recycle, re-register) |
| **Images** | Current and candidate image versions, which hosts run what | Compute Gallery, image pipeline state (decision 0013) | Promotion, as a PR (decision 0013) |
| **Users** | Who can sign in, why a user can't, profile and FSLogix problems, licenses | Graph: AVD user groups, app group assignments, license assignments, sign-in logs; storage and FSLogix logs | Group membership and assignment changes |
| **Recovery** | Which recovery tier the landing zone is on and whether a test proved it, when each item's last good copy was made, what a failover would move | The landing zone's read-only recovery status check, Backup vault and gallery state, Service Health ([recovery.md](recovery.md) §3) | A configuration snapshot, a recovery test, a failover test, failover or failback ([recovery.md](recovery.md) §4) |
| **Cost** | Spend to date, forecast against the budget, idle hosts, auto shutdown savings, what each run cost and whether its teardown finished | Cost Management, retail prices (decision 0010), budget state, and the spend guard's run records ([spend.md](spend.md) §2) | Stop, Resume, size changes |

How the coordinator splits a question, fans it out, checks what comes back and merges it is §4a.

Adding an agent is a PR with its prompt, its tool allowlist and its eval questions. Prompts and allowlists are protected paths: a PR an agent authored that touches them fails CI, as in brain §9.2. The coordinator starts on a smaller, cheaper model, since it routes and merges; specialists start on Claude Opus 5.5 with effort per agent and move to a cheaper model only where evals hold. Cost per question is measured from the first eval run (red team R14).

### 4a. How a question is answered: split, fan out, check, merge

One answer goes through six steps. The pattern is the common goal, split, fan-out workers, fresh-context verifier, merge and final-answer graph; what avd-swarm adds is where code decides instead of a model, and what each edge between the steps is allowed to carry.

| Step | Who | What passes on |
|---|---|---|
| 1. **Goal** | The operator, through avd-buddy | The question, and the operator's identity as a claim (§3) |
| 2. **Split** | The coordinator | A list of **sub-questions**, each naming one specialist from the catalog and the evidence it needs. A question one specialist can answer is a list of one |
| 3. **Fan out** | The named specialists, **concurrently** (an Agent Framework concurrent step inside the handoff workflow) | Each returns **claims**, not prose: `{claim, value, unit, citations: [tool call IDs]}`, plus the sub-questions it couldn't answer and the actions it would propose. Nothing else crosses this edge: no reasoning, no draft answer |
| 4. **Check** | The **claim check**: code first, a model only for what code can't settle | Each claim marked `supported`, `unsupported` or `unchecked` |
| 5. **Merge** | The coordinator | One answer built only from `supported` claims; anything else listed apart as "not confirmed", never dropped silently |
| 6. **Final answer** | avd-buddy | The answer with its citations; a plan, if there is one, rendered from the plan store as always (§5 step 4) |

**Edges carry results, not reasoning.** A specialist's chain of thought, its scratch tool calls and its draft prose stay in the transcript (§6b). They never reach the claim check or the merge, so a check can't be talked into agreeing by the argument that produced the claim, and a merge can't reintroduce what the check removed.

**The claim check (step 4).**
- **Code first.** For every claim: each citation names a tool call made in this turn; the value appears in that call's saved output (numbers within the unit's rounding, names exactly); a C11 or C12 fact names a register ID the pinned C15 register has (§6d). A claim that passes is `supported`; one whose citation is missing or doesn't contain the value is `unsupported`. This costs no tokens and runs on every answer from build step 2.
- **A fresh-context model verifier only for what code can't settle**: a claim that is an inference from several outputs ("host 3 is the cause"), not a value read from one. It gets the claim and the cited tool outputs, and nothing else: not the question's transcript, not the specialist's reasoning or prompt. It answers `supported` or `unsupported` with the output line it relied on, as structured output, at effort low, on a Claude model other than the specialist's (the §6b judge pairing). Until it is built (build order step 4), inference claims are `unchecked` and shown as such.
- **It only marks.** The check never edits a claim, never adds one and never answers the operator. It has no tool beyond reading the saved outputs.
- **After the merge, code checks the merge.** Every sentence of the final answer that states a fact must carry a citation to a `supported` claim; the coordinator's own words are limited to joining them. An answer that fails is sent back to the merge once, then shown with the failing sentences marked.

**Fan-out limits.** Fan-out multiplies tokens (red team R14), so it is bounded: at most 3 specialists per question and one round (a specialist doesn't fan out again); each sub-question carries its own share of the per-question token reservation in the metered client (spend.md §3.7); a specialist that runs out returns the claims it has and the rest as unanswered. The numbers live in `governance/` (protected), next to the daily cap.

**Different angles, by domain.** The specialists are split by domain (§4), not by angle. Two angles are built in instead of being separate workers: the claim check is the "check" angle, and each specialist's list of what it couldn't answer is the "find gaps" angle; those gaps feed Improve as feature requests (§6b). A separate "compare" or "research" worker per question would double the cost of every answer for what one specialist's prompt already asks for.

**The write path doesn't use this check as approval.** A plan is checked by the deterministic gate (governance §2.1), live re-reads (§5 step 7) and a person. The claim check judges answers; it never approves, refuses or edits a plan. A brain proposal (C9) arrives already reviewed by the brain's Critic, which works the same way: it gets the proposal and its evidence, never the Diagnose agent's reasoning (brain operations-spec §4.4).

**How it is graded.** The claim check's code is tested with fixtures. Its model verifier is a judge like any other and is scored by the same auditor with seeded faults (§6b); the faults the auditor plants (a changed number, a swapped name, an uncited claim) are the ones the code check must catch every time, so a code check that misses one fails CI rather than an auditor window.

## 5. Write path

1. The operator asks for a change ("drain host 3 and restart its agent", "add Sam to the AVD users group").
2. The specialist calls `propose_change` with an action from the catalog. When a brain is deployed and its playbook fits, the brain's proposal (C9) arrives instead through `propose_playbook_run`, with the playbook's hash and bound parameters; from here on both are the same kind of plan.
3. `propose_change` builds the plan without the model's help with the details: targets resolved by the tool, parameters validated against the action's schema, the expected effect, the rollback, and an expiry. The plan gets a hash and goes into the **plan store**. Only the `propose_change` function's own identity can write there; agents can't (R2).
4. avd-buddy shows the plan rendered from the plan store, in a fixed layout set apart from agent text. Agent text never describes what a plan does (R4).
5. A GitHub App limited to `actions:write` on the adopter's private repo (§5b) dispatches the approve workflow with **the plan ID only** (R2). Its first job, outside the environment, re-runs the gate. The plan itself (targets, diff, blast radius, gate results, rollback) is shown by avd-buddy from the plan store, behind the operator's sign-in (R3). The run's logs and job summary carry only the plan ID, its hash, the action name, the count of gate rules passed and failed, and a link into the plan store; never a target, a parameter, a resource ID or a name, even in the private repo (R17).
6. The operator approves the second job in the GitHub Environment `approve`. Its federated credential is the only identity with write roles, so no token for a write exists until a person approves. The environment allows `main` only, the credential's subject names that environment and the private repo, and the workflow files are protected paths: a change to them passes the guard and, when it raises capability, the 72-hour time-lock ([governance](governance.md) §2.6) (R1, R19).
7. The job re-reads the targets and every precondition (§5a) from live state, rebuilds the plan from them and compares its hash with the approved one; on any difference it refuses and the operator gets a new plan to approve (brain §4.5's `apply` step). Only then does it apply the plan, verify the result by positive evidence, and report back in the chat with evidence. What it writes to its own log follows the rule in step 5.

Actions are a catalog in Git, each with a schema, the role it needs, its verification and its compliance effect on Defender for Cloud controls ([governance.md](governance.md) §2.5). If no action fits, the specialist says so and offers a Cloud Shell block for the operator to run. It never improvises a write.

### 5a. Action safety rules (ported from the brain's playbooks)

The second brain worked out what makes a host action safe in an estate that other people and other automation also act on (brain operations-spec §4.5 "Guardrails", §6.3, its red team H3 to H6 and M4). Under decision 0003 every brain playbook runs as an action from this catalog, so those rules live here, in the catalog, where the gate enforces them for every plan, whether it came from an operator's request or a brain proposal (C9). A playbook can add preconditions; it can never drop one of these.

**Preconditions every host action declares.** The gate checks each one before approval and the job checks it again, live, before it acts. A precondition that can't be read counts as failed.

| Rule | What the gate checks | Why |
|---|---|---|
| **Never undrain a host someone else drained** (`notDrainedByOthers`) | The host's drain state and who set it. A host already in drain mode that this plan didn't drain is off limits | An admin drains a host on purpose; an action that "undrains" afterwards undoes their work (brain H3) |
| **Restore the drain state it found** | An action that drains records the state it found in the plan and restores exactly that, never "undrain" | Same |
| **Unknown sessions count as occupied** (`noActiveSessions`) | A fresh agent heartbeat **and** no `WVDConnections` activity for the host in the last N minutes (governance policy, 30 to start). If either is missing, the host is occupied and the action isn't offered; the operator gets the Cloud Shell block instead | When the agent is broken, its session count is stale or zero while users still have disconnected sessions (brain H4) |
| **Not power-locked** (`notPowerLocked`) | No `power-locked` tag from the budget Lock (landing-zone decision 0011). Resume of a locked host is its own action, `budget-lock-review`, that says it lifts the Lock | Restarting a host the Lock deallocated breaks the Lock (brain H3) |
| **Not excluded from the scaling plan** (`notScalingExcluded`) | No scaling plan exclusion tag on the host | The scaling plan and the action fight over the same host (brain H3) |
| **Deallocated is not unhealthy** | A host whose power state is `deallocated` is never the target of a health action | It's stopped on purpose (Lock, scaling plan, a person) |
| **Availability floor** (`hostPoolAvailableHostsAbove`) | After the action, available hosts stay at or above current demand plus one host, or the scaling plan's minimum, whichever is higher | `ceil(sessions / limit)` alone leaves no headroom during ramp-up (brain M4) |
| **One host per pool at a time** | No other plan is in flight on the same host pool | Blast radius (governance §2.1) |
| **Service Health clear** (`serviceHealthClear: AVD`) | No active Azure Service Health incident for AVD in the host pool's region | An outage isn't fixed by restarting hosts, and the restarts add noise |

**How actions run.**
- **Parameters come from the tool or the brain's episode, never from a model.** `propose_change` resolves targets live; a C9 proposal carries the episode's bound parameters, which the gate re-resolves (brain C2).
- **Run Command scripts are fixed files**, Windows PowerShell 5.1, with no parameters or only enumerated ones. The catalog pins each script's SHA-256; the job hashes the file at the approved commit, refuses on a mismatch, sends the script inline, reads back its output and deletes the Run Command resource afterwards (brain H6).
- **One write identity per action that uses Run Command**, each with a custom role listing only that action's operations ([identities.md](identities.md)). Run Command is SYSTEM on every host, so it never shares an identity with group or power actions (brain H6).
- **Short-lived secrets stay inside the action.** A registration token is created inside the action and never returned, logged or stored.
- **Verification is positive.** Each action names fresh evidence that must appear after the action, such as the session host's `Available` status with a `lastHeartBeat` later than the run. Missing data is a failed verification, not a pass (brain H5).
- **Rollback restores what was found**, and it is part of what the person approved; anything else needs a new plan.
- **Per-action limits and a circuit breaker.** Each action has `perHour` and `perHostPerDay` limits. Three failed verifications of one action in 24 hours take it out of the catalog's offered set (the agents offer the Cloud Shell block instead) and open an issue; the global watchdog rule (governance §2.4) still trips `halt-actions` at two failures across all actions.
- **Change windows.** Plans outside peak hours are preferred; the approver sees when a plan falls inside them (governance §2.1, time windows).

**In the catalog.** Each action file declares its kind (`host`, `power`, `group`, `deploy-pr`, `recovery`), its preconditions, limits, verification, rollback, script hash and compliance effect. A host or power action that leaves out any precondition in the table above fails CI when the catalog is built (S2). `preconditions`, `limits`, `verify`, `rollback` and the script hash are protected fields, like the brain's (§9.2 there): an agent-authored PR that touches them fails the guard, and a person's change that loosens one needs the time-lock (governance §2.6).

| First actions (S2) | Rules that apply |
|---|---|
| Drain one host | All host preconditions; restores the drain state it found when the plan ends or rolls back |
| Restart the AVD agent (Run Command) | All host preconditions; fixed script by hash; its own identity |
| Recycle an empty host | All host preconditions, including unknown-as-occupied; restores the drain state it found |
| Re-register a host | All host preconditions; the registration token never leaves the action |
| Stop / Resume | Not power-locked unless the action is `budget-lock-review`; availability floor on Stop |
| Group membership | Scope: AVD groups in the administrative unit only; at most N users per plan (governance §2.1) |

**Recovery actions** (kind `recovery`) run the landing zone's own recovery scripts at a pinned release and declare their own preconditions (restore into new names, source intact, test users only, registered scope, priced and time-boxed); they are specified in [recovery.md](recovery.md) §4.

A change to these rules is a pair of PRs with avd-second-brain (CLAUDE.md, decision 0003): its playbooks cite them and its operations spec points here.

### 5b. Where the write path runs: the project and its adopters

This repository is public, so its Actions logs, job summaries, artifacts and run history are public too. A plan's targets, its parameters and the outcome of each change describe a real estate, so none of that can run here. avd-swarm follows the brain's split (brain operations-spec §3.2 and §9.1):

| | The project (this public repo) | An adopter (a private repo) |
|---|---|---|
| Who | This repo and its one maintainer | An organization, or a person, running avd-swarm against their own tenant. Nick's own sandbox is an adopter too |
| Runs here | CI only: the guard, tests and checks against fixtures. No workflow holds an Azure credential, an environment or `id-token: write` | Everything that touches a tenant: `approve`, `stop-spend`, the kill switch trip and drill workflows, Improve and Scribe, the eval runs against Azure, the spend jobs |
| Ships or holds | Code, the action catalog, workflow **templates** (`templates/workflows/`, which GitHub doesn't run), conservative defaults | Their variables (IDs), environments and approvers, their plan store, transcripts and ledger |
| Syncs | n/a | From a **release tag**, never from `main`. A sync PR passes the adopter's own guard, so every guardrail change upstream made is visible before it merges |

**Rules that enforce it.**
1. **The public repo holds no write-path workflow.** The guard's workflow check (`scripts/check_workflows.py`) fails any workflow in a public repo that uses an environment, asks for `id-token: write`, logs in to Azure, or opens a pull request with the default token (W1 to W4 in that script). It runs on every PR and push here, and in every adopter's repo with the visibility the event reports.
2. **Every write-path workflow checks visibility first.** Its first step reads the repository's visibility through the API, and stops if the repo is public, before any token is requested. The kill switch treats a write-path run in a public repo as a guardrail breach and trips `halt-all` (governance §2.4).
3. **Logs and summaries carry no estate data**, private repo included: plan ID, hash, action name, gate counts and a plan-store link only (§5 step 5). avd-buddy shows the plan, from the plan store, to the signed-in operator.
4. **Untrusted triggers hold no credentials** (brain §9.1 rule 3): workflows started by `issues`, `issue_comment`, `pull_request` from a fork, `pull_request_review`, `discussion` or `workflow_run` get read-only permissions, no `id-token`, no secrets; `pull_request_target` is never used; event text is passed through `env:`, never interpolated into `run:`.

**Until the adopter repo exists** (build order step 2) nothing in the write path runs anywhere. The templates written in step 2 follow rule 3 from their first commit, and step 1 adds the check in rule 1, so a write-path workflow can't land here by mistake.

## 6. Identity and data

Every identity, what it runs and its exact rights: [identities.md](identities.md).

- **Operator sign-in:** Static Web Apps Standard plan with a custom Entra app registration for avd-buddy: this tenant only, assignment required, one operators group, and Conditional Access requiring phishing-resistant MFA and a short session (R10, R15). Every plan records the operator who asked.
- **Agent identities:** one per agent, read-only: Reader and Log Analytics Reader on the AVD resource groups, plus Graph read permissions only for the Users agent. **Entra Agent ID is required** (it's GA), with Conditional Access for agent identities, including blocking high-risk agents. A Conditional Access block on the agent identities is also a kill switch layer at `halt-agents` (governance §2.3).
- **Model hosting:** the default is the agent API's Container App calling Claude through Anthropic workload identity federation: its managed identity exchanges an Entra token for a short-lived Anthropic token, so no key exists, and every control (the flag before every call, the meter, the Prompt Shields scan, the decoys) was red-teamed on this path. Every model call goes through the gateway package (`gateway/`), whose providers are `anthropic` (the default), `foundry` and `azure-openai` (Entra tokens). Claude in Foundry (preview, East US 2 and Sweden Central) and Foundry hosted agents are the `model-host: foundry` adapter (§3b) for an adopter who needs that data boundary; the S1a spike's Foundry path is its acceptance test.
- **Operator tooling app (requested 2026-10-18 after the S1a spike's Graph denial):** one Entra enterprise app, `avd-swarm operator tools`, that the operator's scripts sign in through instead of the Azure CLI's first-party app. An admin consents once to exactly the delegated scopes the scripts need (listed in the setup script, nothing tenant-wide beyond them), so its sign-ins show by name in the logs and Conditional Access can target it. Interactive use (Cloud Shell, the spike) is a public client with device code, one sign-in per session; unattended work keeps using GitHub Actions OIDC, never this app. A one-time setup script ([identities.md](identities.md) §3) creates it and prints the client ID, which lives in a repository variable or the operator's environment and is never committed. Built in S1; the spike keeps using the Azure CLI until then.
- **Write identity:** one, federated to the `approve` environment only, with roles limited to the action catalog's needs. For groups: the AVD groups sit in an Entra administrative unit, and the identity holds Groups Administrator scoped to that unit, with no tenant-wide Graph write permission and never a role-assignable group (R7).
- **What "AVD-related only" really means for reads:** Graph read permissions such as `AuditLog.Read.All` are tenant-wide. The Users agent's tool layer filters to the registered groups and users, but the identity itself could read more. On-behalf-of (SQ1) would narrow this to the operator's own rights (R7).
- **Personal data:** user names and sign-in data reach the model only when the question needs them. Brain-sourced data stays pseudonymized as brain §9.4 requires. Where model calls are processed is a deployment setting, printed at deploy time.
- **Untrusted text:** tool output, logs and issue text are data, never instructions. The chat shows them as quoted evidence.

## 6a. Governance, observer and kill switch

Every plan passes a deterministic governance gate before approval and again before it runs. Every step is sent as a signed event to external observers, which avd-swarm doesn't control. Observers can deny sensitive plans and trip a kill switch. The kill switch has four positions from `run` to `halt-all`, is enforced by a separate identity, and can be reset only by a person. Details: [governance.md](governance.md).

## 6b. Self-improvement from conversations

Conversations are the raw material for a better user experience: new features in avd-buddy, better answers, and fewer dead ends. A separate workflow, the **Improve** agent, reads them and proposes changes. It is not one of the operating agents and never talks to the operator.

**UX signals.** Each conversation gets a small set of structured signals, stored beside the transcript, so trends can be read without rereading every conversation:
- **Explicit:** thumbs up or down on an answer in avd-buddy, with an optional one-line reason.
- **Sentiment:** a per-turn score for frustration or satisfaction, from a classifier run after the conversation ends, never during it.
- **Behavior:** the operator rephrased the same question, abandoned a plan, asked for a Cloud Shell block instead, left mid-answer, or waited long for an answer.
- **Gaps:** questions no agent could answer, and requests for actions that aren't in the catalog. These are feature requests.

Improve tunes how the signals are computed too: the sentiment classifier's prompt and thresholds are files it can propose changes to, checked against labeled examples the same way the agents are.

**Flow.** Conversation transcripts (pseudonymized as in §6) go to a private store with a 90-day retention. On a schedule, never on an incoming message, Improve reads the recent ones together with plan outcomes, gate refusals, rejections with their reasons, and failed verifications. It looks for questions the agents answered badly, plans people rejected, missing actions, tools that kept failing, and where the UX signals dropped. It then opens draft PRs:
- prompt changes for a specialist;
- new eval questions, drawn from real conversations (added, never removed). Improve proposes the question; a person writes or confirms the expected answer before it enters the set (R11);
- new or changed actions in the catalog, each with its schema, test and verification;
- avd-buddy UX changes (layout, shortcuts, suggested follow-ups), as draft PRs to `buddy/`;
- feature requests ranked by how often the gap came up and how frustrated people were, as issues;
- changes to the UX signals and the sentiment classifier, with their labeled examples.

**What keeps it from weakening the system.** Conversation text is untrusted: an operator, a log or a tool output could carry text meant to steer Improve. So:
- Improve can only open draft PRs. A person merges.
- **Improve opens its PRs as a GitHub App, never with the workflow's default token** (R20). GitHub doesn't start workflows for events caused by `GITHUB_TOKEN`, so a PR opened with it would never run the guard and the checks that keep Improve out of protected paths would silently not happen. Improve mints a short-lived installation token for its own App (`avd-swarm-improve`: `contents:write` and `pull_requests:write`, with rulesets that let it push only `improve/*` branches; no `actions`, `workflows` or `administration` permission, so it can't change a workflow file, and no merge) with `actions/create-github-app-token` in the same job. The guard's workflow check fails any workflow that opens a PR with `GITHUB_TOKEN` or `github.token` (W2), and the ruleset requires the guard on `main`, so a PR the guard never ran on can't merge. Because the PR's author is the App's bot account, the guard's agent check (`scripts/check_agent_pr.py`) knows it is agent-authored and fails it if it touches a protected path. Scribe (§6e) uses its own App the same way.
- It can't change `governance/`, the observer registry, the kill switch, CI, or the write identity's roles. A PR it authored that touches them fails CI (governance §2.1).
- A change to a prompt, an allowlist or an action passes the eval set first. The eval gate runs the base branch's cases too, so removing a case doesn't help.
- **How answers are graded.** Expected facts are read from Azure (ARM REST) when the eval runs, never written or decided by a model. A pattern check runs first. Then a **judge model** reads each answer's claims with the tool output saved beside them, never the answering model's reasoning, prompt or transcript (the same edge rule as the claim check, §4a), and lists every claim the tools didn't return and every expected fact the answer missed. The judge is a Claude model other than the one that answered (Opus 5.5 grades Haiku and Sonnet; Sonnet 5.5 grades Opus), called through the same keyless federation, with effort low and the claim list as structured output. It only flags: a person confirms a disputed case before it changes an expected answer or reaches the brain. A second judge from another model family (Azure OpenAI through Entra, no key) is added only when the auditor below says so.
- **The judge's auditor is code, not a model** (R24). It scores the judge on three mechanical measures:
  1. **Seeded faults.** The auditor mixes in copies of real answers it has broken on purpose: a number changed, a host or pool name swapped, a claim added that the saved tool output doesn't contain, an expected fact deleted, a unit swapped (GiB for GB, ms for s), and a citation to a stale snapshot. It knows the right verdict for each, so the judge's catch rate is exact.
  2. **Ground-truth misses.** Of the answers the ARM fact check failed, the share the judge passed. The denominator is the failed answers, a handful per window in a healthy system, not every answer.
  3. **Overturned flags.** A person reversed the judge on a disputed case, in either direction.

  **Sized for one operator's volume.** A rolling check on every run can't tell drift from noise at this volume: with 40 seeded faults and a 95% line, a healthy judge that catches 97% falls under the line about 12% of the time, and a check every night makes a false alarm near-certain within weeks. The window's answers aren't independent either, since 10 to 50 eval questions rerun all week. So:
  - **A fixed number of seeded faults per window:** exactly 120 per 7-day window, 20 of each kind above, spread over the window's eval runs and counted as **distinct question-and-fault pairs** (the same fault on the same question in two runs counts once), so a judge blind to one kind can't hide behind repeats.
  - **One check per window**, at its end, never per run; windows don't overlap. A window that hasn't reached a measure's minimum count rolls into the next one for that measure instead of being judged on too little.
  - **Confidence bounds, not point rates,** with minimum counts. Seeded faults: alarm when the one-sided 95% upper bound (Clopper-Pearson) of the catch rate is below 95%; with 120 faults that means 109 or fewer caught. A judge that really catches 97% then raises a false alarm in about 0.1% of windows (about 5% a year, weekly), while one that has slipped to 85% is caught in about 98% of windows and one at 90% in about 66%. The rule asks for evidence that the judge is below 95%, not proof that it is above it: a rule that fires whenever the lower bound dips under 90% would alarm a healthy 97% judge in about 7% of windows, which is a false alarm every few months. Ground-truth misses: the 95% lower bound of the share above 10%, with at least 20 ARM-failed answers. Overturns: the 95% lower bound above 10%, with at least 10 disputed cases.
  - **Seeded faults first.** At one operator's volume, ground-truth misses and overturns will take months to reach their minimums, so both start **advisory**: the auditor reports them with their counts each window but they never alarm, until a person moves them out of `advisory_measures` by PR.

  The thresholds, window, fault count and minimums live in `governance/` (protected), so no agent can tune them. An alarm emits a `judge.degraded` event with the counts (never answer text) and opens a draft PR that adds the second-family judge **in shadow**: it grades the same answers, including the seeded faults, but decides nothing.

  **The shadow is compared paired, item by item.** Counting only the second judge's wins promotes it almost every time, about 99%, even when it is no better, because any two judges disagree on a few items. After at least two windows (240 seeded faults plus the ground-truth items), the auditor counts the items where exactly one judge was right: `b` where the second judge caught what the Claude judge missed, `c` the other way. The PR's next step makes both judges required (an answer passes only if both pass it) only when `b + c` is at least 10, the exact one-sided sign test of `b` against `c` gives p < 0.05, and the second judge's false flags on clean answers, compared the same paired way, are not significantly higher. A judge that is no better is then promoted at most 5% of the time. A person merges that PR. Otherwise the shadow is removed and the result is logged as a lesson.
- Every PR lists the conversations and outcomes it drew on, written by the workflow rather than the model, so the reviewer can see where an idea came from.
- The observer sees Improve's PRs as `guardrail.changed` events when they touch protected paths, but never the transcripts.

The second brain's Retro and Evolve agents work the same way (brain §4.6, §4.7). When a brain is deployed, Improve's findings about the landing zone itself go to the brain as issues instead.

### 6c. What feeds the second brain

What avd-swarm learns also feeds [avd-second-brain](https://github.com/avd-ops/avd-second-brain), so the factory, the landing zone and future estates benefit. The brain's rule holds: only generalized knowledge enters `knowledge/`, only by a PR a person merges, and never transcripts or tenant data.

| Flow | What goes | Form | Brain side |
|---|---|---|---|
| **Insights** | Monthly aggregates from the Improve workflow: question themes per area, unanswered and badly answered topics, missing actions and tools, action outcomes (verified, failed, rolled back, rejected with the reason's category), UX signal averages | `avd-swarm-insights/v1`: counts and topics from a fixed vocabulary, with no free-text field and no field for a name, ID or resource | Contract **C8**, read by the `harvest` skill into rules, runbooks and catalog entries by PR |
| **Incidents** (the negative side) | Every kill switch trip and its trigger, observer denials, gate refusals by rule, writes with no matching plan, guardrail tampering, suspected prompt injection, runaway patterns (loops, plan floods, cost spikes), failed verifications and rollbacks, and what stopped each one | The same `avd-swarm-insights/v1` document, in an `incidents` section: counts by category, trigger and outcome, with no free text. Each incident also gets a lesson in this repo's `docs/lessons/`, written like avd-landing-zone's | C8 for the counts; the lessons through C7. The harvest turns them into rules and guardrail defaults, for example a lower rate limit or a new watchdog trigger that other estates start with |
| **Lessons and decisions** | Lessons from real runs and this repo's decisions | Markdown in `docs/` | Searched in place through C7 (avd-swarm is a connected source of the brain) |
| **Action outcomes** | Each plan's outcome (verified, failed, rolled back, rejected with a reason category), its compliance change per Defender control (fixes, regressions and exemptions counted apart, governance §2.5), and every kill switch position change | Events | Contract **C10**: the brain closes its episodes with them and halts its own proposals and model calls with the switch |
| **Why** (§6f) | Each plan's why block: the lifecycle event kind, the use cases, and the decision it carries out or the decision it drafted | IDs and C18's fixed vocabulary on the C10 outcome event; the drafted decision's values and reason categories, no free text beyond a short note | Brain contract **C18**: the brain adds the event to the environment's lifecycle record and accepts or reverts the drafted decision |
| **Back the other way** | Rules, runbooks and the app catalog, cited in answers; playbook run proposals | Knowledge files at a pinned commit; C9 proposals | C6, as the landing zone does; **C9** |

Both sides count: what went well and what people wanted shapes features, and what went wrong shapes the guardrails every future estate starts with. Examples of what an insight can become: a runbook for a question operators keep asking, a rule change when a sizing answer keeps getting rejected, a catalog note when an app keeps coming up in profile problems, a default governance limit after a runaway, a new watchdog rule after an injection attempt.

The contract has to change in pairs (avd-second-brain's rule): `docs/contracts.md`, `contracts/schemas/swarm-insights.schema.json` and a test in `tests/Connectors.Tests.ps1` land together in avd-second-brain, and Improve's exporter here follows.

### 6d. Facts from avd-current-state

[avd-current-state](https://github.com/avd-ops/avd-current-state)'s collector and validator are the estate's offline sensors: read-only, run by a person in Cloud Shell or on a schedule, with no swarm identity involved. Their packages hold tenant data and go to the customer's private vault (brain C1). What the swarm/brain system works from is a redacted **facts snapshot**, `avd-current-state-facts/v1` (brain contract **C11**): counts, booleans, public Azure values and keyed pseudonyms (`hp-3fa2c1d0`), with no names, IDs, IPs, tags or free text. It is the fact collection layer of the system.

| Step | Who | What |
|---|---|---|
| Collect | A person (collector, validator) | Evidence packages, kept in the vault |
| Redact | avd-current-state `avd_current_state_facts.ps1`, offline, with the estate's key | One snapshot per tenant, refused if anything that looks like an ID or address gets through |
| File | The brain's `Import-CurrentStateFacts.ps1` | Checked again by the brain's guard, filed in the vault under `facts/<estate>/` |
| Reason | The brain's agents (Diagnose, Critic, the Concierge) | Diagnoses, answers and C9 proposals that cite the snapshot by estate and time |
| Act | avd-swarm | Only on a C9 proposal or an operator's request, through §5; never on a snapshot alone |

What that means here:
- **avd-swarm doesn't consume snapshots.** No agent, tool or allowlist reads a package, a vault or a snapshot file. The Readiness agent and the coordinator get current-state facts by asking the brain (the Concierge read tool), and a C9 proposal carries them as evidence.
- **Facts propose, live reads decide.** A snapshot is evidence of when it was collected. The gate treats it as a reason to propose, never as the target list: `propose_change` and `propose_playbook_run` resolve every target live, and the gate refuses a proposal whose only evidence is a snapshot older than the freshness limit in `governance/` (7 days to start, [governance](governance.md) §2.1).
- **Pseudonyms stay pseudonyms in the chat.** An answer quotes `hp-3fa2c1d0` from the brain; the plan shows the real target the tool resolved live, rendered from the plan store (§5).
- **No new identity or spend.** The collector and validator already run read-only as the operator, and the facts script and the importer are offline. Nothing here needs an Azure resource.
- **It changes in three places.** C11 lives in avd-second-brain's `docs/contracts.md` and schema, its producer in avd-current-state, and the gate rule here. A change to one is three linked PRs.

**The forensic tier (C12).** C11 says what is configured. When a question needs what users actually do (sizing, a size change, a performance complaint with no ARM-side cause, a migration), the next level is Stratusphere UX telemetry from an assessment: concurrency by hour, p95 demand per user and per host, logon time, UX score, profile size and latency, measured over 2 to 4 weeks. The factory pseudonymizes it at ingest and hands the brain `avd-forensic-facts/v1` (brain contract **C12**, specified): aggregates per host pool or persona, at least 10 users per group, coarser data for groups under 20 (brain C12 rules), never a user or a machine, keyed with the same estate key so a host pool's `hp-` pseudonym matches C11.
- **Same path as C11.** avd-swarm reads it only through the brain; a size-change or sizing proposal cites it by window, and the gate refuses one whose only evidence is a C12 window that ended more than 90 days ago, or one marked `low-confidence` by the factory's quality gate.
- **Collecting it is an action, not a read.** It deploys the Hub appliance (and optionally Database and Collectors) and needs a license for the window. When the brain says a question needs forensic data, the Readiness agent proposes the collection as a plan: `Deploy-Stratusphere.ps1` and `Deploy-CidKey.ps1` as catalog actions, taken from [avd-liquidware](https://github.com/nickprignano/avd-liquidware) at a pinned commit once the instrument moves there, with the spend block priced for the whole window (spend.md), the approval in §5, and teardown evidence at the end. Until those actions are in the catalog, the agent offers the Cloud Shell blocks instead (§5).

**Facts are cited by register ID (brain C15).** Every fact a proposal cites, from C11, C12 or later tiers, is named by its ID in the fact register (`contracts/fact-register.json`, authored in avd-assess and vendored by avd-second-brain since brain decision 0021), for example `c11.hostPools.loadBalancer@hp-3fa2c1d0`. The gate refuses evidence that names an ID the pinned register doesn't have, or one still `proposed`. The foundation repos work with or without avd-swarm: what the swarm adds is guidance on top of their baseline, and the swarm never asks them for a field they don't already produce.

**The target-state manifest is not a swarm input (brain C14).** The assessment's approved `avd-target-state/v1` manifest goes from the customer's engagement folder to avd-landing-zone's `ConvertFrom-AvdTargetState.ps1`, run by a person. The swarm doesn't read it, render it or deploy it; putting a manifest deployment in the catalog is a later decision with its own linked PRs.

### 6e. Scribe: drafting docs fixes from the brain's findings

The second brain checks that code comments, READMEs, changelogs and docs agree with the code and with its facts of record (contracts, sources, decisions, terms), and publishes what disagrees as contract **C13**, `avd-doc-findings/v1` ([avd-second-brain docs-spec](https://github.com/avd-ops/avd-second-brain/blob/main/docs/docs-spec.md), [decision 0004](decisions/0004-scribe-drafts-docs.md)). The brain's generated handbook (`wiki/`) is retired (its decision 0024, [PR #76](https://github.com/avd-ops/avd-second-brain/pull/76)); avd-ops holds each repo's page. **Scribe** is the avd-swarm role that turns those findings into draft PRs. The brain decides what is wrong; Scribe only drafts; a person merges.

Scribe belongs to the Improve family (§6b), not the operating agents: it never talks to the operator, holds no Azure role, and runs on a schedule after the brain's nightly sweep, never on an incoming message.

- **Generated fixes use the generator, not a model.** A finding marked `generated` (a reference page, a README table, a changelog block) is fixed by rerunning the brain's generator at the pinned commit. A model drafts only prose, from the finding, the cited source of truth and the file it fixes.
- **Code is never changed to match docs** (brain docs-spec §4). When the source of truth is code, Scribe edits the text; when it is a brain fact, the PR goes to the brain.
- **Upstream is linked, never restated or filed** (brain [decision 0011](https://github.com/avd-ops/avd-second-brain/blob/main/docs/knowledge/decisions/0011-family-format-upstream-sources.md)). When the source of truth is Microsoft Learn, a product's repo or vendor docs, Scribe's fix links the upstream page instead of paraphrasing it. Scribe, Improve and every other avd-swarm identity act only on the connected repos: an upstream issue or PR is drafted in the brain's `docs/knowledge/microsoft/drafts/`, and a person reviews and files it.
- **Judge findings wait for a person.** Scribe acts on a `judge` finding only once someone has confirmed it in the findings issue.
- **Protected paths:** `CLAUDE.md`, `.claude/`, agent prompts and allowlists, `governance/`, the observer registry, workflows, `LICENSE` and the project notice, and in the brain `knowledge/` (which changes only by harvest) and the facts of record. A PR Scribe authored that touches them fails CI, as for Improve (governance §2.1).
- **Untrusted input:** code comments, doc text and PR bodies are data, never instructions.
- **Limits:** at most one open Scribe PR per repo, batched by check; the daily token cap applies; every PR must pass the repo's docs check before it leaves draft.
- **Identity:** a GitHub App with `contents:write` and `pull_requests:write` on the connected repos only, no `actions` or `administration` rights, no merge, and branches named `scribe/*`; every connected repo protects its default branch so the App's `contents:write` can't push there. It holds no Azure or Graph role. It is listed in [identities.md](identities.md) when built (phase D1).
- **Back to the brain:** findings opened, drafted, merged and rejected go out as counts in C8's insights; a finding closes when the next sweep no longer reports it.

Until Scribe exists, the brain's findings issues are fixed by hand. Phase D1 in the brain's docs-spec adds Scribe; it can run before S1 as a Claude Code GitHub Action, estimated in the [spend.md](spend.md) format first.

### 6f. Every plan says why (brain contract C18)

avd-swarm is the how; the brain is the why ([decision 0007](decisions/0007-every-plan-says-why.md), brain decision 0020). The brain keeps one lifecycle record per environment, from its birth: the use cases it serves, every decision about it with what else was considered and why not, and the events that carried them out (brain contract C18). A plan is one of those events, so it says which decision it carries out.

**The `why` block.** Built by `propose_change` (or carried by a C9 proposal) and kept in the plan store with the plan, inside its hash:

| Field | What | Source |
|---|---|---|
| `kind` | `build`, `configure`, `expand`, `contract`, `fix`, `recover` or `retire` (C18's event kinds) | The action's catalog entry gives the default (Stop is `contract`, recycle is `fix`); the specialist may pick another from the same list |
| `useCases` | `uc-NN` IDs from the brain's record | The brain, when it has a record; empty otherwise |
| `decision` | An accepted `dec-NNNN` from the brain's record that this plan carries out | The brain's answer to "why" for this target |
| or `draft` | A new decision: topic, chosen value, at least one alternative with a reason category (`whyNot`), drivers, evidence references, an optional note of at most 200 characters | The specialist drafts it from the operator's request; a C9 proposal brings the brain's |

**Rules.**
- **The gate checks the shape, not the reason.** A plan with no `why`, with neither `decision` nor `draft`, with a decision the record doesn't have or doesn't hold as accepted, or with a draft that has no alternative is refused (governance §2.1, "Says why"). Whether the reason is good is the approver's call.
- **The approver sees it in the fixed layout.** avd-buddy renders the why from the plan store next to the targets and rollback, never from agent text (R4). Approving the plan approves the drafted decision.
- **No names, no free text.** The block uses C18's vocabularies and IDs; the note follows C18's note rule. Logs and job summaries still carry only the plan ID, hash, action and gate counts (§5 step 5).
- **The outcome takes it back.** The C10 outcome event for a plan carries its plan ID, `kind`, `useCases` and the decision ID (the draft gets its `dec-` number from the brain when the brain files it). The brain adds an event to the record and marks a drafted decision `accepted` when the plan is verified, `reverted` when it is rolled back, and `rejected` when the approver rejects the plan.
- **Without a brain** the why stays in the plan store, and avd-swarm works as before. A brain without avd-swarm records the operator's own changes as decisions and events itself.

## 7. Cost

- Static Web Apps needs the Standard plan for the sign-in in §6, a small fixed monthly fee. Functions on Flex Consumption scale to zero. A hosted agent is billed for its container compute while it runs, and scales to zero; tokens are the main cost.
- A daily token budget and a resource-group budget cap the spend. Crossing either disables the agents until a person resets it. The token budget is **metered from each model response's own usage**, before the next call, never from Cost Management, whose data arrives hours to a day late; and the provider's own cap sits outside it ([spend.md](spend.md) §3.7, R22).
- The deploy step prices the setup from the meters (decision 0010). This spec doesn't estimate it.
- **Context compression is measured, not assumed.** After S1a sets the baseline (prompt caching on, explicit effort, small allowlists), one extra run asks the same 10 eval questions with [Headroom](https://pypi.org/project/headroom-ai/) in library mode (`compress(messages)` before each call) and compares cost per question and how many answers still meet their expected facts. It's adopted only if it saves money without losing facts, and first for large tool outputs (big estates, long logs). Not the proxy mode: the Anthropic SDK's workload identity federation exchanges its token at the same https address as the model calls, so a local http proxy breaks the keyless sign-in. Tool output is evidence the agents quote, so compression never applies to what a plan or an audit record cites.

## 7a. Spend control (placeholder)

The sandboxes are paid for out of pocket, so spend control is a platform function, not a setting. avd-swarm must give:
- a cost estimate before every run, shown in the plan and checked by the governance gate;
- evidence of what each run actually cost;
- evidence that teardown finished and nothing is left billing;
- a monthly reconciliation of the books.

The design is in [spend.md](spend.md); its first piece, the public spend guard in [spend/](../spend/README.md), runs the S1a spike. Until the rest is built, the governance budget check refuses any plan without a cost estimate.

## 8. Scope

avd-swarm covers everything about AVD, including its users, groups and licenses, and proves the pattern against a real AVD estate. Tenant-wide administration (all of Entra, Microsoft 365 and other workloads) is out of scope here. Tenant-specific values never land in this repo: they live in repository variables, and CI fails on hardcoded IDs.

## 9. Phases

| Phase | Delivers | Exit |
|---|---|---|
| S0 | This spec, a red team, eval questions for read-only answers | Red team findings resolved |
| S1a | Spike: Claude in a Foundry hosted agent in the chosen region, an Agent Framework handoff workflow, the Azure MCP Server read-only with `virtualdesktop`, Entra Agent ID. A throwaway resource group, estimated beforehand in the [spend.md](spend.md) format and torn down at the end with cost and teardown evidence. Plan: [spikes/s1a.md](spikes/s1a.md) | Each item confirmed or replaced, with the cost of the spike |
| S1 | avd-buddy, the coordinator, and the Hosts and Cost agents, read-only, answering through §4a's claim check, with the daily token cap and the `halt-agents` switch position from day one, plus the spend pieces in [spend.md](spend.md) §4 and the operator tooling app (§6) | Answers cite evidence in ≥ 80% of eval questions |
| S2 | The kill switch, watchdog, governance gate and spend gate ([spend.md](spend.md) §4) first, then the `approve` write path with a first action catalog (drain, restart, Stop/Resume, group membership) | Ten approved, verified changes |
| S3 | Users, Deploy and Images agents; brain playbooks when a brain is deployed | Eval gate per agent |
| S4 | Readiness agent across the assessment and inventory repos | Eval gate |

### 9a. Build order (proposed 2026-10-19, after the second red team)

The phases above stay; this is the smallest path that proves the idea, in the order the second red team proposed (its AX-15), so that nothing that can spend money or change an estate exists before the control that stops it. Only step 1 is built. **Everything in step 4 is designed, not scheduled:** a later session doesn't start building from it until step 3's exit is met and a person says so.

| Step | Builds | Spend | Exit |
|---|---|---|---|
| **1. S1a on the Anthropic fallback** (built, not yet run) | The spike's in-process token meter and stop flag (`gateway/avd_gateway/meter.py` since WP3, spend.md §3.7), its own Anthropic workspace with a monthly limit; and, at no cost, the guard's workflow and agent checks and the spec, governance and identity changes for R17 to R25 | The S1a run, estimated in spend.md format; it waits on the owner's go | Each S1a question answered or replaced, with the run's cost and teardown evidence |
| **2. Read-only, side by side** | The brain's Tier 1 (Notice) and swarm S1: avd-buddy, the coordinator, Hosts and Cost, read-only, metered through the gateway package and stopped by `halt-agents` from their first call; runbooks and the check runner on a schedule (§3c, core-and-adapters.md WP1 to WP3), and alert-triggered runs (WP6). Answers follow §4a from the first one: claims on every edge, the fan-out limits, and the claim check's code half (its model verifier waits for step 4). In step 2 the flag is the resource-group tag (layer 1), set by a person's Cloud Shell block; the metered client refuses at the daily cap on its own, and nothing trips automatically until the watchdog exists in step 3 (V1-17). The links to the brain are the Concierge, which is brain code that avd-swarm's tool layer calls over HTTPS with Entra auth (avd-second-brain v1 review §4), and C10's switch read, which the brain does against that tag. C10's outcomes, C8 and C9 are step 4 (V1-7). Before step 2 starts: `evals/README.md` (the spike's ten questions and five per agent) and the agent API's caller auth (only avd-buddy's Functions identity, ingress internal; identities.md) | Eval runs, estimated first | Answers cite evidence in at least 80% of eval questions; a person sets the tag and the next model call is refused on both providers; `facts(ids)` returns only the IDs asked for and the swarm's identities have no read on the vault |
| **3. First writes** (S2) | The private adopter repo synced from a release tag (§5b), the write-path templates, every kill switch layer and the watchdog, the time-lock check (governance §2.6), and two actions, drain and restart agent, with the §5a preconditions; every plan carries its `why` block (§6f) from the first one; the tool layer's MCP server (WP4), the adapter rows in the drill (WP5) and the policy engine spike (WP7), after a red team of WP1 to WP6 (WP8) | Per plan, through the spend gate; a kill switch drill first | Ten approved, verified changes, and a drill that trips each position and resumes |
| **4. Designed, not scheduled** | C9 (brain proposals), Improve and Scribe, the claim check's model verifier (§4a), the judge and its auditor (§6b; only its decision rules exist, `scripts/judge_auditor.py`), the compliance gate (governance §2.5), standing exceptions, group membership actions, S3 and S4, and the Recovery agent and recovery actions (after v1; [recovery.md](recovery.md) §10, whose DR0 exit is the check of Azure's own resiliency drills, v1 review G11). Each gets its own red team pass before it's built | n/a | n/a |

**v1 is steps 1 to 3** (proposed 2026-10-20, [v1-review.md](v1-review.md)): the spike, read-only agents beside the brain's Tier 1, and the first two writes with every control in front of them. Step 4 is after v1.

S1a's Foundry run can follow whenever quota arrives, since the spike carries its own meter. The checks built in step 1 for later steps (the agent PR check, the auditor's rules) are pure functions with tests, so the rules are fixed before anything uses them.

## 10. Open questions

- **SQ1** ~~Can Foundry pass the operator's identity to tools?~~ **Answered:** yes, through user token passthrough, but only if avd-buddy forwards the operator's token, which §3 currently rules out. Default for S1: tools read as the agent's identity. Revisit if R7's tenant-wide Graph reads need narrowing.
- **SQ2** Cost forecasts? **Answered for the tool, open for the identity** (red team V1-28): the ARM MCP server's `forecast_costs` (preview), read tools only, signs in with delegated user auth, which an agent identity can't do and avd-buddy doesn't forward. Until it supports a managed identity, the Cost agent wraps the Cost Management forecast REST call in the tool layer under Cost Management Reader.
- **SQ3** Teams as a second front end beside avd-buddy, or avd-buddy only?
- **SQ7** Does avd-buddy live in this repo (`buddy/`) or in its own repo once it grows? Default: `buddy/` here until S2.
- **SQ4** Should S1 start with Claude Code in GitHub Actions as the coordinator, before the Foundry setup?
- **SQ5** ~~A custom MCP server for tools the Azure MCP Server doesn't cover?~~ **Answered (2026-10-21, decision 0009):** no MCP server of our own in build order steps 1 and 2; the swarm's tools are one tool layer, published as an MCP server at step 3 (§3, WP4), first for the check runner, then Claude Code, with the same limit: read tools and `propose_change` or `propose_playbook_run` only, never a direct write.
- **SQ8** Where do transcripts live (Cosmos DB, Blob), and is 90 days the right retention for what Improve needs?
- **SQ9** The topic vocabulary for C8: start from the agents' areas and the brain's check categories, or derive it from the first months of transcripts?
- **SQ10** Should the claim check's model verifier run on every answer that has an inference claim, or on a sample to save tokens? Default: every one, once built (step 4), priced per question first, since it is the only check an inference gets.
- **SQ11** ~~Azure SRE Agent?~~ **Answered (2026-10-21, decision 0009):** not used. The owner said yes on 2026-10-20 for what it covers, then on 2026-10-21 dropped the interim use and the sandbox pilot as cost-prohibitive (about $292 a month until deleted). §3a is the `sre-agent` adapter's design, off by default; whether the kill switch should also turn SRE Agent off (that needs its Administrator) is the adapter manifest's `switch` field, and stays the residual it states until an adopter turns it on.
- **SQ6** Language for the tools: PowerShell to match the landing zone and the brain, or another language where an SDK or the MCP server is stronger?
