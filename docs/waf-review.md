# Spec: a chat-driven Well-Architected review in avd-buddy

- **Status:** Proposed (2026-10-20). Design only: nothing here is built, scheduled or run, and it needs no spend until its first eval run, which is estimated in the [spend.md](spend.md) format first.
- **Part of:** [spec.md](spec.md) (agents §4, write path §5, facts §6d), [governance.md](governance.md). Build order: §9a step 4, "designed, not scheduled", until a person moves it (§9 below).
- **Pairs with:** avd-second-brain, which feeds the review its knowledge and facts and takes its findings into the Posture loop ([decision 0003](decisions/0003-swarm-acts-brain-thinks.md), §7 below).
- **Builds on:** avd-landing-zone's Well-Architected review, `Test-AvdLandingZoneReadiness.ps1 -WellArchitected` ([landing-zone decision 0009](https://github.com/nickprignano/avd-landing-zone/blob/master/build/decisions/0009-well-architected-review.md)).

## 1. Summary

An operator opens avd-buddy and says "review my AVD estate against Well-Architected". A **Reviewer** agent walks them through the five pillars, one at a time, in the order they care about. For each pillar it first reads what it can (the landing zone's `waf-*` checks, Azure Advisor, Defender for Cloud, Azure Policy, the estate's resources, and current-state facts through the brain), and asks only the questions the evidence can't answer, such as "have you tested a restore of a profile share?". Every finding comes with its evidence and a link to the Microsoft Learn page that says why it matters. Scores are computed by code from the check results, never by a model.

The review changes nothing. A finding's fix is handed to the specialist that owns it, which turns it into a plan through the ordinary write path (spec §5), a PR to the landing zone's parameters, or a Cloud Shell block. Accepting a trade-off is a PR a person merges, never a click in the chat.

**What this adds to what exists.** The landing zone's `-WellArchitected` review already reads the deployed resources and Azure's own evaluations, and its decision says "the questionnaire part of Microsoft's Well-Architected assessment remains manual". Microsoft's own assessments ([Azure Well-Architected Review](https://learn.microsoft.com/assessments/azure-architecture-review/) and its Azure Virtual Desktop workload variant) are questionnaires that don't read the estate, beyond Advisor recommendations for one subscription. This review joins the two: evidence first, questions only for the gaps, and remediation that goes through a person's approval.

## 2. Goals and non-goals

### Goals
- Answer "would this AVD estate pass a Well-Architected review, pillar by pillar, and why?" with evidence for every claim.
- Ask the operator as little as possible: never a question that a read could answer.
- Turn findings into governed plans or PRs from the same screen, with no new write path.
- Repeat the review and show what changed since the last one (a milestone, as Microsoft's assessment calls it).
- Link Microsoft Learn for the guidance instead of restating it ([avd-second-brain decision 0011](https://github.com/nickprignano/avd-second-brain/blob/main/docs/knowledge/decisions/0011-family-format-upstream-sources.md)).

### Non-goals
- Any change made by the review itself, or any "accept" or "fix" that completes inside the chat (spec §2).
- A model deciding a finding or a score (avd-second-brain's rule "a model never decides").
- Re-implementing a check the landing zone's preflight already has. The review reads its result.
- **Out of scope here:** comparing one estate with others, reviewing more than one tenant or workloads other than AVD, and formatted documents beyond Markdown and CSV.

## 3. How the conversation goes

The Reviewer is a specialist in the coordinator's handoff workflow (spec §3, §4). A review is a sequence of fixed steps; the model chooses the wording, not the steps.

| Step | The Reviewer | The operator sees |
|---|---|---|
| 1. Scope | Lists the landing zones and host pools the agent identities can read (Resource Graph) and asks which to review. Defaults to the one the conversation is about | A pick list, from the tool's output, never typed IDs |
| 2. Context | Asks the workload questions that change what "good" means: how critical, how many users, working hours and time zones, recovery targets (RTO and RPO for profiles and hosts), compliance needs, and the pillar order. Answers that the landing zone already records (environment `dev`, `test`, `prod`; the parameter file's deliberate trade-offs) are filled in and shown, not asked | Five to eight questions, each with "why we ask" and a Learn link |
| 3. Evidence | Runs every check for the scope in one pass (§4), read-only, and stores the results in a **review record** (§6) | A progress line per source before each slow read (landing-zone lesson 0006); a source that can't be read is listed with its error, and the rest still run (landing-zone decision 0009) |
| 4. Pillar walk | For each pillar in the operator's order: the findings that came from evidence, then the open questions for checks that need the operator (§4, `answeredBy: operator`), one at a time | A pillar card (§6) rendered from the record, and the questions under it |
| 5. Scorecard | Computes scores and confidence (§5) and shows changes since the previous review of the same scope | The scorecard, rendered from the record |
| 6. Next steps | For each finding the operator picks, hands off to the owning specialist (§8), or records a request to accept the trade-off | Plans and PR links, from the plan store and GitHub, never described in agent text (spec §5 step 4) |

The operator can stop after any pillar; the record keeps what was done, and a later "continue the review" resumes it. A full review is about the length of Microsoft's own (about 60 questions there); most of this one's checks are answered by reads, so the operator answers far fewer.

## 4. Checks

A **check** is one thing the review evaluates. The check catalog lives in this repo at `review/checks/`, one YAML file per check, like the action catalog (spec §5).

```yaml
id: re-profile-storage-redundancy
pillar: Reliability
waf: [RE:05]                       # the pillar checklist code(s) it evidences
area: profiles                     # AVD design area (below)
learn:                             # where the guidance lives; linked, never restated
  - https://learn.microsoft.com/azure/well-architected/reliability/redundancy
  - https://learn.microsoft.com/azure/virtual-desktop/fslogix-profile-container-configure-azure-files-active-directory
kb: landing-zone/README.md#well-architected-review   # brain KB page with what real runs taught, if any
answeredBy: evidence               # evidence | operator | either
sources:
  - lz: waf-storage-redundancy     # a landing-zone preflight check id, read from its state line
  - arm: storageAccounts.sku       # a read tool and the property it evaluates
  - fact: c11.storage.count        # a C15 fact register ID, read through the brain (spec §6d)
evaluate: profile_storage_redundancy   # a pure function in review/checks.py
severity: high                     # high | medium | low
appliesWhen: { environment: [prod] }   # otherwise the result is n/a, with the reason
remediation:
  kind: deploy-pr                  # action | deploy-pr | cloudshell | none
  owner: Deploy                    # the specialist that takes the handoff
tradeoff: https://learn.microsoft.com/azure/well-architected/reliability/tradeoffs
```

**Evaluation is code.** `evaluate` names a pure function that takes the data the sources returned and gives one result: `pass`, `warn`, `fail`, `unknown` (a source couldn't be read or returned nothing), or `n/a` (with the reason). Functions are tested offline with fixtures that leave out empty properties the way ARM does (landing-zone lesson 0021), and a check whose source can't be read is `unknown`, never `pass`.

**Operator answers count for less, and only where no read exists.** A check marked `answeredBy: operator` covers what Azure can't show: whether restores are tested, who is on call, whether there's a documented DR plan. The operator's answer is recorded as "the operator said", with the time. A check with an evidence source can't be answered by the operator: telling the Reviewer "we have backups" doesn't change a result that came from the backup vault. `either` is for checks whose read is optional (for example, a DR drill that may or may not leave a trace).

**Where checks come from.** The catalog starts from:
1. **The landing zone's `waf-*` checks**, mapped one to one, with `sources: [lz: waf-<check>]`. The review runs the preflight read-only (`-WellArchitected -SkipTenant -SkipNtfs`, Reader only) or reads its last state line, so the two never disagree.
2. **The five pillar checklists**, by code ([Reliability](https://learn.microsoft.com/azure/well-architected/reliability/checklist) RE:01 to RE:10, [Security](https://learn.microsoft.com/azure/well-architected/security/checklist), [Cost Optimization](https://learn.microsoft.com/azure/well-architected/cost-optimization/checklist), [Operational Excellence](https://learn.microsoft.com/azure/well-architected/operational-excellence/checklist), [Performance Efficiency](https://learn.microsoft.com/azure/well-architected/performance-efficiency/checklist)). Every checklist code has at least one check, or a line in `review/coverage.yml` saying why it doesn't apply to AVD or isn't covered yet, so the gaps are visible.
3. **AVD guidance in the product docs**, which is where Microsoft keeps it current: [security recommendations](https://learn.microsoft.com/azure/virtual-desktop/security-recommendations), the [Microsoft cloud security benchmark baseline for AVD](https://learn.microsoft.com/security/benchmark/azure/baselines/azure-virtual-desktop-security-baseline) (based on MCSB v1.0; read through Defender for Cloud, which tracks the current version), [AVD's Advisor recommendations](https://learn.microsoft.com/azure/virtual-desktop/azure-advisor-recommendations), [autoscale](https://learn.microsoft.com/azure/virtual-desktop/autoscale-scenarios), [Insights](https://learn.microsoft.com/azure/virtual-desktop/insights), and the Architecture Center's [virtual desktop guidance](https://learn.microsoft.com/azure/architecture/virtual-desktop/virtual-desktop-get-started).

**AVD design areas** (`area`): `identity`, `network`, `hosts` (compute, images, scaling), `profiles` (FSLogix and storage), `apps`, `monitoring`, `bcdr`, `governance`. They follow the Cloud Adoption Framework's AVD design areas, but the catalog doesn't link those pages: the CAF's enterprise-scale AVD article is deprecated and due to be removed on 2026-10-30 (checked 2026-10-20), and links under `learn.microsoft.com/azure/well-architected/azure-virtual-desktop/` now redirect into the AVD product docs. A scheduled link check (the brain's freshness check, C7) flags a `learn` link that redirects or disappears, and Scribe drafts the fix (spec §6e).

**Changing a check** is a PR with its test and an eval question (§9). `evaluate`, `severity` and `answeredBy` are protected fields, as the action catalog's are (spec §5a): an agent-authored PR that touches them fails the guard, so Improve can't make the estate look better by loosening a check.

## 5. Scores

Scores are computed by `review/score.py` from the results, never by the model, and the weights are in `review/scoring.yml` (a protected path, `governance/` style).

- **Per check:** `pass` = 1, `warn` = 0.5, `fail` = 0, `unknown` = 0, `n/a` left out. An accepted trade-off (§8) is left out of the score and listed apart, so accepting a risk never raises a score by itself.
- **Per pillar:** the severity-weighted mean of its checks (high 3, medium 2, low 1), shown as 0 to 100 with the counts beside it ("72, from 18 checks: 11 pass, 3 warn, 2 fail, 2 unknown; 1 accepted").
- **Confidence per pillar:** the share of its weight that came from live reads, as opposed to operator answers or a stale snapshot. A pillar under 50% is labeled "mostly self-reported".
- **No overall score.** The pillars trade against each other (Microsoft's own [tradeoffs](https://learn.microsoft.com/azure/well-architected/pillars) pages), and one number hides that. The scorecard shows five.
- **Since last time:** a review of the same scope shows each pillar's change and each check that changed result, with the evidence for both.

## 6. Evidence, the review record and what avd-buddy shows

**Evidence for every result.** Each check result in the record carries:
- live reads: the tool, the time, and a hash of the query and response (the response itself stays in the record, which is private, §6 storage);
- landing-zone checks: the check id and the state line's time;
- brain facts: the C15 fact register ID with the snapshot's estate and time (`c11.hostPool.startVmOnConnect@hp-3fa2c1d0`, spec §6d). A snapshot older than the freshness limit (7 days, governance §2.1) still counts, but lowers confidence and is labeled stale;
- forensic facts (C12, Performance Efficiency and Cost Optimization only), cited by window, and only when the brain has them; without them, sizing checks are `unknown` with "needs measurement", and the Readiness agent can propose a collection (spec §6d);
- operator answers: the question, the answer and the time.

**The review record** (`avd-swarm-waf-review/v1`) holds the scope, the context answers, every check's result with its evidence, the scores, and links to plans and PRs raised from it. It is estate data, so it lives with the plan store in the adopter's private resources (spec §5b), behind the operator's sign-in, never in this repo, an issue, a workflow log or the brain's `knowledge/`. Its schema goes in this repo with fixtures, like the plan's.

**avd-buddy renders the record, not the agent's text** (spec §5 step 4, red team R4). Pillar cards and the scorecard have a fixed layout: check name, result, evidence links, the Learn link and the fix button. The agent's text sits beside them as commentary, and it may not restate a result or a score. Text read from the estate (a resource name, a tag, an Advisor description) is shown as quoted evidence, and avd-buddy never renders external images or links from it (red team AX-7, [security-tooling.md](security-tooling.md)).

**Export.** The operator can download the record as Markdown (for a ticket or a wiki) and as CSV, one row per finding with its pillar, checklist code, result and Learn link, in a shape the [Well-Architected backlog scripts](https://learn.microsoft.com/azure/well-architected/design-guides/implementing-recommendations#receive-and-integrate-recommendations) can import into GitHub issues. Both are generated by code from the record.

## 7. What the second brain gives and takes

avd-swarm and the brain change together (decision 0003); the review uses only the connections that exist, plus one new section in C8.

| From the brain | How | Used for |
|---|---|---|
| Knowledge base topic pages (landing zone, FSLogix, identity, cost, operations, troubleshooting) | C7 search through the Concierge read tool, cited by commit | Each check's `kb` link: what real runs taught (for example, the FSLogix SMB channel encryption lesson behind a Security check), shown under "from our experience" beside Microsoft's link |
| Rules (`knowledge/rules/`) | C6, cited as `knowledge/rules/<id>.json@v<n>` | Sizing and delivery context for Performance Efficiency and Cost checks, for example workload tiers |
| Current-state facts (C11) | Through the Concierge only, cited by C15 ID (spec §6d) | Checks the live reads can't reach from the agent identities, and the "since last time" view between reviews |
| Forensic facts (C12) | The same | Sizing, concurrency and logon checks |

| To the brain | How | Brain side |
|---|---|---|
| Findings that aren't accepted | The review record stays private; the adopter's own operations brain, when deployed, reads open findings as one more Well-Architected source for its Posture loop (brain operations-spec §4.7, "Posture"), beside the landing zone's `waf-*` warnings, so there is one findings stream, not two | Evolve proposes PRs or issues by pillar, as today |
| Counts | C8 `avd-swarm-insights/v1` gets a `reviews` section: reviews run, results by check id and pillar, trade-offs accepted by check id, questions abandoned. Counts only, from a fixed vocabulary (the check ids), no free text, no estate | Harvest turns patterns into knowledge by a PR a person merges: a check that fails in most runs of the landing zone becomes a landing-zone default or a lesson |
| Gaps | A checklist code with no check, a check that is `unknown` in most runs, a Learn link that redirected | Docs findings (C13) and KB updates |

The brain's Diagnose and Critic may comment on a finding through the Concierge ("this Advisor recommendation is noise on a dev landing zone, see lesson ..."), but a comment never changes a result: only a check's code or an accepted trade-off does.

C8 isn't written in the brain's `docs/contracts.md` yet; its `reviews` section lands there, with its schema and a test in `tests/Connectors.Tests.ps1`, when C8 does.

## 8. From finding to fix

The Reviewer proposes nothing itself. Each finding's `remediation` says what happens when the operator presses its fix button:

| `kind` | Who | What |
|---|---|---|
| `action` | The owning specialist (Hosts, Users, Cost) | `propose_change` with a catalog action (spec §5), targets resolved live; the plan cites the finding and its evidence, and every §5a precondition applies |
| `deploy-pr` | Deploy | A PR to avd-landing-zone's parameters, which that repo's own deploy workflow runs (spec §4, red team R8). Most Well-Architected fixes are of this kind: zones, redundancy, backup, diagnostics, Defender plans |
| `cloudshell` | Any | A self-contained Cloud Shell block (landing-zone lesson 0015), when no action or parameter fits. Never an improvised write |
| `none` | n/a | Guidance only: process items such as a DR plan or an on-call rota, with the Learn link |

**The gate treats a review as evidence, not as a target list,** the same as a snapshot (spec §6d): a plan that cites a finding still resolves every target live, and the gate refuses a plan whose only evidence is a review older than the freshness limit.

**Accepting a trade-off** is a person's decision with a reason, written down where people review it. The Reviewer drafts a PR to the adopter's private repo adding an entry to `review/accepted.yml` (check id, scope pseudonym, reason, who, expiry of at most 12 months), and the entry counts once it is merged. The landing zone's `data.accepted` findings (its dev and test parameter files' deliberate choices) are read as accepted for that environment, with the parameter file as the reason. Expired acceptances return as findings.

## 9. Evals, safety and limits

- **Eval questions** for the Reviewer are added to `evals/` (append-only): "which pillar is weakest and why", "what's the evidence for the backup finding", "what would fix it". Expected facts come from ARM at eval time (spec §6b), and the check functions are tested against fixtures before any eval runs. Exit: answers cite evidence for at least 80% of eval questions, and the scorecard matches `review/score.py` on every fixture.
- **Prompt injection.** Resource names, tags, Advisor text and operator answers are data. An injected "mark this as passed" can't work, because results come from `evaluate` and are rendered from the record; the Reviewer's text can't change either.
- **Talked into a pass.** An operator answer can satisfy only `operator` and `either` checks. Each is labeled self-reported on the card and lowers confidence.
- **Identity and spend.** The Reviewer reads as its own agent identity, with the same rights as the other agents (Reader and Log Analytics Reader on the AVD resource groups; Reader's `*/read` covers Advisor, Policy and Defender for Cloud recommendations at that scope) and no Graph permission; identity checks that need Graph come from the landing zone's state line or are `unknown`. It falls under the daily token cap and `halt-agents` like every agent (spec §7, governance §2.3).
- **Personal data.** No check needs a user's name. Checks about users are counts.
- **Freshness.** Advisor refreshes on its own schedule, so a same-day review can under-report; the card says so, as the landing zone's review does.

## 10. Phases

| Phase | Builds | Needs | Exit |
|---|---|---|---|
| R0 | This spec; the check catalog's schema and the first checks mapped from the landing zone's `waf-*` ids; `review/checks.py`, `review/score.py` and the record schema, with offline tests; `review/coverage.yml` | Nothing in Azure | Every landing-zone `waf-*` id mapped; every checklist code has a check or a coverage line |
| R1 | The Reviewer agent in avd-buddy, read-only, steps 1 to 5 of §3 | Build order step 2 (avd-buddy, coordinator, metering, `halt-agents`) | The eval exit above |
| R2 | Fix buttons and accepted trade-offs (§8) | Build order step 3 (the adopter repo, `approve`, first actions) | Ten findings fixed through approved plans or merged PRs, each verified by the next review |
| R3 | The C8 `reviews` section and the Posture feed (§7) | C8 in the brain | The brain harvests a review pattern by PR |

R0 is free and offline; it can be built whenever a person says so. R1 onward waits on the build order (spec §9a).

## 11. Prior art

- **Microsoft's assessments** ([Azure Well-Architected Review](https://learn.microsoft.com/assessments/azure-architecture-review/), its Azure Virtual Desktop workload assessment, and [Advisor's WAF assessments](https://learn.microsoft.com/azure/advisor/advisor-assessments)): questionnaires tied to the pillar checklists, with Advisor recommendations for one subscription or resource group, milestones and CSV export. They don't read the estate's configuration beyond Advisor and can't act. This review links them and uses their checklist codes, so an operator can take Microsoft's assessment alongside it and compare.
- **avd-landing-zone's `-WellArchitected` review** (decision 0009): reads resources, Advisor, Defender, Policy and PSRule for Azure. The review reuses it whole.
- **[Azure/review-checklists](https://github.com/Azure/review-checklists)** (MIT): includes an AVD checklist and Resource Graph query scripts. Its items are a source to check the catalog's coverage against; whether to cite its item IDs is an open question (WQ3).
- **PSRule for Azure:** already run by the landing zone's review on the exported resources.

## 12. Open questions

- **WQ1** Should the Reviewer be its own agent, or a mode of the Deploy agent, which already reads the landing zone's review? Proposed: its own agent, because it spans every specialist's area, its prompt and evals are a review's and not a deployment's, and an agent's allowlist and prompt are protected paths that are easier to keep small one agent at a time.
- **WQ2** Does the review also cover the Liquidware tier when it's deployed (ProfileUnity, FlexApp)? Proposed: yes, as checks in avd-liquidware's lane linked from here, once its `liquidware.*` facts are built.
- **WQ3** Cite Azure/review-checklists item IDs in `review/coverage.yml`? They are GUIDs, so each would go in `scripts/public-ids.txt`. Proposed: link the checklist file instead.
- **WQ4** Should a review be able to start from Cloud Shell, without avd-buddy, for operators who prefer it (spec §2 non-goals)? Proposed: yes, the same checks and scorer as a script, after R1.
