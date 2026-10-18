# 0004. Scribe drafts docs fixes; the brain decides what is inconsistent

- **Status:** Proposed
- **Date:** 2026-10-18
- **Pair:** [avd-second-brain decision 0004](https://github.com/nickprignano/avd-second-brain/blob/main/docs/decisions/0004-docs-consistency.md) says the same from the other side. The two change together.

## Context
The owner asked on 2026-10-18 for a repo or swarm function that builds a wiki and documentation for people and agents and keeps code comments, changelogs, READMEs and the wiki consistent with the second brain. Under decision 0003 the brain thinks and avd-swarm acts, so the work splits.

## Decision
- **No new repo.** The deterministic check, the facts of record, the generator and the wiki live in avd-second-brain ([docs-spec](https://github.com/nickprignano/avd-second-brain/blob/main/docs/docs-spec.md), [red team](https://github.com/nickprignano/avd-second-brain/blob/main/docs/docs-redteam.md)).
- **avd-swarm gets Scribe** ([spec §6e](../spec.md)), a scheduled role in the Improve family that turns the brain's findings (contract C13, `avd-doc-findings/v1`) into draft PRs. It never merges, never changes code to match docs, and can't touch `CLAUDE.md`, prompts, governance, workflows or the notice.
- **This repo is also a checked repo:** it gains the vendored docs check under `scripts/docs/`, `docs/reference/`, generated README blocks and a `CHANGELOG.md` in the brain's phase D0.

## Consequences
- One more draft-PR identity (a GitHub App scoped to the connected repos, no Azure role), added to identities.md when built.
- A change to C13 or Scribe's limits is a pair of PRs here and in avd-second-brain.
