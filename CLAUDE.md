# CLAUDE.md

Private development repo for avd-swarm. Read [docs/spec.md](docs/spec.md) first; it connects to the second brain (avd-second-brain) and follows avd-landing-zone's CLAUDE.md rules where they apply.

## Rules
- Agents read and propose; they never write. Every change is a plan a person approves: a brain playbook when a brain is deployed, otherwise the `approve` environment (spec §5).
- Graph reads only for AVD-related objects, and only for the Users agent. Tenant-wide work belongs in tenant-swarm.
- Never commit a tenant, subscription, object or client ID; run `scripts/check-no-ids.sh` before pushing. Public Microsoft IDs go in `scripts/public-ids.txt` with a comment.
- No secrets: OIDC federated credentials and managed identities only.
- A custom MCP server is allowed if it exposes only read tools and plan proposals (spec SQ5).
- Every plan passes the governance gate; `governance/` and the observer registry are protected paths. No agent can trip or reset the kill switch ([docs/governance.md](docs/governance.md)).
- Conversations feed the Improve workflow (spec §6b), which only opens draft PRs and can't touch governance, the observer registry, the kill switch, CI or write roles.
- American English in docs, output and comments.
