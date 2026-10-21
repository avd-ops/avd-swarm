# foundry: Microsoft Foundry as the model host

**Off by default. Designed, not built.** Manifest: [adapter.yml](adapter.yml).

**What it adds.** Claude in Foundry (preview, East US 2 and Sweden Central), or the same Agent Framework workflow as a Foundry hosted agent, for an adopter who needs Foundry's data boundary or its guardrails. The default model host is the agent API's Container App calling Anthropic's API through workload identity federation (spec section 6); the S1a spike built both paths, and its Foundry path (`spikes/s1a/agent`, Q1 to Q3) is this adapter's acceptance test.

**What it costs.** Tokens at Foundry's Claude prices, hosted agent compute while it runs, and a small standing cost for the resource and project; read from the [retail prices API](https://prices.azure.com/api/retail/prices) when an adopter estimates it. The gateway's `foundry` provider has no price rows until then, so a call is refused even with the adapter on.

**The controls.** Every call still goes through the gateway: flag, prices, caps, Prompt Shields. Foundry's guardrails add to that scan; Foundry's tool approval is never the write approval. The landing zone's location policy needs an exemption for Foundry's regions, which is the adopter's own decision.

**What the kill switch can't reach.** Foundry spend is outside the Anthropic workspace limit, so the gateway's caps are its only cap; the deployment's tokens-per-minute quota is the rate ceiling.

Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
