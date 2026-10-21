# Changelog

All notable changes, in the format of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). New lines come from the `Changelog:` line in each merged pull request's body; earlier history is in the merged pull requests.

## Unreleased

### Added

- A review and plan for an open-source core with paid Microsoft services as optional adapters (`docs/core-and-adapters.md`, decision 0009, proposed): runbooks as code, a scheduled and alert-triggered check runner, the gateway package and a provider interface; spec section 3a and SQ11 note that the SRE Agent interim use was dropped on 2026-10-21.
- Recovery tests and failover as catalog actions that run the landing zone's recovery scripts, with a read-only Recovery agent (`docs/recovery.md`, decision 0006). Spec only.
- The second brain's docs consistency check on every PR (`.github/workflows/docs.yml`, pinned), reference pages for the spike's scripts in `docs/reference/`, and the README's entry point table.

### Fixed

- Decision 0004's Pair link points at the brain's decision where it now lives, in its family knowledge base.
