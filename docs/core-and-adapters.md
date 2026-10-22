# avd-swarm: review and the plan for an open-source core with optional adapters

Fable review, 2026-10-21, of `avd-ops/avd-swarm` at `b49c03b` (main). Review and plan only; nothing in the repo was built and nothing ran against Azure. The owner's framing (2026-10-21): Azure SRE Agent (about $292 a month until deleted), Logic Apps Automation (preview, about $37 a month idle) and API Management's AI gateway (Basic v2, about $150 a month) are cost-prohibitive for a personal project and poor fits besides. avd-swarm builds **its own open-source alternatives**, and the paid Microsoft services plug in as **optional adapters**: off by default, never required, never the thing that does the job ([avd-second-brain decision 0023](https://github.com/avd-ops/avd-second-brain/blob/main/docs/knowledge/decisions/0023-family-format-best-open-source-or-build.md)).

Everything below is measured against one test: **can an adopter run the whole of avd-swarm from this public repo and a free GitHub account, paying only for the tokens it uses, with every paid Microsoft service a switch they may leave off?**

Status: accepted by the owner on 2026-10-21 ("yes to direction and defaults", every item in [section 8](#8-what-needs-nick)). The work packages in section 7 may start in that order; the build order in [spec.md](spec.md) section 9a still decides when anything that spends money runs.

Progress (2026-10-21): WP0 (spec sections 3, 3a, 3b, 3c, 6 and SQ5, SQ11), WP1 (`runbooks/`, `catalog/kql/`, `scripts/check_runbooks.py`), WP3 in library mode (`gateway/`) and WP5's manifests and check (`adapters/`, `scripts/check_adapters.py`) are built, offline only; WP2's code is built offline (`runner/avd_runner/`, `templates/workflows/runbooks.yml`, the runner identity), and its first scheduled run waits on an estimate and the owner's go. WP6 is next; WP4, WP5's drill rows and WP7 wait for step 2's exit, after WP8's red team.

## 1. Verdict in five lines

1. **The design already is an open-source core.** The governed write path, the gate, the layered kill switch, the metered model client, the Prompt Shields scan, the decoy tools, the spend guard and the claim check are all ours, MIT, and run on Microsoft's platform pieces that are also MIT (Agent Framework, the Azure MCP Server). Nothing in build order steps 1 to 3 depends on a paid Microsoft agent service. That test passes today on paper.
2. **The spec still leans on SRE Agent in three places that no longer hold.** Section 3a records the owner's 2026-10-20 choice to use SRE Agent as the interim investigator (dropped 2026-10-21); SQ11 says "yes"; and the scheduled health-check job is written as "the brain's Tier 1 plus SRE Agent adds to it", with no design of our own for scheduled checks, runbooks or alert-triggered investigations. Those are the gaps this plan fills.
3. **Three capabilities need a design of their own:** runbooks as code (what SRE Agent's skills, knowledge files and scheduled tasks do), a scheduled and alert-triggered check runner (what Logic Apps Automation and SRE Agent's scheduled tasks do), and a model and tool gateway (what APIM's token limits and MCP governance do). The metered client in the S1a spike is the seed of the third; the first two have no seed yet.
4. **No outside open-source project does our job, but four are the parity checklist.** HolmesGPT (Apache-2.0, CNCF sandbox) for the investigator and scheduled checks; Bifrost (Apache-2.0) and LiteLLM (MIT core, paid gates on SSO, JWT auth and audit logs) for budgets and per-key allowlists; IBM's MCP Context Forge (Apache-2.0) for MCP federation and tool policy; Microsoft's Agent Governance Toolkit (MIT, public preview) for the policy engine, already in the spec with limits. Each fails at least one of our requirements (keyless Entra identities end to end, the kill switch flag before every call, pseudonymized facts, no Kubernetes, Cloud Shell), so under decision 0023 we build and they stay the checklist.
5. **The default deployment costs about ten dollars a month fixed plus tokens.** The adapters add $37 to $292 a month each, and each one needs a spend estimate and the owner's go before it is turned on, in the sandbox or anywhere.

## 2. Review findings

Read against the goal, at `b49c03b`. "Fix" means this plan proposes it. The PR that carries this plan changes only two things in the spec, to record a fact: section 3a's opening and SQ11 say the interim SRE Agent use was dropped on 2026-10-21. Everything else, including the rest of section 3a and the two sentences in section 3 and section 3a that still call an SRE Agent connector "the second trigger" to publish the tool layer, waits for WP0.

| # | Finding | Where | Fix |
|---|---|---|---|
| F1 | Section 3a opened with "the owner decided on 2026-10-20 to use Azure SRE Agent for what it does ... while avd-buddy and the agents are still built." The 2026-10-21 decision dropped the pilot (this PR records that in the opening and in SQ11; the rest waits). The design under it (Policy deny, `halt-all` rows, Standard User role, evidence provenance) is still the right adapter design and keeps its red-team pedigree (V1-1 to V1-5, V1-29, V1-30, V1-33) | spec section 3a, SQ11, v1-review D10 and D11 | WP0 rewrites the section as an adapter page, keeps the controls and marks D10 superseded on the brain side |
| F2 | Scheduled health checks have no design of our own. Section 3a says the brain's Tier 1 (scheduled preflight, detections, episode issues) is the baseline, but Tier 1 runs in the brain's repo as Claude Code in GitHub Actions and reads C11 facts; it does not run the Hosts agent's reads on a schedule, and nothing here says how a check is defined, scheduled, run, reported or costed | spec sections 3a, 4, 9a | WP1 (runbooks as code) and WP2 (the check runner) |
| F3 | "Runbooks" appear only as something uploaded to SRE Agent ("avd-swarm's catalog as text") and as the brain's playbooks (C9, step 4). There is no format in this repo for a diagnostic procedure an agent follows, with its reads, its expected facts and the catalog action it may propose. SRE Agent's skills, knowledge files and `.runbook`-style typed blocks are the prior art | spec sections 3a, 5, 6d | WP1 |
| F4 | Alert-triggered investigation is not designed: section 3a let SRE Agent receive Azure Monitor alerts; without it nothing here wakes an agent when an alert fires. The watchdog's alerts go to the kill switch, not to an investigation | governance section 2.4, spec section 3a | WP6 |
| F5 | The metered model client exists only inside the spike (`spikes/s1a/agent_api/meter.py`, `shield.py`) with prices hard-coded in Python; spend.md section 3.7 wants `governance/model-prices.yml` (protected), a counter outside the agents, per-run, per-day and per-month caps, and the brain consuming the client as a pinned package. It is also in-process only, so two processes (the agents, a scheduled check job, the brain's Tier 3) would each meter alone | spend.md section 3.7, governance section 2.3 layer 6 | WP3 builds the gateway package out of the spike's code |
| F6 | The tool layer's MCP publication has two triggers in the spec (Foundry's Toolbox, an SRE Agent connector). With SRE Agent dropped, the second trigger is gone and the first is a Foundry hosting detail. An MCP server for other clients (Claude Code, a scheduled runner in another process) is still "a later step" with no work package | spec section 3, SQ5 | WP4 gives it a step and an acceptance test; SQ5's v1 answer stands; WP0 removes the two "second trigger" sentences (section 3, section 3a) |
| F7 | There is no provider or adapter interface. Section 3a is one adapter described in prose; nothing says what every adapter must declare (what it replaces, its meters, its identity, how the kill switch reaches it, which events it emits) or what CI checks for it (off by default, nothing depends on it) | spec sections 3, 3a | WP5 |
| F8 | Model hosting is still an either-or in prose ("Claude in Foundry ... if not, the Anthropic API directly"); the spike built both paths. The cheaper, keyless Container App plus Anthropic federation path is the one every control was red-teamed on (R21, R26, layer 6) and should be the default; Foundry Agent Service hosting is an adapter | spec section 6, section 3 | WP0 names the default; Foundry becomes the `model-host` adapter |
| F9 | Section 3 still says "Foundry guardrails and tracing ... are on when the agents run in Foundry" as if that were the main path, and the architecture diagram's first box is "Foundry Agent Service: hosted agent". Both read as Foundry-first | spec section 3 | WP0 redraws the diagram with the Container App path first and Foundry as the adapter |
| F10 | `README.md`'s "Try it in a lab" says the spike needs Claude quota in Foundry and offers `-Provider Anthropic` as the fallback. Reversed under this plan | README | WP0 |
| F11 | The kill switch's own Logic App (Consumption) is a Microsoft service with a meter, but a pay-per-execution one measured in cents, with no idle charge, and it is the independent enforcer by design (R5). It is not an adapter and stays | governance section 2.3 | none; recorded so the rule "no paid Microsoft service required" is read right: *no standing-meter agent service required* |
| F12 | What held up, unchanged: no write token before a person approves the exact plan; the plan store written only by `propose_change`; the four switch positions and the Cloud Shell-only reset; stop-spend never blocked; the base pair invariant; claims on every edge; the judge auditor's rules; the spend guard with run records outside the repo | spec sections 2, 4a, 5, 5b, 6a; governance; spend.md | none |

## 3. The target: an open-source core, a provider interface, and adapters

### 3.1 Capability table

Each row is one thing a paid Microsoft service would do for an adopter, what avd-swarm does instead with open-source code, the open-source prior art that is the parity checklist, and the optional adapter that may add to it. "Core" means it is in this repo, MIT, and the v1 exit tests use it. "Adapter" means an adopter may turn it on; nothing in the core, CI or an exit test depends on it.

| Need | Paid service it maps to | Open-source core (ours, in this repo) | Parity checklist (open source, used as prior art only) | Optional adapter |
|---|---|---|---|---|
| **Investigate** ("why is host 3 unhealthy", "what changed in the last hour") | Azure SRE Agent (investigate and advise) | The Hosts, Cost and later agents in an Agent Framework workflow (MIT), reading through the Azure MCP Server `--read-only` (MIT), Log Analytics and Resource Graph, answering through the claim check (spec section 4a) | HolmesGPT: read-only investigator, toolsets declared in YAML with per-toolset instructions, any LLM provider, alert fetch and write-back; runs its Azure reads through Azure's `azure-api-mcp` (MIT, `--readonly`, managed identity) | `investigator: sre-agent` (spec section 3a's design): avd-buddy starts an SRE Agent thread and shows its findings labeled; a fix comes back only as `propose_change` |
| **Scheduled health checks** (daily preflight, drift, idle hosts, cost tail) | SRE Agent scheduled tasks; Logic Apps Automation schedules | **Runbooks as code** (WP1): Markdown files with a front matter that names the trigger, the reads, the expected facts and the one catalog action the runbook may propose. **The check runner** (WP2): a scheduled job in the adopter's private repo (GitHub Actions `schedule`, OIDC read-only identity) or a Container Apps Job, which runs the runbook's reads through the same agents and tool layer, writes findings as issues and avd-buddy cards, and counts them for C8 | HolmesGPT Operator: `HealthCheck`, `ScheduledHealthCheck` and `TriggeredHealthCheck` as declarative resources with execution history and cost warnings (Kubernetes CRDs, alpha); SRE Agent's skills and knowledge files (Markdown, 50 MB, 1,000 files per agent) | `scheduler: logic-apps` (Standard or Automation) as a trigger source only, once it can be kept as code |
| **Alert-triggered investigation** | SRE Agent incident response (Azure Monitor alerts, PagerDuty, ServiceNow) | An Azure Monitor alert rule whose action group calls a Consumption Logic App; its managed identity puts a message holding the alert ID only on a storage queue; a Container Apps Job with the queue scaler and a read-only managed identity reads the alert back from Azure Monitor and runs the runbook whose `trigger.alert` matches (WP6). Keyless end to end: GitHub has no inbound OIDC, so nothing in Azure holds a GitHub credential and no workflow is started by the alert. Findings carry `threadStartedBy: alert`, a value WP6 adds to section 3a's provenance vocabulary and to the gate's V1-4 rule, so a plan whose only evidence is an alert-started run is refused like one from a schedule | HolmesGPT's alert integrations (AlertManager, PagerDuty) | `incident: sre-agent`, `incident: pagerduty` (the latter is not a Microsoft service; listed because the adapter shape is the same) |
| **Runbook content, memory and knowledge** | SRE Agent knowledge base, retained context | The brain's knowledge (C6), lessons and decisions (C7), episodes and the Concierge, the action catalog's text; runbooks in `runbooks/` cite them by pinned commit | SRE Agent knowledge files; HolmesGPT's Confluence toolset | none needed; an adapter that uploads reviewed public knowledge to SRE Agent is part of `investigator: sre-agent` |
| **Deterministic workflows** (approve, stop-spend, drills, sync from a release tag) | Logic Apps Standard and Consumption; Logic Apps Automation | GitHub Actions workflow **templates** in `templates/workflows/`, run from the adopter's private repo (spec section 5b); Bicep for everything deployed | n/a (GitHub Actions is the platform, like Azure) | `scheduler: logic-apps` as above |
| **Agentic orchestration** (handoffs, fan-out, concurrent steps) | Logic Apps Automation agents; Foundry Agent Service workflows | Agent Framework workflows (MIT) in our own process (the spike's `agent_api`), with the fan-out limits in `governance/answers.yml` | Agent Framework itself | `model-host: foundry` runs the same workflow as a Foundry hosted agent (the spike's `agent/`) |
| **Model gateway** (per-agent token caps, daily and monthly caps, pinned prices, keyless auth, audit) | APIM AI gateway (`llm-token-limit`, Basic v2 and up) | **The gateway package** (WP3), grown from the spike's `meter.py` and `shield.py`: one metered client every process uses, Entra tokens only (Anthropic workload identity federation; Foundry and Azure OpenAI with Entra), `governance/model-prices.yml`, the kill switch flag before every call, the Prompt Shields scan, per-caller caps, a counter outside the agents, usage events to the observer | Bifrost: virtual keys with hierarchical dollar budgets (customer, team, key, provider), rate limits, MCP tool allowlists per key, default deny; LiteLLM: per-key and per-team budgets (MIT core; JWT auth, SSO and audit logs are paid) | `gateway: apim` in front of the gateway package for adopters who already run APIM; never instead of it |
| **Tool governance** (per-caller tool allowlist, default deny, audit of every tool call, tool poisoning checks) | APIM MCP server governance | The tool layer (spec section 3): per-agent allowlists as protected files, decoy tools, refusal counts, the untrusted-text scan; published as an MCP server with Entra app-role auth when another process needs it (WP4) | IBM MCP Context Forge: MCP federation, REST-to-MCP, per-server policy; Bifrost's per-key tool allowlists; the Agent Governance Toolkit's MCP security gateway (tool poisoning, typosquatting, hidden instructions) | `gateway: apim` as above |
| **Policy evaluation** on every plan | SRE Agent tool access policies (allow, ask, deny) | `governance/policy.yml`, deterministic code, evaluated by the Agent Governance Toolkit (MIT) **inside the limits of governance section 2.1** (allow or deny only; its approval, quorum, in-process switch and MCP gateway are never used; vendored and offline), or by our own code if it can't run offline | The toolkit itself (public preview, Rust core, YAML, OPA and Cedar policies) | none |
| **Approval, write identity, kill switch, observers, drills** | SRE Agent Review mode and Approve button | Ours, unchanged ([section 5](#5-governance-unchanged)) | The toolkit's `require_approval` (rejected: in-chat, no federated credential) | none, by rule |
| **Spend control** (estimate, run record, teardown evidence, reconciliation) | none (no Microsoft service does this) | `spend/spend_guard.py` and spend.md | microsoft/budget-control-sample (pattern for the backstop), ACE arm-estimator (a cross-check) | none |

### 3.2 Runbooks as code (WP1)

A runbook is a file in `runbooks/<area>/<name>.md`: Markdown a person reads, with a YAML front matter code reads. It is the open-source answer to SRE Agent's skills and knowledge files and to Logic Apps Automation's model-chosen steps, with one difference: **the steps are fixed and the model only interprets what they return.**

```yaml
---
id: hosts.agent-unhealthy
version: 1
trigger:
  schedule: "0 6 * * 1-5"          # or alert: <alert rule name pattern>, or manual
  runMode: read-only                # the only value in v1; "propose" allowed from step 3
agent: hosts
reads:                              # each must be in the agent's allowlist; the runner refuses otherwise
  - tool: virtualdesktop_sessionhost_list
  - kql: catalog/wvd-agent-health-last-24h   # from the KQL catalog, by ID, never inline
expect:                             # facts the runner checks in code before the model sees anything
  - fact: sessionHosts.unhealthy == 0
    onFail: investigate            # or: report
# proposes: restart-avd-agent       # only with runMode: propose (step 3): at most one catalog action
evidence: [c11.hostPools.loadBalancer]   # C15 register IDs it may cite
budget: { tokensUsd: 0.50 }         # per run, inside the daily cap
---
# Session host agent unhealthy
What this checks, why, what "unhealthy" means (link to the brain's playbook at a pinned commit), and what the operator does when the runbook can't.
```

Rules the checker (`scripts/check_runbooks.py`, pure, offline, in CI) enforces: every read is in the named agent's allowlist; every KQL is a catalog ID; every evidence ID is in the pinned C15 register; `proposes` names a catalog action whose preconditions (spec section 5a) are declared; a `schedule` runbook has a budget; `runMode: propose` runbooks are protected fields like the catalog's. A runbook is content, not a prompt: the agent's prompt stays a protected path, and the runbook's Markdown reaches the model as untrusted text through the same scan as a tool result.

### 3.3 The check runner (WP2)

Runs runbooks on their trigger, in the adopter's private repo, under the rules of spec section 5b. Two hosting shapes, same code:

- **GitHub Actions `schedule`** (default for schedules): one workflow template per trigger kind; the job signs in with the read-only runner identity through OIDC, calls the agent API (the spike's `agent_api`, or the tool layer's MCP server once WP4 exists) with the runbook ID, and writes the result. Free minutes on a private repo cover daily checks; the first step reads the kill switch flag and exits at `halt-agents` or above, like the brain's Tier 1 (C10).
- **Container Apps Job** (alert-triggered runs, and schedules for an adopter who wants checks inside their network): cron or queue trigger, scales to zero, managed identity, same code.

**The runner never calls a model.** It asks the agent API to run a runbook and waits for the result; the agent API is the one process that holds the meter, so the runbook's `budget` and the daily cap are enforced there (section 3.4) whichever host the runner is on. The runner's identity is read-only and has no model credential.

What it writes: a finding per runbook run (runbook ID, outcome `clear`, `finding` or `could-not-run`, the supported claims with citations, the proposed plan ID if any), to the plan store's findings table and as an avd-buddy card; a GitHub issue only when the outcome changed since the last run (so a standing problem makes one issue, not one a day); counts to C8 (`questions[].topic` gains runbook IDs; a `checks` block is proposed to the brain). Never a resource name in a job summary (R17).

What it costs: tokens per run, metered by the gateway and capped by the runbook's budget; nothing else when it runs on Actions.

### 3.4 The gateway package (WP3)

`gateway/` in this repo: `meter.py` and `shield.py` move out of the spike as a package every process imports (the agents, the check runner, the judge, Improve, Scribe, and the brain's Tier 3 as a pinned package), with these additions over the spike:

- prices from `governance/model-prices.yml` (protected), not Python constants; a model with no price is refused;
- caps per caller, per run, per day and per month, read from `governance/`; the counter in a table outside the agents with optimistic concurrency (spend.md section 3.7 item 3);
- providers behind one interface: `anthropic` (workload identity federation, the built path), `foundry` and `azure-openai` (Entra tokens); each declares the model IDs it serves and the price rows it needs;
- the kill switch flag check and the Prompt Shields scan stay where they are, before every call;
- `usage` events to the observer (counts and dollars, never text).

The gateway is a library first, inside the agent API, which is the only process that calls a model in build steps 1 to 3 (the runner asks the agent API to run runbooks, section 3.3). It becomes a service (a Container App that other processes call with an Entra token) only when a second model-calling process exists: the judge, Improve, Scribe or the brain's Tier 3, all step 4. Compared with Bifrost's and LiteLLM's proxies: ours has no virtual keys at all (a key is a secret, and the rule is no secrets), so a caller is its Entra identity and its cap is looked up by that identity's app role.

### 3.5 The tool layer as an MCP server (WP4)

The tool layer stays plain functions in v1 (SQ5). WP4 publishes the same layer, with no change to what it exposes (read tools and `propose_change` or `propose_playbook_run` only), as an MCP server with Entra app-role auth, for three callers in this order: the check runner when it moves out of process, Claude Code for the owner, and an adapter's connector (SRE Agent, if ever on). Per-caller allowlists, default deny, the flag check on every call (governance section 2.3 layer 5), and the drill's "refused at each position" test (V1-3). The official Python MCP SDK (MIT) is the implementation; Context Forge and Bifrost are the checklist for what a governed MCP endpoint logs and refuses.

### 3.6 The provider interface (WP5)

`adapters/<name>/adapter.yml` plus a page, one per paid or closed service, with the schema in `adapters/schema.json` and a check in CI (`scripts/check_adapters.py`):

| Field | What it declares |
|---|---|
| `kind` | `investigator`, `incident`, `scheduler`, `gateway`, `model-host`, `policy-engine` |
| `replaces` | Nothing. An adapter adds to a core capability; the field names the core capability it adds to, and the check fails an adapter that claims to replace one |
| `default` | Always `off` |
| `meters` | The retail meters it bills on, with a note on standing charges ("bills until deleted"), so the spend estimate is written from the manifest |
| `identity` | What it runs as, the roles it must not hold, and the Azure Policy deny that enforces that (section 3a's pattern) |
| `switch` | How each kill switch position reaches it, and what it can't reach (the residual, stated) |
| `events` | The observer events it emits (counts only) and the watchdog rows it adds |
| `writes` | `none`: every write it wants comes back as `propose_change` through the gate |
| `exitTests` | Must be empty: no v1 exit test, CI job or eval may depend on it |

Rules, enforced by the check and the drill: off by default; no CI job, eval or exit test depends on one; turning one on needs a spend estimate in spend.md's format and the owner's go, and in the sandbox the exact go phrase; the same gate and kill switch apply; the drill refuses each registered adapter's calls at each halt position. The four first manifests are written as designed, not scheduled: `sre-agent` (section 3a's content), `logic-apps` (scheduler, trigger source only), `apim` (gateway in front of ours), `foundry` (model host; the spike's `agent/` path is its acceptance test).

## 4. Foundry Agent Service hosting, checked against the Container App path

The spike built both. The check asked for on 2026-10-21:

| | Container App + Anthropic federation (`spikes/s1a/agent_api`) | Foundry hosted agent (`spikes/s1a/agent`) |
|---|---|---|
| Keyless | Yes: the Container App's managed identity exchanges an Entra token for a short-lived Anthropic token under a federation rule (10-minute lifetime) | Yes: Foundry's own identity; Claude in Foundry is a preview in East US 2 and Sweden Central only, Global Standard, Marketplace access required |
| Idle cost | Consumption plan, scales to zero; the monthly free grant covers one operator's volume | Hosted agents scale to zero after idle; container compute billed while running; a Foundry resource and project stand in the subscription |
| Where the controls were red-teamed | Here: layer 6 (flag before every call, archive the federation rule), R26 (Prompt Shields in the client), the decoy tools, the meter | Foundry's guardrails apply only here; tool-response scanning there is preview; the Toolbox MCP endpoint is the only way to attach custom tools |
| Region | North Central US with the rest of the sandbox; no policy exemption | Needs the exemption from the landing zone's location policy (the spike's step 2) |
| Verdict | **Default** | **Adapter** `model-host: foundry`, kept because an enterprise adopter may need the data boundary; the spike's Q1 to Q3 become its acceptance test |

## 5. Governance unchanged

Nothing in this plan touches these, and the adapter check fails a manifest that tries:

- every write is a plan a person approves in the GitHub Environment `approve` (spec section 5); the plan store is written only by `propose_change`; the job re-reads live state and compares hashes;
- the deterministic gate runs before approval and again in the job (governance section 2.1); its policy is in Git, protected;
- the kill switch has four positions, enforced in layers by an identity that can only take capability away, and **only a person resets it** from Cloud Shell (governance section 2.3); every adapter and every published tool layer is refused at the positions that forbid it;
- observers can deny and trip and never reset; they see events, never text (section 2.2);
- stopping spend is never blocked (spend.md section 3.3);
- the base pair stands alone (spec section 2): the runner runs the landing zone's own scripts at a pinned commit and nothing swarm-specific lands there;
- solo mode's time-lock on capability-raising changes (section 2.6); runbooks with `runMode: propose` and adapter manifests are protected paths.

## 6. Cost model

List prices as this review knows them (North Central US where it matters; the retail prices API is re-read when each work package writes its estimate, decision 0010; nothing here is a quote).

| | Monthly, idle | Monthly, one operator's use | Notes |
|---|---|---|---|
| **Core, default** | about $10 | $10 plus tokens | Static Web Apps Standard for avd-buddy's sign-in (about $9); Container Apps consumption inside the free grant; Logic App Consumption for the switch (cents per trip and drill); storage for the plan store and the immutable audit log (cents); Content Safety F0 (5,000 records a month free); GitHub Actions on a private repo inside the free minutes; Log Analytics ingestion at one operator's volume (under a dollar) |
| Tokens | $0 | capped: the daily cap in `governance/`, the per-runbook budget, the Anthropic workspace limit | The only cost that grows with use; metered before every call |
| Adapter `investigator: sre-agent` | about $292 | more (tokens metered as agent units) | Bills until deleted; a 30-day evaluation may waive the always-on meter, check on day one |
| Adapter `gateway: apim` | about $150 (Basic v2) | same | Consumption tier can't run `llm-token-limit` or MCP; Developer (about $48) has no SLA |
| Adapter `scheduler: logic-apps` (Automation) | about $37 | more (execution seconds, AI credits) | Preview, no export to code yet; Logic Apps Standard costs more idle, Consumption has no agents |
| Adapter `model-host: foundry` | small standing cost for the resource and project; compute while a hosted agent runs | tokens at Foundry's Claude prices | Preview; two regions |

Sandbox runs for this plan (step 2's eval run, WP2's first scheduled check, the drill) each get their own estimate and the owner's exact `go batch <id> $<total>` before anything deploys; this plan schedules none.

## 7. The sequenced plan

Work packages are sized for one thread each. Every PR is Claude-reviewed, passes the guard, and carries `KB: <link>` to avd-second-brain or `no-kb`. A change to a contract, an agent's role or governance comes with its brain PR (decision 0003). **None starts before the owner's yes to this plan**, and the spec's build order (steps 1 to 3, then "designed, not scheduled") is kept: WP0 is docs, WP1 and WP3 are pure code with tests, WP2 and WP6 belong to build step 2, WP4 and WP5's enforcement to step 3.

| WP | Title | Depends on | Build step | Needs Nick | Size |
|---|---|---|---|---|---|
| WP0 | **Spec revision**: section 3 reorganized as core plus adapters; section 3a becomes the `sre-agent` adapter page with its controls intact and the 2026-10-20 interim choice marked superseded; SQ11 answered; section 6's model hosting names the Container App path as default and Foundry as the `model-host` adapter; the diagram redrawn; README's "Try it" leads with `-Provider Anthropic`; the decision in [0009](decisions/0009-open-source-core-optional-adapters.md) accepted; brain pair: the agents KB page (done with this plan) and v1-review D10 and D11 marked superseded | this plan's yes | docs | merge | 1 thread, paired PRs |
| WP1 | **Runbooks as code**: `runbooks/` schema, `scripts/check_runbooks.py` with tests and fixtures, the KQL catalog by ID, three read-only runbooks (agent unhealthy, failed sign-ins, idle hosts and cost tail) citing the brain's playbooks at a pinned commit | WP0 | 2 (pure code lands anytime) | none | 1 thread |
| WP2 | **Check runner**: the Actions template and the Container Apps Job template, the read-only runner identity in identities.md, a `runbook` endpoint on the agent API that enforces the runbook's budget, findings table in the plan store, avd-buddy card, issue-on-change rule, C8 `checks` block proposed to the brain, flag check first; first scheduled run in the sandbox | WP1, WP3 (library mode) | 2 | estimate, then `go batch <id> $<total>` | 1 thread, 2 PRs plus a brain PR for C8 |
| WP3 | **Gateway package**: `gateway/` from the spike's `meter.py` and `shield.py`, `governance/model-prices.yml`, per-caller caps, the provider interface (anthropic, foundry, azure-openai), usage events; the spike imports it; service mode designed, built when WP2's runner leaves the process | WP0 | 2 | none | 1 thread |
| WP4 | **Tool layer as MCP server**: Entra app-role auth, per-caller allowlist default deny, flag check per call, the drill's refusal test; first client the check runner, second Claude Code | WP3, WP2 | 3 | none | 1 thread |
| WP5 | **Provider interface**: `adapters/schema.json`, `scripts/check_adapters.py` with tests, four manifests (`sre-agent`, `logic-apps`, `apim`, `foundry`) marked designed, the drill rows | WP0 | 3 (enforcement), manifests anytime | none | 1 thread |
| WP6 | **Alert-triggered runs**: alert rule to action group to a Consumption Logic App to a storage queue (alert ID only) to the Container Apps Job, all managed identities; `trigger.alert` in runbooks; `alert` added to the `threadStartedBy` vocabulary (spec section 3a, identities.md) and to the gate's V1-4 rule, with a gate test; `repository_dispatch` added to the guard's untrusted events | WP2 | 2 | estimate (cents) and go | 1 thread |
| WP7 | **Policy engine spike** (already in the spec, V1-6): the toolkit evaluating three gate rules offline in an Actions job; our code if it can't | WP0 | 3 | none | 1 thread, offline |
| WP8 | **Red team of WP1 to WP6 before step 3 builds on them**: a Fable pass on the runbook format, the runner's identity and the gateway's service mode (the usual rule: each step-4 item gets its own red team) | WP1, WP2, WP3 | before 3 | reads the report | 1 thread |

Order: WP0 first (two days of thread time; it unblocks the rest and lands the KB). WP1, WP3 and WP5's manifests run in parallel next, all offline. WP2 and WP6 follow and are the first things in this plan that spend money. WP4, WP5's enforcement and WP7 are step 3 work, after step 2's exit (answers cite evidence in at least 80% of eval questions).

Expected end state: an adopter clones this repo, syncs a release tag into a private repo, sets variables, runs the Cloud Shell blocks, and has read-only agents, scheduled runbooks and alert-triggered investigations for about ten dollars a month plus tokens, with every write through the gate and every paid Microsoft service off until they estimate it and turn it on.

## 8. What needs Nick

1. **Yes to the direction**: open-source core, adapters off by default, section 3a kept as the SRE Agent adapter's design rather than deleted (its controls cost nothing to keep and were red-teamed). Recommendation: yes, keep.
2. **Default model host**: Container App plus Anthropic workload identity federation, with Foundry as an adapter. Recommendation: yes; it is the path every control was built on.
3. **Scheduler**: GitHub Actions `schedule` in the adopter's private repo as the default, Container Apps Job as the in-network alternative. Recommendation: Actions; free, keyless through OIDC, already the write path's home.
4. **Runbook format**: Markdown with YAML front matter, steps fixed, reads by allowlist, one proposable action. Recommendation: yes; it is what SRE Agent's knowledge files and HolmesGPT's toolsets both converge on, and it keeps the model out of choosing steps.
5. **MCP server timing**: stays out of v1 (SQ5); WP4 at step 3 with the runner as first client. Recommendation: yes.
6. **HolmesGPT**: parity checklist only, no adoption (Kubernetes and a Helm chart for one operator fails our Cloud Shell and cost rules; its Azure path is `azure-api-mcp`, an Azure CLI wrapper, rather than the Azure MCP Server's typed, read-only annotated AVD tools). Recommendation: checklist only; revisit if it ships a non-Kubernetes operator.
7. **Sandbox**: WP2's first scheduled run and WP6's alert path each get an estimate; the go phrase as always.

## 9. Red team of this plan

Done offline against the plan as written; each finding is folded in above or listed as open.

| ID | Sev | Finding | Disposition |
|---|---|---|---|
| P-1 | H | A runbook is text the model reads, so a runbook PR is a prompt change by another name; Improve could propose one that steers the agent | Runbooks are untrusted text through the scan; the prompt stays protected; `runMode: propose` runbooks are protected fields; an agent-authored PR touching them fails the guard (section 3.2) |
| P-2 | H | The check runner is a new identity that reads on a schedule nobody asked for; a plan from its findings would be "a thread a schedule started" | Runner identity read-only, listed in identities.md; findings carry `threadStartedBy: schedule` or `alert`; `alert` is added to the vocabulary and the gate's V1-4 rule by WP6, with a test, so a plan whose only evidence is such a run is refused |
| P-3 | H | An alert that starts a GitHub workflow needs a GitHub credential in Azure (GitHub has no inbound OIDC), and `repository_dispatch` is neither in spec section 5b rule 4's list nor in `check_workflows.py`'s untrusted events | The alert path starts no workflow: action group, Logic App, storage queue and Container Apps Job, every hop a managed identity, the message carrying the alert ID only, the Job reading the alert back from Azure Monitor with its read-only identity (section 3.1, WP6). WP6 also adds `repository_dispatch` to the guard's untrusted events so nobody builds the other path by mistake |
| P-4 | M | The gateway in service mode becomes a single point every process trusts; a compromised gateway could under-meter | Its counter is written only by its identity, the watchdog reads the counter and the provider's usage independently (spend.md 3.7 items 3 and 5), and the provider workspace limit is the outer wall; service mode waits for a real second process (WP3) |
| P-5 | M | An adapter manifest could declare `writes: none` and still write through its own identity (SRE Agent's Monitoring Contributor) | The manifest's `identity` field names the Policy deny that enforces it, and the watchdog row trips `halt-all` on any write by the adapter's identity; `check_adapters.py` fails a manifest without both (section 3.6) |
| P-6 | M | Scheduled checks are the first thing that spends tokens with nobody watching | Per-runbook budget inside the daily cap; the runner's first step reads the flag; the watchdog's daily cap row stands; issue-on-change so a loop of findings is visible |
| P-7 | M | "About $10 a month" will be read as a promise | Section 6 says list prices, not a quote; every WP that deploys writes its own estimate from the retail prices API |
| P-8 | L | Container Apps Job as an alternative runner duplicates the Actions template | Same code, two templates; the Job template is written only when an adopter asks (WP2 ships the Actions one and designs the other) |
| P-9 | L | HolmesGPT and Bifrost move fast; the checklist rots | The KB page carries the dates checked and the brain's freshness workflow flags the sources |
| P-10 | open | Whether Logic Apps Automation ever exports to code, and whether APIM's AI Gateway tier gets Entra client auth, decides whether those adapters are worth writing beyond a manifest | Re-check at GA (KB agents page) |

## 10. Coordination

- **avd-second-brain**: the agents KB page gains the prior-art facts of this review (paired PR, decision 0023 applied); WP0 marks v1-review D10 and D11 superseded; WP2 proposes the C8 `checks` block; C10's rule that the brain's Tier 1 reads the flag first is the model for the runner.
- **avd-landing-zone and avd-current-state**: no change. Runbooks call the pair's scripts at pinned commits; a read the runner needs that the pair lacks is a feature of the pair first (spec section 2).
- **avd-ops**: its avd-swarm page's quick start follows WP0 (lead with the Anthropic path); the site still names no partner.
- **Brain operations spec**: unchanged; Tier 1 stays the brain's own scheduled job and the runner is avd-swarm's.

## 11. Method of this review

Read every file in the repo at `b49c03b`, spec, governance, spend, security tooling, identities, v1 review, red team and the spike's code; read avd-second-brain PR #80's agents page, decision 0023, the 2026-10-20 prior-art report and adoption spec, the 2026-10-19 red-team fixes, and the native-fit report; ran the family prior-art script (own KB only; sibling KBs and GitHub search return 403 from the container, as before) and dispatched the brain's `kb-prior-art` workflow for the GitHub side; read LICENSE files at source for HolmesGPT (Apache-2.0), LiteLLM (MIT with a separately licensed `enterprise/` directory), Bifrost (Apache-2.0), runme (Apache-2.0), IBM mcp-context-forge (Apache-2.0), microsoft/agent-governance-toolkit (MIT), microsoft/agent-framework (MIT), microsoft/mcp (MIT) and Azure/azure-api-mcp (MIT); read Microsoft Learn for SRE Agent's overview, custom agents and knowledge files, and Logic Apps Automation's comparison page. The brain's `kb-prior-art` run 8 (2026-10-21) found no repository match for any of the six topics; its code search surfaced two Microsoft reference architectures, azure-agents-control-plane and agent-landing-zone (both MIT), which route every agent call through API Management on AKS and Foundry, the paid stack this plan makes optional, so they are prior art for an enterprise adopter's adapters and nothing for the core. Nothing was deployed.
