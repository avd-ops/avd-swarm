# sre-agent: Azure SRE Agent as an extra investigator

**Off by default. Designed, not built.** Manifest: [adapter.yml](adapter.yml). The full design, with its red-team controls, is [spec section 3a](../../docs/spec.md#3a-the-sre-agent-adapter-azure-sre-agent-off-by-default).

**What it adds.** A second investigator beside the Hosts agent and the runbooks: avd-buddy starts an SRE Agent thread and shows its findings, labeled as SRE Agent's. Its scheduled tasks and incident response add to the check runner; they never replace it.

**What it costs.** About $292 a month while it exists, in agent units, whether or not anyone uses it ([pricing](https://learn.microsoft.com/azure/sre-agent/pricing-billing)). Its tokens are agent units too, so the gateway's meter can't see them: a budget on its resource group (notify and `halt-actions`) caps it.

**The controls.** Its identity is held to Reader and Desktop Virtualization Reader by an Azure Policy deny on role assignments, and a write by it trips `halt-all`. avd-buddy reaches it through a separate Azure MCP Server instance as a Standard User. A fix it finds comes back only as `propose_change` through the gate, never through its Approve button. Its thread text is untrusted text through the Prompt Shields scan.

**What the kill switch can't reach.** SRE Agent's own investigations keep running, read-only. Stopping it needs its Administrator role, held only by a break-glass account.

Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
