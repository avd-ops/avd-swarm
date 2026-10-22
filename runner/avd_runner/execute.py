# avd-swarm runner: run one runbook. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Runs a runbook's fixed reads, checks its facts in code, and asks its agent only about what failed
(core-and-adapters.md section 3.3; spec section 3c).

This is what the agent API's runbook endpoint does. The reads go through the tool layer's allowlisted
functions (a tool by name, or a KQL catalog query by ID), never through a model. Only facts with
`onFail: investigate` that fail reach the agent, through the gateway with the runbook's own budget; a
refusal there (the kill switch, a cap) leaves the finding standing with the investigation marked refused.
The check runner never calls this with a model of its own: it asks the agent API, which holds the gateway.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "gate"))
from avd_gate.evidence import UNATTENDED  # noqa: E402
from avd_gateway.prices import SpendRefused  # noqa: E402

from .runbook import Runbook, evaluate, resolve_args  # noqa: E402

# Who started a run: a schedule, a workflow dispatch or an alert (the alert job, WP6). None is an operator in
# avd-buddy, so the gate refuses a plan whose only evidence is such a run (gate/avd_gate/evidence.py, V1-4).
STARTED_BY = UNATTENDED


class Reads(Protocol):
    scope: dict  # the deployment's values for ${placeholders} in a tool read's args (runbook.SCOPE_KEYS)

    def tool(self, name: str, args: dict) -> list[dict]:
        """Calls one allowlisted read tool with the runbook's resolved args; raises on any failure."""

    def kql(self, query_id: str) -> list[dict]:
        """Runs one KQL catalog query by ID; raises on any failure."""


# investigate(runbook, evaluation, results, budget_usd) -> {"claims": [...], "unanswered": [...], "shield": ""}
# (shield: '', 'flagged' or 'unscanned', the gateway shield's mark on the conversation)
Investigate = Callable[[Runbook, dict, dict, float], dict]


# The gateway refuses with SpendRefused (the kill switch, a cap, an unpriced model, an unknown caller).
Refused = SpendRefused


@dataclass
class ReadFailure:
    read: str
    error: str  # the exception's type only: a message can carry a host or resource name


def run_reads(rb: Runbook, reads: Reads) -> tuple[dict, list[ReadFailure]]:
    results, failures = {}, []
    for read in rb.meta["reads"]:
        try:
            if "tool" in read:
                rows = reads.tool(read["tool"], resolve_args(read, getattr(reads, "scope", {}) or {}))
            else:
                rows = reads.kql(read["kql"])
            if not isinstance(rows, list):
                raise TypeError("a read returns a list of rows")
            results[read["id"]] = rows
        except Exception as error:  # a failed read is could-not-run, never clear
            results[read["id"]] = None
            failures.append(ReadFailure(read["id"], type(error).__name__))
    return results, failures


def run(rb: Runbook, reads: Reads, investigate: Investigate | None, started_by: str,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict:
    """One run of one runbook. The finding carries facts and counts, never the rows the reads returned."""
    if started_by not in STARTED_BY:
        raise ValueError(f"started_by must be one of {STARTED_BY}")
    started = now()
    results, failures = run_reads(rb, reads)
    evaluation = evaluate(rb, results)
    investigation = {"status": "not-needed"}
    if evaluation["investigate"]:
        if investigate is None:
            investigation = {"status": "not-available"}
        else:
            try:
                answer = investigate(rb, evaluation, results, float(rb.meta["budget"]["tokensUsd"]))
                investigation = {"status": "answered", "claims": answer.get("claims", []),
                                 "unanswered": answer.get("unanswered", [])}
                if answer.get("shield"):  # flagged or unscanned: a plan citing this needs the observer (security-tooling.md)
                    investigation["shield"] = answer["shield"]
            except Refused as refused:
                investigation = {"status": "refused", "reason": str(refused)[:200]}
            except Exception as error:
                investigation = {"status": "failed", "error": type(error).__name__}
    return {
        "runbook": evaluation["runbook"], "version": evaluation["version"], "outcome": evaluation["outcome"],
        "facts": evaluation["facts"], "failedReads": [f.read for f in failures],
        "readErrors": {f.read: f.error for f in failures}, "investigation": investigation,
        # Provenance (spec section 3a, V1-4): the gate refuses a plan whose only evidence is a run like this.
        "threadStartedBy": started_by, "startedAt": started.isoformat(), "finishedAt": now().isoformat(),
    }
