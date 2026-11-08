# Security policy

avd-swarm is a personal research project, not for production, provided as is, and not affiliated with the author's employer, Liquidware or Microsoft. There is no support agreement and no response time, but reports are welcome and read.

## Reporting a vulnerability

- **Privately, please.** Use **Report a vulnerability** on this repository's Security tab (GitHub private vulnerability reporting). If that button isn't there, open an issue titled "Security contact request" with no details, and a private channel will be arranged.
- **Never put secrets, tenant, subscription or object IDs, names or estate data in a report or an issue.** Issues are public.
- Say what is affected (a gate rule, the gateway, a runbook, a workflow check), how it can be misused, and how to reproduce it with synthetic data.

## What is in scope here

This repo holds the agentic operations core, model gateway, check runner, and governance gate for the AVD family. No workflow in this public repository connects to an Azure tenant or holds a write credential (enforced by `scripts/check_workflows.py`). See [docs/redteam.md](docs/redteam.md) and [docs/governance.md](docs/governance.md) for the formal red-team review and governance rules, and the [security review page](https://avd-ops.dev/security.html) for the family-wide map.
