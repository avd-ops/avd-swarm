# 0002. A governance gate, an external observer hook and a kill switch that fail closed

- **Status:** Proposed

## Context
Approval of each plan (decision 0001) stops a bad change. It doesn't catch the platform misbehaving: agents looping, a write identity used outside a plan, approval fatigue, a compromised component. The owner wants a failsafe that includes an observer outside avd-swarm and a kill switch. Details: [governance.md](../governance.md).

## Decision
- **Governance gate:** deterministic checks on every plan (allowlist, scope, blast radius, rate, time window, budget, switch position, observer verdict), with the policy in Git as a protected path. No model decides.
- **External observer hook:** every step is sent as a signed event to registered observers outside avd-swarm's trust boundary. Observers can deny plans that require their verdict, and trip the kill switch. They can't reset it. A silent required observer moves the switch to `halt-actions`.
- **Kill switch:** four positions, from `run` to `halt-all`, enforced in layers (flag, deleted federated credential, removed roles, stopped front end) by a separate Logic App with its own identity. People, observers and the watchdog can trip it. Only a person can reset it, through one workflow.

## Alternatives considered
- **A kill switch inside avd-swarm only:** fails when the part it should stop is the part that's broken.
- **A model as the only observer:** useful as a second opinion, but a model can be wrong or unavailable; the deterministic watchdog is the floor.

## Consequences
- More moving parts (Event Grid, a Logic App, alert rules), all cheap at idle.
- Observers that require verdicts add latency to sensitive plans, and their outages stop writes.
- The policy file becomes the place to tune how much autonomy the platform has.
