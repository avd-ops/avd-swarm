# Runbooks

A runbook is a diagnostic procedure as code: a Markdown page a person reads, under a YAML front matter code reads ([core-and-adapters.md](../docs/core-and-adapters.md) section 3.2; spec section 3c). It is the open-source answer to Azure SRE Agent's skills and knowledge files and to Logic Apps Automation's model-chosen steps, with one difference: **the steps are fixed and the model only interprets what they return.**

| Runbook | Agent | Trigger | Checks |
|---|---|---|---|
| [hosts.agent-unhealthy](hosts/agent-unhealthy.md) | Hosts | weekdays 06:00 UTC | agents report, and no session host is unhealthy or silent (a shut-down host is neither) |
| [hosts.connection-errors](hosts/connection-errors.md) | Hosts | weekdays 06:15 UTC | fewer than 25 AVD errors in the last day |
| [cost.idle-hosts](cost/idle-hosts.md) | Cost | Mondays 07:00 UTC | no running host without a connection in seven days |

## The front matter

```yaml
id: hosts.agent-unhealthy          # <agent>.<name>; the file is runbooks/<agent>/<name>.md
version: 1                         # bump on any change, so findings say which version ran
title: Session host agent unhealthy
trigger:
  schedule: "0 6 * * 1-5"          # exactly one of schedule (UTC cron), alert: <alert rule pattern>, manual: true
  runMode: read-only               # the only mode in v1; propose waits for the action catalog (build step 3)
agent: hosts
reads:                             # each in the agent's allowlist (governance/allowlists/<agent>.yml)
  - id: health
    kql: wvd-agent-health-latest-7d # KQL only by catalog ID (catalog/kql/catalog.yml), never inline
  - id: hosts
    tool: virtualdesktop_hostpool_sessionhost_list
expect:                            # checked in code before a model sees anything
  - fact: unhealthyHosts
    from: health                   # a read's id
    measure: rows                  # rows, or sum:<column>, max:<column>, min:<column>
    op: eq                         # eq, ne, lt, le, gt, ge
    value: 0
    onFail: investigate            # investigate (the agent looks) or report (a finding, no model)
evidence: [c11.hostPool.sessionHosts.total]   # C15 register IDs it may cite (contracts/fact-register.json, built only)
cites:                             # <owner>/<repo>@<commit>:<path>[#anchor], or a Microsoft Learn link
  - avd-ops/avd-second-brain@<40-hex commit>:docs/knowledge/operations/README.md#diagnosing
budget: {tokensUsd: 0.50}          # per run, inside the daily cap in governance/gateway.yml
# proposes: restart-avd-agent      # only with runMode: propose (step 3): at most one catalog action
```

The body starts with a `# ` title and says what the runbook checks, what clear means, what the agent does when it isn't, what the operator does when the runbook can't help, and what personal data it touches.

## How a run ends

`runner/avd_runner/runbook.py` evaluates the facts from the reads' results:

- `clear`: every fact holds. No model is called.
- `finding`: a fact fails. Facts with `onFail: investigate` go to the runbook's agent with the reads' results; the agent answers through the claim check (spec section 4a). `report` facts are recorded without a model.
- `could-not-run`: a read failed or a fact couldn't be measured. Missing data never passes.

Findings carry `threadStartedBy: schedule`, `manual` (a workflow dispatch) or `alert` (the alert job), and the gate refuses a plan whose only evidence is such a run, since none is an operator in avd-buddy (spec section 3a, red team V1-4; `gate/avd_gate/evidence.py`).

## The check runner

`templates/workflows/runbooks.yml` runs them in the adopter's private repo (a template: GitHub doesn't run it here). On each cron it stops unless the repo is private, signs in as the read-only runner identity ([identities.md](../docs/identities.md)), reads the kill switch flag and stops at `halt-agents` or above, picks the runbooks whose `trigger.schedule` is that cron, and asks the agent API to run each one (`POST /runbook`, `runner/avd_runner/api.py`). **The runner never calls a model**: the agent API runs the reads through the tool layer, checks the facts in code, and only for `investigate` facts asks the agent through the gateway with the runbook's budget (`runner/avd_runner/execute.py`). An unreachable agent API is `could-not-run`.

What it writes (`runner/avd_runner/report.py`): a GitHub issue only when the outcome changes (open when a runbook stops being clear, comment when it changes while open, close when clear again), and a job summary. Both carry the runbook ID, the outcome and fact values only, never a host, user or resource name (R17); the agent's claims stay in the plan store and avd-buddy. Counts per runbook and outcome are the proposed C8 `checks` block for the brain. `scripts/check_runbooks.py` fails when a runbook's schedule has no cron in the template, or a cron starts no runbook.

Alert-triggered runs use the same code from a Container Apps Job (`python -m avd_runner alert-job`, template in `templates/alert-path/`, WP6): an alert rule's action group calls a Consumption Logic App, which queues the alert ID only; the job reads the kill switch, reads the alert back from Azure Monitor and runs up to four read-only runbooks whose `trigger.alert` glob matches the rule's name, one message per execution. Each summary goes to the job's log and the agent API keeps the finding; the alert path opens no GitHub issue. Every hop is a managed identity; nothing in Azure holds a GitHub credential. The first scheduled run in the sandbox needs a spend estimate and the owner's exact go.

## Rules the checker enforces

`python3 scripts/check_runbooks.py`, in CI on every PR: the shape above; the path matches the ID; every tool read is in the agent's allowlist and every KQL read is a catalog ID that agent may run; every evidence ID is in the pinned C15 register and built; the vendored register matches `contracts/PINS.json`; the budget fits the daily cap; `runMode: propose` is refused until `catalog/actions/` exists.

**A runbook is content, not a prompt** (red team P-1). Its Markdown reaches the model as untrusted text through the gateway's Prompt Shields scan, like a tool result; the agent's prompt stays a protected path. The allowlists, the KQL catalog, the vendored register and the evaluator are protected paths, so an agent-authored PR can't widen what a runbook may read. When `propose` arrives at step 3, the `proposes` field and `runMode: propose` become protected fields, like the action catalog's.

Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
