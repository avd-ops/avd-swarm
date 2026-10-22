#!/usr/bin/env python3
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Checks every runbook against the repo (core-and-adapters.md section 3.2; spec section 3c).

Per runbook: its own shape (runner/avd_runner/runbook.py), its path matches its ID, every tool read is in its
agent's allowlist (governance/allowlists/<agent>.yml), every KQL read is a catalog ID that agent may run, every
evidence ID is in the pinned C15 fact register and built, its budget fits the daily cap in
governance/gateway.yml, and `propose` waits for the action catalog (build step 3). Repo-wide: the catalog's
files exist, the vendored register matches its pin. Exit 1 on any finding.

Usage: python3 scripts/check_runbooks.py [repo root]
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runner"))
from avd_runner.runbook import RunbookError, parse, shape_errors  # noqa: E402

ACTION_CATALOG = "catalog/actions"  # build step 3; until it exists, runMode: propose is refused
RUNNER_TEMPLATE = "templates/workflows/runbooks.yml"
RUNNER_LIVE = ".github/workflows/runbooks.yml"


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def repo_errors(root: Path) -> tuple[list[str], dict]:
    """Checks of the shared inputs; returns the errors and what the runbook checks need."""
    errors = []
    pins = json.loads((root / "contracts/PINS.json").read_text(encoding="utf-8"))
    for source in pins["sources"].values():
        for name, digest in source["files"].items():
            actual = hashlib.sha256((root / "contracts" / name).read_bytes()).hexdigest()
            if actual != digest:
                errors.append(f"contracts/{name}: sha256 {actual} isn't the pinned {digest}; copy it from the source at the pinned commit")
    register = json.loads((root / "contracts/fact-register.json").read_text(encoding="utf-8"))
    facts = {f["id"]: f.get("status") for f in register["facts"]}

    catalog = load_yaml(root / "catalog/kql/catalog.yml").get("queries") or []
    kql_ids = set()
    for entry in catalog:
        if not (root / "catalog/kql" / entry["file"]).is_file():
            errors.append(f"catalog/kql/catalog.yml: {entry['id']} points at a missing file {entry['file']}")
        kql_ids.add(entry["id"])

    allowlists = {}
    for path in sorted((root / "governance/allowlists").glob("*.yml")):
        data = load_yaml(path)
        if data.get("agent") != path.stem:
            errors.append(f"{path.relative_to(root)}: agent must be {path.stem}")
        for kql in data.get("kql") or []:
            if kql not in kql_ids:
                errors.append(f"{path.relative_to(root)}: KQL {kql} isn't in the catalog")
        allowlists[path.stem] = {"tools": set(data.get("tools") or []), "kql": set(data.get("kql") or [])}

    daily = float(load_yaml(root / "governance/gateway.yml")["caps"]["dailyUsd"])
    return errors, {"facts": facts, "kql": kql_ids, "allowlists": allowlists, "daily": daily,
                    "actions": (root / ACTION_CATALOG).is_dir()}


def runbook_errors(root: Path, path: Path, ctx: dict) -> list[str]:
    rel = path.relative_to(root).as_posix()
    try:
        rb = parse(rel, path.read_text(encoding="utf-8"))
    except (RunbookError, yaml.YAMLError) as error:
        return [f"{rel}: {error}"]
    errors = shape_errors(rb)
    if errors:
        return [f"{rel}: {e}" for e in errors]
    m = rb.meta
    expected = f"runbooks/{m['agent']}/{m['id'].split('.', 1)[1]}.md"
    if rel != expected:
        errors.append(f"id {m['id']} belongs at {expected}")
    allow = ctx["allowlists"].get(m["agent"])
    if allow is None:
        errors.append(f"agent {m['agent']} has no allowlist in governance/allowlists/")
    else:
        for read in m["reads"]:
            if "tool" in read and read["tool"] not in allow["tools"]:
                errors.append(f"read {read['id']}: tool {read['tool']} isn't in the {m['agent']} allowlist")
            if "kql" in read:
                if read["kql"] not in ctx["kql"]:
                    errors.append(f"read {read['id']}: KQL {read['kql']} isn't in catalog/kql/catalog.yml (inline KQL is never allowed)")
                elif read["kql"] not in allow["kql"]:
                    errors.append(f"read {read['id']}: KQL {read['kql']} isn't in the {m['agent']} allowlist")
                if "kql_catalog_run" not in allow["tools"]:
                    errors.append(f"agent {m['agent']} can't run KQL: kql_catalog_run isn't in its allowlist")
    for fact in m.get("evidence", []):
        status = ctx["facts"].get(fact)
        if status is None:
            errors.append(f"evidence {fact} isn't in the pinned C15 fact register")
        elif status != "built":
            errors.append(f"evidence {fact} is {status} in the register, not built yet")
    if m["budget"]["tokensUsd"] > ctx["daily"]:
        errors.append(f"budget ${m['budget']['tokensUsd']} is over the daily cap ${ctx['daily']} in governance/gateway.yml")
    if m["trigger"]["runMode"] == "propose" and not ctx["actions"]:
        errors.append("runMode: propose waits for the action catalog (build step 3); v1 runbooks are read-only")
    return [f"{rel}: {e}" for e in errors]


def template_crons(root: Path, rel: str = RUNNER_TEMPLATE) -> set[str] | None:
    path = root / rel
    if not path.is_file():
        return None
    workflow = load_yaml(path)
    on = workflow.get("on", workflow.get(True)) or {}  # PyYAML reads the bare key `on` as True
    return {" ".join(str(c.get("cron", "")).split()) for c in (on.get("schedule") or [])}


def check(root: Path) -> list[str]:
    errors, ctx = repo_errors(root)
    paths = sorted(p for p in (root / "runbooks").rglob("*.md") if p.name != "README.md")
    ids, schedules = {}, set()
    for path in paths:
        errors += runbook_errors(root, path, ctx)
    for path in paths:
        try:
            meta = parse(str(path), path.read_text(encoding="utf-8")).meta
        except (RunbookError, yaml.YAMLError):
            continue
        rid = meta.get("id")
        if rid in ids:
            errors.append(f"{path.relative_to(root)}: id {rid} is also {ids[rid]}")
        ids[rid] = path.relative_to(root)
        trigger = meta.get("trigger") if isinstance(meta.get("trigger"), dict) else {}
        if "schedule" in trigger:
            schedules.add(" ".join(str(trigger["schedule"]).split()))
    for rel in (RUNNER_TEMPLATE, RUNNER_LIVE):  # the template here, and the adopter's live copy
        crons = template_crons(root, rel)
        if crons is None:
            continue
        for cron in sorted(schedules - crons):
            errors.append(f"{rel}: no cron for \"{cron}\", so a runbook with that schedule would never run")
        for cron in sorted(crons - schedules):
            errors.append(f"{rel}: cron \"{cron}\" starts no runbook")
    return errors


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1]
    errors = check(root)
    for e in errors:
        print(f"::error::{e}")
    count = len([p for p in (root / "runbooks").rglob("*.md") if p.name != "README.md"])
    print(f"{count} runbooks checked, {len(errors)} findings.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
