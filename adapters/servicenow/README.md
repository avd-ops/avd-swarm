# servicenow: ServiceNow ITSM incident trigger and change co-signer

**Off by default. Designed, paired with [avd-servicenow](https://github.com/avd-ops/avd-servicenow).** Manifest: [adapter.yml](adapter.yml).

**What it adds.** An inbound ServiceNow incident webhook that enqueues a reference ID on the alert path's Azure Storage Queue (`threadStartedBy: alert`), posts redacted runbook findings (`C15` citations) back to the incident's `work_notes`, and mirrors `propose_change` plans into ServiceNow Change Requests (`change_request`) whose approval co-signs the GitHub Environment `approve` gate.

**What it costs.** Azure Logic Apps Consumption and Azure Storage Queue operations (cents per month, $0 idle). ServiceNow is customer-licensed or a free Personal Developer Instance ($0 PDI at `https://developer.servicenow.com/`).

**The controls.** Ticket text is untrusted external input (`threadStartedBy: alert`), scrubbed through redaction and Prompt Shields (`shield.py`), and under gate rule `V1-4` can never be the sole evidence for a write plan. ServiceNow Change approval is a co-signature, never a gate bypass: `avd-swarm`'s deterministic policy gate, kill switch, and live state-hash checks still run before acquiring a short-lived write token.

**What the kill switch can't reach.** The ServiceNow instance continues its own ticket lifecycle independently with zero Azure write credentials.

Personal project, not for production, provided as is; not affiliated with the author's employer, ServiceNow, Liquidware or Microsoft.
