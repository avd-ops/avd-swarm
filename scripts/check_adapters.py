#!/usr/bin/env python3
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Checks every adapter manifest (core-and-adapters.md section 3.6; spec section 3b; decision 0009).

An adapter is a paid or closed service an adopter may add to the open-source core. Each manifest must be off by
default, add to a core capability and replace none, write nothing, name no exit test, declare its meters with a
source, name the Policy deny that keeps its identity read-only and the watchdog row behind it (red team P-5),
and say how every kill switch position reaches it and what it can't reach. Repo-wide: no CI workflow or eval
depends on an adapter, and every adapter the gateway's providers need is registered here.

Usage: python3 scripts/check_adapters.py [repo root]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

KINDS = ("investigator", "incident", "scheduler", "gateway", "model-host", "policy-engine")
CORE = ("investigate", "scheduled-checks", "alert-triggered-runs", "model-gateway", "tool-governance",
        "model-host", "policy-evaluation")
STATUSES = ("designed", "built")
POSITIONS = ("run", "halt-actions", "halt-agents", "halt-all")
FIELDS = {"name", "kind", "status", "addsTo", "replaces", "default", "meters", "identity", "switch", "events",
          "writes", "exitTests"}
EVENT = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)+$")


def text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def manifest_errors(name: str, m: dict) -> list[str]:
    if not isinstance(m, dict):
        return ["adapter.yml must be a mapping"]
    errors = [f"unknown key {k!r}" for k in sorted(set(m) - FIELDS)]
    errors += [f"missing {k!r}" for k in sorted(FIELDS - set(m))]
    if errors:
        return errors
    if m["name"] != name:
        errors.append(f"name must be the folder's, {name}")
    if m["kind"] not in KINDS:
        errors.append(f"kind must be one of {KINDS}")
    if m["status"] not in STATUSES:
        errors.append(f"status must be one of {STATUSES}")
    if m["addsTo"] not in CORE:
        errors.append(f"addsTo must name a core capability, one of {CORE}")
    if m["replaces"] != []:
        errors.append("an adapter adds to the core and never replaces it: replaces must be []")
    # YAML 1.1 reads a bare off as False; both spellings mean off, anything else is refused.
    if m["default"] not in (False, "off"):
        errors.append("default must be off: every adapter is off until an adopter estimates it and turns it on")
    if m["writes"] != "none":
        errors.append("writes must be none: every write an adapter wants comes back as propose_change through the gate")
    if m["exitTests"] != []:
        errors.append("exitTests must be []: no v1 exit test, CI job or eval may depend on an adapter")

    meters = m["meters"] if isinstance(m["meters"], list) else []
    if not meters:
        errors.append("meters must list what it bills on, so the spend estimate is written from the manifest")
    for i, meter in enumerate(meters):
        meter = meter if isinstance(meter, dict) else {}
        for key in ("service", "meter"):
            if not text(meter.get(key)):
                errors.append(f"meters[{i}].{key} is required")
        if not isinstance(meter.get("standing"), bool):
            errors.append(f"meters[{i}].standing must say whether it bills while idle (true or false)")
        idle = meter.get("monthlyUsdIdle")
        if idle is not None and (isinstance(idle, bool) or not isinstance(idle, (int, float)) or idle < 0):
            errors.append(f"meters[{i}].monthlyUsdIdle must be a number or null")
        if not (text(meter.get("source")) and meter["source"].startswith("https://")):
            errors.append(f"meters[{i}].source must be an https link to the published price")

    identity = m["identity"] if isinstance(m["identity"], dict) else {}
    for key in ("runsAs", "policyDeny", "watchdog"):
        if not text(identity.get(key)):
            errors.append(f"identity.{key} is required (red team P-5: writes: none is enforced by Azure, not trusted)")
    if not isinstance(identity.get("mustNotHold"), list) or not identity["mustNotHold"]:
        errors.append("identity.mustNotHold must list the roles it may never hold")
    elif not {"Owner", "Contributor"} <= set(identity["mustNotHold"]):
        errors.append("identity.mustNotHold must include Owner and Contributor")

    switch = m["switch"] if isinstance(m["switch"], dict) else {}
    for key in (*POSITIONS, "residual"):
        if not text(switch.get(key)):
            errors.append(f"switch.{key} must say what that position does to it" if key != "residual"
                          else "switch.residual must state what the kill switch can't reach")
    if set(switch) - {*POSITIONS, "residual"}:
        errors.append(f"switch has unknown keys {sorted(set(switch) - {*POSITIONS, 'residual'})}")

    events = m["events"] if isinstance(m["events"], list) else None
    if events is None or not all(isinstance(e, str) and EVENT.match(e) for e in events):
        errors.append("events must be a list of dotted event names (counts only, governance section 2.2)")
    return errors


def check(root: Path) -> list[str]:
    errors, names = [], set()
    adapters = root / "adapters"
    for folder in sorted(p for p in adapters.iterdir() if p.is_dir()) if adapters.is_dir() else []:
        rel = folder.relative_to(root).as_posix()
        manifest = folder / "adapter.yml"
        if not manifest.is_file():
            errors.append(f"{rel}: no adapter.yml")
            continue
        if not (folder / "README.md").is_file():
            errors.append(f"{rel}: no README.md, the page a person reads before turning it on")
        names.add(folder.name)
        try:
            data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            errors.append(f"{rel}/adapter.yml: {error}")
            continue
        errors += [f"{rel}/adapter.yml: {e}" for e in manifest_errors(folder.name, data)]

    # Nothing in CI or the evals depends on an adapter.
    dependents = list((root / ".github/workflows").glob("*.y*ml")) + [p for p in (root / "evals").rglob("*") if p.is_file()]
    for path in dependents:
        body = path.read_text(encoding="utf-8", errors="replace")
        for name in sorted(names):
            if f"adapters/{name}" in body:
                errors.append(f"{path.relative_to(root)}: depends on adapters/{name}; no CI job or eval may")

    # Every adapter the gateway's providers need is a registered model-host adapter.
    if (root / "gateway/avd_gateway/providers.py").is_file():
        sys.path.insert(0, str(root / "gateway"))
        try:
            from avd_gateway.providers import PROVIDERS
        finally:
            sys.path.pop(0)
        for provider in PROVIDERS.values():
            if provider.adapter and provider.adapter not in names:
                errors.append(f"gateway/avd_gateway/providers.py: {provider.name} needs adapter {provider.adapter}, which has no manifest")
    return errors


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1]
    errors = check(root)
    for e in errors:
        print(f"::error::{e}")
    count = len([p for p in (root / "adapters").iterdir() if p.is_dir()]) if (root / "adapters").is_dir() else 0
    print(f"{count} adapters checked, {len(errors)} findings.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
