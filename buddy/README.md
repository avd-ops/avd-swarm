# avd-buddy (`buddy/`)

> **Personal research project — not for production.** Provided as is, without warranty; not affiliated with the author's employer, Liquidware or Microsoft.

`avd-buddy` is the installable Progressive Web App (PWA) chat front end for `avd-swarm` ([`docs/buddy-app.md`](../docs/buddy-app.md), [`docs/buddy-voice.md`](../docs/buddy-voice.md), [`docs/decisions/0010-avd-buddy-container-app-hosting.md`](../docs/decisions/0010-avd-buddy-container-app-hosting.md)).

## Two ways to run `buddy/app/`

1. **Production Container App hosting ([decision 0010](../docs/decisions/0010-avd-buddy-container-app-hosting.md)):**
   - Deployed via [`templates/buddy/main.bicep`](../templates/buddy/main.bicep) and [`buddy/Dockerfile`](Dockerfile) onto an Azure Container App in the Consumption plan (`minReplicas: 0`, inside the monthly free grant).
   - Uses Container Apps built-in Entra ID authentication ("Easy Auth") with a user-assigned managed identity as a federated credential (`OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID`) — no client secret, and no token in browser storage (`HttpOnly` cookie + `X-MS-CLIENT-PRINCIPAL` header).
   - Serves the static PWA shell and the thin `/api` relay from the same container (`buddy/server.py`), calling `agent_api` over internal Container Apps environment ingress.
2. **Public Demo / Simulation mode (`?sim=1` or static hosting at [buddy.avd-ops.dev](https://buddy.avd-ops.dev/)):**
   - When `buddy/app/` is served from a static host without `/api` (published on GitHub Pages at [buddy.avd-ops.dev](https://buddy.avd-ops.dev/)) or opened with `?sim=1`, it automatically uses the deterministic fixtures in [`buddy/app/fixtures.js`](app/fixtures.js) with zero Azure spend.
   - Supports full PWA installation on Android (`beforeinstallprompt` / Install app) and iOS/iPadOS (`Share` → `Add to Home Screen`).

## Build icons and run locally

```bash
python3 scripts/build_buddy_icons.py
python3 buddy/server.py
```
