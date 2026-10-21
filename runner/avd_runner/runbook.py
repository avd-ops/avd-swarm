# avd-swarm runner: runbooks as code. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Parse a runbook, check its shape, and evaluate its expected facts (core-and-adapters.md section 3.2).

A runbook is runbooks/<agent>/<name>.md: Markdown a person reads under a YAML front matter code reads. The
steps are fixed; the model only interprets what they return. The runner checks the `expect` facts in code
before a model sees anything, so a clear run costs no tokens at all.

Pure functions: the repo checks (allowlists, the KQL catalog, the fact register) are in
scripts/check_runbooks.py, and the runner (WP2) calls evaluate() with the reads' results.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

TOP_KEYS = {"id", "version", "title", "trigger", "agent", "reads", "expect", "evidence", "cites", "budget", "proposes"}
REQUIRED = ("id", "version", "title", "trigger", "agent", "reads", "expect", "budget")
TRIGGER_KINDS = ("schedule", "alert", "manual")
RUN_MODES = ("read-only", "propose")
OPS = {"eq": lambda a, b: a == b, "ne": lambda a, b: a != b, "lt": lambda a, b: a < b,
       "le": lambda a, b: a <= b, "gt": lambda a, b: a > b, "ge": lambda a, b: a >= b}
ON_FAIL = ("investigate", "report")
ID = re.compile(r"^[a-z][a-z0-9]*\.[a-z0-9][a-z0-9-]*$")
NAME = re.compile(r"^[a-z][A-Za-z0-9_-]*$")
MEASURE = re.compile(r"^(rows|(sum|max|min):[A-Za-z][A-Za-z0-9_]*)$")
CRON_FIELD = re.compile(r"^[0-9*,/-]+$")
CITE = re.compile(r"^([\w.-]+/[\w.-]+@[0-9a-f]{40}:[\w./-]+(#[\w-]+)?|https://learn\.microsoft\.com/\S+)$")


class RunbookError(ValueError):
    pass


@dataclass
class Runbook:
    path: str
    meta: dict
    body: str

    @property
    def id(self) -> str:
        return self.meta["id"]

    @property
    def trigger_kind(self) -> str:
        return next(k for k in TRIGGER_KINDS if k in self.meta["trigger"])


def split(text: str) -> tuple[str, str]:
    """(front matter, body). The file must start with a line '---' and close it with another."""
    if not text.startswith("---\n"):
        raise RunbookError("a runbook starts with a '---' front matter line")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise RunbookError("the front matter has no closing '---' line")
    return text[4:end], text[end + 5:]


def parse(path: str, text: str) -> Runbook:
    import yaml
    head, body = split(text)
    meta = yaml.safe_load(head)
    if not isinstance(meta, dict):
        raise RunbookError("the front matter must be a mapping")
    return Runbook(path, meta, body)


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def shape_errors(rb: Runbook) -> list[str]:
    """Everything wrong with a runbook's own shape, without reading anything else in the repo."""
    m, errors = rb.meta, []
    errors += [f"unknown key {k!r}" for k in sorted(set(m) - TOP_KEYS)]
    errors += [f"missing {k!r}" for k in REQUIRED if k not in m]
    if errors:
        return errors

    if not isinstance(m["agent"], str) or not NAME.match(m["agent"]):
        errors.append("agent must be an agent's name, like hosts")
    if not isinstance(m["id"], str) or not ID.match(m["id"]):
        errors.append("id must be <agent>.<name> in lowercase, like hosts.agent-unhealthy")
    elif m["id"].split(".")[0] != m["agent"]:
        errors.append(f"id {m['id']} must start with its agent, {m['agent']}")
    if not isinstance(m["version"], int) or isinstance(m["version"], bool) or m["version"] < 1:
        errors.append("version must be a whole number from 1")
    if not isinstance(m["title"], str) or not m["title"].strip():
        errors.append("title must be text")

    trigger = m["trigger"] if isinstance(m["trigger"], dict) else {}
    kinds = [k for k in TRIGGER_KINDS if k in trigger]
    extra = sorted(set(trigger) - set(TRIGGER_KINDS) - {"runMode"})
    if len(kinds) != 1:
        errors.append("trigger needs exactly one of schedule, alert or manual")
    if extra:
        errors.append(f"trigger has unknown keys {extra}")
    if "schedule" in trigger:
        fields = str(trigger["schedule"]).split()
        if len(fields) != 5 or not all(CRON_FIELD.match(f) for f in fields):
            errors.append("trigger.schedule must be a five-field cron expression (UTC, as GitHub Actions reads it)")
    if "alert" in trigger and (not isinstance(trigger["alert"], str) or not trigger["alert"].strip()):
        errors.append("trigger.alert must name the alert rule pattern it answers")
    if "manual" in trigger and trigger["manual"] is not True:
        errors.append("trigger.manual must be true")
    mode = trigger.get("runMode")
    if not isinstance(mode, str) or mode not in RUN_MODES:
        errors.append(f"trigger.runMode must be one of {RUN_MODES}")
    if "proposes" in m and mode != "propose":
        errors.append("proposes needs runMode: propose")
    if mode == "propose" and (not isinstance(m.get("proposes"), str) or not m.get("proposes")):
        errors.append("runMode: propose names exactly one catalog action in proposes")

    reads = m["reads"] if isinstance(m["reads"], list) else []
    if not reads:
        errors.append("reads must list at least one read")
    seen = set()
    for i, read in enumerate(reads):
        where = f"reads[{i}]"
        if not isinstance(read, dict):
            errors.append(f"{where} must be a mapping")
            continue
        if set(read) - {"id", "tool", "kql"}:
            errors.append(f"{where} has unknown keys {sorted(set(read) - {'id', 'tool', 'kql'})}")
        if ("tool" in read) == ("kql" in read):
            errors.append(f"{where} needs exactly one of tool or kql")
        elif not _text(read.get("tool", read.get("kql"))):
            errors.append(f"{where}: tool or kql must be a name")
        rid = read.get("id")
        if not isinstance(rid, str) or not NAME.match(rid):
            errors.append(f"{where} needs an id")
        elif rid in seen:
            errors.append(f"{where} id {rid} is used twice")
        seen.add(rid)

    expect = m["expect"] if isinstance(m["expect"], list) else []
    if not expect:
        errors.append("expect must list at least one fact the runner checks in code")
    for i, fact in enumerate(expect):
        where = f"expect[{i}]"
        if not isinstance(fact, dict):
            errors.append(f"{where} must be a mapping")
            continue
        if set(fact) - {"fact", "from", "measure", "op", "value", "onFail"}:
            errors.append(f"{where} has unknown keys")
        if not isinstance(fact.get("fact"), str) or not NAME.match(fact.get("fact", "")):
            errors.append(f"{where} needs a fact name")
        if not isinstance(fact.get("from"), str) or fact["from"] not in seen:
            errors.append(f"{where}.from must name one of this runbook's reads")
        if not isinstance(fact.get("measure"), str) or not MEASURE.match(fact["measure"]):
            errors.append(f"{where}.measure must be rows, or sum:, max: or min: and a column")
        if not isinstance(fact.get("op"), str) or fact["op"] not in OPS:
            errors.append(f"{where}.op must be one of {sorted(OPS)}")
        if not _number(fact.get("value")):
            errors.append(f"{where}.value must be a number")
        if not isinstance(fact.get("onFail"), str) or fact["onFail"] not in ON_FAIL:
            errors.append(f"{where}.onFail must be one of {ON_FAIL}")

    evidence = m.get("evidence", [])
    if not isinstance(evidence, list) or not all(isinstance(e, str) for e in evidence):
        errors.append("evidence must be a list of C15 fact register IDs")
    cites = m.get("cites", [])
    if not isinstance(cites, list) or not all(isinstance(c, str) and CITE.match(c) for c in cites):
        errors.append("cites must be <owner>/<repo>@<40-hex commit>:<path>[#anchor] or a Microsoft Learn link")

    budget = m["budget"] if isinstance(m["budget"], dict) else {}
    if set(budget) != {"tokensUsd"} or not _number(budget.get("tokensUsd")) or budget["tokensUsd"] <= 0:
        errors.append("budget must be {tokensUsd: <positive number>}")

    if not re.search(r"^# \S", rb.body, re.MULTILINE):
        errors.append("the body needs a '# ' title a person reads")
    return errors


def measure(rows: list[dict], how: str) -> float:
    """rows, or sum:, max: or min: of a column; a missing or non-numeric value is an error, never a zero."""
    if how == "rows":
        return len(rows)
    kind, column = how.split(":", 1)
    values = []
    for row in rows:
        value = row.get(column)
        if not _number(value):
            raise RunbookError(f"column {column} is missing or not a number")
        values.append(value)
    if kind == "sum":
        return sum(values)
    if not values:
        raise RunbookError(f"{kind} of no rows")
    return max(values) if kind == "max" else min(values)


def evaluate(rb: Runbook, results: dict[str, list[dict] | None]) -> dict:
    """Checks every expected fact against the reads' results (None for a read that failed).

    Outcome: `clear` when every fact holds, `finding` when one doesn't, `could-not-run` when a read
    failed or a fact couldn't be measured (missing data never passes). `investigate` lists the facts
    whose onFail asks the agent to look; only those reach a model."""
    facts, failed_reads = [], [r["id"] for r in rb.meta["reads"] if results.get(r["id"]) is None]
    for fact in rb.meta["expect"]:
        entry = {"fact": fact["fact"], "op": fact["op"], "expected": fact["value"]}
        rows = results.get(fact["from"])
        if rows is None:
            entry.update(status="unknown", reason=f"read {fact['from']} failed")
        else:
            try:
                value = measure(rows, fact["measure"])
                holds = OPS[fact["op"]](value, fact["value"])
                entry.update(value=value, status="holds" if holds else "fails", onFail=fact["onFail"])
            except RunbookError as error:
                entry.update(status="unknown", reason=str(error))
        facts.append(entry)
    if failed_reads or any(f["status"] == "unknown" for f in facts):
        outcome = "could-not-run"
    elif any(f["status"] == "fails" for f in facts):
        outcome = "finding"
    else:
        outcome = "clear"
    return {"runbook": rb.id, "version": rb.meta["version"], "outcome": outcome, "facts": facts,
            "failedReads": failed_reads,
            "investigate": [f["fact"] for f in facts if f["status"] == "fails" and f.get("onFail") == "investigate"]}
