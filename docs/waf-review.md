# Spec: a chat-driven Well-Architected review in avd-buddy

- **Status:** Proposed (2026-10-20). Design only: nothing here is built, scheduled or run, and it needs no spend until its first eval run, which is estimated in the [spend.md](spend.md) format first.
- **Part of:** [spec.md](spec.md) (agents §4, write path §5, facts §6d), [governance.md](governance.md). Build order: §9a step 4, "designed, not scheduled", until a person moves it (§9 below).
- **Pairs with:** avd-second-brain, which feeds the review its knowledge and facts and takes its findings into the Posture loop ([decision 0003](decisions/0003-swarm-acts-brain-thinks.md), §7 below).
- **Consumes:** the [Azure/review-checklists](https://github.com/Azure/review-checklists) AVD checklist, Microsoft's [WARA](https://github.com/Azure/Well-Architected-Reliability-Assessment) collector, and avd-landing-zone's Well-Architected review, `Test-AvdLandingZoneReadiness.ps1 -WellArchitected` ([landing-zone decision 0009](https://github.com/nickprignano/avd-landing-zone/blob/master/build/decisions/0009-well-architected-review.md)).

## 1. Summary

An operator opens avd-buddy and says "review my AVD estate against Well-Architected". The Deploy agent, in its **review mode**, walks them through the five pillars, one at a time, in the order they care about. For each pillar it first reads what Microsoft's own tools and the family already produce (the [Azure/review-checklists](https://github.com/Azure/review-checklists) AVD checklist as the list of what to check, Microsoft's WARA collector for Reliability, the landing zone's `waf-*` checks, Azure Advisor, Defender for Cloud, Azure Policy, and current-state facts through the brain), and asks only the questions the evidence can't answer, such as "have you tested a restore of a profile share?". Every finding comes with its evidence and a link to the Microsoft Learn page that says why it matters. There is no score of our own: each item shows its source's own verdict, and Reliability is prioritized by WARA's analyzer.

The review changes nothing. A finding's fix is handed to the specialist that owns it, which turns it into a plan through the ordinary write path (spec §5), a PR to the landing zone's parameters, or a Cloud Shell block. Accepting a trade-off is a PR a person merges, never a click in the chat.

**Consume, don't rebuild** (prior art check, 2026-10-20, with the owner's go the same day). The rules and the evidence collection already exist. Microsoft publishes them as the AVD review checklist, the WARA module, Advisor, Defender for Cloud and Policy, and the landing zone's `-WellArchitected` review wraps several of them. This review writes **no Well-Architected rule engine of its own**. What it adds is what none of those has:
- a chat front end that walks the pillars and asks only what the reads left open;
- evidence from the brain's redacted C11 and C12 facts beside Microsoft's results;
- accepted trade-offs that a person merges, with a reason and an expiry;
- a fix path through avd-swarm's governed plans.

Microsoft's assessments ([Azure Well-Architected Review](https://learn.microsoft.com/assessments/azure-architecture-review/) and its Azure Virtual Desktop workload variant) stay questionnaires, and the landing zone's decision 0009 says that part "remains manual". This review is where it stops being manual.

## 2. Goals and non-goals

### Goals
- Answer "would this AVD estate pass a Well-Architected review, pillar by pillar, and why?" with evidence for every claim.
- Ask the operator as little as possible: never a question that a read could answer.
- Turn findings into governed plans or PRs from the same screen, with no new write path.
- Repeat the review and show what changed since the last one (a milestone, as Microsoft's assessment calls it).
- Link Microsoft Learn for the guidance instead of restating it ([avd-second-brain decision 0011](https://github.com/nickprignano/avd-second-brain/blob/main/docs/knowledge/decisions/0011-family-format-upstream-sources.md)).

### Non-goals
- Any change made by the review itself, or any "accept" or "fix" that completes inside the chat (spec §2).
- A score or rule engine of our own, and a model deciding a finding (avd-second-brain's rule "a model never decides").
- Re-implementing a check the landing zone's preflight already has. The review reads its result.
- **Out of scope here:** comparing one estate with others, reviewing more than one tenant or workloads other than AVD, and formatted documents beyond Markdown and CSV.

## 3. How the conversation goes

Review mode belongs to the Deploy agent (spec §4), which already reads the landing zone's Well-Architected review; it adds a prompt section, eval questions and the reader's tools to that agent, not a new agent. A review is a sequence of fixed steps; the model chooses the wording, not the steps.

| Step | Review mode | The operator sees |
|---|---|---|
| 1. Scope | Lists the landing zones and host pools the agent identities can read (Resource Graph) and asks which to review. Defaults to the one the conversation is about | A pick list, from the tool's output, never typed IDs |
| 2. Context | Asks the workload questions that change what "good" means: how critical, how many users, working hours and time zones, recovery targets (RTO and RPO for profiles and hosts), compliance needs, and the pillar order. Answers that the landing zone already records (environment `dev`, `test`, `prod`; the parameter file's deliberate trade-offs) are filled in and shown, not asked | Five to eight questions, each with "why we ask" and a Learn link |
| 3. Evidence | Runs every check for the scope in one pass (§4), read-only, and stores the results in a **review record** (§6) | A progress line per source before each slow read (landing-zone lesson 0006); a source that can't be read is listed with its error, and the rest still run (landing-zone decision 0009) |
| 4. Pillar walk | For each pillar in the operator's order: the findings that came from evidence, then the open questions for checks that need the operator (§4, `answeredBy: operator`), one at a time | A pillar card (§6) rendered from the record, and the questions under it |
| 5. Summary | Counts results per pillar (§5) and shows what changed since the previous review of the same scope | The summary, rendered from the record |
| 6. Next steps | For each finding the operator picks, hands off to the owning specialist (§8), or records a request to accept the trade-off | Plans and PR links, from the plan store and GitHub, never described in agent text (spec §5 step 4) |

The operator can stop after any pillar; the record keeps what was done, and a later "continue the review" resumes it. A full review is about the length of Microsoft's own (about 60 questions there); most of this one's checks are answered by reads, so the operator answers far fewer.

## 4. Checks: Microsoft's lists, our mapping

A **check** is one item the review evaluates. The items come from Microsoft, and this repo only maps each one to the evidence that answers it.

**What to check: the AVD review checklist.** [Azure/review-checklists](https://github.com/Azure/review-checklists) (MIT) keeps an AVD checklist, `checklists/avd_checklist.en.json`. Checked 2026-10-20, each item has a category (BCDR, Compute, Foundation, Identity, Monitoring and Management, Networking, Security, Storage), a subcategory, text, a description, a `waf` pillar, a severity, a short `id` (`A01.01`) and a Learn `link`. The AVD items checked carried no Resource Graph query of their own. Where the repo's query scripts do return results for an item, the reader takes them as evidence like any other source. For every other item, the evidence mapping is the part this repo adds. The review reads the checklist from a pinned commit (a pin bump is a PR), and never copies its text: the card shows the item's `text` and `link` at that commit. Items are cited by their short `id`, never their `guid`, so no GUID enters this repo (the ID guard).

**Reliability evidence: WARA.** Microsoft's [Well-Architected Reliability Assessment](https://github.com/Azure/Well-Architected-Reliability-Assessment) module ([PowerShell Gallery `WARA`](https://www.powershellgallery.com/packages/WARA), MIT, 1.0.6) runs a read-only Resource Graph collection, and its collector has an `-AVD` switch for AVD resource types. It covers the Reliability pillar only. `Start-WARACollector` writes a JSON file. The review's reader takes that file (and the analyzer's action plan, if the operator ran it) and turns each finding into evidence for its checklist item. It doesn't use the reporter, which needs Windows with PowerPoint. WARA needs PowerShell 7.4, Az.Accounts 3.0 and Az.ResourceGraph 1.0, which Cloud Shell has. It runs one of two ways:
- the operator runs the collector in Cloud Shell and hands the JSON to avd-buddy (the default, with no new identity);
- once build step 2 exists, a read tool runs it as the Deploy agent's identity (Reader).

**The other pillars' evidence: what Azure and the family already evaluate.**
1. **The landing zone's `waf-*` checks** (decision 0009: design checks, Advisor, Defender for Cloud, Policy and PSRule for Azure, by pillar), read from the preflight's state line or run read-only (`-WellArchitected -SkipTenant -SkipNtfs`).
2. **Advisor** recommendations (their category is the pillar), **Defender for Cloud** recommendations and **Policy** compliance, read directly for anything outside the landing zone's resource groups.
3. **C11 and C12 facts** through the brain (spec §6d), for what the agent identities can't read live and for sizing.
4. **The operator**, for items Azure can't show (a tested restore, a DR runbook, an on-call rota).

**The mapping file.** `review/map.yml` holds one line per checklist item: its `id` and the evidence that answers it. That evidence is a WARA recommendation id, a landing-zone `waf-*` id, an Advisor or Defender recommendation type, a Policy definition, a C15 fact ID, or `operator`. Each line also records which family specialist owns the fix (§8).

```yaml
- item: B03.01                # checklist id at the pinned commit (an illustrative id)
  evidence:
    - wara: <recommendation id>   # from the collector's JSON
    - lz: waf-profile-backup      # a landing-zone check
  answeredBy: evidence        # evidence | operator | either
  appliesWhen: { environment: [prod] }
  fix: { kind: deploy-pr, owner: Deploy }   # action | deploy-pr | cloudshell | none
  kb: fslogix/README.md       # brain KB page with what real runs taught, if any
```

**Evaluation stays mechanical, and mostly isn't ours.** A Microsoft source that already gives a verdict (WARA, Advisor, Defender, Policy, a landing-zone check) is read as-is: open recommendation means `fail` or `warn` by its severity, and none means `pass`. Our own code (`review/evaluate.py`, pure functions tested against fixtures that leave out empty properties the way ARM does, landing-zone lesson 0021) only covers items no Microsoft source evaluates and a C11 or C12 fact can. Every new function is a gap worth proposing upstream to the checklist or to WARA (brain decision 0011; a person files it). When a source can't be read, the item is `unknown`, never `pass`.

**Operator answers count for less, and only where no read exists.** An item marked `answeredBy: operator` covers what Azure can't show. The answer is recorded as "the operator said", with the time. An item with an evidence source can't be answered by the operator: saying "we have backups" doesn't change a result that came from the backup vault. `either` is for items whose read is optional, such as a DR drill that may or may not leave a trace.

**Coverage is visible.** Every checklist item at the pinned commit is mapped, marked `operator`, or listed as `n/a` for this landing zone with a reason. A new checklist commit that adds items fails the mapping test until each new item has a line. The pillar checklist codes ([Reliability](https://learn.microsoft.com/azure/well-architected/reliability/checklist) RE:01 to RE:10, and the [Security](https://learn.microsoft.com/azure/well-architected/security/checklist), [Cost Optimization](https://learn.microsoft.com/azure/well-architected/cost-optimization/checklist), [Operational Excellence](https://learn.microsoft.com/azure/well-architected/operational-excellence/checklist) and [Performance Efficiency](https://learn.microsoft.com/azure/well-architected/performance-efficiency/checklist) checklists) are linked from each pillar card.

**Links.** The catalog doesn't link the Cloud Adoption Framework's AVD scenario pages, because its enterprise-scale AVD article is deprecated and due to be removed on 2026-10-30. Links under `learn.microsoft.com/azure/well-architected/azure-virtual-desktop/` now redirect into the AVD product docs (both checked 2026-10-20). The brain's freshness check (C7) flags a link that redirects or disappears, and Scribe drafts the fix (spec §6e).

**Changing the mapping** is a PR with its test and an eval question (§9). `answeredBy`, `evidence` and our own evaluate functions are protected fields, like the action catalog's (spec §5a): an agent-authored PR that touches them fails the guard, so Improve can't make an estate look better by loosening a mapping. Moving to a new checklist or WARA version is a pin bump PR, and the first review after it shows which results changed because of the bump, not because of the estate.

## 5. No score of our own

The review doesn't compute scores (prior art check, 2026-10-20). It shows Microsoft's verdicts and counts them:

- **Per item:** the source's own result (`pass`, `warn`, `fail`, `unknown`, `n/a`), or `accepted` for a trade-off a person merged (§8), which is listed separately.
- **Reliability:** prioritized by WARA's analyzer when the operator ran it, else by the collector's own severity.
- **Per pillar:** counts only ("18 items: 11 pass, 3 warn, 2 fail, 2 unknown; 1 accepted"), with the share answered by live reads rather than operator answers or a stale snapshot. A pillar under 50% is labeled "mostly self-reported". Counts are code (`review/count.py`), never a model.
- **No overall number,** since the pillars trade against each other ([pillars](https://learn.microsoft.com/azure/well-architected/pillars)).
- **Since last time:** each item that changed result, with the evidence for both.

## 6. Evidence, the review record and what avd-buddy shows

**Evidence for every result.** Each check result in the record carries:
- live reads: the tool, the time, and a hash of the query and response (the response itself stays in the record, which is private, §6 storage);
- landing-zone checks: the check id and the state line's time;
- brain facts: the C15 fact register ID with the snapshot's estate and time (`c11.hostPool.startVmOnConnect@hp-3fa2c1d0`, spec §6d). A snapshot older than the freshness limit (7 days, governance §2.1) still counts, but lowers confidence and is labeled stale;
- forensic facts (C12, Performance Efficiency and Cost Optimization only), cited by window, and only when the brain has them; without them, sizing checks are `unknown` with "needs measurement", and the Readiness agent can propose a collection (spec §6d);
- operator answers: the question, the answer and the time.

**The review record** (`avd-swarm-waf-review/v1`) holds the scope, the context answers, every item's result with its evidence, the counts, and links to plans and PRs raised from it. It is estate data, so it lives with the plan store in the adopter's private resources (spec §5b), behind the operator's sign-in, never in this repo, an issue, a workflow log or the brain's `knowledge/`. Its schema goes in this repo with fixtures, like the plan's.

**avd-buddy renders the record, not the agent's text** (spec §5 step 4, red team R4). Pillar cards and the summary have a fixed layout: check name, result, evidence links, the Learn link and the fix button. The agent's text sits beside them as commentary, and it may not restate a result or a count. Text read from the estate (a resource name, a tag, an Advisor description) is shown as quoted evidence, and avd-buddy never renders external images or links from it (red team AX-7, [security-tooling.md](security-tooling.md)).

### 6a. How avd-buddy shows it (design pass)

avd-buddy uses the family's [avd-ops design system](https://github.com/nickprignano/avd-ops/blob/main/docs/design-system.md) (v1.0.0), vendored into `buddy/` like the portal's copy, with no hex values, font CDNs or inline styles of its own. The review needs five patterns. They are built from existing tokens and components, and any new class goes into avd-ops first and is then vendored. These are proposals for that PR, not built yet.

| Pattern | Built from | Notes |
|---|---|---|
| **Summary** | A `.card` per pillar in a grid: one column at the 16px phone gutter, five columns at desktop width. The `.eyebrow` holds the pillar name, the fail and warn counts are set in `--display`, and the rest and the live-read share go in `--muted` | No overall number (§5). A meter bar shows each result's share of the pillar's items, using `--good`, `--caution`, `--bad` and `--line` for unknown. The counts are always written out beside the bar, so color never carries meaning alone. "Since last time" is written out ("2 fewer fails"), never just an arrow |
| **Pillar card** | `.card` with a header (pillar, counts, Learn checklist link) and a list of check rows | Rendered from the record (§6). The agent's commentary sits outside the card, in the chat stream, in body text with no card |
| **Check row** | A row with a result pill, the check name, the area chip (`.chips .chip`), evidence links and a fix button | Result pills reuse the Results tokens: `pass` on `--good-soft`/`--good`, `warn` on `--caution-soft`/`--caution`, `fail` on `--bad-soft`/`--bad`, `unknown` and `n/a` on `--sunken`/`--muted`, `accepted` on `--surface` with a `--line-strong` border. Every text and pill pair clears WCAG AA in both themes (checked 2026-10-20 against v1.0.0: lowest is `pass`, light theme, 4.69:1). Each pill shows its word (and the reason for `n/a` and `unknown`) and is never a bare dot. This adds a `.result.r-*` class family to avd-ops beside `.status.s-*`, scoped the same way so it can't collide with the portal's `.status` |
| **Evidence and self-reported labels** | Evidence goes in `code`/`--mono` links (tool, time, fact ID). Self-reported is a chip with a dashed `--line-strong` border and the words "operator said" | Stale snapshot evidence gets the same chip with "stale, 9 days". Text quoted from the estate is shown as `code`, never as a link or an image (AX-7) |
| **Fix button and question card** | The fix button is a `.cta.ghost` reading "Propose a fix", and it opens the plan or PR link from the plan store. It never reads "Fix", because nothing is fixed until a person approves. The question card is a `.card` with the question, a "why we ask" line with its Learn link, and the answer controls; "Skip" is a `.cta.ghost` | One question at a time. A skipped question leaves its check `unknown`, which the operator can see on the pillar card |

**Accessibility.** The check list is a real `table` (inside `.tbl`, so it scrolls instead of overflowing on a phone), with a header row that screen readers announce. Result pills are text. The fix button and the question controls are `button` elements reachable by Tab and show the `--focus` ring. The progress lines in step 3 go to an `aria-live="polite"` region. Motion uses `--fast` and is off under reduced motion. Dark mode follows the system through the tokens.

**Before the first line of `buddy/` UI code**, the patterns above go through a design review against the avd-ops system (layout at phone and desktop width, both themes, contrast of every result pill), and the new classes land in avd-ops with their tests.

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
| Counts | C8 `avd-swarm-insights/v1` gets a `reviews` section: reviews run, results by checklist item id and pillar, trade-offs accepted by item id, questions abandoned. Counts only, from a fixed vocabulary (the checklist's item ids at the pinned commit), no free text, no estate | Harvest turns patterns into knowledge by a PR a person merges: a check that fails in most runs of the landing zone becomes a landing-zone default or a lesson |
| Gaps | A checklist code with no check, a check that is `unknown` in most runs, a Learn link that redirected | Docs findings (C13) and KB updates |

The brain's Diagnose and Critic may comment on a finding through the Concierge ("this Advisor recommendation is noise on a dev landing zone, see lesson ..."), but a comment never changes a result: only a source's result, the mapping or an accepted trade-off does.

C8 isn't written in the brain's `docs/contracts.md` yet; its `reviews` section lands there, with its schema and a test in `tests/Connectors.Tests.ps1`, when C8 does.

## 8. From finding to fix

Review mode proposes nothing itself. Each finding's `fix` line in the mapping says what happens when the operator presses its fix button:

| `kind` | Who | What |
|---|---|---|
| `action` | The owning specialist (Hosts, Users, Cost) | `propose_change` with a catalog action (spec §5), targets resolved live; the plan cites the finding and its evidence, and every §5a precondition applies |
| `deploy-pr` | Deploy | A PR to avd-landing-zone's parameters, which that repo's own deploy workflow runs (spec §4, red team R8). Most Well-Architected fixes are of this kind: zones, redundancy, backup, diagnostics, Defender plans |
| `cloudshell` | Any | A self-contained Cloud Shell block (landing-zone lesson 0015), when no action or parameter fits. Never an improvised write |
| `none` | n/a | Guidance only: process items such as a DR plan or an on-call rota, with the Learn link |

**The gate treats a review as evidence, not as a target list,** the same as a snapshot (spec §6d): a plan that cites a finding still resolves every target live, and the gate refuses a plan whose only evidence is a review older than the freshness limit.

**Accepting a trade-off** is a person's decision with a reason, written down where people review it. Review mode drafts a PR to the adopter's private repo adding an entry to `review/accepted.yml` (checklist item id, scope pseudonym, reason, who, expiry of at most 12 months), and the entry counts once it is merged. The landing zone's `data.accepted` findings (its dev and test parameter files' deliberate choices) are read as accepted for that environment, with the parameter file as the reason. Expired acceptances return as findings.

## 9. Evals, safety and limits

- **Eval questions** for review mode are added to `evals/` (append-only): "which pillar is weakest and why", "what's the evidence for the backup finding", "what would fix it". Expected facts come from ARM at eval time (spec §6b), and the reader is tested against fixtures before any eval runs. Exit: answers cite evidence for at least 80% of eval questions, and the summary matches `review/count.py` on every fixture.
- **Prompt injection.** Resource names, tags, Advisor text and operator answers are data. An injected "mark this as passed" can't work, because results come from Microsoft's sources and the mapping and are rendered from the record; the agent's text can't change either.
- **Talked into a pass.** An operator answer can satisfy only `operator` and `either` checks. Each is labeled self-reported on the card and lowers confidence.
- **Identity and spend.** Review mode reads as the Deploy agent's identity, with the same rights as the other agents (Reader and Log Analytics Reader on the AVD resource groups; Reader's `*/read` covers Advisor, Policy and Defender for Cloud recommendations at that scope) and no Graph permission; identity checks that need Graph come from the landing zone's state line or are `unknown`. It falls under the daily token cap and `halt-agents` like every agent (spec §7, governance §2.3).
- **Personal data.** No check needs a user's name. Checks about users are counts.
- **Freshness.** Advisor refreshes on its own schedule, so a same-day review can under-report; the card says so, as the landing zone's review does.

## 10. Phases

| Phase | Builds | Needs | Exit |
|---|---|---|---|
| R0 | This spec; the checklist pin; `review/map.yml` for every AVD checklist item; a reader for WARA's collector JSON and the landing zone's state line; `review/evaluate.py` for the few items only C11/C12 can answer; `review/count.py` and the record schema; offline tests over a WARA collector sample built from public examples (never a real estate's output), a sample state line and the checklist at its pin | Nothing in Azure | Every checklist item mapped, marked `operator` or `n/a` with a reason; every landing-zone `waf-*` id reachable from some item |
| R1 | Review mode for the Deploy agent in avd-buddy, read-only, steps 1 to 5 of §3 | Build order step 2 (avd-buddy, coordinator, metering, `halt-agents`) | The eval exit above |
| R2 | Fix buttons and accepted trade-offs (§8) | Build order step 3 (the adopter repo, `approve`, first actions) | Ten findings fixed through approved plans or merged PRs, each verified by the next review |
| R3 | The C8 `reviews` section and the Posture feed (§7) | C8 in the brain | The brain harvests a review pattern by PR |

R0 is free and offline; it can be built whenever a person says so. R1 onward waits on the build order (spec §9a).

## 11. Prior art

- **Microsoft's assessments** ([Azure Well-Architected Review](https://learn.microsoft.com/assessments/azure-architecture-review/), its Azure Virtual Desktop workload assessment, and [Advisor's WAF assessments](https://learn.microsoft.com/azure/advisor/advisor-assessments)): questionnaires tied to the pillar checklists, with Advisor recommendations for one subscription or resource group, milestones and CSV export. They don't read the estate's configuration beyond Advisor and can't act. This review links them and uses their checklist codes, so an operator can take Microsoft's assessment alongside it and compare.
- **avd-landing-zone's `-WellArchitected` review** (decision 0009): reads resources, Advisor, Defender, Policy and PSRule for Azure. The review reuses it whole.
- **[Azure/review-checklists](https://github.com/Azure/review-checklists)** (MIT): its AVD checklist is the review's list of items (§4), read at a pin, cited by short id.
- **[WARA](https://github.com/Azure/Well-Architected-Reliability-Assessment)** (MIT): the Reliability evidence (§4). Its analyzer and PowerPoint reporter aren't used; the review's own output stays Markdown and CSV.
- **Azure Copilot's deployment agent** (preview) grounds deployment plans in Well-Architected guidance, and a community Well-Architected review skill exists for coding agents. Neither reads AVD-specific evidence, records accepted trade-offs or routes fixes through an approval outside the chat.
- **[Azure SRE Agent](https://learn.microsoft.com/azure/sre-agent/run-modes)** is an **optional add-on**, not a dependency. Where an adopter runs it, avd-buddy can read its findings through the Azure MCP Server's SRE Agent tools ([SRE Agent MCP server](https://learn.microsoft.com/azure/sre-agent/mcp-server)), and show them on a pillar card labeled as coming from SRE Agent, cited like Advisor. Its own identity holds only Reader and Desktop Virtualization Reader, and a fix it finds comes back as an avd-swarm plan, so avd-swarm's `approve` path is still the only writer (spec §5). The chat front end, the C11/C12 evidence and the governed fix path are built here either way.
- **PSRule for Azure:** already run by the landing zone's review on the exported resources.

## 12. Open questions

- **WQ1** ~~Own agent or a mode of the Deploy agent?~~ **Answered (2026-10-20, v1 red team):** a mode of the Deploy agent, which already reads the landing zone's review. No new agent.
- **WQ2** Does the review also cover the Liquidware tier when it's deployed (ProfileUnity, FlexApp)? Proposed: yes, as checks in avd-liquidware's lane linked from here, once its `liquidware.*` facts are built.
- **WQ3** ~~Cite the checklist's item GUIDs?~~ **Answered:** cite the short `id` (`A01.01`) at the pinned commit; no GUID enters the repo.
- **WQ5** Where WARA and a landing-zone `waf-*` check disagree on the same item, which wins on the card? Proposed: show both with their sources, and count the worse one, since a missed risk costs more than a duplicate.
- **WQ4** Should a review be able to start from Cloud Shell, without avd-buddy, for operators who prefer it (spec §2 non-goals)? Proposed: yes, the same reader and counts as a script, after R1.
