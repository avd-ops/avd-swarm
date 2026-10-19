#!/usr/bin/env python3
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Fails an agent-authored pull request that touches a protected path (spec §6b, §6e; governance §2.1).

A PR is agent-authored when its author or any commit's author is listed under `agents` in
governance/protected-paths.yml (the GitHub Apps Improve and Scribe open PRs as). Such a PR may not
add, change or delete a `protected` path, and may only add files under an `append_only` path.

Usage: check_agent_pr.py PR_JSON NAME_STATUS
  PR_JSON      output of `gh pr view N --json author,commits`
  NAME_STATUS  output of `git diff --name-status <base>...<head>`
Exit 1 on any finding.
"""

from __future__ import annotations

import fnmatch
import json
import sys
from pathlib import Path

import yaml


def matches(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-2])
    return fnmatch.fnmatchcase(path, pattern)


def authors(pr: dict) -> set[str]:
    logins = {((pr.get("author") or {}).get("login") or "")}
    for commit in pr.get("commits") or []:
        for author in commit.get("authors") or []:
            logins.add(author.get("login") or "")
    # gh reports an App's PR author as "app/<slug>"; commits carry "<slug>[bot]".
    normalized = set()
    for login in logins - {""}:
        normalized.add(f"{login[4:]}[bot]" if login.startswith("app/") else login)
    return normalized


def parse_name_status(text: str) -> list[tuple[str, str]]:
    """(status letter, path) pairs; a rename or copy counts as touching both its old and new path."""
    changes = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0][:1]
        for path in parts[1:]:
            changes.append((status, path))
    return changes


def check(policy: dict, pr: dict, changes: list[tuple[str, str]]) -> list[str]:
    agents = set(policy.get("agents") or [])
    found = authors(pr) & agents
    if not found:
        return []
    who = ", ".join(sorted(found))
    findings = []
    for status, path in changes:
        if any(matches(path, p) for p in policy.get("protected") or []):
            findings.append(f"{path}: protected path changed by {who}")
        elif status != "A" and any(matches(path, p) for p in policy.get("append_only") or []):
            findings.append(f"{path}: append-only path changed or deleted by {who} (status {status})")
    return findings


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    root = Path(__file__).resolve().parent.parent
    policy = yaml.safe_load((root / "governance/protected-paths.yml").read_text(encoding="utf-8"))
    pr = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    changes = parse_name_status(Path(argv[1]).read_text(encoding="utf-8"))
    findings = check(policy, pr, changes)
    for finding in findings:
        print(finding)
    agent = bool(authors(pr) & set(policy.get("agents") or []))
    print(f"{'Agent' if agent else 'Not an agent'}-authored PR, {len(changes)} change(s): {len(findings)} finding(s).")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
