# S1a spike: does the S1 stack actually work?

- **Status:** Planned (2026-10-17). Nothing in this plan has run yet.
- **Part of:** [spec.md](../spec.md) §9 (S1a). Estimated and closed through the spend ledger ([spend.md](../spend.md)).
- **Timebox:** one working session, at most 6 hours of deployed time, torn down the same day.

## 1. What it answers

Each item ends **confirmed** (with evidence) or **replaced** (with what S1 uses instead). The spike makes no writes to anything outside its own resource group, and there is no write path in it at all.

| # | Question | Confirmed when | If not |
|---|---|---|---|
| Q1 | Can the sandbox subscription deploy Claude in Foundry at all? Claude needs Marketplace access, and CSP and sponsored subscriptions are excluded (prior art) | Claude Opus 5.5, Sonnet 5.5 and Haiku 5.5 deploy in East US 2 (Global Standard) and answer a test prompt | The workflow calls the Anthropic API directly (spec §6, "the provider is a setting"). The rest of the spike still runs |
| Q2 | Does an Agent Framework handoff workflow run as a Foundry **hosted agent**, with Claude as the model? | Coordinator (Haiku 5.5) hands a question to the Hosts specialist (Opus 5.5) and the answer comes back merged, inside one hosted agent | The workflow runs in a Container App as plain Agent Framework code (Foundry for models and tracing only) |
| Q3 | Does the Azure MCP Server work as the read tool, with `--read-only` and only the `virtualdesktop` namespace, hosted from the `azmcp-foundry-aca-mi` template? | The Hosts agent lists the host pool and its (zero) session hosts through it; a write tool is absent from its tool list | Note which flag or template part failed; S1 wraps the same reads in the custom MCP server (SQ5) |
| Q4 | Does `allowed_tools` per agent hold? | The coordinator can't call the MCP tools; Hosts can call only its listed ones. A prompt asking the coordinator to list host pools itself is refused by the platform, not by the prompt | The tool layer enforces the allowlist in code (S1 does that anyway as a second layer) |
| Q5 | Do the agents get **Entra Agent ID** identities, and can Conditional Access block them? | The hosted agent's tool calls show up under an agent identity in the sign-in logs; a report-only CA policy targeting it records a match | Note what's missing; governance §2.3 layer 4 stays a design item |
| Q6 | What does a question cost, and which model should the Hosts specialist use? | Token counts and answers for the 10 eval questions with the Hosts agent on each of Opus 5.5, Sonnet 5.5 and Haiku 5.5 (one hosted agent per model, same code and allowlist), from the run log and the Foundry metrics | Can't fail; it's a measurement. It sets S1's specialist model and daily token cap (spec §7) |

**Cost levers measured, not assumed (Q6).** Every agent runs with automatic prompt caching on (tool definitions and instructions are resent on each tool-loop step; cache reads bill at a fraction of input), an explicit effort (coordinator low, Hosts medium), and a tool allowlist that keeps tool definitions small. Each answer's full usage, including cached tokens when the platform reports them, goes into `evals*.jsonl`, with a rough expected-facts check per question so a cheaper model's answers can be compared, not just its price.

Not in scope: avd-buddy, the plan store, `approve`, the observer, the kill switch, Graph, and the other specialists.

## 2. What it deploys

Everything in one throwaway resource group, `rg-avdswarm-spike`, with its resources in **East US 2** (Claude in Foundry's region; ARM reads are global, so the landing zone's region doesn't matter).

**Location policy.** A subscription with the landing zone's guardrails (such as `avd-sandbox`) denies resource groups and resources outside the deployment region (`avdlz-allowed-rg-locations` and `avdlz-allowed-locations`, North Central US only). So the spike, by default:
- creates the resource group **in North Central US**, which the policy allows (a resource group's own location doesn't have to match its resources');
- adds a **policy exemption** for `avdlz-allowed-locations` scoped to **that resource group only**, category `Waiver`, expiring at the end of the spike day, with the run ID in its description;
- deletes the exemption at teardown (it also expires by itself). The spend ledger's teardown check compares resources, and an exemption isn't one, so the teardown block checks for it by name and fails if it's still there.

The rest of the subscription stays guarded throughout. A subscription without those guardrails skips the exemption.

| Resource | Why | Size |
|---|---|---|
| Foundry resource + project | Model deployments, hosted agent, tracing | — |
| Claude Opus 5.5, Sonnet 5.5 and Haiku 5.5 deployments | Coordinator (Haiku) and the Hosts specialist on each model for Q6 | Global Standard, pay per token |
| Hosted agents (three) | The Agent Framework handoff workflow (coordinator → Hosts), one per Hosts model | Scale to zero after 15 minutes idle |
| Container App (from `azmcp-foundry-aca-mi`) | Azure MCP Server, `--read-only --namespace virtualdesktop` | Min replicas 0, smallest size |
| Log Analytics workspace | The Container Apps logs, including the MCP server's record of which tools were called (Q4) | Pay per GB, capped at 0.5 GB a day |
| AVD host pool + workspace, **no session hosts** | Something for `virtualdesktop` to read. Host pools and workspaces have no charge of their own; session hosts are what cost money, and there are none | — |

The agents' identities get **Reader on this resource group only**. Nothing reads the dev landing zone, so the spike can't touch it.

## 3. Cost estimate (spend.md format, kind `other`)

A spike isn't an AVD deployment, so it uses `-Kind other -ManualAmount` with a note, per tenant-swarm's spend.md.

**Model tokens**, priced at Anthropic's list prices, which Claude on Foundry bills at through the Marketplace (Opus 5.5: $4 / $20 per million input / output tokens; Sonnet 5.5: $2 / $10; Haiku 5.5: $0.10 / $0.50). Assumption: 20 conversations with Hosts on Opus (`Invoke-Spike.ps1` asks 13: q01, the two Q4 probes and the ten evals; the rest is room for a rerun), then the ten evals again with Hosts on Sonnet and on Haiku. Each Hosts conversation reads about 40k input tokens: the system prompt, the MCP tool definitions, and the tool output, sent again on every turn of the tool loop.

| Line | Quantity | Amount |
|---|---|---|
| Opus 5.5 input (Hosts specialist) | 20 × 40k = 800k tokens | $3.20 |
| Opus 5.5 output, thinking included | 20 × 4k = 80k tokens | $1.60 |
| Sonnet 5.5 input and output (Hosts, 10 evals) | 10 × 40k in, 10 × 4k out | $1.20 |
| Haiku 5.5 input and output (Hosts, 10 evals) | 10 × 40k in, 10 × 4k out | $0.06 |
| Haiku 5.5 input and output (coordinator, all 40) | 40 × 10k in, 40 × 1k out | $0.06 |
| **Tokens, expected** | | **$6.12** |
| **Tokens, upper bound** (tool output twice as large as assumed) | | **$13.00** |

**Azure lines: unpriced until the start block prices them.** I couldn't reach the retail prices API from here, and the rule is never to guess a price. Step 1 below prints the matching meters, and the run starts only once each line has exactly one meter or is written down as unpriced.

| Line | Quantity |
|---|---|
| Hosted agent compute | Three agents, at most 6 hours each at 0.5 vCPU / 1 GiB |
| Container Apps (Azure MCP Server) | At most 6 hours active, 0.5 vCPU / 1 GiB |
| Log Analytics ingestion | Under 0.5 GB (the workspace's daily cap) |
| Foundry resource | Check whether it has any fixed charge |

**Proposed caps:** run cap **$20**, so the token upper bound plus about $3 of Azure fits and anything larger is refused. (Revised 2026-10-18: the one-command script needs about half the Opus conversations first assumed, and Q6 now compares three Hosts models.)

**Stop rule while it runs:** if the Foundry metrics show more than 2M input tokens (about 2.5× plan), stop, tear down, and write down why.

**If it's not torn down:** the hosted agent and the Container App scale to zero, so per day it's close to the Log Analytics retention and any Foundry fixed charge. Teardown is still required the same day.

## 4. Steps

The runbook is [spikes/s1a/README.md](../../spikes/s1a/README.md): nine self-contained Cloud Shell (PowerShell) blocks, run as you in the sandbox subscription.

1. **Price and open the run:** `Get-SpikeEstimate.ps1` prices the Azure lines from the retail prices API (exactly one meter per line, or it reports what it saw), then tenant-swarm's `Start-SpendRun.ps1 -Kind other -ManualAmount ... -RunCap 20`. A refusal stops the spike.
2. **Deploy and Q1:** `Start-Spike.ps1` creates the resource group and the exemption, deploys `infra/main.bicep`, gives you Foundry User and Foundry Project Manager on the Foundry resource, and deploys the two Claude models by the names and versions the resource actually offers. `test_models.py` calls each with an Entra token.
3. **Q3:** `list_mcp_tools.py` lists every tool the MCP server exposes with its read-only and destructive hints, as you (the server's Entra app pre-authorizes the Azure CLI).
4. **Q2:** `deploy_agent.py` creates the hosted agent from code (`spikes/s1a/agent`), routes traffic to it and prints its agent identity; `Connect-SpikeAgent.ps1` gives that identity the MCP server's app role; the first eval question checks the handoff end to end.
5. **Q4:** two probe questions, plus the MCP server's own log of which tools were called.
6. **Q5:** the agent's startup log of its identity claims, its sign-in logs, and a report-only Conditional Access policy (portal steps, recorded as found).
7. **Q6:** the ten eval questions with Hosts on Opus, then again on a second agent each for Sonnet and Haiku, then `Get-SpikeToken.ps1` for token totals per deployment from Azure Monitor.
8. **Evidence** stays in `~/clouddrive/avdswarm-spike/`, never in this repo.
9. **Tear down:** `Remove-Spike.ps1` deletes the group, purges the soft-deleted Foundry resource, deletes the MCP app registration and checks for every leftover, including the exemption, which the ledger's resource check can't see. Then `Stop-SpendRun.ps1`.

## 5. Result (filled in after the run)

| # | Result | Evidence |
|---|---|---|
| Q1–Q6 | | |
| Cost | Estimate / actual (settled) | Run record path on the `ledger` branch |
| Teardown | Clean / leftovers | Run record |

Spec §3, §6, §7 and §9 change to match the results in the same PR.

## 6. What you need before it can start

- **Sandbox:** your personal tenant, spend sandbox `personal` (decided 2026-10-18). If its subscription turns out to be a Visual Studio, sponsored or CSP subscription, Q1 is answered "replaced" in block 2 before any model spend.
- tenant-swarm's spend setup for `personal` (`setup-spend-sandbox.sh`), or the caps passed as parameters in Cloud Shell, as the runbook does.
- If the subscription has the landing zone's guardrails, the exemption in §2 is part of the run.
- The GitHub CLI signed in once in Cloud Shell, because both repos are private (block 1 does it).
- Quota for Claude in Foundry in East US 2 (block 2 reports it if it's missing).

## 7. Fallback without Foundry (`-Provider Anthropic`)

Added 2026-10-18, when the subscription had no Claude quota in Foundry (0 TPM for every Claude model in East US 2; requested through Microsoft's quota form). It answers what it can while the request is open; the Foundry run still follows when quota arrives.

**What runs.** `agent_api/main.py` in a Container App (North Central US, one replica): the coordinator (Haiku 5.5) gets one tool, a handoff; the Hosts agent gets the MCP server's tools, filtered to the allowlist in code. Claude is called through Anthropic's Messages API with automatic prompt caching and explicit effort (coordinator low, Hosts medium), as on Foundry. The agent identity is made through Graph without Foundry: an agent identity **blueprint** (you as sponsor and owner), the Container App's user-assigned managed identity as the blueprint's federated credential, and an **agent identity** the blueprint creates with its own token. The Hosts agent calls the MCP server with the agent identity's token (managed identity token, then blueprint token for the agent identity, then the agent identity's token for the MCP app). Questions run as jobs the eval script polls, because Container Apps ingress ends requests after 240 seconds.

| # | Answered? |
|---|---|
| Q1 | No: Foundry isn't used. Stays open until the quota request is answered |
| Q2 | Partly: the handoff runs as plain code in a Container App (the "if not" column), and it shows whether an Agent ID can be created and used without Foundry |
| Q3 | Yes, unchanged |
| Q4 | Partly: the allowlist is enforced in code, not by the platform; the probes still run |
| Q5 | Yes for the agent identity: the MCP token's claims are recorded per question, and its calls show in Entra's sign-in logs under the agent identity |
| Q6 | Yes, with Anthropic's list prices; the three Hosts models are compared the same way |

**The one secret.** Anthropic's API has no Entra or federated sign-in, so this path needs an API key. It's an accepted exception to the no-secrets rule for this spike only: typed into a hidden prompt, held in the PowerShell session and the Container App's secret, passed to the deployment through a parameters file that's deleted right after, never written to the state folder or a repo, and gone with the resource group. Create a key just for the spike, with a spend limit on the Anthropic workspace, and delete it after teardown.

**Cost.** Azure: the Container Apps and logs, under $1 for a few hours. Tokens are billed by Anthropic, not Azure, so they're outside the Azure spend check: the 12 eval questions on three Hosts models are expected around $8, and the 2M input token stop rule caps them near $15. Record Anthropic's charge for the run (Console, Usage) in the spend run when you close it.

