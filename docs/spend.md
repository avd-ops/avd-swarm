# Spend as a built-in function

- **Status:** Proposed
- **Part of:** [spec.md](spec.md) §7a (Cost agent, §4) and [governance.md](governance.md) (spend gate §2.1, stop-spend path §2.3, watchdog §2.4). The first piece is built: the public spend guard, [spend/spend_guard.py](../spend/README.md), which the S1a spike runs on.

## 1. Why

The sandboxes avd-swarm runs in are paid for out of pocket. Nick's rule: an estimate before every run, evidence of what each run cost, evidence that its teardown removed everything, and a monthly statement that squares with the invoice. avd-swarm makes changes, so it is where runs start and end. It should not be possible to deploy through avd-swarm without a run record, or to leave one open without being told.

## 2. One public guard, records you keep

avd-swarm ships its own spend guard, [spend/spend_guard.py](../spend/README.md), so anyone can run a lab from this repo alone (decided 2026-10-20; it replaced an import from a private ledger, which a public adopter couldn't run):

- **Before:** `open` checks the estimate against the per-run cap and what's left of the monthly cap (Cost Management's month to date plus this month's unsettled runs), refuses an unpriced line unless allowed on purpose, refuses a second open run in the same sandbox, and gives the run a due time.
- **After:** `close` records the teardown evidence (the leftovers check) and token spend billed outside Azure; `settle` reads the run's actual cost from Cost Management by resource group and day, settled three days after the close, with any charge after the close day flagged.
- **Records** (`avd-swarm-spend-run/v1`, one JSON file per run) hold amounts, dates, the resource group name and the teardown result, never a tenant, subscription or card detail. They live where the owner keeps them (by default `~/clouddrive/avdswarm-spend/` in Cloud Shell), never in this repo. An owner who keeps a private ledger elsewhere can import the same records; avd-swarm doesn't depend on it.
- The rules are pure functions with offline tests (`scripts/tests/test_spend_guard.py`). A change to them is a PR here.
- Not built yet: the monthly statement against the card charge (§3.5) and the daily check that settles runs on its own; until then `settle` and `status` are run by hand.

## 3. What it adds to avd-swarm

### 3.1 Cost agent (spec §4)
Reads the ledger and Cost Management, read-only. Questions it answers with evidence:
- "What will this cost?" The estimate for a plan, line by line, with the per-day cost if it isn't torn down.
- "What did yesterday's run cost?" A run's actual cost, settled or provisional, against its estimate.
- "Is everything torn down?" The teardown check and the cost tail after the close day.
- "Where's the month?" Month to date against the cap, the forecast, runs vs standing cost, and the statement's open items.

### 3.2 Plans that cost money carry a run (governance §2.1, Budget rule)
- `propose_change` adds a **spend block** to any plan that creates, starts or resizes resources: the run estimate (from the deploy agent's sizing or the preflight's estimate), the cap decision (the spend guard's `decide_open`), and the run ID it will open or extend.
- The gate's Budget rule is that decision: a plan is refused when the checked cost is over the per-run cap or what's left of the monthly cap, when a line is unpriced, or when no cap is set. The rule's inputs live in `governance/policy.yml` like every other rule. avd-swarm adds no second budget formula.
- The approve job's first step opens the run (`spend_guard.py open`) before anything is deployed; a refusal there stops the job, even if the gate passed earlier in the day. It then deploys with the `spend-run` tag (§3.3).
- The teardown plan closes it (`spend_guard.py close`). Leftovers fail the plan's verification step, which counts toward the watchdog's "two failed verifications" trigger.
- A plan that would start a second run in a sandbox with one open is refused: one open run per sandbox keeps attribution honest.

### 3.3 Stopping spend is never blocked
Every kill switch position, `halt-all` included, still allows Stop, Lock and teardown plans for resources inside a run. A cost spiral must be stoppable while the platform is halted. Governance §2.3 does this with a separate **`stop-spend`** GitHub Environment and identity: its custom role can only deallocate, stop and delete resources tagged with a run ID, each plan still needs a person's approval, the kill switch never removes it, and any other write by it trips `halt-all`.

That role scopes by tag, so the approve job that opens a run also tags everything it deploys with `spend-run: <run ID>` (avd-landing-zone's `tags` parameter already merges extra tags onto every resource group and resource). The ledger itself doesn't depend on the tag: teardown evidence and actual cost still come from the inventory baseline and resource groups, so resources a tag missed still show up as leftovers.

**Never the last copy** ([avd-second-brain decision 0016](https://github.com/nickprignano/avd-second-brain/blob/main/docs/knowledge/decisions/0016-never-delete-the-last-copy.md)). The run store, where every run's logs and teardown evidence go, lives in its own resource group with a `CanNotDelete` lock, outside the run resource groups the `stop-spend` role is scoped to, and never carries the `spend-run` tag; anything tagged `avd-evidence: keep` is also covered by the Azure Policy `denyAction` on `delete` ([governance.md](governance.md)), so neither a teardown plan nor the `stop-spend` identity can delete it. A teardown plan follows the store's `-TeardownLogs` preference (default `Keep`) and exports the deployment's own logs to the run store before it deletes the deployment. A plan that would delete the last copy of data inside a deployment (profiles, backups, assessment data) names `-DiscardLastCopy` in its summary; only the approving person can accept that, and no agent or watchdog sets it.

### 3.4 Watchdog and observer events (governance §2.2, §2.4)
| Signal (from the daily check) | Event | Default trigger |
|---|---|---|
| Month to date reached the monthly cap | `spend.cap.reached` | `halt-actions` except stopping spend (§3.3) |
| A run past its planned hours | `spend.run.overdue` | none; avd-buddy nags |
| Leftovers after a teardown, or charges after a run closed | `spend.teardown.failed` | `halt-actions` for new runs in that sandbox |
| Resources appeared with no run open | `spend.stray` | none; reported |

Events carry counts and amounts, never conversation text, as governance requires.

### 3.5 avd-buddy
- The plan card shows the estimate, the per-day cost if left up, and what's left of the cap, in the fixed plan layout (agent text never states the cost).
- While a run is open, a banner says when it's due down and what it has cost so far.
- On the 10th, a card with the monthly statement and a field for the card charge, which reruns the statement.

### 3.6 Insights to the brain (spec §6c)
Monthly counts only, in `avd-swarm-insights/v1`: runs, refused runs by reason, median actual-to-estimate ratio per run kind, teardowns with leftovers. These feed the brain's harvest so estimate allowances and caps start from real numbers in other estates.

### 3.7 Token spend: metered as it happens, capped outside (R22)
Cost Management data for Azure arrives hours to a day after the spend, and Anthropic's API usage isn't in Azure's bill at all. A cap that reads either one can be crossed many times over before it fires, by a loop that runs for an afternoon. So model spend is enforced from what each call reports, and capped again at the provider:

1. **One metered model client.** Every model call, by any agent, the judge, Improve, Scribe or the brain, goes through one wrapper. Before a call it reserves the worst case (the request's input tokens, from the previous turn's usage or a token count, plus `max_tokens` of output, at the model's pinned price) and refuses if the reservation would cross the run's, the day's or the month's cap. After the call it books the actual cost from the response's `usage` (input, output, cache read, cache write) and releases the rest of the reservation. It checks the kill switch flag in the same step (governance §2.3, layer 6). If the counter can't be read or written, the call is refused.
2. **Prices are pinned in Git.** `governance/model-prices.yml` (protected) holds the per-million-token prices per model ID, with the date and source they were read from. A model with no price is refused, like an unpriced line in the spend gate.
3. **The counter lives outside the agents.** A small table (one row per day and per run) that only the wrapper's identity writes, with optimistic concurrency so parallel agents can't both spend the last dollar. The watchdog reads it every 5 minutes with the `agents.activity` counts (governance §2.2) and trips `halt-agents` at the daily cap, independently of the wrapper.
4. **The provider caps it again.** The agents' Anthropic federation rule targets a service account in a **workspace of its own**, with a monthly spend limit and per-model token rate limits set by a person in the Claude Console (limits can be set on any workspace but the Default one). The workspace limit is the backstop if everything above fails, and nothing in avd-swarm can raise it. For Claude in Foundry, the deployment's tokens-per-minute quota is the rate ceiling.
5. **Cost Management reconciles; it doesn't enforce.** The daily job compares the meter with Cost Management and Anthropic's usage report, and a gap of more than 10% is a `spend.meter.drift` event and a lesson, never a reason to raise a cap.

The S1a spike carries a minimal version of 1 and 2 from build-order step 1 (spikes/s1a: a per-run token spend cap in the agent service, and a stop flag), since it is the one thing that can already spend tokens.

## 4. Phases
| Phase (spec §9) | Adds |
|---|---|
| S1 | Cost agent reads the ledger; avd-buddy shows open runs |
| S2 | Spend block on plans, Budget rule through the gate, the stop-spend exception, the watchdog events |
| S3 | Deploy and Images agents size and price their own plans |

## 5. Open questions
- ~~**CQ1** Pin a private spend module by commit, or publish it as a package?~~ **Answered 2026-10-20:** neither; avd-swarm ships its own public guard (§2), so the spike runs from this repo alone.
- **CQ2** Should a run that crosses a month count against the month it started in (today's rule), or be split by day?
- ~~**CQ3** Should `spend.cap.reached` trip `halt-agents` too?~~ **Answered 2026-10-19:** token spend has its own metered cap that trips `halt-agents` (§3.7); `spend.cap.reached` for Azure resources stays at `halt-actions`.
