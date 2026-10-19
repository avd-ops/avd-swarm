# 0005. The write path runs in a private adopter repo; solo mode replaces a second person

- **Status:** Proposed
- **Date:** 2026-10-19
- **Pair:** avd-second-brain's operations spec §3.2 (project and adopters) and §9.3 (solo mode), which this follows. The two change together (decision 0003).

## Context
A second red team (2026-10-19, [redteam.md](../redteam.md) R17 to R23) found that the approve workflows were specified for this public repo, whose logs and job summaries are public; that the governance rules assumed a second reviewer this one-maintainer project doesn't have; that Improve's PRs would never start the guard; and that the kill switch and the spend cap didn't reach the Anthropic API fallback.

## Decision
- **Project and adopters.** This repo ships code, the catalog, workflow templates and CI. Everything that touches a tenant (approve, stop-spend, the kill switch, Improve, Scribe, evals against Azure, spend jobs) runs only in an adopter's private repo synced from a release tag ([spec](../spec.md) §5b). Logs and summaries never carry targets or parameters.
- **Solo mode.** With one approver, the 72-hour time-lock and the guard's deterministic summary stand in for a second person on changes that raise capability ([governance](../governance.md) §2.6). AI reviews never count.
- **Agents open PRs as GitHub Apps**, never with the default token, so the guard runs on them.
- **Model calls are stopped and metered in one place:** a model client that checks the kill switch and a token meter before every call, plus the provider's own workspace limit ([spend.md](../spend.md) §3.7).
- **Build order:** close the holes first at $0, then the adopter repo and its controls, then read-only agents, then the first actions (spec §9a).

## Consequences
- Nick's own sandbox becomes an adopter: a private repo he creates, with the write path's variables and environments there, not here.
- A change that raises capability waits three days in solo mode. That is the point; a change that lowers capability merges at once.
- One more GitHub App per drafting role, and a dedicated Anthropic workspace whose limit a person sets in the Console.
