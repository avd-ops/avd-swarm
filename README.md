# avd-swarm

> **Warning:** a personal project, not for production, provided as is under the [MIT license](LICENSE). Not affiliated with the author's employer, Liquidware or Microsoft. It's a design and a spike so far; nothing here is ready to run against a real tenant.

An agent-based platform for managing Azure and Entra ID without the portals, starting with the [AVD landing zone](https://github.com/nickprignano/avd-landing-zone). Its chat front end, **avd-buddy** (not built yet), works from any browser, iPad included. A coordinator agent hands questions to specialist agents (readiness, deploy, hosts, images, users, cost). They read freely and change nothing without a person approving the exact plan. It connects to the [second brain](https://github.com/nickprignano/avd-second-brain) where they overlap.

This is the development version of the pattern. Once it has a record of approved, verified runs, it moves tenant-wide into tenant-swarm, a separate private project.

- Spec: [docs/spec.md](docs/spec.md)
- Decisions: [docs/decisions](docs/decisions)
- Rules: [CLAUDE.md](CLAUDE.md)
