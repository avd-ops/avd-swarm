#!/usr/bin/env python3
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""4-stage deterministic-first router for avd-assistant (docs/assistant-spec.md).

Pure Python stdlib + PyYAML only. Holds zero model credentials and imports zero model SDKs (F3).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_MODES = ("chief_of_staff", "red_team", "guru", "buddy")
DEFAULT_MODE = "chief_of_staff"
MAX_INPUT_CHARS = 1000
MAX_RETRIEVAL_BYTES = 6144
MAX_WINDOW_TURNS = 8

OFF_TOPIC_REFERRAL = (
    "I only cover this estate and its knowledge base. "
    "For general questions use a general-purpose assistant, and don't paste estate names or IDs into it."
)
NO_MATCH_MESSAGE = "Nothing in the estate or the pinned knowledge base matched that query."

_OFF_TOPIC_PATTERNS = [
    re.compile(r"\b(write|generate|solve)\b.*\b(poem|limerick|sonnet|haiku|story|essay|leetcode|regex homework)\b", re.I),
    re.compile(r"\b(recipe|horoscope|sports score|movie review|stock tip|trivia)\b", re.I),
    re.compile(r"\b(tell me a joke about (my|the) (boss|manager|coworker|politics|president))\b", re.I),
]

_DEFAULT_BRAIN_DOCS = [
    {
        "doc_id": "brain/decisions/0016-never-delete-the-last-copy",
        "title": "Decision 0016: Never delete the last copy",
        "source": "avd-second-brain",
        "tags": "backup profile fslogix teardown delete last copy",
        "summary": (
            "Teardown plans keep logs by default (-TeardownLogs Keep) and never delete the last copy of profiles, "
            "backups, or assessment data without explicit human -DiscardLastCopy approval."
        ),
        "learn_url": "https://learn.microsoft.com/azure/virtual-desktop/fslogix-containers-azure-files",
        "snippets": [],
    },
    {
        "doc_id": "brain/lessons/idle-hosts-cost",
        "title": "Idle session hosts and VM billing states",
        "source": "avd-second-brain",
        "tags": "idle host pool vm deallocate billing cost auto shutdown",
        "summary": (
            "A deallocated VM stops compute billing while its OS disk continues billing. "
            "Check WVDAgentHealthStatus and active sessions before draining or deallocating."
        ),
        "learn_url": "https://learn.microsoft.com/azure/virtual-machines/states-billing",
        "snippets": [],
    },
    {
        "doc_id": "assess/standards/host-pool-health",
        "title": "Host pool heartbeat and drain standards",
        "source": "avd-assess",
        "tags": "host pool heartbeat rdagentbootloader drain session host unhealthy",
        "summary": (
            "Missed heartbeats with zero active sessions indicate an agent service stall rather than VM failure; "
            "drain the host before restarting RDAgentBootLoader."
        ),
        "learn_url": "https://learn.microsoft.com/azure/virtual-desktop/troubleshoot-agent",
        "snippets": [],
    },
]


def load_yaml_safe(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def normalize_mode(mode: str | None) -> str:
    if mode in ALLOWED_MODES:
        return str(mode)
    return DEFAULT_MODE


def classify_scope(
    question: str,
    *,
    has_recent_in_scope: bool = False,
    has_open_plan_or_finding: bool = False,
) -> tuple[bool, str | None]:
    """Stage 0 scope gate (0 tokens, F12).

    Never refuses when an in-scope turn occurred recently or a plan/finding is open.
    Refuses only on input length > 1000 chars or a positive match to an off-topic class.
    """
    text = question.strip()
    if len(text) > MAX_INPUT_CHARS:
        return False, f"Input exceeds the {MAX_INPUT_CHARS}-character limit. Trim your question and try again."

    if has_recent_in_scope or has_open_plan_or_finding:
        return True, None

    for pat in _OFF_TOPIC_PATTERNS:
        if pat.search(text):
            return False, OFF_TOPIC_REFERRAL
    return True, None


def load_snippet_allowlist(root: Path = ROOT) -> set[str]:
    data = load_yaml_safe(root / "catalog/snippets/allowlist.yml")
    entries = data.get("snippets") or []
    return {str(item.get("sha256", "")).lower() for item in entries if isinstance(item, dict) and item.get("sha256")}


def format_code_blocks(blocks: list[str], allowlist: set[str]) -> list[dict[str, Any]]:
    """Gate Cloud Shell / code snippets by SHA-256 allowlist (F1)."""
    formatted: list[dict[str, Any]] = []
    for raw in blocks:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        allowed = digest in allowlist
        formatted.append(
            {
                "code": raw,
                "sha256": digest,
                "copyable": allowed,
                "unverified": not allowed,
                "label": "Cataloged snippet" if allowed else "unverified, not for pasting",
            }
        )
    return formatted


class BrainIndex:
    """Read-only SQLite FTS5 index over pinned avd-second-brain and avd-assess docs (F1)."""

    def __init__(self, docs: list[dict[str, Any]] | None = None, pinned_sha: str = "0000000") -> None:
        self.pinned_sha = pinned_sha[:7]
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.execute(
            "CREATE VIRTUAL TABLE docs_fts USING fts5(doc_id, title, source, tags, summary, learn_url, snippets_json)"
        )
        import json

        for doc in docs or _DEFAULT_BRAIN_DOCS:
            self.conn.execute(
                "INSERT INTO docs_fts (doc_id, title, source, tags, summary, learn_url, snippets_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    doc["doc_id"],
                    doc["title"],
                    doc["source"],
                    doc.get("tags", ""),
                    doc["summary"],
                    doc.get("learn_url", ""),
                    json.dumps(doc.get("snippets", [])),
                ),
            )
        self.conn.commit()

    def search(self, query: str, *, limit: int = 3) -> list[dict[str, Any]]:
        import json

        tokens = [t for t in re.findall(r"[a-zA-Z0-9_-]{2,}", query.lower()) if t not in {"the", "and", "for", "how", "why", "what", "is", "are"}]
        if not tokens:
            return []
        fts_query = " OR ".join(f'"{t}"' for t in tokens[:8])
        rows = self.conn.execute(
            "SELECT doc_id, title, source, summary, learn_url, snippets_json "
            "FROM docs_fts WHERE docs_fts MATCH ? ORDER BY rank LIMIT ?",
            (fts_query, limit),
        ).fetchall()
        results: list[dict[str, Any]] = []
        total_bytes = 0
        for doc_id, title, source, summary, learn_url, snippets_json in rows:
            if total_bytes + len(summary.encode("utf-8")) > MAX_RETRIEVAL_BYTES:
                break
            total_bytes += len(summary.encode("utf-8"))
            results.append(
                {
                    "docId": doc_id,
                    "title": title,
                    "source": source,
                    "summary": summary,
                    "learnUrl": learn_url,
                    "snippets": json.loads(snippets_json or "[]"),
                }
            )
        return results


def rank_triage_items(status: dict[str, Any], triage_cfg: dict[str, Any]) -> list[dict[str, str]]:
    """Chief of Staff Stage 1 deterministic triage ranking (governance/assistant-triage.yml, F13)."""
    order = [item["id"] for item in triage_cfg.get("triage_order", []) if isinstance(item, dict) and "id" in item]
    buckets: dict[str, list[dict[str, str]]] = {k: [] for k in order}

    switch_pos = str(status.get("switchPosition", "run"))
    if switch_pos != "run" and "switch-not-run" in buckets:
        buckets["switch-not-run"].append(
            {"kind": "switch-not-run", "title": f"Kill switch is {switch_pos}", "detail": str(status.get("switchMeaning", ""))}
        )

    pending = int(status.get("pendingPlans", 0) or 0)
    if pending > 0 and "pending-operator-approval" in buckets:
        buckets["pending-operator-approval"].append(
            {"kind": "pending-operator-approval", "title": f"{pending} plan(s) awaiting GitHub approval", "detail": "Review exact plan ID and hash in GitHub."}
        )

    alerts = status.get("spendAlerts") or []
    if alerts and "spend-alerts" in buckets:
        for alert in alerts:
            buckets["spend-alerts"].append({"kind": "spend-alerts", "title": str(alert), "detail": "Check spend ledger."})

    overdue = status.get("overdueRuns") or []
    if overdue and "overdue-teardowns" in buckets:
        for run in overdue:
            buckets["overdue-teardowns"].append({"kind": "overdue-teardowns", "title": f"Overdue teardown: {run}", "detail": "Close or extend run."})

    findings = status.get("findings") or []
    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    sorted_findings = sorted(findings, key=lambda f: sev_rank.get(str(f.get("severity", "low")).lower(), 9))
    if sorted_findings and "findings-by-severity" in buckets:
        for f in sorted_findings[:5]:
            buckets["findings-by-severity"].append(
                {
                    "kind": "findings-by-severity",
                    "title": f"[{f.get('severity', 'info')}] {f.get('title', 'Finding')}",
                    "detail": str(f.get("plane", "")),
                }
            )

    ranked: list[dict[str, str]] = []
    for key in order:
        ranked.extend(buckets.get(key, []))
    return ranked


def audit_plan_static(plan: dict[str, Any] | None) -> dict[str, Any]:
    """Red Team Stage 1 static audit with no pass state (F4)."""
    checks_run = 4
    risks: list[str] = []
    assumptions: list[str] = []
    questions: list[str] = []

    if not plan:
        return {
            "checksRun": 0,
            "risks": [],
            "assumptions": ["No active plan is attached to this turn."],
            "questions": ["Which pending plan ID should be audited?"],
            "footer": "No plan attached. This is not a gate result.",
        }

    if not str(plan.get("rollback", "")).strip():
        risks.append("Missing explicit rollback step on the plan.")
    if not str(plan.get("expiry", "")).strip():
        risks.append("Plan has no expiry window.")
    if "all" in str(plan.get("blastRadius", "")).lower():
        risks.append("Blast radius indicates pool-wide or multi-host impact.")
    gates = plan.get("gateResults") or []
    failed_gates = [
        g.get("gate", "gate")
        for g in gates
        if str(g.get("outcome") or g.get("result") or "").lower() not in ("pass", "ok")
    ]
    if failed_gates:
        risks.append(f"Gate check(s) did not pass: {', '.join(failed_gates)}.")

    assumptions.append(f"Plan hash {str(plan.get('planHash', ''))[:12]} matches the GitHub approve run.")
    questions.append("Have active user sessions on target hosts been verified right before approval?")

    footer = (
        f"These {checks_run} checks found nothing. This is not a gate result."
        if not risks
        else f"Static audit ran {checks_run} checks and surfaced {len(risks)} risk(s). This is not a gate result."
    )
    return {
        "checksRun": checks_run,
        "risks": risks,
        "assumptions": assumptions,
        "questions": questions,
        "footer": footer,
    }


def build_stage3_window(
    turns: list[dict[str, Any]],
    *,
    limit: int = MAX_WINDOW_TURNS,
) -> list[dict[str, str]]:
    """Bounded sliding window (F11, F14): operator text + rendered answers only, tagged by mode.

    Excludes retrieved bodies, asides, Red Team assumption lists, and Stage 1 cards.
    """
    window: list[dict[str, str]] = []
    for t in turns[-limit:]:
        op = str(t.get("operatorText", "")).strip()
        ans = str(t.get("answer", "")).strip()
        mode = normalize_mode(t.get("assistantMode"))
        if op:
            window.append({"role": "user", "mode": mode, "content": op})
        if ans:
            window.append({"role": "assistant", "mode": mode, "content": f"[{mode}] {ans}"})
    return window


def resolve_mode_model_config(mode: str, prices_cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve server-pinned model, effort, max_tokens, and daily_cap_usd for mode (F10)."""
    modes = prices_cfg.get("assistant_modes") or {}
    cfg = modes.get(normalize_mode(mode))
    return dict(cfg) if isinstance(cfg, dict) else None
