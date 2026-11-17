#!/usr/bin/env python3
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Cheap, redacted, 90-day-retained transcript store for avd-assistant (docs/assistant-spec.md, F17).

Stores generalized/redacted turns as compact JSONL (or gzipped JSONL / Blob in prod) with zero
GUIDs, emails, IPs, subscription paths, or raw estate names so transcripts can safely feed
offline validation and the Improve workflow.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

RETENTION_DAYS = 90
RETENTION_SECONDS = RETENTION_DAYS * 86400
MAX_RECORDS_PER_OPERATOR = 500

_GUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_SUB_PATH_RE = re.compile(r"/subscriptions/[^\s\"'`]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b")
_ESTATE_PREFIX_RE = re.compile(r"\b(hp|sh|rg|vnet|snet|kv|st)-[A-Za-z0-9-]+\b", re.IGNORECASE)


def _pseudo_tag(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.lower().encode("utf-8")).hexdigest()[:4]
    return f"<{prefix.lower()}-{digest}>"


def redact_text(text: str) -> str:
    """Generalize and redact IDs, emails, IPs, resource paths, and estate names at 0 tokens."""
    if not text:
        return ""
    out = _SUB_PATH_RE.sub("<resource-id>", text)
    out = _GUID_RE.sub("<guid>", out)
    out = _EMAIL_RE.sub("<operator>", out)
    out = _IPV4_RE.sub("<ip>", out)
    out = _ESTATE_PREFIX_RE.sub(lambda m: _pseudo_tag(m.group(1), m.group(0)), out)
    return out


def generalize_turn(turn: dict[str, Any], *, operator_id: str = "operator", now_ts: float | None = None) -> dict[str, Any]:
    """Build a minimal, redacted transcript record safe for validation and Improve."""
    ts = float(now_ts if now_ts is not None else time.time())
    op_hash = hashlib.sha256(operator_id.encode("utf-8")).hexdigest()[:12]
    return {
        "ts": int(ts),
        "turnId": str(turn.get("id", "")),
        "operatorHash": op_hash,
        "assistantMode": str(turn.get("assistantMode", "chief_of_staff")),
        "stage": int(turn.get("stage", 1)),
        "tokenCost": int(turn.get("tokenCost", 0)),
        "safetyMode": str(turn.get("mode", "light")),
        "redactedQuestion": redact_text(str(turn.get("operatorText", ""))),
        "redactedAnswer": redact_text(str(turn.get("answer", ""))),
        "feedback": turn.get("feedback"),
    }


class TranscriptStore:
    """Append-only JSONL transcript store with 90-day retention and per-operator cap."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.records: list[dict[str, Any]] = []
        if self.path and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        self.records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

    def append_turn(self, turn: dict[str, Any], *, operator_id: str = "operator", now_ts: float | None = None) -> dict[str, Any]:
        ts = float(now_ts if now_ts is not None else time.time())
        record = generalize_turn(turn, operator_id=operator_id, now_ts=ts)
        self.records.append(record)
        self.prune(now_ts=ts)
        self._flush()
        return record

    def record_feedback(self, turn_id: str, rating: str, reason: str = "") -> bool:
        for rec in reversed(self.records):
            if rec.get("turnId") == turn_id:
                rec["feedback"] = {"rating": rating, "reason": redact_text(reason)[:120]}
                self._flush()
                return True
        return False

    def prune(self, *, now_ts: float | None = None) -> int:
        ts = float(now_ts if now_ts is not None else time.time())
        cutoff = ts - RETENTION_SECONDS
        before = len(self.records)
        kept = [r for r in self.records if float(r.get("ts", 0)) >= cutoff]

        # Enforce per-operator cap (keep newest)
        counts: dict[str, int] = {}
        capped: list[dict[str, Any]] = []
        for rec in reversed(kept):
            oh = str(rec.get("operatorHash", ""))
            counts[oh] = counts.get(oh, 0) + 1
            if counts[oh] <= MAX_RECORDS_PER_OPERATOR:
                capped.append(rec)
        capped.reverse()
        self.records = capped
        return before - len(self.records)

    def export_jsonl(self) -> str:
        return "\n".join(json.dumps(r, separators=(",", ":")) for r in self.records)

    def _flush(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.export_jsonl() + ("\n" if self.records else ""), encoding="utf-8")
