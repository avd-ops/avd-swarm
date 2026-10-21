# Spend guard

> Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.

Every lab run that costs money gets a run record: an estimate checked against your caps before anything is deployed, the teardown evidence after, and the actual cost a few days later. [`spend_guard.py`](spend_guard.py) does it with the Python standard library and the Azure CLI, so it runs in Azure Cloud Shell as is. The S1a spike ([spikes/s1a](../spikes/s1a/README.md)) uses it; the design it is the first piece of is [docs/spend.md](../docs/spend.md).

| Command | What it does |
|---|---|
| `open` | Refuses the run, listing every reason, when no cap is set, a line is unpriced (unless `--allow-unpriced`), the estimate is over `--run-cap`, the month to date plus the estimate is over `--monthly-cap`, or the sandbox already has an open run. Otherwise opens it with a due time (`--hours`, 6 by default). Exit 3 on a refusal. |
| `close` | Records the teardown evidence (the JSON line `Remove-Spike.ps1` prints) and any token spend billed outside Azure. Leftovers close the run red (exit 1); close it again once they're gone. |
| `settle` | Reads the run's resource group's actual cost from Cost Management, by day. Provisional until three days after the close, then settled; charges after the close day are flagged as a tail. |
| `status` | Lists the sandbox's runs. Exit 1 when an open run is past its due time: tear it down. |

Month to date is Cost Management's actual cost for the subscription so far this month, plus the estimates of this month's runs that aren't settled yet, so a run that hasn't shown up in billing still counts. If Cost Management can't be read (some subscription types don't offer it), `open` refuses and asks for `--month-to-date` by hand rather than guessing.

## Where the records go

`~/clouddrive/avdswarm-spend/<sandbox>/<yyyy-mm>/<run id>.json` by default (`--ledger` to change it, `--sandbox` to name the subscription; `lab` by default). Cloud Shell keeps `~/clouddrive` between sessions. A record holds amounts, dates, the resource group name and the teardown result, never a tenant, subscription or card detail, but it is still yours: keep the folder private, or commit it to a private repo of your own. Nothing in avd-swarm needs it to live anywhere in particular.

## What it can't do

Azure has no hard stop on spend: a budget only alerts. So the caps are enforced before the run opens, and the stop rule (the due time) is checked every time the spike resumes. Token spend is capped separately, before every model call, by the agent service's meter ([gateway/avd_gateway/meter.py](../gateway/avd_gateway/meter.py), [docs/spend.md](../docs/spend.md) section 3.7); give the Claude Console workspace a spend limit of its own as the backstop.

Tests: `python3 -m unittest discover -s scripts/tests` (offline; no Azure calls).
