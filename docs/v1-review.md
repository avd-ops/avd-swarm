# avd-swarm v1 completeness review

- **Date:** 2026-10-20. Reviewed `main` at `53f5fe4` and the open drafts that change the spec: #18 (claims on every edge, §4a), #21 (Well-Architected review), #23 (never delete the last copy), the recovery branch (decision 0006, `recovery.md`) and the "why" work (decision 0007, §6f). Paired with [avd-second-brain's v1 review](https://github.com/nickprignano/avd-second-brain/blob/main/docs/v1-review.md).
- **Status:** Proposed. Offline review only: nothing was deployed, run or spent.
- **What "complete" means here:** every part of v1 has a home, an owner and a rule; what is still undecided is listed with a default; nothing contradicts anything else. It does not mean built.

## 1. What v1 is (proposed)

v1 is build order steps 1 to 3 ([spec](spec.md) §9a). Everything in step 4 comes after v1 and keeps its own red team pass.

| In v1 | After v1 |
|---|---|
| S1a spike on the Anthropic path, then on Foundry when quota arrives | C9 brain proposals and `propose_playbook_run` |
| avd-buddy, the coordinator, Hosts and Cost, read-only, metered, stopped by `halt-agents` | Improve, Scribe, the judge and its auditor running (only the auditor's rules exist) |
| §4a answers with the claim check's code half (#18) | The claim check's model verifier |
| The adopter's private repo, synced from a release tag, with the write-path templates | Users, Deploy, Images, Readiness and Recovery agents (S3, S4) |
| Every kill switch layer, the watchdog, the drill and the time-lock check | Group membership actions, recovery actions, compliance gate, standing exceptions |
| Two actions, drain and restart agent, with the §5a preconditions, through the spend gate | A custom MCP server for other clients |
| The spend guard's open, close and settle on every plan that costs money | The monthly statement job |

The brain side of v1 is its Tier 1 (Notice) plus a deterministic Concierge read tool (brain review §4).

## 2. Does avd-swarm have or need an MCP?

**It uses MCP; it doesn't need its own MCP server for v1.** Spec §3 now says this, and SQ5 is answered for v1.

- **As a client, yes, already designed.** Microsoft's Azure MCP Server is the main read tool: `--read-only`, an explicit namespace list with `virtualdesktop`, and an allowlist per agent. S1a's Q3 and Q4 test exactly that. Cost forecasts come from the ARM MCP server's read tools only, since that server can also create budgets and deploy. The Microsoft MCP Server for Enterprise (Graph) is left out: its reads are tenant-wide, so the Users agent keeps a narrow AVD-scoped tool.
- **As a server, not in v1.** avd-swarm's own tools (`propose_change`, the brain's Concierge, the KQL catalog, AVD-scoped Graph reads) are plain functions behind one tool layer. That layer is where the allowlist, decoy tripwire, untrusted-text scan, token meter and kill switch flag sit, so every provider gets the same checks. avd-buddy is the only front end in v1.
- **Two triggers to revisit.** If S1a's Foundry run shows a hosted agent can attach custom tools only through MCP (its Toolbox endpoint), or an adopter connects Azure SRE Agent (spec SQ11, which would call our read tools and `propose_change` as an MCP connector), the same tool layer is published as an MCP server with nothing added: reads and proposals only.
- **Later:** an MCP server for Claude Code and other clients, exposing read tools and `propose_change` only. A plan it proposes is approved in the GitHub Environment like every other; MCP never carries a write or an approval.
- **The brain builds no MCP server.** Its Concierge reaches avd-swarm as one of these tools (brain review §2).

## 3. What is decided

| Area | Decision | Where |
|---|---|---|
| Roles | The brain thinks (the why), avd-swarm acts (the how); they change in pairs | Decision 0003 |
| Base pair | avd-landing-zone and avd-current-state stand alone; avd-swarm depends down only and never deploys from them | Spec §2 (new, owner's rule 2026-10-20) |
| Write path | One write identity per action kind, token only after a person approves the exact plan in `approve`; plan ID only on dispatch; live re-read and hash check before acting | Spec §5, R1 to R4 |
| Where it runs | Public repo holds code, catalog, templates and CI only; tenant work runs in the adopter's private repo synced from a release tag | Spec §5b, decision 0005 |
| Action safety | §5a preconditions on every host action, protected fields, fixed hash-pinned Run Command scripts | Spec §5a |
| Gate | Deterministic policy in Git, no model | Governance §2.1 |
| Observer | Signed change and security events, never conversation text; verdict and trip, never reset | Governance §2.2 |
| Kill switch | Four positions, six layers, only a person resets from Cloud Shell, monthly drill | Governance §2.3 |
| Solo mode | 72-hour time-lock for changes that raise capability; AI reviews advisory | Governance §2.6 |
| Spend | Estimate before, evidence after, metered tokens with a provider workspace cap; stopping spend never blocked; never the last copy (#23) | spend.md |
| Identity | No secrets; Entra Agent ID; Anthropic through workload identity federation | identities.md |
| MCP | Client for reads; no own server in v1 | Spec §3, SQ5 |
| Facts | Estate facts only through the brain, as C11 and C12 by C15 register ID; facts propose, live reads decide | Spec §6d |

## 4. Contradictions found

| # | What disagreed | Settled as | State |
|---|---|---|---|
| X1 | Decision 0002 said four layers and "reset through one workflow"; governance §2.3 has six layers and a Cloud Shell reset (R6) | Decision 0002 notes the amendment and points to governance | Fixed here |
| X2 | Decision 0005 put the adopter repo before the read-only agents; spec §9a puts it in step 3 | Decision 0005 follows §9a | Fixed here |
| X3 | Governance §2.6 said the time-lock is built in step 2; §9a builds it in step 3 | Step 3 | Fixed here |
| X4 | Spec §4's Cost agent read "tenant-swarm's spend ledger"; spend.md §2 replaced it with the public spend guard | The spend guard's run records | Fixed here |
| X5 | identities.md said stop-spend can stop or deallocate; governance said stop, deallocate and delete | Stop, deallocate and delete, run-tagged only (and never the run store, #23) | Fixed here |
| X6 | redteam.md listed AX-12 (spend module in a private repo) as open; CQ1 closed it | Closed | Fixed here |
| X7 | R1 named the credential subject in this public repo; R17 moved the write path to the adopter's repo | Adopter's repo | Fixed here |
| X8 | Spec §3 draws only the Foundry path, but steps 1 and 2 run on Anthropic's API from a Container App | Both paths are real; the model provider is a setting (§6). Redraw §3 when S1a's Foundry run settles which one S1 uses | Open, waits on S1a |
| X9 | Spec §3 says Foundry guardrails "are on"; they never see the Anthropic path (security-tooling R26, proposed) | Prompt Shields in the model client on every provider | Waits on the security tooling decision |
| X10 | Step 2 says the Concierge is the only link to the brain, but the brain's Concierge is a Tier 3 Foundry agent and step 2 pairs with the brain's Tier 1 | v1 Concierge is a deterministic read tool, no model (brain review §4) | Proposed in the brain review |
| X11 | Public text names private repos and their plans (spec §1, §2, §6d, §8) and links an outside AI knowledge base (security-tooling.md) | Left for the public-repo cleanup that owns it (PR #22 and the layering review's cleanup set) | Not touched here |

## 5. Gaps for v1, with defaults

Each gap names the build step that needs it. Defaults are what a later session builds unless the owner says otherwise.

| # | Gap | Step | Default |
|---|---|---|---|
| G1 | **avd-buddy has no spec of its own**: chat relay API, plan card, approval link, status at each switch position, rendering rules (AX-7 open: links and images in answers can exfiltrate) | 2 | A `buddy/` section in the spec before step 2 starts: text and citations only, no remote images, links only to an allowlist (Learn, GitHub, the portal), plan card rendered from the plan store only; UI patterns from #21 on the avd-ops design system |
| G2 | **Plan store and plan schema** are named but not chosen or written | 3 | A storage table plus blob, written only by `propose_change`'s identity; `avd-swarm-plan/v1` with the fields brain operations-spec §4.5 lists, canonical JSON, SHA-256 |
| G3 | **Action catalog schema** is described, not written | 3 | One YAML file per action, a JSON Schema, and the CI check that a host or power action declares every §5a precondition |
| G4 | **Eval set** for step 2's exit (80% cite evidence) lives only in the spike | 2 | `evals/` with the spike's ten questions plus five per agent, expected facts read from ARM at run time, append-only |
| G5 | **Which observers exist in v1**, and the signing key events need (AX-13) | 3 | The watchdog and a person's channel (email or phone push with trip links); events signed with a Key Vault key the observer's identity can verify and no swarm identity can export; the second-opinion model after v1 |
| G6 | **Release process**: adopters sync from a release tag, but this repo has no tags or attested releases | 3 | Tags `v0.x` cut by the owner, GitHub artifact attestations, a sync PR template for adopters |
| G7 | **Transcript store** (SQ8) | 2 | Blob with a 90-day lifecycle, pseudonymized, in the adopter's tenant; Improve doesn't exist in v1, so nothing reads it yet |
| G8 | **Management locks and deletes** (prior art: SRE Agent never deletes and respects locks) | 3 | A §5a precondition `notLocked` (no `CanNotDelete` or `ReadOnly` lock on the target) and no delete action in the v1 catalog; deletes only through teardown plans on run-tagged resources |
| G9 | **Policy engine**: governance §2.1 names Microsoft's Agent Governance Toolkit but nobody has tried it | Before 3 | An offline spike, no cloud spend: write §2.1's rules in the toolkit, run them against fixture plans, and check the license and the Cloud Shell fit. Keep the observers, the layered switch and "only a person resets" on top: the toolkit has none of those |
| G10 | **Cost of the standing pieces** (Static Web Apps Standard, Event Grid, Logic App, immutable store) has no estimate | 2 | Priced from the meters in spend.md format before step 2's first deploy |
| G11 | **Recovery actions** (DR branch) before building drills | After v1 | Check whether Azure's resiliency drills and recovery plans cover AVD host pools first; if they do, the action calls them instead of scripting failover (prior art report) |
| G12 | **Well-Architected review** (#21) | After v1 | Consume WARA and the AVD review checklist instead of a rule engine of our own (prior art report; the owner agreed 2026-10-20, and #21 is being revised) |

## 6. The base pair check

Every proposed change was checked for a place where avd-landing-zone or avd-current-state would depend upward.

| Where | What depends on what | Verdict |
|---|---|---|
| Recovery (decision 0006, `recovery.md`) | The landing zone owns the scripts; avd-swarm runs them pinned; "recovery never waits on avd-swarm" | Down only. Keep the landing zone's recovery spec free of swarm terms |
| Run records (C17) | The landing zone produces them and vendors the schema by byte copy | Down only |
| Target state (C14) | The landing zone's reader takes an approved manifest as an optional input | Down only; the deploy path works without one |
| Docs check | The landing zone's and avd-current-state's `docs.yml` call the brain's reusable workflow, pinned by SHA | **Upward, CI only.** It can't break a deployment, but a brain outage or rename would fail their PR checks. Default: keep it advisory there (not a required check) |
| The brain's runtime | Operations spec and landing zone decision 0012 put `brain.bicep`, `brain/` and the brain's workflows inside avd-landing-zone | **Upward.** It conflicts with the new invariant and with avd-landing-zone being deployment only. Default: the brain's Tier 1 deploys from avd-second-brain, reading the landing zone by its naming convention, as avd-liquidware does; decision for the owner (brain review D1) |
| avd-swarm's own deploy | Its own resource group and the adopter's repo | Down only, now written as a rule (spec §2) |

## 7. Decisions only the owner can make

There is one list for both repos, in [avd-second-brain's v1 review §7](https://github.com/nickprignano/avd-second-brain/blob/main/docs/v1-review.md#7-decisions-only-the-owner-can-make). The ones that are avd-swarm's: v1 = steps 1 to 3 (D2), no MCP server of our own in v1 (D4), the policy engine spike (D6), Python for the agent service (D7) the Well-Architected scope cut (D8, confirmed by the owner: consume WARA) and Azure SRE Agent (D10: the owner said it can cover what it covers while the chatbot is still built; open is whose approval its actions use, default the GitHub Environment plan, spec SQ11). Each has a default the work follows until answered. Public text that names private repos (X11) is left to the cleanup that owns it.
