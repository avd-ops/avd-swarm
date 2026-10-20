# Changelog

All notable changes, in the format of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). New lines come from the `Changelog:` line in each merged pull request's body; earlier history is in the merged pull requests.

## Unreleased

### Added

- Recovery tests and failover as catalog actions that run the landing zone's recovery scripts, with a read-only Recovery agent (`docs/recovery.md`, decision 0006). Spec only.
- The second brain's docs consistency check on every PR (`.github/workflows/docs.yml`, pinned), reference pages for the spike's scripts in `docs/reference/`, and the README's entry point table.

### Fixed

- Decision 0004's Pair link points at the brain's decision where it now lives, in its family knowledge base.
