# apim: API Management's AI gateway in front of the gateway package

**Off by default. Designed, not built.** Manifest: [adapter.yml](adapter.yml).

**What it adds.** For an adopter who already runs API Management: `llm-token-limit`, its logging and its MCP governance in front of the gateway package ([gateway/](../../gateway/README.md)). Never instead of it: the kill switch flag, the pinned prices, the caps and the Prompt Shields scan stay in the gateway, before every call.

**What it costs.** Basic v2 or above (about $150 a month); Consumption can't run `llm-token-limit` or MCP, and Developer has no SLA ([pricing](https://azure.microsoft.com/pricing/details/api-management/)).

**The controls.** Its identity holds Cognitive Services User on the model resource and nothing else, by Policy deny; a write by it trips `halt-all`. Callers keep their Entra identities: no subscription key is issued to a swarm process, because a key is a secret.

**What the kill switch can't reach.** Nothing it needs to: the gateway refuses before a call reaches it. The instance bills until deleted.

Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
