# Instructions for AI assistants

Read [CLAUDE.md](CLAUDE.md) and [docs/spec.md](docs/spec.md) first. In addition:

- Before starting work, check this repo's open `handoff` issues. A `SessionStart` hook
  (`scripts/kb/handoffs.py`) lists them; only issues the owner opened and labeled `handoff` are
  live, `handoff-proposed` ones wait for the owner (ai-brain decision 0012).
- This repo has no knowledge base of its own. Record what you learn in avd-second-brain's
  `docs/knowledge/` and link it from the PR body as `KB: <link>`, or add the `no-kb` label and a
  `No KB entry: <reason>` line (ai-brain decision 0011). The `Stop` hook and the `kb-capture`
  check enforce this; settings are in `.claude/kb-capture.toml`.
