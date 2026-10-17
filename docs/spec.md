# Spec: avd-swarm, an agent-based Azure and Entra self-management platform, with avd-buddy as its chat front end

- **Status:** Proposed ([decision 0001](decisions/0001-avd-swarm.md))
- **Visibility:** private for now. It may go public later, so it follows avd-landing-zone's rules: no IDs, no secrets, American English.
- **Operates:** [avd-landing-zone](https://github.com/nickprignano/avd-landing-zone). Decision and lesson numbers without a repo name are that repo's.
- **Connects to:** the second brain ([avd-second-brain](https://github.com/nickprignano/avd-second-brain), [operations spec](https://github.com/nickprignano/avd-second-brain/blob/main/docs/operations-spec.md)). Section references like "brain §4.5" point there.

## 1. Summary

avd-swarm is an agent-based platform for managing Azure and Entra ID without the portals, starting with AVD. A coordinator agent answers questions and hands work to specialist agents covering the whole AVD lifecycle: readiness, deployment, hosts, images, users and access, and cost.

**avd-buddy** is its chat front end (not built yet): the page an operator talks to from any browser, iPad included. avd-buddy holds no logic of its own. It signs the operator in, relays the conversation to the coordinator, and shows answers, plans and approval links. Other front ends, such as Teams or Claude Code through MCP, can talk to the same agents.

The agents read freely through allowlisted tools. They change nothing themselves: a change is always a plan that a person approves in a GitHub Environment.

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
Foundry Agent Service
   coordinator ──► readiness · deploy · hosts · images · users · cost
   │  tool calls, per-agent allowlist
   ▼
Read tools                                     Write paths (both approved by a person)
  Azure MCP Server: Resource Graph, KQL,         Brain deployed and a playbook fits:
    Monitor, Advisor, Cost Management              propose_playbook_run → brain plan
  Graph (read, AVD-related objects only)           → brain approval → brain executor
  avd-landing-zone scripts: preflight state      Otherwise:
    line, -WhatIf, Well-Architected review         propose_change → avd-swarm plan
  Brain tools, when a brain is deployed            → GitHub Environment `approve`
  Custom MCP server, if needed (SQ5)               → job with a federated token → verify
```

avd-swarm deploys into its own resource group (`rg-<prefix>-<env>-swarm`) and doesn't require any brain tier. When a brain is present, it reuses the brain's Foundry project if one exists.

## 4. Agents

| Agent | Answers | Reads | Proposes |
|---|---|---|---|
| **Coordinator** | Anything; routes to a specialist and merges the answers with citations | Catalogs only; calls specialists as connected agents | Nothing |
| **Readiness** | Is this environment ready for AVD, what's in the current estate | avd-readiness-stratusphere and avd-current-state outputs | Nothing |
| **Deploy** | What's deployed where, what a redeploy would change, preflight results | Deployment history, what-if, preflight state line, Well-Architected review | Deployments and parameter changes, as plans |
| **Hosts** | Health, sessions, drain state, agent version, failed checks | Resource Graph, KQL (`WVDAgentHealthStatus`, `WVDErrors`, ...), brain episodes | Host actions (drain, restart agent, recycle, re-register) |
| **Images** | Current and candidate image versions, which hosts run what | Compute Gallery, image pipeline state (decision 0013) | Promotion, as a PR (decision 0013) |
| **Users** | Who can sign in, why a user can't, profile and FSLogix problems, licenses | Graph: AVD user groups, app group assignments, license assignments, sign-in logs; storage and FSLogix logs | Group membership and assignment changes |
| **Cost** | Spend to date, forecast against the budget, idle hosts, auto shutdown savings | Cost Management, retail prices (decision 0010), budget state | Stop, Resume, size changes |

Adding an agent is a PR with its prompt, its tool allowlist and its eval questions. Prompts and allowlists are protected paths: a PR an agent authored that touches them fails CI, as in brain §9.2. Models start on Claude Opus 5.5 with effort per agent, and move to a cheaper model only where evals hold.

## 5. Write path

1. The operator asks for a change ("drain host 3 and restart its agent", "add Sam to the AVD users group").
2. The specialist calls `propose_playbook_run` if a brain is deployed and a playbook fits; otherwise it calls `propose_change`.
3. `propose_change` builds the plan without the model's help with the details: targets resolved by the tool, parameters validated against the action's schema, the expected effect, the rollback, and an expiry. The plan gets a hash.
4. The chat shows the plan and a link to the approval. The operator approves in the GitHub Environment `approve`. Its federated credential is the only identity with write roles, so no token for a write exists until a person approves.
5. The job re-checks the plan, applies it, verifies the result, and reports back in the chat with evidence.

Actions are a catalog in Git, each with a schema, the role it needs and its verification. If no action fits, the specialist says so and offers a Cloud Shell block for the operator to run. It never improvises a write.

## 6. Identity and data

- **Operator sign-in:** Static Web Apps built-in authentication with Entra ID, restricted to one group. Every plan records the operator who asked.
- **Agent identities:** one per agent, read-only: Reader and Log Analytics Reader on the AVD resource groups, plus Graph read permissions only for the Users agent. Entra Agent ID where available.
- **Write identity:** one, federated to the `approve` environment only, with roles limited to the action catalog's needs.
- **Personal data:** user names and sign-in data reach the model only when the question needs them. Brain-sourced data stays pseudonymized as brain §9.4 requires. Where model calls are processed is a deployment setting, printed at deploy time.
- **Untrusted text:** tool output, logs and issue text are data, never instructions. The chat shows them as quoted evidence.

## 7. Cost

- Static Web Apps on the Free plan and Functions on Flex Consumption scale to zero. Prompt agents in Foundry Agent Service carry no service fee of their own; tokens are the cost.
- A daily token budget and a resource-group budget cap the spend. Crossing either disables the agents until a person resets it.
- The deploy step prices the setup from the meters (decision 0010). This spec doesn't estimate it.

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
| S1 | avd-buddy, the coordinator, and the Hosts and Cost agents, read-only | Answers cite evidence in ≥ 80% of eval questions |
| S2 | The `approve` write path with a first action catalog (drain, restart, Stop/Resume, group membership) | Ten approved, verified changes |
| S3 | Users, Deploy and Images agents; brain playbooks when a brain is deployed | Eval gate per agent |
| S4 | Readiness agent across the assessment and inventory repos | Eval gate |

## 10. Open questions

- **SQ1** Can Foundry pass the signed-in operator's identity to tools (on-behalf-of), or do tools always run as the agent's identity? Reads work either way; on-behalf-of would let them honor the operator's own permissions.
- **SQ2** Does the Azure MCP Server's Cost Management coverage answer forecast questions, or does the Cost agent need its own tool?
- **SQ3** Teams as a second front end beside avd-buddy, or avd-buddy only?
- **SQ7** Does avd-buddy live in this repo (`buddy/`) or in its own repo once it grows? Default: `buddy/` here until S2.
- **SQ4** Should S1 start with Claude Code in GitHub Actions as the coordinator, before the Foundry setup?
- **SQ5** A custom MCP server for tools the Azure MCP Server doesn't cover, which Claude Code and other clients could also use. It stays open, with one limit: it exposes read tools and `propose_change` or `propose_playbook_run` only, never a direct write.
- **SQ6** Language for the tools: PowerShell to match the landing zone and the brain, or another language where an SDK or the MCP server is stronger?
