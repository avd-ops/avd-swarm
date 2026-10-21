# 0007. avd-swarm is the how; every plan says why, and the brain keeps it

- **Status:** Proposed
- **Date:** 2026-10-20
- **Pair:** [avd-second-brain decision 0020](https://github.com/nickprignano/avd-second-brain/blob/main/docs/knowledge/decisions/0020-the-brain-is-the-why-layer.md) says the same from the other side, and adds the lifecycle record (brain contract C18). The two change together (decision 0003).

## Context
The owner set the roles on 2026-10-20: the second brain understands why AVD is built and performing the way it is, from each environment's birth onwards (its use cases and the decisions made in building, configuring, expanding, contracting and fixing it); "swarm is the how". A plan today says what it changes, how, and how to roll it back. It doesn't say which decision it carries out, what else was considered, or which use case it serves, so after it runs the estate has changed and nobody can later say why.

## Decision
- **avd-swarm is the how.** Plans, the gate, approval, the executor, verification, rollback and the kill switch stay here (decision 0003). The brain keeps and explains the why in its lifecycle record (C18).
- **Every plan carries a `why` block** ([spec](../spec.md) §6f): the lifecycle event kind (build, configure, expand, contract, fix, recover, retire), the use cases it serves when the brain has them, and either an accepted decision from the brain's record (`dec-NNNN`) or a new decision drafted with the plan: the chosen value, at least one alternative with a reason category, the drivers and the evidence, in C18's vocabulary, with no free text beyond a short note. A brain proposal (C9) brings its own.
- **The approver approves the plan and its why together.** avd-buddy renders the why from the plan store in the plan's fixed layout, never from agent text. The gate checks that it is there and well formed ([governance](../governance.md) §2.1, "Says why"); whether the reason is good is the person's call.
- **The outcome closes the loop.** With a brain, the plan's C10 outcome carries its plan ID, event kind, use cases and decision ID, and the brain adds the event to the record and marks a drafted decision accepted (verified) or reverted (rolled back). Without a brain, the why stays in the plan store, and avd-swarm works as before.
- **The landing zone stays on its own.** avd-landing-zone and avd-current-state remain a self-contained pair with no dependency on avd-swarm or the brain; a plan that changes a landing zone (a Deploy agent PR to its parameters) carries its why in the plan, not in that repo.

## Consequences
- One more required block per plan. Agents draft it from the request and the brain's answer; a person approves it.
- The brain can answer "why is it like this?" for changes the swarm made, and flag the ones nobody decided.
- C10 grows by four fields (plan ID, event kind, use cases, decision ID), all IDs or fixed vocabulary, so it still carries no free text or names.
- Built with the plan store in build order step 3; the C10 feed to the brain waits for step 4, like the rest of C10.
