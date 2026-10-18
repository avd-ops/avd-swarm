# Identities: who runs what, with which rights

- **Status:** Proposed (2026-10-18). Built in S1 (read side) and S2 (write side); see [spec.md](spec.md) §6 and §9.
- **Rule:** least privilege per identity, no secrets (federated credentials and managed identities only), and no tenant, subscription, object or client ID committed. Every ID lives in a repository variable or the operator's environment.

Written after the S1a spike's first real run, where creating an Entra app through the Bicep Graph extension was denied in Cloud Shell even for a Global Administrator. The swarm's own Graph work goes through apps it owns and names, so it shows by name in sign-in logs and Conditional Access can target it.

## 1. The identities

| Identity | Kind | Runs | Azure roles | Graph (application permissions unless noted) |
|---|---|---|---|---|
| **Agent identities**, one per agent | Entra Agent ID (from the Foundry hosted agent) | Coordinator, Hosts, Users, Cost, Deploy, Images, Readiness | Hosts, Deploy, Images: Desktop Virtualization Reader and Log Analytics Reader on the AVD resource groups. Cost: Cost Management Reader on the subscription. Coordinator: none | None, except the **Users** agent: `GroupMember.Read.All`, `User.ReadBasic.All`, `AuditLog.Read.All` (sign-ins). The tool layer filters to registered AVD groups (spec §6, R7). All of them hold the MCP server app's role |
| **MCP server app** | Entra app registration + the Container App's managed identity | The read tools (Azure MCP Server `--read-only`, or the custom server, SQ5) | Managed identity: Desktop Virtualization Reader and Log Analytics Reader on the AVD resource groups | None. The app only **exposes** one app role (`Mcp.Tools.ReadWrite.All`, a name the server requires), held by the agent identities |
| **Read job identity** | User-assigned managed identity, federated to `main` workflows | Scheduled reports, evals, the Improve workflow, the spend daily job | Reader on the AVD resource groups, Cost Management Reader | None |
| **Write identity** | User-assigned managed identity, federated to the `approve` environment only | The approved plan's job, nothing else (spec §5) | Only what the action catalog needs (drain, restart, Stop/Resume): a custom role on the AVD resource groups | None. Group membership through **Groups Administrator scoped to an administrative unit** holding the AVD groups, never a tenant-wide permission (R7) |
| **Stop-spend identity** | User-assigned managed identity, federated to its own environment | The spend watchdog's stop (governance §2.3) | A custom role that can only stop or deallocate resources tagged with a spend run | None |
| **Kill switch** | The Logic App's managed identity | The kill switch (governance §2.3) | Custom roles that only take capability away | None |
| **Operator tooling app** | Public client app (device code), delegated | The operator's own scripts in Cloud Shell: setup, spikes, break-glass reads | The operator's own rights; the app adds none | Delegated `User.Read`, plus only the scopes a setup step needs, listed in the setup script (creating the MCP app needs `Application.ReadWrite.All` delegated, still limited by the operator's own role). Admin consents once |

## 2. Decisions

- **Operator Cloud Shell runs keep a dedicated public-client app.** One device-code sign-in per Cloud Shell session, with the app's name in the sign-in logs and a Conditional Access policy that can target it (for example phishing-resistant MFA). Unattended work never uses it: anything scheduled or approved runs through GitHub Actions OIDC.
- **The spike (S1a) keeps using the Azure CLI** until the setup script exists; its MCP app is registered with `az rest` against Graph as the operator.
- **Nothing gets a Graph write permission.** The only Graph writes are group membership changes, through an administrative-unit-scoped role on the write identity.

## 3. Setup script (to build, S1)

`scripts/setup/New-SwarmIdentity.ps1`, run once by an admin in Cloud Shell:

1. Creates the operator tooling app, the MCP server app, and the managed identities above, each with only the roles in §1.
2. Adds the federated credentials: read job (`main`), write (`environment:approve`), stop-spend (its environment).
3. Grants admin consent for exactly the listed Graph permissions, and prints them.
4. Prints the client IDs and the `gh variable set` commands to store them; commits nothing.
5. Is idempotent (finds each object by a unique name first) and has a matching `-Remove`.

Agent identities aren't created by the script: Foundry creates them with each hosted agent. The script assigns their roles and the MCP app role after a deploy.
Without Foundry, a blueprint can create them through Graph instead; the S1a fallback tests that ([spikes/s1a.md](spikes/s1a.md) §7).
