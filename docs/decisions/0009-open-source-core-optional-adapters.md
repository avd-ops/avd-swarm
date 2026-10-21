# 0009. avd-swarm is an open-source core; paid Microsoft agent services are optional adapters

- **Status:** Accepted (the owner, 2026-10-21: "yes to direction and defaults"; plan in [core-and-adapters.md](../core-and-adapters.md))
- **Date:** 2026-10-21
- **Applies:** [avd-second-brain decision 0023](https://github.com/avd-ops/avd-second-brain/blob/main/docs/knowledge/decisions/0023-family-format-best-open-source-or-build.md) to this repo.

## Context

On 2026-10-21 the owner judged Azure SRE Agent (about $292 a month until deleted), Logic Apps Automation (preview, about $37 a month idle, no export to code) and API Management's AI gateway (Basic v2, about $150 a month) cost-prohibitive for a personal project and poor fits for its rules (keyless identities, everything as code, Cloud Shell). The 2026-10-20 interim choice to use SRE Agent as the investigator while the agents are built was dropped. The owner's words: "we need our own open source alternatives in the swarm with support for paid ms services."

## Decision

- **The core is ours and open source.** Investigation, scheduled and alert-triggered checks, runbooks as code, the model and tool gateway, orchestration and policy evaluation are built in this repo under MIT, on Microsoft's open-source platform pieces (Agent Framework, the Azure MCP Server, the Agent Governance Toolkit within governance section 2.1's limits) and on Azure and GitHub as the platform.
- **Paid or closed services are adapters.** Each is a manifest in `adapters/` that declares what it adds to, its meters, its identity and the Policy deny that keeps it read-only, how the kill switch reaches it and what it emits. An adapter is off by default; no CI job, eval or v1 exit test depends on one; turning one on needs a spend estimate in spend.md's format and the owner's go; its writes come back only as `propose_change` through the gate; the drill refuses it at every halt position.
- **The default model host is a Container App calling Claude through Anthropic workload identity federation.** Foundry Agent Service hosting is the `model-host` adapter.
- **Outside open-source projects are the parity checklist** (HolmesGPT, Bifrost, LiteLLM, MCP Context Forge), never a dependency, because each fails at least one of our requirements.
- **Governance is unchanged**: every write through the approved-plan gate, one kill switch, only a person resets it.

## Consequences

- Good: an adopter runs everything from this repo and a free GitHub account for about ten dollars a month plus tokens; every dependency is readable and forkable.
- Good: spec section 3a's red-teamed SRE Agent controls are kept as the adapter's design and cost nothing while off.
- Bad: three capabilities (runbooks, the check runner, the gateway package) are ours to build and keep; the parity checklists and the brain's freshness workflow keep that honest.
- It supersedes v1-review D10's default and D11's framing (avd-second-brain `docs/v1-review.md`) and turns spec section 3a into the `sre-agent` adapter page; spec sections 3b and 3c carry the adapter rule and the runbooks and check runner (WP0).
