# logic-apps: a Logic App as a trigger source for the check runner

**Off by default. Designed, not built.** Manifest: [adapter.yml](adapter.yml).

**What it adds.** A schedule or an event source in a Logic App (Standard, or Logic Apps Automation once it can be kept as code) that puts a runbook ID on the check runner's queue. The runner, the runbooks and every check stay the core's (spec section 3c); the default scheduler is GitHub Actions `schedule`, free and keyless.

**Not this adapter:** the kill switch's own Consumption Logic App and the Consumption Logic App on the alert path (WP6). Both bill per execution with no idle charge and are core ([core-and-adapters.md](../../docs/core-and-adapters.md) finding F11).

**What it costs.** Logic Apps Automation (preview) about $37 a month idle; Logic Apps Standard more ([comparison](https://learn.microsoft.com/azure/logic-apps/automation/compare-automation-services)).

**The controls.** Its identity can only send messages to the runner's queue, held there by a Policy deny; the runner reads the kill switch flag first and drops the message at `halt-agents` or above, so the Logic App can start nothing the switch forbids. Model-chosen steps (Automation's agent loops) are never used: a runbook's steps are fixed.

**What the kill switch can't reach.** The workflow keeps firing and billing executions until a person disables it.

Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
