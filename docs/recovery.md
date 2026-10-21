# Spec: recovery tests and failover, run by avd-swarm

- **Status:** Proposed ([decision 0006](decisions/0006-recovery-tests-and-failover.md)). Spec only: nothing here is built, nothing has run, no spend.
- **Date:** 2026-10-20
- **Part of:** [spec.md](spec.md) (§4 agents, §5 write path, §5a catalog, §9a build order), [governance.md](governance.md) and [identities.md](identities.md).
- **The why:** [avd-second-brain decision 0019](https://github.com/avd-ops/avd-second-brain/blob/main/docs/knowledge/decisions/0019-recovery-runs-through-the-swarm.md) records why recovery testing and failover belong in avd-swarm and what they must prove. This page is the how.
- **Builds on:** avd-landing-zone's recovery spec (`build/specs/recovery-spec.md`, draft PR #75): the inventory P1 to P9, tiers R0 to R2, `Get-AvdRecoveryStatus.ps1`, `Export-AvdConfiguration.ps1`, `Start-AvdRecoveryTest.ps1` and its tests T-profile, T-config, T-image, T-signin and T-failover; and contract C17's use case `recovery` (avd-second-brain draft PR #40). Section numbers prefixed "LZ" point at that spec.

This is a personal project, not for production, provided as is.

## 1. Summary

The landing zone owns recovery: what to protect, the tiers, the scripts that check copies and restore them, and the tests that prove a tier by measuring its real recovery point (RPO) and recovery time (RTO). An operator can run all of it by hand from Cloud Shell, and always will be able to.

avd-swarm adds the governed way to run it from chat: a Recovery agent that answers "could we come back, and how fast?" with evidence, and four catalog actions (configuration snapshot, recovery test, failover test, failover and failback) that run the landing zone's own scripts at a pinned release, through the same gate, approval, kill switch and verification as every other change. avd-swarm never reimplements a restore. It decides nothing about tiers; it plans, asks, runs, verifies and reports.

Everything here stays inside the operator's own tenant: any region and any subscription in that tenant that the adopter has registered. That is the whole public scope (§8).

## 2. Goals and non-goals

### Goals
1. An operator asks "are we recoverable?" and gets the landing zone's status check, read live, with each `dr-*` result and the date and measured RPO and RTO of the last test.
2. "Run a recovery test" becomes a plan with a printed cost estimate, a time box and its own teardown, that a person approves once.
3. Failover and failback for an R2 landing zone are catalog actions with preconditions the gate checks, so a person approving under pressure sees exactly what moves.
4. Every outcome reaches the brain, so a tier counts as met only when a test proved it (LZ D3).

### Non-goals
- **Being the only way to recover.** The landing zone's Cloud Shell blocks stay the primary path. In a region outage avd-swarm itself may be down (§6.4).
- **Choosing the tier, the recovery region or when to fail over.** Those are a person's calls. The agent can recommend; it never decides.
- **Starting a recovery run without a person asking.** Plans start from an operator's request, like every other plan (spec §5).
- **Copying secrets, opening real users' data, or restoring over a source.** The landing zone's rules (LZ D2, D4, section 8) hold here too, and the gate enforces them.

## 3. The Recovery agent

A new specialist, read-only like the others (spec §4). Adding it is a PR with its prompt, its tool allowlist and its eval questions.

| Answers | Reads | Proposes |
|---|---|---|
| Which tier the landing zone is on and whether it is met; when each item's last good copy was made; when the last test ran and what it measured; what a failover would move and what it would cost | `Get-AvdRecoveryStatus.ps1 -AsJson` (read-only, run by the read tool as the agent's identity at the pinned release); Backup vault jobs and recovery points, Compute Gallery replication and the standby pool through the Azure MCP Server (read-only); Service Health for the landing zone's regions; the brain's answer on past recovery outcomes, when a brain is deployed | A configuration snapshot, a recovery test, a failover test, a failover or a failback, as a plan (§4) |

- The status check's results are the evidence an answer cites, by `id` (`dr-profile-backup`, `dr-tested`, ...) and time. An answer that says a tier is met without a passing `dr-tested` is wrong, and the eval set has a question for it.
- The agent reads the run store's run records only through the status check, which summarizes them. It never reads a configuration snapshot's contents: they hold object IDs and names (LZ 7.3).
- Facts from the brain (C11, C12) can give a reason to propose a test; they never name its targets (spec §6d).
- Eval questions start with: "Is our profile backup working?" (must surface U1, the shared key question, LZ 12), "When did we last test recovery?", "What would a failover to the standby region do?", and "Is R1 met?" against a fixture where the last test failed.

## 4. Catalog actions

A new action kind, `recovery`, beside `host`, `power`, `group` and `deploy-pr` (spec §5a). Each action runs one landing zone script, pinned by the release tag the adopter synced (spec §5b) and by the script's SHA-256 in the catalog, the same way Run Command scripts are pinned. A script whose hash doesn't match at the approved commit is refused.

| Action | Runs | Writes | Spend | Rollback |
|---|---|---|---|---|
| `recovery-config-snapshot` | `Export-AvdConfiguration.ps1` then `Stop-AvdRunRecorder.ps1 -CopyTo github` | A run folder in the run store and a commit to the adopter's private companion repo | None beyond storage already there | None needed: a snapshot only adds a copy (LZ 11) |
| `recovery-test` | `Start-AvdRecoveryTest.ps1 -Test T-profile, T-config, T-image or T-signin` | New resources only, under new names, in a run resource group inside the registered recovery scope | The script's own estimate, printed first (LZ 13: about $3 to $5 per test); the spend gate checks it | The test's own teardown, with evidence (LZ 8, 11) |
| `recovery-failover-test` (R2) | `Start-AvdRecoveryTest.ps1 -Test T-failover` | A test group's application group assignments, in both pools; starts standby hosts | Standby hosts running for the test's time box | Restore the assignments it found and deallocate the hosts it started |
| `recovery-failover` and `recovery-failback` (R2) | The landing zone's failover procedure (LZ 4, F16): start standby hosts, drain the pool being left, assign the users groups to the standby pool's application groups, remove them from the primary's | Application group assignments for the registered AVD groups; power state of standby hosts | The standby pool running until failback | Failback is its own plan, approved on its own; it restores the assignments failover found |

Every recovery action declares these preconditions. The gate checks each before approval and the job checks it again live, as for host actions (spec §5a). A precondition that can't be read counts as failed.

| Rule | What the gate checks | Why |
|---|---|---|
| **Into new, never over** (`restoreIntoNew`) | Every target the plan creates doesn't exist yet; no target is a source item from LZ 3.1 | Overwriting is deleting what is there (LZ D2, decision 0016) |
| **Source intact** (`sourceIntact`) | For a test: the source item still exists and the status check shows a copy of it | A test proves a copy; it never touches what it copies |
| **Test identities only** (`testUsersOnly`) | Every user or group a test signs in, mounts or assigns is in the registered test group | Real users' data is never opened by a test (LZ 8) |
| **Registered scope** (`recoveryScopeRegistered`) | The target subscription and region are in `governance/recovery-scope.yml` for this landing zone, and the subscription is in the operator's own tenant | Keeps every recovery write inside what the adopter declared (§8) |
| **Policy allows the region** (`regionAllowed`) | Azure Policy's allowed locations for the target scope include the region, read live | The project's sandbox allows one region only (LZ 4.1); fail at the gate, not halfway through a restore |
| **Priced and time-boxed** (`spendBlock`, `timeBox`) | The plan has a cost estimate from the script, and a time box after which teardown starts on its own | Decision 0017: never leave the meter running |
| **One recovery run at a time** | No other recovery plan is in flight for the landing zone | Two restores into the same scope collide |
| **Tier fits** (`tierAtLeast`) | Failover actions only for R2, with `dr-standby` met at the last status check and that check under 24 hours old | No standby, no failover; a person runs the Cloud Shell rebuild instead |
| **No sessions in both pools** (`noDualSessions`) | Before failover or failback: the pool being left is drained, or its region is reported down by Service Health | FSLogix profile lock (LZ F15) |
| **Whole-pool blast radius, failover only** (`failoverBlastRadius`) | Draining the pool being left is part of the failover or failback action, not a separate host action, so the one-host-per-pool rule (spec §5a) doesn't apply to it. Its own rule does: one failover or failback per landing zone at a time, no other plan in flight on either pool, the standby pool verified `Available` before the drain starts, and the drain restored by failback | A failover moves every user at once; the approver sees that as one plan, not as many host plans (v1 red team V1-38) |
| **Capacity is a warning, never a pass** | The plan shows the recovery region's quota and says capacity isn't guaranteed (LZ F11) | So the approver isn't surprised |

Rules for how they run:
- **The script's own run record is the evidence.** The job reads the C17 run record the script writes (use case `recovery`, `extensions.recovery.measured`) and puts the measured RPO and RTO in the plan's outcome. avd-swarm doesn't measure them itself.
- **Verification is positive** (spec §5a). A recovery test is verified when its run record says the test passed and its teardown evidence is complete. A failover is verified when a session host in the standby pool reports `Available` with a heartbeat after the run, and a test user's session there succeeds. Missing data is a failed verification.
- **Teardown runs even when the switch is down.** A recovery test creates its resources in a run resource group, the scope the `stop-spend` identity's role is assigned on (governance §2.3, v1 red team V1-14), so that path can remove them at any switch position.
- **Failover is never pre-approved.** No standing exception (brain §4.5.1) covers a recovery action. Each plan is one approval.

## 5. Identities

Two write identities, each federated to the `approve` environment of the adopter's private repo only. Only the recovery test identity is in [identities.md](identities.md); the failover identity stays in this spec until the owner answers §9 question 1 (avd-second-brain v1 review D16, whose default is that failover stays a Cloud Shell block, v1 red team V1-27).

| Identity | For | Rights |
|---|---|---|
| **Recovery test** | `recovery-config-snapshot`, `recovery-test` | Read on the landing zone's resource groups; Backup Operator on the profile share's vault (restore to an alternate location); Contributor only on the recovery resource groups the person creates at setup in each registered subscription and region (each one is a test's run resource group, emptied by its teardown; one recovery run at a time), never on the landing zone's own groups. For the snapshot's commit, a GitHub App limited to `contents:write` on the companion repo |
| **Failover** | `recovery-failover-test`, `recovery-failover`, `recovery-failback` | Role Based Access Control Administrator on the primary and standby application groups only, **with a condition** that allows assigning and removing only the Desktop Virtualization User role, and only for the registered AVD users and test groups (Azure ABAC delegation conditions, "constrain roles and principals"); start and deallocate on the standby session hosts |

The failover identity (proposed) would be the first swarm identity that writes role assignments, which the brain's guardrails rule out today (brain §4.5, "no identity can write role assignments"). The condition keeps it to one role, named groups and named application groups; anything else is refused by Azure, not only by the gate. This is the owner's call (§9, question 1). Until it is answered, failover stays a Cloud Shell block, and neither the identity nor its watchdog row is in identities.md or governance.md.

## 6. Governance

### 6.1 Gate
- The new preconditions (§4) are rules in `governance/policy.yml`.
- **Observer verdict is required** for every recovery action except `recovery-config-snapshot`: they create resources or move users.
- `governance/recovery-scope.yml` (protected) lists, per landing zone, the subscriptions and regions recovery may write to, the recovery resource groups and the test group. Adding a scope raises capability, so it waits out the time-lock in solo mode (governance §2.6).

### 6.2 Kill switch
- Recovery actions are writes: `halt-actions` and above stop them, like any other action.
- A test already running when the switch trips is stopped at its next step and its run resource group is torn down through `stop-spend`.
- The switch is never bypassed for failover. In a real disaster with the switch down, the person runs the landing zone's Cloud Shell block as themselves.

### 6.3 Watchdog
New triggers; the first and last are in governance §2.4 now:

| Trigger | Position |
|---|---|
| A write by the recovery test identity outside a run resource group in a registered recovery scope | `halt-all` |
| A role assignment by the failover identity with no matching approved plan (added with the identity, §5) | `halt-all` |
| A recovery test past its time box with resources still billing | `spend.teardown.failed` (governance §2.4) |

### 6.4 When avd-swarm itself is down
The swarm's resource group, its plan store and avd-buddy live in one region. If that region is out, no plan can be made and the switch's flag can't be read, so every swarm write fails closed. That is intended: recovery never waits on avd-swarm. The Recovery agent's answers and the landing zone's docs both end with the Cloud Shell block for the same step. The Recovery agent also compares the landing zone's primary and recovery regions, read from the status check, with avd-swarm's own region and warns when they are the same. The landing zone's status check knows nothing about avd-swarm and stays free of swarm terms (v1 red team V1-21).

### 6.5 Events
New change-workflow events, signed and sent like the others (governance §2.2): `recovery.started`, `recovery.measured` (RPO and RTO in minutes, tier, test name, pass or fail), `recovery.teardown.done`, `failover.started`, `failover.verified`, `failback.verified`. No conversation text, as always.

## 7. What goes back to the brain

| Flow | What | Contract |
|---|---|---|
| Outcome | Each recovery plan's outcome (verified, failed, rolled back, rejected with a reason category) | C10, unchanged |
| Measurements | The landing zone's run record for the test, with `extensions.recovery` | C17, use case `recovery` (brain draft #40); produced by the landing zone's script, not by avd-swarm |
| Insights | Counts per month: tests run, passed and failed per test name, failovers and failbacks, tier met or not, as categories only | C8, `avd-swarm-insights/v1`; the topic vocabulary gains `recovery` (SQ9) |
| Lessons | A failed test or failover that taught something gets a lesson here and a fact on the brain's operations page | C7, and the brain's KB by PR |

The brain decides what a result means for the estate over time (a tier that keeps failing, an RTO that keeps growing) and records it as knowledge and decisions; avd-swarm only reports what happened. No contract changes in this spec. If the brain later needs recovery fields in C10, that is a contract pair (brain CLAUDE.md).

## 8. Scope and the hooks later work builds on

**In this repo, free:** everything above, for any region and any subscription in the operator's own tenant that `governance/recovery-scope.yml` lists, started by a person's request and approved per plan.

**Outside this repo:** anything that leaves the operator's own tenant or Azure, or starts a recovery run without a person asking. Such work can only build on the hooks below, and never changes their rules.

| Hook | What it is | Rule it keeps |
|---|---|---|
| H1. The `recovery` action kind | Catalog actions with a schema, preconditions, verification and rollback | A new action passes the same CI catalog check; it can add preconditions, never drop one |
| H2. The recovery scope file | `governance/recovery-scope.yml`: where recovery may write | Protected; widening it waits out the time-lock |
| H3. The plan store and `propose_change` | The only way a recovery plan is made | Every plan, wherever it starts, passes the gate and one person's approval |
| H4. Events and outcomes | §6.5 events, C10 outcomes, the C17 run record | Counts and measurements only; no conversation text, no estate data in logs (spec §5b rule 3) |
| H5. The landing zone's copy hook | Copies beyond the companion repo (LZ 10, run recorder 9.2 step 4) | The landing zone's, unchanged here |

## 9. Open questions for the owner

1. **The failover identity writes role assignments** (§5), with an ABAC condition limiting it to one role, named groups and named application groups. Accept that exception to the brain's "no role assignment writes" guardrail, or keep failover a Cloud Shell block only? Recommended: accept, with the condition and the watchdog trigger, after the first T-failover test by hand.
2. **A Recovery agent, or the Deploy agent's job?** Recommended: its own agent, so its allowlist stays small and its evals separate.
3. **Recovery tests in the sandbox** inherit the landing zone's open question on the one-region policy (LZ 14, question 2). Until it is answered, `regionAllowed` refuses anything but same-region tests.

## 10. Phases

They follow the build order (spec §9a): all of it comes after v1 (avd-swarm v1 review), and nothing writes before step 3's exit.

| Phase | What | Needs | Spend |
|---|---|---|---|
| **DR0** | This spec, decision 0006, the brain's decision 0019. **Exit:** the check of Azure's own resiliency drills and recovery plans (v1 review G11): if they cover AVD host pools, the recovery actions call them instead of scripting failover, and §4 is rewritten around them before DR1 starts (v1 red team V1-26) | n/a | $0 |
| **DR1, offline** | The Recovery agent's read tool and eval questions against fixtures of `Get-AvdRecoveryStatus.ps1 -AsJson`; the catalog entries and their CI check | Landing zone P1 (the status check) | $0 |
| **DR2, read-only** | The Recovery agent, reading a real landing zone | Step 2's exit; landing zone P3 | Eval runs, estimated first |
| **DR3, first recovery writes** | `recovery-config-snapshot` and `recovery-test` (T-profile, T-config, T-signin) in the adopter repo, after a red team pass | Step 3's exit; landing zone P2 and P4 | Per test, printed estimate and the owner's go |
| **DR4** | Failover test, failover and failback | Question 1 answered; an R2 landing zone (landing zone P6) | Same |
