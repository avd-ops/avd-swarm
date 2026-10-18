# 0001. Operate the AVD estate through chat agents that read freely and change nothing without a person's approval

- **Status:** Proposed

## Context
The owner wants to run AVD estates, and eventually the whole tenant, from a chat window on any device instead of the Azure, Entra and Microsoft 365 portals. The second brain ([avd-landing-zone decision 0012](https://github.com/nickprignano/avd-landing-zone/blob/master/docs/decisions/0012-second-brain.md)) already watches a landing zone and heals it through approved playbooks, but its scope is deliberately narrow: no Graph, no deployments, no users. avd-swarm needs all of those. Details: [spec.md](../spec.md).

## Decision
- Build **avd-swarm** in its own repository: a coordinator with specialist agents (readiness, deploy, hosts, images, users, cost) with a chat front end, **avd-buddy**, on Static Web Apps with Entra sign-in.
- It connects to the brain but doesn't depend on it. When a brain is deployed, avd-swarm reads its episodes and proposes its playbooks. Otherwise, it uses its own plan-and-approve path.
- Agents read through allowlisted tools, including Graph for AVD-related objects. Every change is a plan a person approves in the GitHub Environment `approve`, the only place a write identity gets a token.
- Tenant-wide administration that isn't about AVD belongs to **tenant-swarm**, which adopts this pattern once it has proven itself here.

## Alternatives considered
- **avd-swarm as the brain's Concierge agent with a front end:** keeps one write path, but it would be limited to the brain's scope and wait on brain Tier 3.
- **A confirmation button in the chat as the write path:** simpler, but nothing outside the chat enforces it. Rejected.
- **A custom MCP server for tools:** open (spec SQ5), as long as it carries no direct writes.

## Consequences
- Two write paths (brain playbooks and `approve` actions), both enforced by Azure through federated credentials. Both need audit records that read the same way.
- Graph read permissions add surface; they are limited to the Users agent.
- A new eval set for conversational questions.
