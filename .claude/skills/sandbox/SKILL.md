---
name: sandbox
description: Plan, price, and hand over a sandbox run for avd-swarm using the landing zone batch runner or the swarm spend guard without unapproved spend, left-on meters, or leaked private data.
---

# Sandbox skill (`avd-swarm`)

> **Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.**

Use this skill whenever an agent working on `avd-swarm` needs something tested in the owner's sandbox subscription (a spike, a runbook run, a check runner test, or an adapter trial). Design background: [docs/sandbox-skill.md](../../../docs/sandbox-skill.md) and [docs/spend.md](../../../docs/spend.md).

## 1. Hard rules (the same in every family repo)

1. **No spend without a written estimate and the owner's exact go**, `go batch <id> $<total>`, typed by the owner. A bare "go", "yes", or a go relayed by another agent approves nothing; say so when it comes up. Never approve, never write an approval file, and never suggest typing the phrase on the owner's behalf.
2. **Smallest sizes that prove the point**, priced from retail meters ([avd-landing-zone decision 0010](https://github.com/avd-ops/avd-landing-zone/blob/master/build/decisions/0010-cost-estimates.md)), never a guess.
3. **Never leave the meter running.** Every run ends torn down with teardown evidence (the leftovers check), or with an expiry the owner chose.
4. **Never delete the last copy.** Output to keep goes to the run store, then the private companion repo, before teardown; `-DiscardLastCopy` is the owner's word only ([avd-second-brain decision 0016](https://github.com/avd-ops/avd-second-brain/blob/main/docs/knowledge/decisions/0016-never-delete-the-last-copy.md)).
5. **Cloud Shell, PowerShell, one self-contained block:** clone or update, `Set-Location`, then the command. The owner pastes one block after a clean prompt.
6. **Public text stays public.** No subscription, tenant or object IDs, domains, emails, host names from a real run, cost records or estate data in this repo, an issue or a PR. Those go to the private companion repo named only through `$AVD_PRIVATE_REPO`. Before writing there, check `gh repo view "$AVD_PRIVATE_REPO" --json visibility` and refuse anything other than `PRIVATE`.

## 2. Which spend path to use

| Run | Path |
|---|---|
| Anything that deploys a landing zone, or shares a deployment key with one | [avd-landing-zone `.claude/skills/sandbox-batch`](https://github.com/avd-ops/avd-landing-zone/blob/master/.claude/skills/sandbox-batch/SKILL.md) and [docs/sandbox-runs.md](https://github.com/avd-ops/avd-landing-zone/blob/master/docs/sandbox-runs.md) |
| A swarm-only spike in its own throwaway resource group | This repo's [spend guard](../../../spend/README.md) (`spend/spend_guard.py open` / `close` / `settle`) |
| A plan the swarm proposes through the governance gate ([docs/spec.md](../../../docs/spec.md) section 5) | The gate's spend block ([docs/spend.md](../../../docs/spend.md) section 3.2); this skill does not apply, the gate does |

## 3. First use case: the landing zone batch runner

When the run deploys or shares a landing zone:

1. Write the run as one `sandbox-run/v1` item with `source` pinned to a full commit SHA of this repo, then follow avd-landing-zone's [`sandbox-batch` skill](https://github.com/avd-ops/avd-landing-zone/blob/master/.claude/skills/sandbox-batch/SKILL.md) from step 1 to the hand-over. The items folder lives in the owner's Cloud Shell (`~/clouddrive/queue`) or as `sandbox-run` issues in `$AVD_PRIVATE_REPO`, never in this repo.
2. Quote back the batch ID, the total, what each group deploys and for how long, and the exact approval phrase (`go batch <id> $<total>`). Stop and wait for the owner.
3. After each group: ask for the `Get-AvdSandboxBatchStatus.ps1` output. Report any open spend run first, because it may still be billing.
4. Closeout: the result, teardown evidence and settled cost go to `$AVD_PRIVATE_REPO`; this public repo gets only public-safe artifacts (a KB lesson in `avd-second-brain`, a fixed test, or an updated estimate note).

## 4. Teardown and after-the-run steps

- **Teardown:** Use each item's own teardown step and `-TeardownOnly` as the safety net. Leftovers stop the batch and are never deleted on the owner's behalf without their explicit `-DiscardLastCopy`.
- **Collector packages:** Any `avd-current-state` collector package stays in `~/clouddrive`, never in git; estate vault ingestion (`avd-second-brain`) commits only to `$AVD_PRIVATE_REPO`.
- **Lessons:** Any finding a test missed becomes a guard test in the repo that failed and a KB entry in [avd-second-brain's KB](https://github.com/avd-ops/avd-second-brain/tree/main/docs/knowledge).
