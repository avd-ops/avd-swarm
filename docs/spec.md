# Spec: avd-swarm, an agent-based Azure and Entra self-management platform, with avd-buddy as its chat front end

- **Status:** Proposed ([decision 0001](decisions/0001-avd-swarm.md))
- **Visibility:** public, MIT. It follows avd-landing-zone's rules: no IDs, no secrets, American English.
- **Operates:** [avd-landing-zone](https://github.com/nickprignano/avd-landing-zone). Decision and lesson numbers without a repo name are that repo's.
- **Connects to:** the second brain ([avd-second-brain](https://github.com/nickprignano/avd-second-brain), [operations spec](https://github.com/nickprignano/avd-second-brain/blob/main/docs/operations-spec.md)). Section references like "brain §4.5" point there.

## 1. Summary

avd-swarm is an agent-based platform for managing Azure and Entra ID without the portals, starting with AVD. A coordinator agent answers questions and hands work to specialist agents covering the whole AVD lifecycle: readiness, deployment, hosts, images, users and access, and cost.

**avd-buddy** is its chat front end (not built yet): the page an operator talks to from any browser, iPad included. avd-buddy holds no logic of its own. It signs the operator in, relays the conversation to the coordinator, and shows answers, plans and approval links. Other front ends, such as Teams or Claude Code through MCP, can talk to the same agents.

The agents read freely through allowlisted tools. They change nothing themselves: a change is always a plan that a person approves in a GitHub Environment.

**Where the value is** ([prior art](prior-art.md)). Chat over Azure and read tools are becoming commodity: Azure Copilot, Azure SRE Agent, the Azure MCP Server. None of them combines per-plan approval outside the chat, no standing write credential, a deterministic gate, an external observer and a layered kill switch. That governed write path is what avd-swarm builds; for chat and reads it reuses what exists. If Azure SRE Agent adds AVD and out-of-band approval, revisit the build-or-reuse choice for the read side.

**Scope compared with the second brain.** The brain watches a deployed landing zone and heals it through tested playbooks. avd-swarm is wider: it is the interface for everything an AVD operator does, including the parts the brain deliberately leaves out, like users, groups, licenses and deployments. The two connect where they overlap:
- When a brain is deployed, avd-swarm reads its episodes and proposes its playbooks, so the brain's guardrails and audit apply to those changes.
- Without a brain, or for changes the brain doesn't cover, avd-swarm uses its own plan-and-approve path (§5), which follows the same rule: a person approves the exact change before any identity can make it.

It is also the development version of **tenant-swarm**, a private project for the whole tenant. What proves itself here moves there; see §8.

## 2. Goals and non-goals

### Goals
- Answer operator questions about the estate with evidence: cost to date and forecast, host health, sessions, image versions, user assignments, profile issues, drift, failed checks, readiness findings.
- Turn "fix it" into a plan the operator can approve from the same screen.
- Cover the AVD lifecycle end to end: assess (avd-readiness-stratusphere), inventory (avd-current-state), deploy and operate (avd-landing-zone).
- Work from a phone or tablet with nothing installed.
- Cost close to nothing when idle, and stop itself at a budget.

### Non-goals
- Any change without a person's approval of that exact change. There is no "confirm in chat" button.
- Tenant-wide administration that isn't about AVD (all of Entra, Exchange, Teams, other workloads). That belongs to tenant-swarm.
- Replacing Cloud Shell blocks for operators who prefer them (lesson 0015).

## 3. Architecture

```
iPad / browser
   │  Entra sign-in (Static Web Apps built-in auth, one allowed group)
   ▼
avd-buddy: Static Web App chat page + thin API (Functions)
   │  passes the signed-in operator's identity as a claim, never a token
   ▼
Foundry Agent Service: hosted agent running an Agent Framework workflow
   coordinator ──► readiness · deploy · hosts · images · users · cost   (handoffs)
   │  tool calls, per-agent allowlist
   ▼
Read tools                                     Write paths (both approved by a person)
  Azure MCP Server (--read-only, namespaces       Brain deployed and a playbook fits:
    incl. virtualdesktop, allowed_tools per agent)  propose_playbook_run → brain plan
  ARM MCP server cost read tools (forecast_costs)
  Graph (read, AVD-related objects only)           → brain approval → brain executor
  avd-landing-zone scripts: preflight state      Otherwise:
    line, -WhatIf, Well-Architected review         propose_change → avd-swarm plan
  Brain tools, when a brain is deployed            → GitHub Environment `approve`
  Custom MCP server, if needed (SQ5)               → job with a federated token → verify
```

avd-swarm deploys into its own resource group (`rg-<prefix>-<env>-swarm`) and doesn't require any brain tier. When a brain is present, it reuses the brain's Foundry project if one exists.

- **Multi-agent:** Foundry's GA Agent Service has no connected agents, and portal workflows retire on 2026-12-01. The coordinator and specialists are one Agent Framework workflow (handoffs) deployed as a hosted agent that scales to zero.
- **Read tools:** the Azure MCP Server, hosted from Microsoft's Container Apps template with a managed identity, run with `--read-only`, an explicit namespace list, and `allowed_tools` per agent. Its `virtualdesktop` namespace covers host pools, session hosts and user sessions. A custom MCP server (SQ5) only fills what that leaves out. Cost forecasts come from the ARM MCP server's `forecast_costs`, with only its read tools allowed, since that toolset can also create budgets and deploy.
- **Foundry's own tool approval** (`require_approval`, MCP approval requests) answers inside the conversation, so it is never the write approval. At most it is an extra check in front of `propose_change`.
- **Foundry guardrails and tracing:** indirect prompt injection and task adherence checks on tool calls and responses are on, and traces go to Application Insights. They feed the observer's security events.

## 4. Agents

| Agent | Answers | Reads | Proposes |
|---|---|---|---|
| **Coordinator** | Anything; routes to a specialist and merges the answers with citations | Catalogs only; hands off to specialists in the workflow | Nothing |
| **Readiness** | Is this environment ready for AVD, what's in the current estate | avd-readiness-stratusphere and avd-current-state outputs | Nothing |
| **Deploy** | What's deployed where, what a redeploy would change, preflight results | Deployment history, what-if, preflight state line, Well-Architected review | A PR to avd-landing-zone's parameters, which that repo's own `deploy.yml` runs. Never an `approve` action (red team R8) |
| **Hosts** | Health, sessions, drain state, agent version, failed checks | Resource Graph, KQL (`WVDAgentHealthStatus`, `WVDErrors`, ...), brain episodes | Host actions (drain, restart agent, recycle, re-register) |
| **Images** | Current and candidate image versions, which hosts run what | Compute Gallery, image pipeline state (decision 0013) | Promotion, as a PR (decision 0013) |
| **Users** | Who can sign in, why a user can't, profile and FSLogix problems, licenses | Graph: AVD user groups, app group assignments, license assignments, sign-in logs; storage and FSLogix logs | Group membership and assignment changes |
| **Cost** | Spend to date, forecast against the budget, idle hosts, auto shutdown savings, what each run cost and whether its teardown finished | Cost Management, retail prices (decision 0010), budget state, and tenant-swarm's spend ledger ([spend.md](spend.md)) | Stop, Resume, size changes |

Adding an agent is a PR with its prompt, its tool allowlist and its eval questions. Prompts and allowlists are protected paths: a PR an agent authored that touches them fails CI, as in brain §9.2. The coordinator starts on a smaller, cheaper model, since it routes and merges; specialists start on Claude Opus 5.5 with effort per agent and move to a cheaper model only where evals hold. Cost per question is measured from the first eval run (red team R14).

## 5. Write path

1. The operator asks for a change ("drain host 3 and restart its agent", "add Sam to the AVD users group").
2. The specialist calls `propose_playbook_run` if a brain is deployed and a playbook fits; otherwise it calls `propose_change`.
3. `propose_change` builds the plan without the model's help with the details: targets resolved by the tool, parameters validated against the action's schema, the expected effect, the rollback, and an expiry. The plan gets a hash and goes into the **plan store**. Only the `propose_change` function's own identity can write there; agents can't (R2).
4. avd-buddy shows the plan rendered from the plan store, in a fixed layout set apart from agent text. Agent text never describes what a plan does (R4).
5. A GitHub App limited to `actions:write` on this repo dispatches the approve workflow with **the plan ID only** (R2). Its first job, outside the environment, re-runs the gate and renders the plan into the run summary: targets, diff, blast radius, gate results, rollback (R3). avd-buddy links straight to that summary.
6. The operator approves the second job in the GitHub Environment `approve`. Its federated credential is the only identity with write roles, so no token for a write exists until a person approves. The environment allows `main` only, the credential's subject names that environment, and the workflow files are protected paths with CODEOWNERS review (R1).
7. The job checks the plan's hash again, applies it, verifies the result, and reports back in the chat with evidence.

Actions are a catalog in Git, each with a schema, the role it needs and its verification. If no action fits, the specialist says so and offers a Cloud Shell block for the operator to run. It never improvises a write.

## 6. Identity and data

- **Operator sign-in:** Static Web Apps Standard plan with a custom Entra app registration for avd-buddy: this tenant only, assignment required, one operators group, and Conditional Access requiring phishing-resistant MFA and a short session (R10, R15). Every plan records the operator who asked.
- **Agent identities:** one per agent, read-only: Reader and Log Analytics Reader on the AVD resource groups, plus Graph read permissions only for the Users agent. **Entra Agent ID is required** (it's GA), with Conditional Access for agent identities, including blocking high-risk agents. A Conditional Access block on the agent identities is also a kill switch layer at `halt-agents` (governance §2.3).
- **Model hosting:** Claude in Foundry is in preview and offered in East US 2 and Sweden Central. Confirm in the S1 spike that the chosen Claude model runs in a hosted agent in the chosen region and billing type. If not, the workflow calls the Anthropic API directly; the agent definitions keep the provider a setting.
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
- It can't change `governance/`, the observer registry, the kill switch, CI, or the write identity's roles. A PR it authored that touches them fails CI (governance §2.1).
- A change to a prompt, an allowlist or an action passes the eval set first. The eval gate runs the base branch's cases too, so removing a case doesn't help.
- Every PR lists the conversations and outcomes it drew on, written by the workflow rather than the model, so the reviewer can see where an idea came from.
- The observer sees Improve's PRs as `guardrail.changed` events when they touch protected paths, but never the transcripts.

The second brain's Retro and Evolve agents work the same way (brain §4.6, §4.7). When a brain is deployed, Improve's findings about the landing zone itself go to the brain as issues instead.

### 6c. What feeds the second brain

What avd-swarm learns also feeds [avd-second-brain](https://github.com/nickprignano/avd-second-brain), so the factory, the landing zone and future estates benefit. The brain's rule holds: only generalized knowledge enters `knowledge/`, only by a PR a person merges, and never transcripts or tenant data.

| Flow | What goes | Form | Brain side |
|---|---|---|---|
| **Insights** | Monthly aggregates from the Improve workflow: question themes per area, unanswered and badly answered topics, missing actions and tools, action outcomes (verified, failed, rolled back, rejected with the reason's category), UX signal averages | `avd-swarm-insights/v1`: counts and topics from a fixed vocabulary, with no free-text field and no field for a name, ID or resource | Proposed contract **C8**, read by the `harvest` skill into rules, runbooks and catalog entries by PR |
| **Incidents** (the negative side) | Every kill switch trip and its trigger, observer denials, gate refusals by rule, writes with no matching plan, guardrail tampering, suspected prompt injection, runaway patterns (loops, plan floods, cost spikes), failed verifications and rollbacks, and what stopped each one | The same `avd-swarm-insights/v1` document, in an `incidents` section: counts by category, trigger and outcome, with no free text. Each incident also gets a lesson in this repo's `docs/lessons/`, written like avd-landing-zone's | C8 for the counts; the lessons through C7. The harvest turns them into rules and guardrail defaults, for example a lower rate limit or a new watchdog trigger that other estates start with |
| **Lessons and decisions** | Lessons from real runs and this repo's decisions | Markdown in `docs/` | Searched in place through C7 (needs read access to this private repo) |
| **Back the other way** | Rules, runbooks and the app catalog, cited in answers | Knowledge files at a pinned commit | C6, as the landing zone does |

Both sides count: what went well and what people wanted shapes features, and what went wrong shapes the guardrails every future estate starts with. Examples of what an insight can become: a runbook for a question operators keep asking, a rule change when a sizing answer keeps getting rejected, a catalog note when an app keeps coming up in profile problems, a default governance limit after a runaway, a new watchdog rule after an injection attempt.

The contract has to change in pairs (avd-second-brain's rule): `docs/contracts.md`, `contracts/schemas/swarm-insights.schema.json` and a test in `tests/Connectors.Tests.ps1` land together in avd-second-brain, and Improve's exporter here follows.

## 7. Cost

- Static Web Apps needs the Standard plan for the sign-in in §6, a small fixed monthly fee. Functions on Flex Consumption scale to zero. A hosted agent is billed for its container compute while it runs, and scales to zero; tokens are the main cost.
- A daily token budget and a resource-group budget cap the spend. Crossing either disables the agents until a person resets it.
- The deploy step prices the setup from the meters (decision 0010). This spec doesn't estimate it.

## 7a. Spend control (placeholder)

The sandboxes are billed to personal and work cards, so spend control is a platform function, not a setting. avd-swarm must give:
- a cost estimate before every run, shown in the plan and checked by the governance gate;
- evidence of what each run actually cost;
- evidence that teardown finished and nothing is left billing;
- a monthly reconciliation of the books.

The design is in [spend.md](spend.md), on top of tenant-swarm's spend ledger. Until it is merged, the governance budget check refuses any plan without a cost estimate.

## 8. Relationship to tenant-swarm

| | avd-swarm (this repo) | tenant-swarm |
|---|---|---|
| Scope | Everything about AVD, including its users, groups and licenses | The whole tenant: subscriptions, Entra, Microsoft 365 |
| Purpose | Prove the pattern against a real AVD estate | Daily tenant operations for one owner |

The pattern moves to tenant-swarm once avd-swarm has a record of approved, verified changes. Tenant-specific values never land in either repo: both keep them in repository variables, and CI fails on hardcoded IDs.

## 9. Phases

| Phase | Delivers | Exit |
|---|---|---|
| S0 | This spec, a red team, eval questions for read-only answers | Red team findings resolved |
| S1a | Spike: Claude in a Foundry hosted agent in the chosen region, an Agent Framework handoff workflow, the Azure MCP Server read-only with `virtualdesktop`, Entra Agent ID. A throwaway resource group, estimated beforehand in the [spend.md](spend.md) format and torn down at the end with cost and teardown evidence. Plan: [spikes/s1a.md](spikes/s1a.md) | Each item confirmed or replaced, with the cost of the spike |
| S1 | avd-buddy, the coordinator, and the Hosts and Cost agents, read-only, with the daily token cap and the `halt-agents` switch position from day one, plus the spend pieces in [spend.md](spend.md) §4 | Answers cite evidence in ≥ 80% of eval questions |
| S2 | The kill switch, watchdog, governance gate and spend gate ([spend.md](spend.md) §4) first, then the `approve` write path with a first action catalog (drain, restart, Stop/Resume, group membership) | Ten approved, verified changes |
| S3 | Users, Deploy and Images agents; brain playbooks when a brain is deployed | Eval gate per agent |
| S4 | Readiness agent across the assessment and inventory repos | Eval gate |

## 10. Open questions

- **SQ1** ~~Can Foundry pass the operator's identity to tools?~~ **Answered:** yes, through user token passthrough, but only if avd-buddy forwards the operator's token, which §3 currently rules out. Default for S1: tools read as the agent's identity. Revisit if R7's tenant-wide Graph reads need narrowing.
- **SQ2** ~~Cost forecasts?~~ **Answered:** the ARM MCP server's `forecast_costs` (preview), read tools only.
- **SQ3** Teams as a second front end beside avd-buddy, or avd-buddy only?
- **SQ7** Does avd-buddy live in this repo (`buddy/`) or in its own repo once it grows? Default: `buddy/` here until S2.
- **SQ4** Should S1 start with Claude Code in GitHub Actions as the coordinator, before the Foundry setup?
- **SQ5** A custom MCP server for tools the Azure MCP Server doesn't cover, which Claude Code and other clients could also use. It stays open, with one limit: it exposes read tools and `propose_change` or `propose_playbook_run` only, never a direct write.
- **SQ8** Where do transcripts live (Cosmos DB, Blob), and is 90 days the right retention for what Improve needs?
- **SQ9** The topic vocabulary for C8: start from the agents' areas and the brain's check categories, or derive it from the first months of transcripts?
- **SQ6** Language for the tools: PowerShell to match the landing zone and the brain, or another language where an SDK or the MCP server is stronger?
