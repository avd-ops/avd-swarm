# avd-swarm agent API: the runbook endpoint's logic. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""POST /runbook {"runbook": "<id>", "startedBy": "schedule" | "manual"} -> a finding.

The agent API is the only process that calls a model (core-and-adapters.md section 3.4), so the runbook
endpoint lives there: it loads the runbook from the commit it was deployed from (never from the request),
runs it with the tool layer's reads, and hands `investigate` facts to the runbook's agent through the
gateway with the runbook's budget. Only the check runner's identity may call it (Entra app role, checked
by the caller's token before this function runs). This module is the endpoint's pure part; the web
framework and the token check sit around it.
"""

from __future__ import annotations

from typing import Callable

from .execute import STARTED_BY, Investigate, Reads, run
from .runbook import Runbook


class BadRequest(ValueError):
    pass


def handle(body: object, runbooks: dict[str, Runbook], reads: Reads, investigate: Investigate | None,
           allowed_agents: set[str] | None = None) -> dict:
    """Validates the request and runs the runbook. runbooks: the deployed commit's runbooks, by ID."""
    if not isinstance(body, dict) or set(body) - {"runbook", "startedBy"}:
        raise BadRequest("the body is {runbook, startedBy} and nothing else")
    runbook_id, started_by = body.get("runbook"), body.get("startedBy")
    if not isinstance(runbook_id, str) or runbook_id not in runbooks:
        raise BadRequest("unknown runbook")  # never echo the requested ID back
    if started_by not in STARTED_BY:
        raise BadRequest(f"startedBy must be one of {STARTED_BY}")
    rb = runbooks[runbook_id]
    if rb.meta["trigger"]["runMode"] != "read-only":
        raise BadRequest("only read-only runbooks run in v1")
    if allowed_agents is not None and rb.meta["agent"] not in allowed_agents:
        raise BadRequest(f"agent {rb.meta['agent']} isn't deployed")
    return run(rb, reads, investigate, started_by)


def load_all(root, parse: Callable | None = None) -> dict[str, Runbook]:
    """Every runbook under root/runbooks, by ID, parsed once at start-up."""
    from pathlib import Path

    from .runbook import parse as default_parse, shape_errors
    parse = parse or default_parse
    out = {}
    for path in sorted(Path(root, "runbooks").rglob("*.md")):
        if path.name == "README.md":
            continue
        rb = parse(str(path), path.read_text(encoding="utf-8"))
        if shape_errors(rb):
            raise ValueError(f"{path}: not a valid runbook; run scripts/check_runbooks.py")
        if rb.id in out:
            raise ValueError(f"{path}: runbook id {rb.id} is used twice")
        out[rb.id] = rb
    return out
