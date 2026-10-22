# avd-swarm S1a spike agent API: the runbook endpoint. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""POST /runbook on the spike's agent API (core-and-adapters.md WP2; spec section 3c).

The check runner (`python -m avd_runner run`) and the alert job (`python -m avd_runner alert-job`) call it; the
operator can too, from Cloud Shell, for a manual run. This module is the endpoint's logic with its Azure and
model calls passed in, so it is tested offline (scripts/tests/test_runbook_endpoint.py); main.py wires it to
the MCP server, Log Analytics and Claude. It imports nothing the spike's container doesn't already have.

- **Who may call, and as what.** The operator's own token (the same check as /ask) may start `manual` runs.
  An app token may call only with the `runbook.run` role and an object ID listed in RUNBOOK_CALLERS, each
  caller with its own startedBy values (`<oid>=schedule|manual,<oid>=alert`): the runner can't claim an
  alert, and the alert job can't claim a schedule.
- **Only the Hosts agent's runbooks.** This API deploys one agent; a runbook for another is refused (400).
- **Reads.** A tool read goes to the MCP server as the agent identity, only for a tool in the Hosts allowlist,
  with the runbook's args filled from this deployment's scope. A KQL read runs the catalog query by ID against
  LOG_WORKSPACE_ID as the agent identity. Rows go to code; a model sees them only when a fact asks to
  investigate, as documents the shield scans (enforce withholds a flagged one), apart from the instructions.
- **Budget.** An investigation gets its own meter capped at the runbook's `budget.tokensUsd`, on top of the
  run's meter and the kill switch: whichever refuses first wins, and the finding says `refused`.
- **Deadline.** Every call shares one DEADLINE_SECONDS, under the Container Apps ingress timeout (240 s), so the
  caller always gets the finding this API keeps: a read past it fails (could-not-run when a fact needs it), an
  investigation past it is cancelled and marked failed.
- **Findings** stay in memory (the plan store is build step 3) and are listed at GET /runbook/findings for the
  operator. The log gets the summary only (report.summary: no names).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from avd_runner import api, report
from avd_runner.execute import STARTED_BY

ROLE = "runbook.run"
KEEP = 100  # findings kept in memory
DEADLINE_SECONDS = 200  # the whole request, under the ingress's 240 s
AGENTS = {"hosts"}  # the agents this API deploys


def parse_callers(text: str) -> dict[str, set[str]]:
    """RUNBOOK_CALLERS: `<oid>=schedule|manual,<oid>=alert`. Unknown startedBy values are refused at start."""
    out: dict[str, set[str]] = {}
    for part in (p.strip() for p in text.split(",") if p.strip()):
        oid, _, kinds = part.partition("=")
        allowed = {k.strip() for k in kinds.split("|") if k.strip()}
        if not oid.strip() or not allowed or allowed - set(STARTED_BY):
            raise ValueError(f"RUNBOOK_CALLERS entry {part!r}: want <object id>=<one or more of {'|'.join(STARTED_BY)}>")
        out[oid.strip()] = allowed
    return out


def allowed_started_by(claims: dict, operator_oid: str, callers: dict[str, set[str]]) -> set[str]:
    """What this verified token may start: {} when nothing."""
    oid = claims.get("oid", "")
    if oid == operator_oid and "Mcp.Tools.ReadWrite" in str(claims.get("scp", "")).split():
        return {"manual"}
    if ROLE in (claims.get("roles") or []) and oid in callers:
        return set(callers[oid])
    return set()


def catalog_queries(root: Path) -> dict[str, str]:
    """The KQL catalog's query text by ID."""
    import yaml
    catalog = yaml.safe_load((root / "catalog/kql/catalog.yml").read_text(encoding="utf-8"))
    return {q["id"]: (root / "catalog/kql" / q["file"]).read_text(encoding="utf-8") for q in catalog["queries"]}


def rows_from_tool_text(text: str) -> list[dict]:
    """A tool's text result as rows: a JSON list of objects, or objects (an envelope like {status, results: {...}})
    holding exactly one list at any depth."""
    data = json.loads(text)
    if isinstance(data, dict):
        lists, objects = [], [data]
        while objects:
            for value in objects.pop().values():
                if isinstance(value, list):
                    lists.append(value)
                elif isinstance(value, dict):
                    objects.append(value)
        if len(lists) != 1:
            raise TypeError("the tool's result holds no single list of rows")
        data = lists[0]
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise TypeError("the tool's result isn't a list of rows")
    return data


def rows_from_log_analytics(body: dict) -> list[dict]:
    """The Log Analytics query API's first table as rows."""
    table = (body.get("tables") or [None])[0]
    if not table:
        raise TypeError("Log Analytics returned no table")
    names = [c["name"] for c in table["columns"]]
    return [dict(zip(names, row)) for row in table["rows"]]


@dataclass
class Deps:
    """What the endpoint needs from the running app. Each async callable raises on failure."""
    scope: dict
    allowed_tools: list[str]
    call_tool: Callable[[str, dict], Awaitable[str]]          # MCP, as the agent identity: the result's text
    query_logs: Callable[[str], Awaitable[dict]]              # Log Analytics, as the agent identity: the JSON body
    # (instructions, rows as JSON, budget USD) -> {"claims", "unanswered", "shield"}; the rows are scanned as documents
    investigate: Callable[[str, str, float], Awaitable[dict]]
    queries: dict[str, str]
    findings: list = field(default_factory=list)
    log: Callable[[str], None] = print


class Reads:
    """execute.Reads for the endpoint: runs in a worker thread and hands each call to the app's event loop."""

    def __init__(self, deps: Deps, run: Callable[[Awaitable], object]):
        self.deps, self.run, self.scope = deps, run, deps.scope

    def tool(self, name: str, args: dict) -> list[dict]:
        if name not in self.deps.allowed_tools:
            raise PermissionError("tool not in the Hosts allowlist")
        return rows_from_tool_text(self.run(self.deps.call_tool(name, args)))

    def kql(self, query_id: str) -> list[dict]:
        if query_id not in self.deps.queries:
            raise KeyError("not a catalog query")
        return rows_from_log_analytics(self.run(self.deps.query_logs(self.deps.queries[query_id])))


def investigation(rb, evaluation: dict, results: dict) -> tuple[str, str]:
    """The question for the runbook's agent and, apart, every read's rows (a failed read is null): the rows are
    untrusted, so they go to the shield as documents; the runbook's text comes from the deployed commit."""
    failing = [f for f in evaluation["facts"] if f["fact"] in evaluation["investigate"]]
    instructions = (f"Runbook {rb.id} v{rb.meta['version']} ({rb.meta['title']}) found facts that don't hold:\n"
                    f"{json.dumps(failing, default=str)}\n\nThe runbook says:\n{rb.body}\n\n"
                    "The rows of every read, by read ID, follow as a document. Answer what each failing fact means, "
                    "citing the reads. Propose nothing.")
    return instructions, json.dumps(results, default=str)


def handle(body: object, claims: dict, operator_oid: str, callers: dict[str, set[str]], runbooks: dict, deps: Deps,
           run: Callable[[Awaitable], object]) -> tuple[int, dict]:
    """(status, JSON body). Runs synchronously; main.py calls it in a worker thread. run(coroutine) runs one call on
    the app's event loop; here each call is held to what is left of the request's deadline."""
    deadline = time.monotonic() + DEADLINE_SECONDS

    def bounded(coroutine):
        left = deadline - time.monotonic()
        if left <= 0:
            coroutine.close()
            raise TimeoutError("past the request's deadline")
        return run(asyncio.wait_for(coroutine, left))

    allowed = allowed_started_by(claims, operator_oid, callers)
    if not allowed:
        return 403, {"error": "this caller may not start runbooks"}
    if isinstance(body, dict) and body.get("startedBy") in STARTED_BY and body.get("startedBy") not in allowed:
        return 403, {"error": f"this caller may start {sorted(allowed)} runs only"}

    def investigate(rb, evaluation, results, budget):
        return bounded(deps.investigate(*investigation(rb, evaluation, results), budget))

    try:
        finding = api.handle(body, runbooks, Reads(deps, bounded), investigate, allowed_agents=AGENTS)
    except api.BadRequest as error:
        return 400, {"error": str(error)}
    deps.findings.append(finding)
    del deps.findings[:-KEEP]
    deps.log(report.summary(finding))
    return 200, finding
