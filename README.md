# avd-swarm

> **Warning:** a personal project, not for production, provided as is under the [MIT license](LICENSE). Not affiliated with the author's employer, Liquidware or Microsoft. It's a design and a spike so far; nothing here is ready to run against a real tenant.

An agent-based platform for managing Azure and Entra ID without the portals, starting with the [AVD landing zone](https://github.com/nickprignano/avd-landing-zone). Its chat front end, **avd-buddy** (not built yet), works from any browser, iPad included. A coordinator agent hands questions to specialist agents (readiness, deploy, hosts, images, users, cost). They read freely and change nothing without a person approving the exact plan. It connects to the [second brain](https://github.com/nickprignano/avd-second-brain) where they overlap.

This is the development version of the pattern. Once it has a record of approved, verified runs, it moves tenant-wide into tenant-swarm, a separate private project.

**Build order** ([spec §9a](docs/spec.md#9a-build-order-proposed-2026-10-19-after-the-second-red-team)): 1. the S1a spike on Anthropic's API with its own token cap; 2. read-only Hosts and Cost agents beside the brain's Tier 1; 3. the first two write actions from a private adopter repo, to ten verified changes. **Everything after step 3 (brain proposals through C9, Improve, the judge machinery, the compliance gate, standing exceptions) is designed, not scheduled.** Don't build it from the spec until step 3 is done and the owner says so.

The write path never runs in this public repo: adopters run it from a private repo synced from a release tag ([spec §5b](docs/spec.md#5b-where-the-write-path-runs-the-project-and-its-adopters)).

- Spec: [docs/spec.md](docs/spec.md)
- Decisions: [docs/decisions](docs/decisions)
- Rules: [CLAUDE.md](CLAUDE.md)
