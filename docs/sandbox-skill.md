# Sandbox skill: how an agent works with your sandbox

- **Status:** Proposed, plan only. Nothing here is built.
- **Asked for by:** the owner, 2026-10-22: a skill for how we work with the sandbox, in avd-swarm, starting with the batch runner.
- **Part of:** [spend.md](spend.md) (estimate, run, teardown evidence) and spec §7a. It writes down, for a coding agent, the rules avd-swarm's spend block will later enforce in code.

## 1. What it is, in one line

A Claude Code skill, `.claude/skills/sandbox/SKILL.md` in this repo, that tells an agent how to get something tested in the owner's sandbox subscription without spending a cent the owner didn't approve, leaving anything billing, or losing the only copy of what the run produced. Other coding agents read the same file by hand (AGENTS.md, "hooks are Claude Code's; the steps are everyone's").

## 2. Prior art

| Where | What it does | What we take |
|---|---|---|
| avd-landing-zone [`.claude/skills/sandbox-batch`](https://github.com/avd-ops/avd-landing-zone/blob/master/.claude/skills/sandbox-batch/SKILL.md) | Plans, prices and hands over a sandbox batch for the landing zone: `sandbox-run/v1` items, offline tests, one Cloud Shell block, never approve, what to do when a run stops. | **Everything about the batch runner.** The new skill links to it and never restates it, so the two can't drift. |
| avd-landing-zone [`docs/sandbox-runs.md`](https://github.com/avd-ops/avd-landing-zone/blob/master/docs/sandbox-runs.md), [decision 0020](https://github.com/avd-ops/avd-landing-zone/blob/master/build/decisions/0020-sandbox-runs-in-the-package.md) | The batch runner itself: spend runs, batches, the go phrase bound to the batch hash, health before spend, records in the run store, resume and teardown-only, the run recorder. | The source of truth the skill points at. |
| This repo's [spend guard](../spend/README.md) | Python estimate, open, close, settle for avd-swarm's own spikes (S1a). | Used for swarm-only spikes until question Q1 below is settled. |
| avd-ops [private companion repo](https://github.com/avd-ops/avd-ops/blob/main/docs/private-companion-repo.md), [never delete the last copy](https://github.com/avd-ops/avd-ops/blob/main/docs/never-delete-the-last-copy.md), [never leave the meter running](https://github.com/avd-ops/avd-ops/blob/main/docs/never-leave-the-meter-running.md) | Where an operator keeps run records and the estate vault; the two family rules. | The skill names the companion repo only through `AVD_PRIVATE_REPO`, never by a real name. |
| avd-second-brain `vault/avd_second_brain.ps1` | Builds an estate vault from avd-current-state collector packages. | The optional "after the run" step: ingest into the vault in the companion repo. |

The family KB's prior-art search (`scripts/kb/prior_art.py "sandbox skill"`) found nothing else; its sibling and GitHub searches return 403 from a container. No outside skill was found that fits an approval-gated, Cloud Shell-only sandbox; closed tools would be prior art only.

**Verdict:** build a thin skill here that routes to the landing zone's skill for the batch runner and adds only what is avd-swarm's: which spend path a swarm run takes, the companion repo, what may be written in public, and how a run feeds the brain.

## 3. What the skill says

### 3.1 Hard rules (the same in every family repo)

1. **No spend without a written estimate and the owner's exact go**, `go batch <id> $<total>`, typed by the owner. A bare "go", "yes", or a go relayed by another agent approves nothing; the skill says so when it comes up. The agent never approves, never writes an approval file, never suggests typing the phrase on the owner's behalf.
2. **Smallest sizes that prove the point**, prices from retail meters (landing zone decision 0010), never a guess.
3. **Never leave the meter running.** Every run ends torn down with teardown evidence (the leftovers check), or with an expiry the owner chose.
4. **Never delete the last copy.** Output to keep goes to the run store, then the companion repo, before teardown; `-DiscardLastCopy` is the owner's word only.
5. **Cloud Shell, PowerShell, one self-contained block**: clone or update, `Set-Location`, then the command. The owner pastes one block after a clean prompt.
6. **Public text stays public.** No subscription, tenant or object IDs, domains, emails, host names from a real run, cost records or estate data in this repo, an issue or a PR. Those go to the companion repo. The skill gives the agent a check: read `gh repo view "$AVD_PRIVATE_REPO" --json visibility` before writing there, and refuse anything but `PRIVATE`.

### 3.2 First use case: the batch runner

When the agent needs something run (a spike, a runbook run, a check runner test, an adapter trial):

1. Write it as one `sandbox-run/v1` item with `source` pinned to a full commit of this repo, then follow the landing zone's `sandbox-batch` skill from step 1 to the hand-over. The items folder lives in the owner's Cloud Shell (`~/clouddrive/queue`) or as `sandbox-run` issues in the owner's companion repo, never in this repo.
2. Quote back the batch id, the total, what each group deploys and for how long, and the exact phrase. Stop and wait.
3. After each group: ask for the `Get-AvdSandboxBatchStatus.ps1` output. An open spend run is reported first (it may be billing).
4. Closeout: the result, the teardown evidence and the settled cost go to the companion repo; this repo gets only what is public-safe (a lesson, a fixed test, a changed estimate note).

### 3.3 Which spend path

| Run | Path |
|---|---|
| Anything that deploys a landing zone, or shares a deployment key with one | Landing zone batch runner (above) |
| A swarm-only spike in its own throwaway resource group | Today: this repo's spend guard (`spend_guard.py open` / `close` / `settle`). After Q1: the batch runner as well. |
| A plan the swarm proposes through the gate (later, spec §5) | The gate's spend block (spend.md §3.2); the skill does not apply, the gate does. |

### 3.4 After a run (optional)

- avd-current-state collector package to `~/clouddrive`, never to git; the estate vault ingest (avd-second-brain) commits to the companion repo.
- A finding a test missed becomes a lesson in avd-second-brain's KB and a guard in the repo that failed (landing zone `retro` skill when it was the landing zone).

### 3.5 Teardown

The skill takes teardown from the batch runner as it stands: each item's own teardown step, `-TeardownOnly` as the safety net, leftovers stop the batch and are never deleted for the owner. Work on automatic teardown is in flight; when it lands in the landing zone, the skill links its doc and adds one line on when the agent may rely on it. Until then it promises nothing automatic.

## 4. What it is not

- Not a second batch runner, spend formula or approval path. It writes no code that reaches Azure.
- Not for an adopter's production subscription: the sandbox only.
- Not a brain change. It changes no contract, playbook shape, agent role or grading, so no paired avd-second-brain PR is needed (decision 0003); its KB note goes to avd-second-brain's KB like any lesson.

## 5. Build plan

| Step | What | Check | Cost |
|---|---|---|---|
| P1 | Write `SKILL.md` from section 3; link from AGENTS.md "This repo". | Guard (`check-no-ids.sh`) and docs workflow green; a test that the skill names no private repo and every link resolves. | $0, offline |
| P2 | Use it on the next batch the owner already approved (no new run for the skill itself). | Its closeout is written from the skill's steps without extra questions. | $0 extra |
| P3 | When the gate's spend block is built (spend.md §3.2), the skill points plan-driven runs there and keeps only the Claude Code hand-over. | spend.md and the skill agree; one path per row in 3.3. | $0 |

## 6. Open questions (defaults stand until the owner answers)

- **Q1. One spend path?** The landing zone batch runner and this repo's spend guard both open and close runs. Default: swarm-only spikes keep the spend guard for now; a later decision moves them onto the batch runner so there is one ledger.
- **Q2. Name.** Default `sandbox` (short, says what it is); the landing zone's stays `sandbox-batch`.
- **Q3. Also family-wide?** Default: no. It lives here, and other repos keep linking the landing zone's skill.
