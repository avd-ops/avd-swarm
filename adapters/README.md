# Adapters: paid or closed services, off by default

avd-swarm is an open-source core ([decision 0009](../docs/decisions/0009-open-source-core-optional-adapters.md)). Everything it needs to do its job is in this repo under MIT. A paid or closed Microsoft service an adopter may add is an **adapter**: a manifest (`adapter.yml`) and a page (`README.md`) in its own folder here, checked in CI by `scripts/check_adapters.py` (spec section 3b; [core-and-adapters.md](../docs/core-and-adapters.md) section 3.6).

| Adapter | Kind | Adds to | Idle cost (list, re-read before estimating) | Status |
|---|---|---|---|---|
| [sre-agent](sre-agent/README.md) | investigator | investigation and scheduled checks | about $292 a month | designed |
| [logic-apps](logic-apps/README.md) | scheduler | the check runner, as a trigger source | about $37 a month (Automation) | designed |
| [apim](apim/README.md) | gateway | the gateway package, in front of it | about $150 a month (Basic v2) | designed |
| [foundry](foundry/README.md) | model-host | the agent API's model host | small standing cost plus tokens | designed |

## What every manifest declares

| Field | What it says |
|---|---|
| `kind` | `investigator`, `incident`, `scheduler`, `gateway`, `model-host` or `policy-engine` |
| `addsTo` | The core capability it adds to |
| `replaces` | Always `[]`. An adapter never replaces a core capability |
| `default` | Always `off` |
| `meters` | The retail meters it bills on, whether it bills while idle (`standing`), the idle cost if known, and the source |
| `identity` | What it runs as, the roles it must never hold, the **Azure Policy deny** that keeps it read-only and the **watchdog row** that trips the switch if it writes (red team P-5) |
| `switch` | What each kill switch position does to it, and the `residual`: what the switch can't reach |
| `events` | The observer events it emits, counts only |
| `writes` | Always `none`: every write it wants comes back as `propose_change` through the gate |
| `exitTests` | Always `[]`: no v1 exit test, CI job or eval depends on it |

The check also fails a workflow or an eval that names an adapter, and a gateway provider whose adapter has no manifest.

## Turning one on

1. Write a spend estimate in [spend.md](../docs/spend.md) format from the manifest's meters, re-reading the prices (the retail prices API for Azure; never guess a price).
2. Get the owner's go. In the owner's sandbox that is the exact phrase `go batch <id> $<total>`; a bare "go" is not approval.
3. Deploy it from the adopter's private repo with the Policy deny and the watchdog row first, then the service.
4. Add it to the drill: the drill refuses its calls at each halt position (build order step 3).
5. Turning it off is deleting it, with teardown evidence: most of these bill until deleted.

Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
