#!/usr/bin/env python3
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Checks every action manifest in catalog/actions/ against spec section 5a and governance section 2.5.

Enforces:
- Required S2 action manifests exist and match their filename stem.
- Closed action kinds (`host`, `power`, `group`, `deploy-pr`, `recovery`), defaultWhyKind, and complianceEffect.
- All 9 section 5a host/power preconditions (`notDrainedByOthers`, `restoreDrainStateFound`, `noActiveSessions`,
  `notPowerLocked`, `notScalingExcluded`, `deallocatedNotUnhealthy`, `hostPoolAvailableHostsAbove`,
  `oneHostPerPoolAtATime`, `serviceHealthClear`).
- Rate limits (`perHour`, `perHostPerDay`) and 24-hour circuit breaker (`circuitBreakerFailures24h: 3`).
- Positive verification (`positiveEvidenceRequired: true` with non-empty `fields`) and rollback (`restoreStateFound: true`).
- Run Command scripts exist under `catalog/actions/scripts/`, start with `#Requires -Version 5.1`, match their pinned
  SHA-256 digest, declare a dedicated identity, and require `deleteRunCommandAfter: true`.
- `host.reregister` requires `secretsInActionOnly: true`.

Usage: python3 scripts/check_actions.py [repo root]
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import yaml

ACTION_DIR = "catalog/actions"
REQUIRED_S2_ACTIONS = {
    "host.drain",
    "host.restart-agent",
    "host.recycle-empty",
    "host.reregister",
    "host.stop",
    "host.resume",
}
KINDS = {"host", "power", "group", "deploy-pr", "recovery"}
WHY_KINDS = {"build", "configure", "expand", "contract", "fix", "recover", "retire"}
COMPLIANCE_RE = re.compile(r"^(none|(improves|may-lower)\s+\S.*)$")
ALLOWED_KEYS = {
    "id",
    "kind",
    "title",
    "defaultWhyKind",
    "complianceEffect",
    "preconditions",
    "limits",
    "verify",
    "rollback",
    "runCommand",
    "secretsInActionOnly",
}
REQUIRED_PRECONDITIONS = {
    "notDrainedByOthers": True,
    "restoreDrainStateFound": True,
    "notPowerLocked": True,
    "notScalingExcluded": True,
    "deallocatedNotUnhealthy": True,
    "hostPoolAvailableHostsAbove": "demand-plus-one-or-min",
    "oneHostPerPoolAtATime": True,
    "serviceHealthClear": "AVD",
}
ZERO_SESSION_ACTIONS = {
    "host.restart-agent",
    "host.recycle-empty",
    "host.reregister",
    "host.stop",
}


def load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def action_errors(root: Path, path: Path) -> list[str]:
    rel = path.relative_to(root).as_posix()
    try:
        data = load_yaml(path)
    except yaml.YAMLError as error:
        return [f"{rel}: invalid YAML: {error}"]
    if not data:
        return [f"{rel}: empty or non-mapping action manifest"]

    errors: list[str] = []
    unknown = sorted(set(data) - ALLOWED_KEYS)
    if unknown:
        errors.append(f"unknown key(s): {', '.join(unknown)}")

    action_id = data.get("id")
    if action_id != path.stem:
        errors.append(f"id {action_id!r} must match filename stem {path.stem!r}")

    kind = data.get("kind")
    if kind not in KINDS:
        errors.append(f"kind {kind!r} must be one of {sorted(KINDS)}")

    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append("title must be non-empty text")

    why_kind = data.get("defaultWhyKind")
    if why_kind not in WHY_KINDS:
        errors.append(f"defaultWhyKind {why_kind!r} must be one of {sorted(WHY_KINDS)}")

    compliance = data.get("complianceEffect")
    if not isinstance(compliance, str) or not COMPLIANCE_RE.match(compliance):
        errors.append("complianceEffect must be 'none', 'improves <controls>', or 'may-lower <controls>'")

    pre = data.get("preconditions")
    if not isinstance(pre, dict):
        errors.append("preconditions must be a mapping")
    elif kind in {"host", "power"}:
        for key, expected in REQUIRED_PRECONDITIONS.items():
            if pre.get(key) != expected:
                errors.append(f"preconditions.{key} must be {expected!r} (spec section 5a)")
        if not isinstance(pre.get("noActiveSessions"), bool):
            errors.append("preconditions.noActiveSessions must be a boolean")
        elif action_id in ZERO_SESSION_ACTIONS and pre.get("noActiveSessions") is not True:
            errors.append(f"{action_id} requires preconditions.noActiveSessions: true")
        extra_pre = sorted(set(pre) - set(REQUIRED_PRECONDITIONS) - {"noActiveSessions"})
        if extra_pre:
            errors.append(f"preconditions has unknown key(s): {', '.join(extra_pre)}")

    limits = data.get("limits")
    if not isinstance(limits, dict):
        errors.append("limits must be a mapping")
    else:
        for key in ("perHour", "perHostPerDay"):
            val = limits.get(key)
            if isinstance(val, bool) or not isinstance(val, int) or val < 1:
                errors.append(f"limits.{key} must be a positive integer")
        cb = limits.get("circuitBreakerFailures24h")
        if isinstance(cb, bool) or not isinstance(cb, int) or cb < 1 or cb > 3:
            errors.append("limits.circuitBreakerFailures24h must be an integer between 1 and 3 (spec section 5a)")

    verify = data.get("verify")
    if not isinstance(verify, dict):
        errors.append("verify must be a mapping")
    else:
        if verify.get("positiveEvidenceRequired") is not True:
            errors.append("verify.positiveEvidenceRequired must be true (zero errors is not enough)")
        fields = verify.get("fields")
        if not isinstance(fields, list) or not fields or not all(isinstance(f, str) and f.strip() for f in fields):
            errors.append("verify.fields must be a non-empty list of field names")

    rollback = data.get("rollback")
    if not isinstance(rollback, dict):
        errors.append("rollback must be a mapping")
    else:
        if rollback.get("restoreStateFound") is not True:
            errors.append("rollback.restoreStateFound must be true")
        fields = rollback.get("fields")
        if not isinstance(fields, list) or not fields or not all(isinstance(f, str) and f.strip() for f in fields):
            errors.append("rollback.fields must be a non-empty list of field names")

    if action_id == "host.restart-agent" and "runCommand" not in data:
        errors.append("host.restart-agent must declare runCommand with a pinned script hash")

    if "runCommand" in data:
        rc = data["runCommand"]
        if not isinstance(rc, dict):
            errors.append("runCommand must be a mapping")
        else:
            script_rel = rc.get("script")
            expected_sha = rc.get("sha256")
            identity = rc.get("identity")
            if not isinstance(script_rel, str) or not script_rel.startswith("catalog/actions/scripts/"):
                errors.append("runCommand.script must live under catalog/actions/scripts/")
            else:
                script_path = root / script_rel
                if not script_path.is_file():
                    errors.append(f"runCommand.script points at a missing file {script_rel}")
                else:
                    raw = script_path.read_bytes()
                    text = raw.decode("utf-8", errors="replace")
                    if not text.startswith("#Requires -Version 5.1"):
                        errors.append(f"{script_rel} must start with '#Requires -Version 5.1' for Windows PowerShell 5.1")
                    actual_sha = hashlib.sha256(raw).hexdigest()
                    if actual_sha != expected_sha:
                        errors.append(f"runCommand.sha256 {expected_sha!r} doesn't match {script_rel} sha256 {actual_sha}")
            forbidden_shared = {"approve", "approve-power", "approve-group", "stop-spend"}
            if not isinstance(identity, str) or not identity.strip() or identity in forbidden_shared:
                errors.append("runCommand.identity must name a dedicated identity, never shared with group or power actions (spec section 5a)")
            if rc.get("deleteRunCommandAfter") is not True:
                errors.append("runCommand.deleteRunCommandAfter must be true")

    if action_id == "host.reregister" and data.get("secretsInActionOnly") is not True:
        errors.append("host.reregister must set secretsInActionOnly: true (token never enters the plan)")

    return [f"{rel}: {e}" for e in errors]


def check(root: Path) -> list[str]:
    action_dir = root / ACTION_DIR
    if not action_dir.is_dir():
        return [f"{ACTION_DIR}: directory does not exist"]
    paths = sorted(action_dir.glob("*.yml"))
    found_ids = {p.stem for p in paths}
    errors: list[str] = []
    missing = sorted(REQUIRED_S2_ACTIONS - found_ids)
    if missing:
        errors.append(f"{ACTION_DIR}: missing required S2 action(s): {', '.join(missing)}")
    for path in paths:
        errors.extend(action_errors(root, path))
    return errors


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[1]
    errors = check(root)
    for e in errors:
        print(f"::error::{e}")
    count = len(list((root / ACTION_DIR).glob("*.yml"))) if (root / ACTION_DIR).is_dir() else 0
    print(f"{count} catalog actions checked, {len(errors)} findings.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
