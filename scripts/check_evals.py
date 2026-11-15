# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Offline CI check for evals/README.md, spikes/s1a/evals/questions.json, and evals/agents/cases.yml (spec §9a)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Iterable

import yaml

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_AGENTS = ("coordinator", "hosts", "cost", "intune")
MIN_CASES_PER_AGENT = 5
MIN_SPIKE_EVAL_QUESTIONS = 10


def _load_allowlists(root: Path) -> dict[str, dict]:
    allowlists: dict[str, dict] = {}
    for agent in ("hosts", "cost", "intune"):
        path = root / "governance" / "allowlists" / f"{agent}.yml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        allowlists[agent] = data
    return allowlists


def check_evals(root: Path = ROOT) -> list[str]:
    findings: list[str] = []

    readme = root / "evals" / "README.md"
    if not readme.is_file():
        findings.append("evals/README.md is missing (required by docs/spec.md §9a)")
    else:
        text = readme.read_text(encoding="utf-8")
        for ref in (
            "spikes/s1a/evals/questions.json",
            "evals/agents/cases.yml",
            "evals/rag/cases.yml",
            "evals/voice/cases.yml",
        ):
            if ref not in text:
                findings.append(f"evals/README.md does not reference {ref}")

    spike_path = root / "spikes" / "s1a" / "evals" / "questions.json"
    if not spike_path.is_file():
        findings.append("spikes/s1a/evals/questions.json is missing")
    else:
        spike_items = json.loads(spike_path.read_text(encoding="utf-8"))
        seen_spike: set[str] = set()
        eval_count = 0
        for item in spike_items:
            qid = item.get("id", "")
            if not qid or qid in seen_spike:
                findings.append(f"spikes/s1a/evals/questions.json has duplicate or empty id: {qid!r}")
            seen_spike.add(qid)
            if item.get("kind") == "eval":
                eval_count += 1
                if not item.get("expect"):
                    findings.append(f"spike question {qid} is missing expect patterns")
        if eval_count < MIN_SPIKE_EVAL_QUESTIONS:
            findings.append(
                f"spikes/s1a/evals/questions.json has {eval_count} eval questions, expected >= {MIN_SPIKE_EVAL_QUESTIONS}"
            )

    cases_path = root / "evals" / "agents" / "cases.yml"
    if not cases_path.is_file():
        findings.append("evals/agents/cases.yml is missing")
        return findings

    doc = yaml.safe_load(cases_path.read_text(encoding="utf-8")) or {}
    cases = doc.get("cases")
    if not isinstance(cases, list) or not cases:
        findings.append("evals/agents/cases.yml must contain a non-empty 'cases' list")
        return findings

    allowlists = _load_allowlists(root)
    counts: dict[str, int] = {a: 0 for a in REQUIRED_AGENTS}
    seen_ids: set[str] = set()

    for idx, case in enumerate(cases):
        if not isinstance(case, dict):
            findings.append(f"evals/agents/cases.yml case #{idx} must be a mapping")
            continue
        cid = case.get("id", "")
        if not cid or cid in seen_ids:
            findings.append(f"evals/agents/cases.yml has duplicate or empty id: {cid!r}")
        seen_ids.add(cid)

        agent = case.get("agent", "")
        if agent not in REQUIRED_AGENTS:
            findings.append(f"case {cid}: unknown agent {agent!r}")
            continue
        counts[agent] += 1

        if not str(case.get("question", "")).strip():
            findings.append(f"case {cid}: question must be non-empty")

        for pattern in case.get("expect_patterns") or []:
            try:
                re.compile(pattern)
            except re.error as exc:
                findings.append(f"case {cid}: invalid regex {pattern!r}: {exc}")

        if agent == "coordinator":
            handoff = case.get("expect_handoff")
            if handoff is not None and handoff not in allowlists:
                findings.append(f"case {cid}: expect_handoff {handoff!r} is not a valid specialist")
        else:
            allowed_tools = set(allowlists[agent].get("tools") or [])
            allowed_kql = set(allowlists[agent].get("kql") or [])
            for tool in case.get("expected_tools") or []:
                if tool not in allowed_tools:
                    findings.append(f"case {cid}: expected_tools {tool!r} not in {agent} allowlist")
            for tool in case.get("forbidden_tools") or []:
                if tool in allowed_tools:
                    findings.append(f"case {cid}: forbidden_tools {tool!r} is in {agent} allowlist")
            for kql in case.get("expected_kql") or []:
                if kql not in allowed_kql:
                    findings.append(f"case {cid}: expected_kql {kql!r} not in {agent} allowlist")

    for agent, count in counts.items():
        if count < MIN_CASES_PER_AGENT:
            findings.append(f"agent {agent!r} has {count} eval cases, expected >= {MIN_CASES_PER_AGENT}")

    return findings


def main(argv: Iterable[str] | None = None) -> int:
    _ = list(argv or sys.argv[1:])
    findings = check_evals(ROOT)
    for finding in findings:
        print(f"::error::{finding}")
    if findings:
        return 1
    doc = yaml.safe_load((ROOT / "evals" / "agents" / "cases.yml").read_text(encoding="utf-8"))
    print(f"Evals checked ({len(doc['cases'])} agent cases, spike questions, README): 0 finding(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
