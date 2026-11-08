# 0010. Host avd-buddy on a scale-to-zero Container App with built-in Entra auth and GHCR images

- **Status:** Accepted (the owner, 2026-11-08)
- **Date:** 2026-11-08
- **Updates:** [0009](0009-open-source-core-optional-adapters.md), [spec.md](../spec.md) §3, §6 and §7, [buddy-app.md](../buddy-app.md) §4 and §9, [identities.md](../identities.md).

## Context

Under [decision 0009](0009-open-source-core-optional-adapters.md), the core deployment of `avd-swarm` cost about $10 a month idle plus tokens. Practically the entire fixed monthly charge (about $9 a month) was **Azure Static Web Apps Standard** for `avd-buddy`, chosen for two things Static Web Apps Free lacks:

1. A custom single-tenant Entra ID app registration (`assignmentRequired`, one operators group, Conditional Access MFA) that keeps tokens out of the browser by setting an HTTP-only session cookie and passing `x-ms-client-principal` to the backend (red team R10).
2. A linked ("bring your own") Azure Functions app so the `/api` chat relay could hold a managed identity to call `agent_api`.

Static Web Apps also brought three operational drawbacks documented in the brain's knowledge base: it deploys in only five Azure regions (requiring an exemption when the landing zone's allowed-locations policy targets another region such as `northcentralus`), CLI uploads require a deployment token secret (`SWA_CLI_DEPLOYMENT_TOKEN`), and its `/api` proxy cuts off HTTP requests at 45 seconds. Meanwhile, `avd-swarm` already deploys an Azure Container Apps Consumption environment for `agent_api`.

## Decision

- **Host `avd-buddy` (the PWA shell and its thin `/api` chat relay) on an Azure Container App in the Consumption plan, scaling to zero.** The Container Apps monthly free grant (180,000 vCPU-seconds, 360,000 GiB-seconds and 2 million requests per subscription) covers one operator's volume at **$0/month idle and active compute**.
- **Use Container Apps built-in authentication ("Easy Auth") with the same custom Entra ID app registration.** Built-in auth on Container Apps is included at no extra cost, runs the OpenID Connect flow server-side, sets an HTTP-only session cookie so no token enters browser storage, injects the same `X-MS-CLIENT-PRINCIPAL` header, and trusts a user-assigned managed identity as a federated credential (`OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID`) with no client secret.
- **Drop the separate Azure Functions app.** `avd-buddy`'s Container App holds the managed identity that calls `agent_api` over internal Container Apps environment ingress (keeping red team V1-16's caller isolation).
- **Publish container images to GitHub Container Registry (`ghcr.io`) and pull by pinned SHA-256 digest.** No Azure Container Registry (ACR Basic, about $5 a month) is deployed for the core.
- **Keep deterministic status and read paths zero-token.** Status views (`GET /api/status`), open-run banners, and code-first runbook checks whose `expect:` assertions pass ([core-and-adapters.md](../core-and-adapters.md) §3.2) return data from the plan store or C11 facts without calling a model.

## Consequences

- Good: standing idle cost drops from about $10/month to **under $0.10/month** (pennies for Table/Blob storage and Consumption Logic App switch drills), and active use is metered tokens only.
- Good: deploys in the same region as the rest of the swarm (`northcentralus` included) with no location-policy exemption, no `SWA_CLI_DEPLOYMENT_TOKEN` secret, no separate Functions app, and no 45-second HTTP proxy cliff.
- Good: every security invariant in [buddy-app.md](../buddy-app.md) §2 and [identities.md](../identities.md) (no token in the browser, no client secret, internal ingress to `agent_api`, operator identity passed as a verified claim) holds unchanged.
