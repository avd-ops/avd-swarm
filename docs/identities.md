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
| **Write identity** (one per Run Command action, one shared by the other actions; spec §5a) | User-assigned managed identity, federated to the `approve` environment of the adopter's private repo only (spec §5b) | The approved plan's job, nothing else (spec §5) | Only what the action catalog needs (drain, restart, Stop/Resume), plus `Microsoft.PolicyInsights/policyStates/triggerEvaluation/action` for the compliance verify step (governance §2.5): a custom role on the AVD resource groups. Never a policy, exemption or `Microsoft.Security` write | None. Group membership through **Groups Administrator scoped to an administrative unit** holding the AVD groups, never a tenant-wide permission (R7) |
| **Stop-spend identity** | User-assigned managed identity, federated to its own environment | The spend watchdog's stop (governance §2.3) | A custom role that can only stop, deallocate and delete, assigned on the run resource groups only; a tag can't limit control-plane actions (governance §2.3, red team V1-14) | None |
| **Kill switch** | The Logic App's managed identity | The kill switch (governance §2.3) | Custom roles that only take capability away, including `containerApps/stop/action` on the agents' and the tool layer's Container Apps, and `roleAssignments/delete` limited by a delegation condition to the agent identities' principal IDs (red team V1-15) | None. It holds no Anthropic credential; cutting the federation rule is in the person's trip block |
| **Token meter** | User-assigned managed identity | The metered model client's counter (spend.md §3.7) | Write on the meter's table only | None |
| **GitHub Apps** (no Azure or Graph role) | GitHub App installations on the adopter's repos | `avd-swarm-dispatch`: `actions:write`, dispatches the approve workflow with a plan ID (spec §5). `avd-swarm-improve` and `avd-swarm-scribe`: `contents:write` and `pull_requests:write`, pushing only `improve/*` or `scribe/*` by ruleset, no `actions`, `workflows` or `administration` (spec §6b, §6e) | None | None. Their installation tokens are minted per job and expire within the hour; never the workflow's `GITHUB_TOKEN` for a PR, which wouldn't start the guard (R20) |
| **avd-buddy's API** | The Functions app's managed identity | avd-buddy's chat relay | None | None. The agent API accepts tokens only from this identity (Entra app auth on the Container App, ingress internal to the swarm's VNet); the operator's identity travels as a claim inside that token, so a caller that reaches the ingress directly can't present a claim of its own (red team V1-16) |
| **avd-buddy's SRE Agent tools** (only if the add-on is on, spec §3a) | The second Azure MCP Server instance's managed identity | `sreagent` start-thread, follow-up and read-thread only | **None** | None. Holds the SRE Agent Standard User role, nothing that can approve |
| **Brain runtime** (only if a brain is deployed) | The brain's own managed identity, created by the brain's deployable | Reads the kill switch flag (brain contract C10); serves the Concierge to the tool layer | Reader on the swarm's resource group only | None. Listed in `observers.yml` as a subscriber, with no `verdict` and no `trip` (red team V1-10). avd-swarm's identities hold no read on any brain store; the tool layer calls the Concierge over HTTPS with Entra auth |
| **Operator tooling app** | Public client app (device code), delegated | The operator's own scripts in Cloud Shell: setup, spikes, break-glass reads | The operator's own rights; the app adds none | Delegated `User.Read`, plus only the scopes a setup step needs, listed in the setup script (creating the MCP app needs `Application.ReadWrite.All` delegated, still limited by the operator's own role). Admin consents once |

## 2. Decisions

- **Operator Cloud Shell runs keep a dedicated public-client app.** One device-code sign-in per Cloud Shell session, with the app's name in the sign-in logs and a Conditional Access policy that can target it (for example phishing-resistant MFA). Unattended work never uses it: anything scheduled or approved runs through GitHub Actions OIDC.
- **The spike (S1a) keeps using the Azure CLI** until the setup script exists; its MCP app is registered with `az rest` against Graph as the operator.
- **Claude through Anthropic's API uses workload identity federation, never an API key.** When the provider setting (spec §6) is Anthropic's API, the agents' managed identity is federated to an Anthropic service account through a rule that matches its object ID; the S1a fallback proves it ([spikes/s1a.md](spikes/s1a.md) §7). The service account lives in a workspace of its own with a monthly spend limit, and the rule's token lifetime is 10 minutes, so the kill switch's trip block can cut the path by archiving the rule (governance §2.3, layer 6; spend.md §3.7).
- **Nothing gets a Graph write permission.** The only Graph writes are group membership changes, through an administrative-unit-scoped role on the write identity.

## 3. Setup script (to build, S1)

`scripts/setup/New-SwarmIdentity.ps1`, run once by an admin in Cloud Shell:

1. Creates the operator tooling app, the MCP server app, and the managed identities above, each with only the roles in §1.
2. Adds the federated credentials: read job (`main`), write (`environment:approve`), stop-spend (its environment), each with a subject that names the adopter's private repo; it refuses to run against a public repo (spec §5b).
3. Grants admin consent for exactly the listed Graph permissions, and prints them.
4. Prints the client IDs and the `gh variable set` commands to store them; commits nothing.
5. Is idempotent (finds each object by a unique name first) and has a matching `-Remove`.

Agent identities aren't created by the script: Foundry creates them with each hosted agent. The script assigns their roles and the MCP app role after a deploy.
Without Foundry, a blueprint can create them through Graph instead; the S1a fallback tests that ([spikes/s1a.md](spikes/s1a.md) §7).
