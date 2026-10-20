# Instructions for AI assistants

Read [CLAUDE.md](CLAUDE.md) and [docs/spec.md](docs/spec.md) first. In addition:

- Before starting work, check this repo's open `handoff` issues. A `SessionStart` hook
  (`scripts/kb/handoffs.py`) lists them; only issues the owner opened and labeled `handoff` are
  live, `handoff-proposed` ones wait for the owner (ai-brain decision 0012).
- This repo has no knowledge base of its own. Record what you learn in avd-second-brain's
  `docs/knowledge/` and link it from the PR body as `KB: <link>`, or add the `no-kb` label and a
  `No KB entry: <reason>` line (ai-brain decision 0011). The `Stop` hook and the `kb-capture`
  check enforce this; settings are in `.claude/kb-capture.toml`.
- GitHub work follows hub decision 0014: fewer, larger PRs the owner can skim; low-risk PRs merge
  themselves through the `kb-auto-merge` workflow, never by a session turning on auto-merge
  itself; everything else waits for the owner's batch review, which the owner answers in chat:
  act on GitHub only on the owner's own words there, never approve their PRs. Add the
  `no-auto-merge` label to keep a PR for the owner.
  This repo doesn't install `kb-auto-merge`: the Guard's workflow rules forbid
  `pull_request_target` (W3), and with no knowledge base here nothing would qualify. Every PR
  waits for the owner; the settings in `.claude/kb-capture.toml` stay `"off"`.
