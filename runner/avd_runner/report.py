# avd-swarm runner: what a run reports and where. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Issue on change, a summary with no estate data, and the counts for the brain (core-and-adapters.md section 3.3).

A standing problem makes one issue, not one a day: the runner opens an issue when a runbook stops being
clear, comments when the outcome changes while it is open, and closes it when the runbook is clear again.
Job summaries and issues carry the runbook ID, the outcome and fact values only: never a host, user or
resource name, even in the private repo (R17). The finding itself, with the agent's claims, goes to the
plan store and avd-buddy behind the operator's sign-in.
"""

from __future__ import annotations

from collections import Counter

OUTCOMES = ("clear", "finding", "could-not-run")


def issue_action(open_issue_outcome: str | None, outcome: str) -> str:
    """open, comment, close or none. open_issue_outcome is the outcome recorded on the runbook's open issue, or None
    when there is no open issue. An open issue whose outcome can't be read (an edited body) passes "unknown": it is
    still an issue, so it is commented on or closed, never duplicated."""
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}")
    if open_issue_outcome is None:
        return "none" if outcome == "clear" else "open"
    if outcome == "clear":
        return "close"
    return "none" if outcome == open_issue_outcome else "comment"


def issue_title(runbook: str) -> str:
    return f"Runbook {runbook} is not clear"


def summary(finding: dict) -> str:
    """Markdown for the job summary and the issue: ID, outcome, each fact's value. Nothing from the rows."""
    lines = [f"**{finding['runbook']}** v{finding['version']}: `{finding['outcome']}` "
             f"(started by {finding['threadStartedBy']})", "", "| Fact | Value | Expected | Status |", "|---|---|---|---|"]
    for fact in finding["facts"]:
        value = fact.get("value", "")
        lines.append(f"| {fact['fact']} | {value} | {fact['op']} {fact['expected']} | {fact['status']} |")
    if finding["failedReads"]:
        lines += ["", "Reads that failed: " + ", ".join(f"{r} ({finding['readErrors'][r]})" for r in finding["failedReads"])]
    status = finding["investigation"]["status"]
    if status == "answered":
        lines += ["", f"The agent answered with {len(finding['investigation']['claims'])} claims; read them in avd-buddy."]
    elif status != "not-needed":
        lines += ["", f"Investigation: {status}."]
    return "\n".join(lines)


def checks_block(findings: list[dict]) -> dict:
    """Counts per runbook and outcome for the brain (proposed C8 `checks` block): no names, no free text."""
    counts = Counter((f["runbook"], f["outcome"]) for f in findings)
    started = Counter(f["threadStartedBy"] for f in findings)
    runbooks = sorted({r for r, _ in counts})
    return {"runs": len(findings), "startedBy": dict(sorted(started.items())),
            "runbooks": [{"id": r, **{o: counts.get((r, o), 0) for o in OUTCOMES}} for r in runbooks]}
