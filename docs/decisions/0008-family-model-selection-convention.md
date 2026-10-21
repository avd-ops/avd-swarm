# 0008. avd-swarm follows the family's model-selection convention

- **Status:** Accepted (by the owner on 2026-10-21, together with avd-second-brain decision 0010)
- **Date:** 2026-10-21
- **Pair:** [avd-second-brain decision 0010](https://github.com/nickprignano/avd-second-brain/blob/main/docs/knowledge/decisions/0010-family-model-selection-convention.md). The two change together (decision 0003).

## Context
The family of knowledge bases pilots one model-selection convention across its four projects, with the AI KB as its source of truth ([AI 0006](https://github.com/nickprignano/ai-brain/blob/main/docs/knowledge/decisions/0006-model-selection-convention.md)). avd-swarm is where the AVD repos call Claude: the S1 spike calls Opus, Sonnet and Haiku 5.5 through Microsoft Foundry or, without Foundry quota, through the Claude API with workload identity federation.

## Decision
- avd-swarm follows the convention. Any exception is recorded here as a "Model override" decision with its evidence.
- Full model IDs are pinned in config; model, effort and provider come from the environment.
- The metered model client logs, per request, the model, effort, `usage`, `stop_reason` and latency, and those pilot numbers go back to the AI KB as feedback.
- A model never decides: the gate, the claim check's code and a person's approval stay where they are (spec §5).

## Consequences
- A model change follows evidence and the family's rules, in both repos at once.
- Nothing logs usage yet; the pilot numbers start when the metered client is built.
- No default changes today. Revisit at the family's pilot review on 2026-12-01.
