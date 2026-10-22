# 0006. avd-swarm runs the landing zone's recovery tests and failover as catalog actions

- **Status:** Proposed
- **Date:** 2026-10-20
- **Pair:** [avd-second-brain decision 0019](https://github.com/avd-ops/avd-second-brain/blob/main/docs/knowledge/decisions/0019-recovery-runs-through-the-swarm.md) records why; this records how. The two change together (decision 0003).

## Context
The owner decided on 2026-10-20 that basic recovery testing and failover are part of avd-swarm, and that more advanced recovery builds on them. avd-landing-zone's recovery spec (draft PR #75) already defines what to protect, the tiers, a read-only status check, a configuration snapshot and recovery tests an operator runs by hand from Cloud Shell. Without avd-swarm, a test is only as regular as the operator's memory, and a failover under pressure is a sequence of commands typed without a gate.

## Decision
- **The landing zone owns recovery; avd-swarm runs it.** Four catalog actions of a new kind, `recovery` (configuration snapshot, recovery test, failover test, failover and failback), each running one landing zone script pinned by release tag and hash ([recovery.md](../recovery.md) §4). avd-swarm never reimplements a restore.
- **A read-only Recovery agent** answers from the landing zone's status check and cites its `dr-*` results (§3).
- **Same write path as every change:** preconditions checked by the gate and again live in the job, a person's approval per plan, the observer's verdict, the kill switch, positive verification and teardown through `stop-spend` (§4, §6). No standing exception covers a recovery action.
- **Scope:** any region and subscription in the operator's own tenant that `governance/recovery-scope.yml` lists. Anything beyond that, or a run no person asked for, is outside this repo and can only build on the hooks in §8.
- **Recovery never waits on avd-swarm.** The landing zone's Cloud Shell blocks stay the primary path; when avd-swarm is down it fails closed (§6.4).

## Consequences
- One new write identity for recovery tests. A failover identity would write role assignments under an ABAC condition, an exception to the brain's guardrail that waits on the owner (recovery.md §9, question 1; avd-second-brain v1 review D16). Until then it is proposed in recovery.md only and failover stays a Cloud Shell block.
- All of it comes after v1, and nothing writes before build order step 3's exit (spec §9a). The first gate is checking whether Azure's own resiliency drills and recovery plans cover AVD host pools (v1 review G11); if they do, the actions call them.
- Results go to the brain as C10 outcomes, C8 counts and the landing zone's C17 run record; no contract changes now.
