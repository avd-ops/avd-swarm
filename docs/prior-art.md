# avd-swarm prior art review (as of 2026-10-17)

Scope: what already exists that overlaps `docs/spec.md` and `docs/governance.md`, and what to reuse instead of build.
Status labels come from the cited pages. When a page gave no status, the entry says "unstated". **SPEC** marks a finding that should change the spec.

## 1. Microsoft

**Foundry Agent Service: multi-agent.** The new Agent Service reached GA on 2026-03-16 **without connected agents**. Classic connected agents retire on 2027-03-31. Portal workflows never left preview and retire on 2026-12-01. Microsoft now recommends three things: orchestration in **Agent Framework workflows** (GA v1.0, April 2026; sequential, concurrent, group chat, handoff and magentic patterns; human-in-the-loop through `approval_mode="always_require"`), deployment as **hosted agents** (GA July 2026; a VM-isolated sandbox per session that scales to zero after 15 minutes idle; tools through one Toolbox MCP endpoint), and the **A2A tool** (preview) for delegation.
- Sources: https://jannikreinhard.com/microsoft-foundry-multi-agent-orchestration/ and https://learn.microsoft.com/en-in/answers/questions/5908461/foundry-no-more-connected-agents
- **SPEC:** spec §3 and §4 say "calls specialists as connected agents". Change this to an Agent Framework handoff or group-chat workflow running as one hosted agent, or to A2A. Recommendation: **reuse** Agent Framework workflows.

**Foundry: Claude support.** The Learn page lists Claude models as preview, Global Standard only, in East US 2 and Sweden Central. Projects need Marketplace access, and CSP and sponsored subscriptions are excluded. The Agent Service overview names the Anthropic Agent SDK as a framework for *hosted* agents. It does not confirm Claude for *prompt* agents.
- Sources: https://learn.microsoft.com/azure/ai-foundry/foundry-models/how-to/use-foundry-models-claude and https://learn.microsoft.com/azure/foundry/agents/overview
- **SPEC:** §7 assumes "prompt agents carry no service fee". With Claude, the agents are probably hosted agents, which add container compute (it scales to zero). Verify that Opus 5.5 is available in Foundry for your region and billing type, and add the region constraint to §6 "where model calls are processed". Recommendation: **verify in S0**.

**Foundry: MCP tool, approval and identity.** The MCP tool supports four relevant features:
- `allowed_tools`, a per-agent allowlist.
- `require_approval`, which defaults to `always`. Approval is an `mcp_approval_request` that comes back in the conversation.
- Several auth types: `project-managed-identity`, `agentic-identity` (the per-agent Entra identity) and **`user-entra-token` (identity passthrough)**.
- A 100-second timeout for non-streaming calls. Private endpoints need Standard setup with a VNet you bring.
- Source: https://learn.microsoft.com/azure/ai-foundry/agents/how-to/tools/model-context-protocol
- **SPEC:** this answers SQ1. On-behalf-of works, but only if avd-buddy forwards a user token, which conflicts with §3 "a claim, never a token". Decide which one you want; my suggestion is the agent identity for reads in S1. Never treat in-chat `require_approval` as the write approval, because spec §2 rules out confirming in chat. Use it only as defense in depth on `propose_change`. Recommendation: **reuse** (`allowed_tools` and `agentic-identity`).

**Foundry: guardrails and tracing.** Agent guardrails are in preview. They scan user input, **tool calls, tool responses** and output for jailbreaks, **indirect prompt injection**, PII and **task adherence**. Network egress controls are in preview for hosted agents. Agent Service also includes end-to-end tracing to Application Insights.
- Sources: https://learn.microsoft.com/en-my/Azure/foundry/guardrails/guardrails-overview and https://learn.microsoft.com/azure/ai-foundry/agents/concepts/tracing
- Caveat: guardrails apply to "models sold by Azure", so coverage for Claude is unclear.
- Recommendation: **integrate** both as cheap extra layers in front of the deterministic gate, not as replacements for it. Use the traces as the Improve agent's source alongside transcripts (SQ8).

**Azure MCP Server.** It has a **`virtualdesktop`** namespace (host pools, session hosts, user sessions), plus `monitor`, `kusto`, `advisor`, `pricing`, `resourcehealth`, `policy`, `role`, `quota`, `wellarchitectedframework` and `sreagent`. It does not cover Entra or Graph. Two start options matter here: `--read-only` and a namespace filter. Tools carry Destructive, ReadOnly and Secret annotations, and Secret tools ask for elicitation. The `azmcp-foundry-aca-mi` template hosts the server on Container Apps behind a managed identity for Foundry. The status of that how-to is unstated, and its app role is named `Mcp.Tools.ReadWrite.All`, so read-only comes from RBAC and the flag, not from the role name.
- Sources: https://learn.microsoft.com/en-us/azure/developer/azure-mcp-server/tools and https://learn.microsoft.com/en-us/azure/developer/azure-mcp-server/how-to/deploy-remote-mcp-server-microsoft-foundry
- Recommendation: **reuse**. Run it with `--read-only` and an explicit namespace list, and set Foundry `allowed_tools` per agent. Check the `virtualdesktop` tool shapes against what the Hosts agent needs before writing the custom MCP server (SQ5).

**Azure Resource Manager MCP server.** This is a hosted Microsoft endpoint, in public preview, with delegated user auth. The default cost tools are `query_costs` and `get_retail_prices`. The opt-in `CostManagement` toolset adds **`forecast_costs`**, budgets, alerts and savings recommendations. It also includes budget creation and template deployments, so it is not read-only.
- Sources: https://news.baeke.info/articles/microsoft-adds-cost-management-tools-to-azure-arm-mcp-server and https://itnext.io/azure-resource-manager-mcp-server-the-day-your-ai-agent-got-the-keys-to-your-cloud-44e343c04165
- **SPEC:** this answers SQ2: forecasting exists. Only allow the read tools (`forecast_costs`, `query_costs`, `list_dimensions`). The documented setup runs through GitHub Copilot clients and delegated sign-in, so its use from Foundry is unverified. Recommendation: **watch**. Until then, wrap the Cost Management query and forecast REST calls in the custom read tool.

**Microsoft MCP Server for Enterprise (Graph).** It is in public preview, **read-only** and delegated, with the tools `microsoft_graph_get`, `suggest_queries` and `list_properties`. It is limited to 100 calls per minute per user, its calls are logged under its own app ID in Graph activity logs, and it has no extra cost.
- Source: https://learn.microsoft.com/graph/mcp-server/overview
- It overlaps the Users agent but is **tenant-wide**: it cannot limit reads to AVD groups.
- Recommendation: **watch** for avd-swarm and **reuse** in tenant-swarm. For avd-swarm, keep a narrow custom Graph tool scoped to the AVD groups, as the CLAUDE.md rule requires.

**Azure SRE Agent.** This is the closest Microsoft product to avd-swarm. It is a chat ops agent with:
- **Review and Autonomous run modes.** Writes are approved in chat by an "SRE Agent Administrator".
- Reader and Privileged modes, and on-behalf-of access requests.
- **Tool access policies** (global allow, ask and deny rules with argument globs; only the global scope can deny).
- **Agent hooks** (Stop and PostToolUse only, no pre-tool hook).
- Custom MCP servers, subagents and scheduled tasks.
- Deployment in Sweden Central, East US 2 and Australia East only.
- Its GA status is unstated on the pages read. A 30-day trial is GA. AVD is not mentioned.
- Sources: https://learn.microsoft.com/azure/sre-agent/run-modes, https://learn.microsoft.com/azure/sre-agent/tool-access-policies, https://learn.microsoft.com/azure/sre-agent/agent-hooks and https://learn.microsoft.com/en-us/azure/sre-agent/faq
- Gaps compared with avd-swarm: approval happens in chat with standing write permission, and **a hook that returns allow can override a global deny**. That is the anti-pattern your gate avoids.
- Recommendation: **watch, and borrow** the allow/ask/deny-with-argument-glob shape for `governance/policy.yml`. Consider it later as a Hosts observer or for incident triage, not as the platform.

**Azure Copilot agents.** These run in the portal: Troubleshooting (GA for VMs and VMSS; Entra in preview), Deployment, Optimization, Resiliency, Migration, and Observability (GA). Direct access started in August 2026 at no added charge, with an on/off switch per agent. Actions are "proposed, you confirm" in chat. AVD is not supported, and the pages show no MCP or extensibility.
- Sources: https://learn.microsoft.com/azure/copilot/agents-preview, https://news.baeke.info/articles/azure-copilot-to-offer-direct-access-to-specialized-agents-in-august-2026 and https://learn.microsoft.com/en-sg/Azure/copilot/troubleshooting-agent
- They are portal-bound, which is the opposite of avd-swarm's goal. Recommendation: **ignore**. At most, the Troubleshooting agent could be linked as a fallback for VM-level host faults.

**Microsoft Entra Agent ID.** It is GA, with preview creation wizards for blueprints and identities. New Conditional Access templates cover agents: autonomous agents, on-behalf-of agents, and **blocking high-risk agent identities**. Registry work is converging under Agent 365.
- Source: https://learn.microsoft.com/en-us/entra/agent-id/whats-new-agent-id
- **SPEC:** in §6, change "Entra Agent ID where available" to "use Agent ID", and add CA policies for the agent identities. A CA block is also a natural extra layer for `halt-agents`. Recommendation: **reuse**.

**Agent 365.** It provides a registry covering Foundry, Copilot Studio and third-party agents, a Tools view where admins can block MCP servers and approve tool requests, and monitoring. Its GA status (reportedly May 2026) and licensing are not confirmed in the sources read.
- Sources: https://redmondmag.com/blogs/redmond-dispatch/2026/09/microsoft-agent-365-adds-centralized-controls.aspx and https://www.epcgroup.net/blog/microsoft-agent-365-general-availability-may-2026-registry-sync-aws-google-cloud
- Recommendation: **watch**. It fits tenant-swarm more than avd-swarm.

**Security Copilot Entra agents.** The Conditional Access Optimization agent and the Identity Risk Management agent (preview) are billed in SCUs. They run on fixed identities whose permissions can't be changed.
- Source: https://learn.microsoft.com/en-us/entra/security-copilot/entra-agents
- Recommendation: **ignore** for avd-swarm, since it is tenant-wide; tenant-swarm should **watch** them.

**Windows 365 and AVD AI features.** Copilot in Intune gives Cloud PC insights and has four Intune agents, none of them for Windows 365. Nothing AVD-specific was found.
- Source: https://learn.microsoft.com/en-us/intune/copilot/
- The **MicrosoftDocs/Agent-Skills `azure-virtual-desktop`** skill is a Docs-grounded AVD guidance skill.
- Source: https://skillselion.com/skills/microsoftdocs/agent-skills/azure-virtual-desktop
- Recommendation: **reuse** the skill for the Readiness agent and the Claude Code front end. **Ignore** Intune Copilot.

## 2. AVD management tools

**Nerdio Manager.** Version 8.0 (NerdioCon 2026) "reintroduces" **Nerdio Copilot**, in-console AI help for troubleshooting and admin tasks. Its status is unstated, and no MCP, plan approval or external governance was found.
- Sources: https://storagereview.com/news/nerdiocon-2026-manager-8-0-brings-avd-hybrid-to-nutanix-adds-global-pools and https://vmblog.com/interview/nerdio-ai-powered-cloud-desktop-solutions-revolutionizing-microsoft-avd-and-windows-365-management/
- Recommendation: **watch**. It is the main commercial alternative for AVD operators, but it is a portal you install, and its AI sits inside that portal.

**Hydra (Login VSI).** It handles AVD and Windows 365 operations: scaling, images, a session agent, RBAC and multi-tenant management. The page shows no AI, chat or MCP features.
- Source: https://www.loginvsi.com/platform/hydra/
- Recommendation: **ignore**.

No AVD-specific AI ops agent or community AVD MCP server turned up. The Azure MCP `virtualdesktop` namespace is the only AVD tool surface.

## 3. Published patterns and open source

**Microsoft Agent Governance Toolkit.** It is MIT-licensed and was released in April 2026. It includes a policy engine (YAML, **OPA Rego** or **Cedar**; checks before each action), a kill switch, circuit breakers, SLOs, audit and compliance evidence, and an MCP security gateway. It ships as Python, TypeScript and .NET SDKs. It plugs into Agent Framework middleware and Foundry, and it covers the OWASP agentic top 10. The project is new, and its own samples are "not production-ready".
- Sources: https://opensource.microsoft.com/blog/2026/04/02/introducing-the-agent-governance-toolkit/ and https://devblogs.microsoft.com/agent-framework/governance-at-the-speed-of-agents-microsoft-agent-framework-and-agent-governance-toolkit-better-together/
- It overlaps the gate's rate, scope and allowlist checks and the agent-side flag check. Its kill switch is in-process, so it can't replace credential deletion or the separate Logic App.
- Recommendation: **integrate** it as an in-process tool-call guard on the agents, write policies in a standard language (Rego or Cedar) instead of a bespoke `policy.yml` schema, and keep the out-of-band switch.

**Copilot Studio external security webhook.** Before each tool call, Copilot Studio sends `analyze-tool-execution` to a provider and gets back an allow or block verdict. Providers include Defender and Zenity. The payload **includes the chat history and the planner's reasoning**, and the call **fails open after 1 second**.
- Source: https://learn.microsoft.com/en-us/microsoft-copilot-studio/external-security-webhooks-interface-developers
- It is the nearest shipped analogue to the observer `verdict` call, and it makes the opposite choices on both points. That supports avd-swarm's decisions: no conversation text (GQ1) and failing closed. It is Copilot Studio only. Recommendation: **ignore**, but cite it in decision 0002 as the alternative that was rejected.

**Defender for Endpoint AI agent runtime protection.** It is in preview. It inspects *local* agents (Claude Code, Codex CLI, Copilot CLI) for injection in tool output and blocks risky actions.
- Source: https://learn.microsoft.com/defender-endpoint/ai-agent-runtime-protection-overview
- Recommendation: **watch**. It is relevant only to the Claude Code front end, not to Foundry agents.

**APIM AI gateway.** These features are GA: **content safety on MCP tool arguments and responses and on A2A payloads**, prompt shields, and token metrics for **Anthropic Messages**. The API Center MCP registry is also GA.
- Source: https://infoq.com/news/2026/06/azure-apim-ai-gateway-build
- Recommendation: **watch**. It is an option for enforcing the daily token budget (§7) at a gateway rather than in code, but it adds APIM cost.

**Policy decision point outside the agent (Cerbos, OPA).** These tools give a deterministic allow or deny for each tool call, based on the user, the tool and its arguments, at every hop.
- Sources: https://cerbos.dev/blog/policy-driven-mcp-routing-tool-gating and https://dev.to/policylayer/what-is-mcp-policy-enforcement-and-why-every-agent-needs-it-5dpa
- They validate the gate's design. Recommendation: **reuse** the pattern, meaning a standard policy language, possibly through the Agent Governance Toolkit.

**Gartner Market Guide for Guardian Agents (2026).** It defines three required capabilities: visibility and traceability, continuous assurance, and runtime enforcement. It also recommends **a guardian layer independent of the platform being watched**. Deployments are mostly pilots so far.
- Source: https://thehackernews.com/2026/03/5-learnings-from-first-ever-gartner.html
- This matches §2.2's independent observer and second-opinion model. Recommendation: **watch** the vendors (Straiker, Onyx and Astrix are listed) as candidate third-party observers.

**Human approval of tool calls (HumanLayer, the 12-factor-agents pattern).** These tools ask for approval in Slack or email. LangGraph interrupts and Agent Framework's `always_require` work the same way.
- Sources: https://pypi.org/project/humanlayer/0.6.0 and https://www.developersdigest.tech/blog/12-factor-agents-production-principles
- In all of them, a model-side process holds the write credential and waits. avd-swarm's GitHub Environment plus OIDC means no write token exists before approval, which is stronger. Recommendation: **ignore**, and keep the current design.

**Kill switch and circuit breaker write-ups.** These are mostly vendor and blog patterns: revoke credentials, spend caps, quarantine.
- Sources: https://gravitee.io/corpus/gen-1099/anything-can-happen/kill-switch-design-patterns-for-ai-agents-mcp-servers-and-llm-gateways.html and https://www.kunalganglani.com/blog/ai-agent-kill-switch-spend-limits
- None found enforces in layers through credential deletion and a separate identity, as governance §2.3 does. Recommendation: no change.

**NeMo Guardrails and AgentTrust (LLM-as-judge or interception for tool use).**
- Sources: https://docs.nvidia.com/nemo/guardrails/latest/introduction.html and https://arxiv.org/pdf/2605.04785
- Recommendation: **watch**. These are options for the "second-opinion model" observer. Foundry's task-adherence guardrail covers part of the same ground at no build cost.

## 4. "Chat to manage Azure and Entra" end to end

Several products cover parts of this: Azure Copilot with its agents (portal), Azure SRE Agent (chat with in-chat approval), the ARM MCP server and Azure MCP Server driven from GitHub Copilot or Claude Code (developer-local, delegated), and Nerdio Copilot (inside the Nerdio console). **None combines an AVD lifecycle scope, out-of-band per-plan approval with no standing write credential, a deterministic gate, an external observer and a layered kill switch.** avd-swarm's distinct value is its governance and write path, not its chat or read tools. Build that, and reuse the rest.

## Recommendations for the spec

1. **§3 and §4:** replace "connected agents" with an Agent Framework handoff or group-chat workflow running as a hosted agent. Connected agents are gone from the GA service, and portal workflows retire on 2026-12-01.
2. **§4 and §7:** confirm Claude Opus 5.5 is available in Foundry for prompt or hosted agents in your region and billing type. Expect hosted agents, and change §7's "no service fee" to "container compute that scales to zero". Record the regions (East US 2, Sweden Central) in §6.
3. **SQ1:** resolved. Foundry supports `user-entra-token` passthrough, but that means avd-buddy forwards a token. Pick agent identity for reads in S1, or amend §3.
4. **SQ2:** resolved. `forecast_costs` exists in the ARM MCP server's CostManagement toolset (preview, and not read-only as a whole). Allow only its read tools, or wrap the Cost Management REST calls in the custom read tool.
5. **§3 read tools:** name the Azure MCP Server `virtualdesktop` namespace and run it with `--read-only`, an explicit namespace list and `allowed_tools` per agent, using the `azmcp-foundry-aca-mi` template. Narrow SQ5 to what that namespace lacks.
6. **§6:** make Entra Agent ID mandatory, add Conditional Access for the agent identities (including blocking high-risk agents), and use a CA block as another enforcement layer for `halt-agents` in governance §2.3.
7. **§5:** state that Foundry's `require_approval` and `mcp_approval_request` are never the write approval. They may only be defense in depth on `propose_change`.
8. **Governance §2.1:** consider writing `policy.yml` in Rego or Cedar, evaluated through the Agent Governance Toolkit, so the same policy runs in agent middleware (early refusal) and in the job (authoritative).
9. **Governance §2.2:** add Foundry guardrails (indirect injection and task adherence on tool calls and responses) and Foundry tracing as inputs. List the Copilot Studio webhook as the rejected alternative, since it sends text and fails open.
10. **Users agent:** keep a narrow AVD-scoped Graph tool. Microsoft MCP Server for Enterprise is read-only but tenant-wide; adopt it in tenant-swarm.
11. **§1 positioning:** say plainly that avd-swarm's value is the governed write path. Revisit the build-or-reuse choice for read and chat if Azure SRE Agent adds AVD and out-of-band approval.
