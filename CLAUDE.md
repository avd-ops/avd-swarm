# CLAUDE.md

Public (MIT) development repo for avd-swarm. Read [docs/spec.md](docs/spec.md) first; it connects to the second brain (avd-second-brain) and follows avd-landing-zone's CLAUDE.md rules where they apply.

## Rules
- **avd-swarm acts, the second brain thinks, and they change together** (decision 0003). avd-swarm is the only write path and holds the one kill switch; the brain never holds a write credential. A change here to a contract (C8–C12), the action catalog, an agent's role, grading, models, identity or governance comes with the matching avd-second-brain PR, and each links the other.
- Agents read and propose; they never write. Every change, a brain playbook included, is a plan a person approves in the `approve` environment (spec §5).
- avd-swarm never reads avd-current-state packages, Stratusphere exports, a vault or a facts snapshot file. Estate facts reach it only from the brain, as cited C11 (current state) or C12 (forensic) evidence, and the gate re-reads live state before any change (spec §6d).
- Graph reads only for AVD-related objects, and only for the Users agent. Tenant-wide work belongs in tenant-swarm.
- Never commit a tenant, subscription, object or client ID; run `scripts/check-no-ids.sh` before pushing. Public Microsoft IDs go in `scripts/public-ids.txt` with a comment.
- No secrets: OIDC federated credentials and managed identities only.
- A custom MCP server is allowed if it exposes only read tools and plan proposals (spec SQ5).
- Every plan passes the governance gate; `governance/` and the observer registry are protected paths. No agent can trip or reset the kill switch ([docs/governance.md](docs/governance.md)).
- Conversations feed the Improve workflow (spec §6b), which only opens draft PRs and can't touch governance, the observer registry, the kill switch, CI or write roles.
- American English in docs, output and comments.
- **Personal project, not for production, as is, not affiliated with the author's employer, Liquidware or Microsoft.** The LICENSE, the README warning and the notice at the top of every entry point stay.
- Public repo: nothing tenant-swarm-specific and no private evidence (spike outputs stay in Cloud Shell or the private evidence folder).
