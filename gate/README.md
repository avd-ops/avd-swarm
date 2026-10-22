# Gate

> Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.

The governance gate's rules as pure functions with offline tests ([spec](../docs/spec.md) section 5, [governance](../docs/governance.md)). The gate itself is build step 3; it grows from this package the way the gateway grew from the spike's meter.

| Rule | Module | What it refuses |
|---|---|---|
| V1-4 | [`avd_gate/evidence.py`](avd_gate/evidence.py) | A plan whose evidence has no item from a thread an operator started in avd-buddy. `schedule`, `manual` (the check runner) and `alert` (the alert job) may support a plan but never carry it alone; a value outside the vocabulary, or a missing one, is refused too. |

The runner takes its provenance vocabulary from here (`runner/avd_runner/execute.py`), so the two can't drift. `gate/**` is a protected path.
