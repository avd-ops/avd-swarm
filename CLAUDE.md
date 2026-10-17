# CLAUDE.md

Private development repo for avd-swarm. Read [docs/spec.md](docs/spec.md) first; it builds on the operations brain spec in avd-second-brain and the rules in avd-landing-zone's CLAUDE.md.

## Rules
- Agents read and propose; they never write to Azure. The only write path is the brain's `propose_playbook_run`, approved in a GitHub Environment (spec §5).
- No Microsoft Graph here. Tenant-wide work belongs in tenant-swarm.
- Never commit a tenant, subscription, object or client ID; run `scripts/check-no-ids.sh` before pushing. Public Microsoft IDs go in `scripts/public-ids.txt` with a comment.
- No secrets: OIDC federated credentials and managed identities only.
- Tools start as the brain's PowerShell tools (its §4.4). A custom MCP server is allowed if it exposes only read tools and `propose_playbook_run` (spec SQ5).
- American English in docs, output and comments.
