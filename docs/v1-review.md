# avd-swarm v1 completeness review

- **Date:** 2026-10-20. Reviewed `main` at `53f5fe4` and the open drafts that change the spec: #18 (claims on every edge, §4a), #21 (Well-Architected review), #23 (never delete the last copy), the recovery branch (decision 0006, `recovery.md`) and the "why" work (decision 0007, §6f). Paired with [avd-second-brain's v1 review](https://github.com/nickprignano/avd-second-brain/blob/main/docs/v1-review.md).
- **Status:** Proposed, ready to lock once the owner answers §8's decisions (each has a default). Red-teamed on 2026-10-20 (findings V1-1 to V1-38, §8). Offline review only: nothing was deployed, run or spent.
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
- **The brain builds no MCP server.** Its Concierge is brain code with the brain's identity; the tool layer calls it over HTTPS with Entra auth, as one of these tools (brain review §4).

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
| X10 | Step 2 says the Concierge is the only link to the brain, but the brain's Concierge is a Tier 3 Foundry agent and step 2 pairs with the brain's Tier 1 | v1 Concierge is a deterministic read service with no model, in brain code with the brain's identity; the tool layer calls it over HTTPS (brain review §4, V1-11) | Fixed here |
| X12 | Step 2 said "no C10" while brain C10 said the switch read applies from v1 (V1-7) | The switch read is step 2; C10's outcomes, C8 and C9 are step 4 | Fixed here |
| X11 | Public text names private repos and their plans (spec §1, §2, §6d, §8) and links an outside AI knowledge base (security-tooling.md) | Left for the public-repo cleanup that owns it (PR #22 and the layering review's cleanup set) | Not touched here |

## 5. Gaps for v1, with defaults

Each gap names the build step that needs it. Defaults are what a later session builds unless the owner says otherwise.

| # | Gap | Step | Default |
|---|---|---|---|
| G1 | **avd-buddy has no spec of its own**: chat relay API, plan card, approval link, status at each switch position, rendering rules (AX-7 open: links and images in answers can exfiltrate) | 2 | A `buddy/` section in the spec before step 2 starts: the agent API accepts only avd-buddy's Functions identity, ingress internal (identities.md, V1-16); text and citations only, no remote images, links only to an allowlist (Learn, GitHub, the portal), plan card rendered from the plan store only; UI patterns from #21 on the avd-ops design system |
| G2 | **Plan store and plan schema** are named but not chosen or written | 3 | A storage table plus blob, written only by `propose_change`'s identity; `avd-swarm-plan/v1` with the fields brain operations-spec §4.5 lists, canonical JSON, SHA-256 |
| G3 | **Action catalog schema** is described, not written | 3 | One YAML file per action, a JSON Schema, and the CI check that a host or power action declares every §5a precondition |
| G4 | **Eval set** for step 2's exit (80% cite evidence) lives only in the spike | 2 | Written before step 2 starts (V1-34): `evals/README.md` with the spike's ten questions plus five per agent, expected facts read from ARM at run time, append-only |
| G5 | **Which observers exist in v1**, and the signing key events need (AX-13) | 3 | Written in governance §2.2 (V1-18): the watchdog and a person's channel, no v1 plan needs a verdict, the silence rule is first exercised in step 4; (email or phone push with trip links); events signed with a Key Vault key the observer's identity can verify and no swarm identity can export; the second-opinion model after v1 |
| G6 | **Release process**: adopters sync from a release tag, but this repo has no tags or attested releases | 3 | Tags `v0.x` cut by the owner, GitHub artifact attestations, a sync PR template for adopters |
| G7 | **Transcript store** (SQ8) | 2 | Blob with a 90-day lifecycle, pseudonymized, in the adopter's tenant; Improve doesn't exist in v1, so nothing reads it yet |
| G8 | **Management locks and deletes** (prior art: SRE Agent never deletes and respects locks) | 3 | A §5a precondition `notLocked` (no `CanNotDelete` or `ReadOnly` lock on the target) and no delete action in the v1 catalog; deletes only through teardown plans on run-tagged resources |
| G9 | **Policy engine**: governance §2.1 names Microsoft's Agent Governance Toolkit but nobody has tried it | Before 3 | Its limits are written now (governance §2.1, V1-6: allow or deny only, never its approval or switch, pinned, no network). An offline spike, no cloud spend: write §2.1's rules in the toolkit, run them against fixture plans, and check the license and the Cloud Shell fit. Keep the observers, the layered switch and "only a person resets" on top: the toolkit has none of those |
| G10 | **Cost of the standing pieces** (Static Web Apps Standard, Event Grid, Logic App, immutable store) has no estimate | 2 | Priced from the meters in spend.md format before step 2's first deploy |
| G11 | **Recovery actions** (DR branch) before building drills | After v1 | Check whether Azure's resiliency drills and recovery plans cover AVD host pools first; if they do, the action calls them instead of scripting failover (prior art report) |
| G12 | **Well-Architected review** (#21) | After v1 | Consume WARA and the AVD review checklist instead of a rule engine of our own (prior art report; the owner agreed 2026-10-20, and #21 is being revised) |

## 6. The base pair check

Every proposed change was checked for a place where avd-landing-zone or avd-current-state would depend upward.

| Where | What depends on what | Verdict |
|---|---|---|
| Recovery (decision 0006, `recovery.md`) | The landing zone owns the scripts; avd-swarm runs them pinned; "recovery never waits on avd-swarm". The draft has the landing zone's status check report avd-swarm's region (V1-21) | Down only once fixed: the Recovery agent compares the two regions, and `Get-AvdRecoveryStatus.ps1` stays free of swarm terms |
| Pinned scripts (spec §2, §5a) | avd-swarm pins the pair's scripts by release tag, but the landing zone has no tags and no restart-agent host script (V1-22) | Down only: pin by commit SHA and file hash; the missing script is a prerequisite PR to the pair, usable from Cloud Shell first |
| The kill switch's role removal | `roleAssignments/delete` on the AVD resource groups could remove the landing zone's deploy identity (V1-15) | Down only once the delegation condition limits it to agent principals (governance §2.3) |
| Run records (C17) | The landing zone produces them and vendors the schema by byte copy | Down only |
| Target state (C14) | The landing zone's reader takes an approved manifest as an optional input | Down only; the deploy path works without one |
| Docs check and KB capture | The landing zone's and avd-current-state's `docs.yml` call the brain's reusable workflow, pinned by SHA; their `kb-capture` check and Stop hook follow the brain's KB conventions (V1-23) | **Upward, CI only.** It can't break a deployment, but a brain outage or rename would fail their PR checks. Default: no check that depends on the brain's repo is ever required in either repo; which checks are required gets read from the rulesets once and written down there |
| The brain's runtime | Operations spec and landing zone decision 0012 put `brain.bicep`, `brain/` and the brain's workflows inside avd-landing-zone | **Upward.** It conflicts with the new invariant and with avd-landing-zone being deployment only. Default: the brain's Tier 1 deploys from avd-second-brain, reading the landing zone by its naming convention, as avd-liquidware does; decision for the owner (brain review D1) |
| avd-swarm's own deploy | Its own resource group and the adopter's repo | Down only, now written as a rule (spec §2) |

## 7. Decisions only the owner can make

There is one list for both repos, in [avd-second-brain's v1 review §7](https://github.com/nickprignano/avd-second-brain/blob/main/docs/v1-review.md#7-decisions-only-the-owner-can-make). The ones that are avd-swarm's: v1 = steps 1 to 3 (D2), no MCP server of our own in v1 (D4), the policy engine spike (D6), Python for the agent service (D7), the Well-Architected scope cut (D8, answered: consume WARA), Azure SRE Agent (D10) and the red team's owner items D11 to D17 (SRE Agent's residual risk, the failover identity, public text). Each has a default the work follows until answered.

## 8. Red team of the v1 specs (2026-10-20)

An independent, offline red team read both specs at this branch's head, the open drafts and the base pair. 38 findings: 1 critical, 14 high, 19 medium, 4 low. What held up: the write path (no write token before a person approves the exact plan, live re-read, hash check, protected fields, time-lock), facts propose and live reads decide, the base pair invariant, one kill switch with a Cloud Shell-only reset, the spend rules, and consuming prior art where it exists.

| ID | Sev | Finding | Where it landed |
|---|---|---|---|
| V1-1 | C | SRE Agent was a second write-capable actor kept read-only by setup alone | Spec §3a: Azure Policy deny on role assignments to its identity, `halt-all` on any write by or role change to it, Administrator on a break-glass account, the residual stated (owner D11) |
| V1-2 | H | "A hook denies writes" can't be built: hooks are Stop and PostToolUse only | Spec §3a: tool access policy at global scope, hook only as an event source; approver's daily account holds no write role outside PIM |
| V1-3 | H | No kill switch layer for the tool layer once published as MCP | Governance §2.3 layer 5 and drill |
| V1-4 | H | `sreagent` calls can't go through the read-only server; no field for "a task nobody asked for" | Spec §3a: a second server instance with no RBAC; evidence provenance fields; identities.md |
| V1-5 | M | Nothing said the brain's Tier 1 stays the free baseline | Spec §3a "It adds, it never replaces" |
| V1-6 | M | No limits on the governance toolkit | Governance §2.1 |
| V1-7 | H | Step 2 "no C10" vs brain C10 "applies from v1" | Spec §9a step 2; brain C10 (X12) |
| V1-8 | H | Tier 1's Diagnose doesn't use the shared metered client | Governance §2.3 layer 6; brain C10 and operations spec |
| V1-9 | H | A brain without a swarm could never call a model | Brain C10 and §3.0 default (owner D13) |
| V1-10 | M | The brain runtime's identity was missing | identities.md row |
| V1-11 | H | The v1 Concierge as written was avd-swarm reading the vault | Brain review §4: brain code, facts-only store; step 2 exit |
| V1-12 | H | C10 outcomes carry resource IDs into pseudonym-only stores | Brain C10: ingest filter, `targets` as pseudonyms |
| V1-13 | H | The gate can't check pseudonym evidence without the key, which lives in the landing zone | Brain review (owner D12) |
| V1-14 | H | A stop-spend role limited by tag can't be built | Governance §2.3 and identities.md: scoped by run resource groups, lock, Policy `denyAction` |
| V1-15 | H | `roleAssignments/delete` can't be scoped to assignments and could break the base pair | Governance §2.3: delegation condition; drill; §6 above |
| V1-16 | M | The agent API didn't authenticate its caller | identities.md, G1 |
| V1-17 | M | Who sets the flag in step 2 | Spec §9a step 2 |
| V1-18 | M | No outside observer in v1 | Governance §2.2, G5 |
| V1-19 | H | The operations spec still tells Tier 1 to build its own switch | Brain operations spec, five places |
| V1-20 | H | More upward dependencies in the brain spec | Brain review §6 (owner D14) |
| V1-21 | H | Recovery draft puts swarm terms in the landing zone's status check | §6 above; sent to the recovery work |
| V1-22 | M | No release tags and no restart-agent script in the landing zone | Spec §2, §6 above |
| V1-23 | M | Brain-dependent checks in the pair's CI | §6 above (owner D15) |
| V1-24 | M | Three draft branches would revert X4, X5 and SQ5 if merged after this one | Sent to those drafts: rebase on this branch first |
| V1-25 | M | The Well-Architected draft still has its own rule engine | Sent to that draft: reduce to a WARA reader, no new agent |
| V1-26 | M | Recovery drills before the Resiliency Manager check | Sent to the recovery work: G11 becomes its DR0 exit |
| V1-27 | H | The failover identity writes role assignments before the owner answers | Sent to the recovery work: keep it out of identities.md until answered (owner D16) |
| V1-28 | M | The ARM MCP server needs delegated auth | Spec SQ2 |
| V1-29 | M | How SRE Agent authenticates to our MCP connector | Spec §3a: Entra only, or no connector |
| V1-30 | M | SRE Agent text wasn't in the untrusted-text scan | security-tooling.md |
| V1-31 | M | C8's grading window is finer than its period; SQ9 has no owner | Brain C8 |
| V1-32 | L | Tool names differ | `propose_change` and `propose_playbook_run` only; SRE Agent gets `propose_change` |
| V1-33 | M | SRE Agent isn't metered per response | Spec §3a "Cost" |
| V1-34 | M | Step 2 and Tier 1 exits have no eval set or cost estimate | G4; brain G2 |
| V1-35 | M | Public text names a private project and its roadmap | Owner D17 (default: apply the cleanup now) |
| V1-36 | L | Hosts read brain episodes directly | Spec §4 |
| V1-37 | L | How a budget's action group authenticates to `trip` | Governance §2.2 |
| V1-38 | L | Failover's whole-pool drain vs one host per pool | Sent to the recovery work |
