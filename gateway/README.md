# gateway: one metered path to every model

The open-source answer to an API gateway's token limits ([core-and-adapters.md](../docs/core-and-adapters.md) section 3.4, WP3). Every avd-swarm process that calls a model goes through `avd_gateway`, and nothing else reaches a model. Before each call, in this order:

1. **The kill switch flag** (`flag.py`): the position tag on the swarm's resource group, read through ARM. At `halt-agents` or `halt-all`, or when the flag can't be read, the call is refused (governance section 2.3 layer 6).
2. **The provider** (`providers.py`): `anthropic` through workload identity federation is the default; `foundry` and `azure-openai` need the `foundry` adapter turned on ([adapters/foundry](../adapters/foundry/README.md)), which is off by default. No provider takes a key.
3. **The price** (`prices.py`): from [governance/model-prices.yml](../governance/model-prices.yml), by provider and model. An unpriced model is refused.
4. **The run's own cap** (`meter.py`): the worst case of the call against the run's budget (a runbook's `budget`, or `perRunUsd`).
5. **The day, month and caller caps** (`ledger.py`): the worst case committed on three rows of a table outside the agents, in one transaction with optimistic concurrency, so two processes can't both spend the last dollar. A counter that can't be read or written refuses the call. Caps are in [governance/gateway.yml](../governance/gateway.yml).

After the call it books the real cost from the response's usage and settles the counter; each call and each refusal is an event with counts and dollars, never text (`usage.py`). The Prompt Shields scan and the decoy tripwire (`shield.py`) scan the operator's message and every tool result before the model sees them.

Both files in `governance/` and this folder are protected paths: an agent-authored PR can't touch them.

**Library first.** In build steps 1 to 3 the agent API is the only process that calls a model; the check runner asks the agent API to run a runbook and never calls a model itself. The gateway becomes a service (a Container App other processes call with an Entra token) only when a second model-calling process exists (the judge, Improve, Scribe or the brain's Tier 3, all step 4). A caller is the app role of its Entra identity: there are no virtual keys, because a key is a secret.

**Parity checklist, not dependencies** (decision 0009): Bifrost (Apache-2.0) and LiteLLM (MIT core) for budgets and per-key limits, API Management's `llm-token-limit` for what an enterprise adopter may already run (the `apim` adapter sits in front of this package, never instead of it).

**Tests:** `python3 -m unittest discover -s scripts/tests` (`test_gateway.py`, `test_meter.py`, `test_shield.py`), offline. The table counter's test runs only where `azure-data-tables` is installed.

Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
