# 0003. avd-swarm is the actor and control layer; the second brain is its intelligence layer

- **Status:** Proposed
- **Date:** 2026-10-18
- **Pair:** [avd-second-brain decision 0003](https://github.com/nickprignano/avd-second-brain/blob/main/docs/decisions/0003-brain-thinks-swarm-acts.md) says the same from the other side. The two change together.

## Context
Decision 0001 gave avd-swarm its own write path and left the brain its own: two approval paths, two executors, two kill switches and two front ends for one estate. The owner decided on 2026-10-18: the second brain is the intelligence layer of the swarm, avd-swarm is the actor and control layer, and anything that changes in one is integrated into the other.

## Decision
- **avd-swarm acts and controls.** It is the only thing that changes an estate: the governance gate, a person's approval of the exact plan, the write identities, the executor, verification and rollback, the observer, the watchdog and **the one kill switch** ([governance.md](../governance.md)). Pre-approved exceptions run here.
- **The brain thinks.** Detections, episodes, diagnosis, the Critic, playbooks as tested procedure, knowledge, lessons, evals and grading live in the brain. It never holds a write credential.
- **One write path.** A brain playbook arrives as a proposal (contract C9) and goes through the same gate and approval as `propose_change`. The action catalog is the set of primitives playbooks are built from.
- **One front end.** avd-buddy, Teams and MCP clients talk to avd-swarm; the brain's Concierge is a read tool the coordinator calls.
- **Contracts:** C8 (insights, incidents, grading scores to the brain), C9 (playbook run proposals from the brain), C10 (action outcomes and the switch position to the brain), defined in avd-second-brain's `docs/contracts.md`.
- **Paired changes.** A change here to a contract, the action catalog, an agent's role, grading, models, identity or governance comes with the matching avd-second-brain PR, and each links the other.

## Supersedes
Decision 0001's "connects to the brain but doesn't depend on it" for acting: avd-swarm still runs without a brain (its own actions, its own evals), but a brain no longer acts without avd-swarm.

## Consequences
- One approval path, one kill switch and one audit trail to test, drill and red-team.
- The brain's operations spec marks its executor, its kill switch and its watchdog as moved here; they are built here.
- avd-swarm's identities (identities.md) are the only write identities for an estate.
