# Runbooks

A runbook is a diagnostic procedure as code: a Markdown page a person reads, under a YAML front matter code reads ([core-and-adapters.md](../docs/core-and-adapters.md) section 3.2; spec section 3c). It is the open-source answer to Azure SRE Agent's skills and knowledge files and to Logic Apps Automation's model-chosen steps, with one difference: **the steps are fixed and the model only interprets what they return.**

| Runbook | Agent | Trigger | Checks |
|---|---|---|---|
| [hosts.agent-unhealthy](hosts/agent-unhealthy.md) | Hosts | weekdays 06:00 UTC | no session host's AVD agent reports anything but `Available` |
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
    kql: wvd-agent-health-last-24h # KQL only by catalog ID (catalog/kql/catalog.yml), never inline
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

Findings carry `threadStartedBy: schedule` or `alert`, and the gate refuses a plan whose only evidence is such a run (spec section 3a, red team V1-4). The check runner that runs them on their trigger is WP2.

## Rules the checker enforces

`python3 scripts/check_runbooks.py`, in CI on every PR: the shape above; the path matches the ID; every tool read is in the agent's allowlist and every KQL read is a catalog ID that agent may run; every evidence ID is in the pinned C15 register and built; the vendored register matches `contracts/PINS.json`; the budget fits the daily cap; `runMode: propose` is refused until `catalog/actions/` exists.

**A runbook is content, not a prompt** (red team P-1). Its Markdown reaches the model as untrusted text through the gateway's Prompt Shields scan, like a tool result; the agent's prompt stays a protected path. The allowlists, the KQL catalog, the vendored register and the evaluator are protected paths, so an agent-authored PR can't widen what a runbook may read. When `propose` arrives at step 3, the `proposes` field and `runMode: propose` become protected fields, like the action catalog's.

Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
