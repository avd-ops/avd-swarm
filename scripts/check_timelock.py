#!/usr/bin/env python3
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Solo-mode 72-hour time-lock and team-mode capability classifier (governance.md section 2.6; R19).

Compares protected files before and after a change to classify each change as `lowers` or `raises`
capability (defaulting to `raises` whenever a change is ambiguous or touches workflows/code), and
evaluates whether the pull request satisfies solo-mode (72-hour time-lock + pinned issue for raising
changes; immediate merge when guard is green for lowering changes) or team-mode (2 human code-owner
approvals for raising changes; 1 human code-owner approval for lowering changes; AI/bot reviews
never count).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from check_agent_pr import matches

TIMELOCK_HOURS = 72
AI_REVIEWER_SLUGS = {
    "claude",
    "claude-code-review",
    "copilot",
    "github-copilot",
    "copilot-pull-request-reviewer",
    "github-actions",
}


def is_protected_path(path: str, policy: dict[str, Any]) -> bool:
    patterns = (policy.get("protected") or []) + (policy.get("append_only") or [])
    return any(matches(path, p) for p in patterns)


def is_human_reviewer(login: str, user_type: str = "User") -> bool:
    if not login or not isinstance(login, str):
        return False
    norm = login.strip().lower()
    if norm.endswith("[bot]") or norm.startswith("app/"):
        return False
    if user_type and user_type.lower() != "user":
        return False
    if norm in AI_REVIEWER_SLUGS:
        return False
    return True


def _compare_numeric_caps(
    prefix: str, before: dict[str, Any], after: dict[str, Any]
) -> tuple[str, list[str]]:
    keys = sorted(set(before) | set(after))
    if not keys:
        return "raises", [f"{prefix}: empty mapping"]
    reasons: list[str] = []
    direction = "lowers"
    for k in keys:
        if k not in before or k not in after:
            return "raises", [f"{prefix}.{k}: key added or removed ({before.get(k)!r} -> {after.get(k)!r})"]
        old_val, new_val = before[k], after[k]
        if isinstance(old_val, bool) or isinstance(new_val, bool) or not isinstance(old_val, (int, float)) or not isinstance(new_val, (int, float)):
            if old_val != new_val:
                return "raises", [f"{prefix}.{k}: non-numeric change ({old_val!r} -> {new_val!r})"]
            continue
        if new_val > old_val:
            return "raises", [f"{prefix}.{k}: raised from {old_val} to {new_val}"]
        if new_val < old_val:
            if new_val <= 0:
                return "raises", [f"{prefix}.{k}: non-positive cap {new_val} is invalid"]
            reasons.append(f"{prefix}.{k}: lowered from {old_val} to {new_val}")
    if not reasons:
        return "raises", [f"{prefix}: no capability-lowering change detected"]
    return direction, reasons


def _classify_action_manifest(
    path: str, before: dict[str, Any] | None, after: dict[str, Any] | None
) -> tuple[str, list[str]]:
    if before is None and after is not None:
        return "raises", [f"{path}: new action added ({after.get('id', path)})"]
    if before is not None and after is None:
        return "lowers", [f"{path}: action removed ({before.get('id', path)})"]
    if not isinstance(before, dict) or not isinstance(after, dict):
        return "raises", [f"{path}: unreadable action manifest"]

    reasons: list[str] = []
    all_keys = sorted(set(before) | set(after))
    for k in all_keys:
        if before.get(k) == after.get(k):
            continue
        if k == "limits" and isinstance(before.get(k), dict) and isinstance(after.get(k), dict):
            effect, limit_reasons = _compare_numeric_caps(f"{path}:limits", before[k], after[k])
            if effect == "raises":
                return "raises", limit_reasons
            reasons.extend(limit_reasons)
        elif k == "preconditions" and isinstance(before.get(k), dict) and isinstance(after.get(k), dict):
            b_pre, a_pre = before[k], after[k]
            for pk in sorted(set(b_pre) | set(a_pre)):
                if b_pre.get(pk) == a_pre.get(pk):
                    continue
                if b_pre.get(pk) is False and a_pre.get(pk) is True:
                    reasons.append(f"{path}:preconditions.{pk}: tightened (false -> true)")
                else:
                    return "raises", [f"{path}:preconditions.{pk}: changed ({b_pre.get(pk)!r} -> {a_pre.get(pk)!r})"]
        else:
            return "raises", [f"{path}:{k}: changed ({before.get(k)!r} -> {after.get(k)!r})"]

    if not reasons:
        return "raises", [f"{path}: modified without lowering capability"]
    return "lowers", reasons


def _classify_gateway(
    path: str, before: dict[str, Any] | None, after: dict[str, Any] | None
) -> tuple[str, list[str]]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return "raises", [f"{path}: added, removed, or invalid YAML"]
    for k in sorted(set(before) | set(after)):
        if k in {"caps", "callers"}:
            continue
        if before.get(k) != after.get(k):
            return "raises", [f"{path}:{k}: changed ({before.get(k)!r} -> {after.get(k)!r})"]
    if not isinstance(before.get("caps"), dict) or not isinstance(after.get("caps"), dict):
        return "raises", [f"{path}:caps: missing mapping"]
    reasons: list[str] = []
    if before["caps"] != after["caps"]:
        effect, cap_reasons = _compare_numeric_caps(f"{path}:caps", before["caps"], after["caps"])
        if effect == "raises":
            return "raises", cap_reasons
        reasons.extend(cap_reasons)
    b_callers = before.get("callers") if isinstance(before.get("callers"), dict) else {}
    a_callers = after.get("callers") if isinstance(after.get("callers"), dict) else {}
    if b_callers != a_callers:
        added_callers = sorted(set(a_callers) - set(b_callers))
        removed_callers = sorted(set(b_callers) - set(a_callers))
        if added_callers:
            return "raises", [f"{path}:callers: added {', '.join(added_callers)}"]
        for c in removed_callers:
            reasons.append(f"{path}:callers.{c}: caller removed")
        for c in sorted(set(b_callers) & set(a_callers)):
            if b_callers[c] == a_callers[c]:
                continue
            if not isinstance(b_callers[c], dict) or not isinstance(a_callers[c], dict):
                return "raises", [f"{path}:callers.{c}: invalid mapping"]
            effect, caller_reasons = _compare_numeric_caps(f"{path}:callers.{c}", b_callers[c], a_callers[c])
            if effect == "raises":
                return "raises", caller_reasons
            reasons.extend(caller_reasons)
    if not reasons:
        return "raises", [f"{path}: no capability-lowering change detected"]
    return "lowers", reasons


def _classify_allowlist(
    path: str, before: dict[str, Any] | None, after: dict[str, Any] | None
) -> tuple[str, list[str]]:
    if before is None and after is not None:
        return "raises", [f"{path}: new agent allowlist added"]
    if before is not None and after is None:
        return "lowers", [f"{path}: agent allowlist removed"]
    if not isinstance(before, dict) or not isinstance(after, dict):
        return "raises", [f"{path}: invalid allowlist YAML"]
    if before.get("agent") != after.get("agent") or before.get("status") != after.get("status"):
        return "raises", [f"{path}: agent or status changed"]
    extra_keys = (set(before) | set(after)) - {"agent", "status", "tools", "kql"}
    if extra_keys:
        return "raises", [f"{path}: unknown key(s) {', '.join(sorted(extra_keys))}"]

    reasons: list[str] = []
    for key in ("tools", "kql"):
        b_set = set(before.get(key) or [])
        a_set = set(after.get(key) or [])
        added = sorted(a_set - b_set)
        removed = sorted(b_set - a_set)
        if added:
            return "raises", [f"{path}:{key}: added {', '.join(added)}"]
        if removed:
            reasons.append(f"{path}:{key}: removed {', '.join(removed)}")
    if not reasons:
        return "raises", [f"{path}: modified without removing tools or KQL"]
    return "lowers", reasons


def _classify_protected_paths(
    path: str, before: dict[str, Any] | None, after: dict[str, Any] | None
) -> tuple[str, list[str]]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return "raises", [f"{path}: added, removed, or invalid YAML"]
    extra_keys = (set(before) | set(after)) - {"agents", "protected", "append_only"}
    if extra_keys:
        return "raises", [f"{path}: unknown key(s) {', '.join(sorted(extra_keys))}"]
    reasons: list[str] = []
    for key in ("agents", "protected", "append_only"):
        b_set = set(before.get(key) or [])
        a_set = set(after.get(key) or [])
        removed = sorted(b_set - a_set)
        added = sorted(a_set - b_set)
        if removed:
            return "raises", [f"{path}:{key}: removed {', '.join(removed)}"]
        if added:
            reasons.append(f"{path}:{key}: added {', '.join(added)}")
    if not reasons:
        return "raises", [f"{path}: modified without tightening protected paths"]
    return "lowers", reasons


def _classify_compliance(
    path: str, before: dict[str, Any] | None, after: dict[str, Any] | None
) -> tuple[str, list[str]]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return "raises", [f"{path}: added, removed, or invalid YAML"]
    extra_keys = (set(before) | set(after)) - {"floor", "verifyMinutes", "standards", "allowedMayLowerControls"}
    if extra_keys:
        return "raises", [f"{path}: unknown key(s) {', '.join(sorted(extra_keys))}"]
    if before.get("standards") != after.get("standards") or before.get("verifyMinutes") != after.get("verifyMinutes"):
        return "raises", [f"{path}: standards or verifyMinutes changed"]
    old_floor = before.get("floor")
    new_floor = after.get("floor")
    if isinstance(old_floor, bool) or isinstance(new_floor, bool) or not isinstance(old_floor, (int, float)) or not isinstance(new_floor, (int, float)):
        return "raises", [f"{path}:floor: non-numeric floor"]
    if new_floor < old_floor:
        return "raises", [f"{path}:floor: lowered from {old_floor} to {new_floor}"]
    reasons: list[str] = []
    if new_floor > old_floor:
        reasons.append(f"{path}:floor: raised from {old_floor} to {new_floor}")
    b_ctrl = set(before.get("allowedMayLowerControls") or [])
    a_ctrl = set(after.get("allowedMayLowerControls") or [])
    added_ctrl = sorted(a_ctrl - b_ctrl)
    removed_ctrl = sorted(b_ctrl - a_ctrl)
    if added_ctrl:
        return "raises", [f"{path}:allowedMayLowerControls: added {', '.join(added_ctrl)}"]
    if removed_ctrl:
        reasons.append(f"{path}:allowedMayLowerControls: removed {', '.join(removed_ctrl)}")
    if not reasons:
        return "raises", [f"{path}: modified without tightening compliance floor or controls"]
    return "lowers", reasons


SWITCH_SEVERITY = {"halt-actions": 1, "halt-agents": 2, "halt-all": 3}


def _classify_watchdog(
    path: str, before: dict[str, Any] | None, after: dict[str, Any] | None
) -> tuple[str, list[str]]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return "raises", [f"{path}: added, removed, or invalid YAML"]
    extra_keys = (set(before) | set(after)) - {"switch", "thresholds", "rules"}
    if extra_keys:
        return "raises", [f"{path}: unknown key(s) {', '.join(sorted(extra_keys))}"]
    if before.get("switch") != after.get("switch"):
        return "raises", [f"{path}:switch: switch configuration changed"]
    reasons: list[str] = []
    b_thresh = before.get("thresholds") if isinstance(before.get("thresholds"), dict) else {}
    a_thresh = after.get("thresholds") if isinstance(after.get("thresholds"), dict) else {}
    if b_thresh != a_thresh:
        effect, thresh_reasons = _compare_numeric_caps(f"{path}:thresholds", b_thresh, a_thresh)
        if effect == "raises":
            return "raises", thresh_reasons
        reasons.extend(thresh_reasons)

    b_rules = {r["id"]: r for r in (before.get("rules") or []) if isinstance(r, dict) and "id" in r}
    a_rules = {r["id"]: r for r in (after.get("rules") or []) if isinstance(r, dict) and "id" in r}
    removed_rules = sorted(set(b_rules) - set(a_rules))
    if removed_rules:
        return "raises", [f"{path}:rules: removed {', '.join(removed_rules)}"]
    for rid, a_rule in sorted(a_rules.items()):
        if rid not in b_rules:
            reasons.append(f"{path}:rules.{rid}: added watchdog rule")
            continue
        b_rule = b_rules[rid]
        if b_rule == a_rule:
            continue
        b_pos = SWITCH_SEVERITY.get(str(b_rule.get("position")), 0)
        a_pos = SWITCH_SEVERITY.get(str(a_rule.get("position")), 0)
        if a_pos < b_pos:
            return "raises", [f"{path}:rules.{rid}: position lowered ({b_rule.get('position')} -> {a_rule.get('position')})"]
        if b_rule.get("pageOwner") is True and a_rule.get("pageOwner") is not True:
            return "raises", [f"{path}:rules.{rid}: pageOwner disabled"]
        if a_pos > b_pos:
            reasons.append(f"{path}:rules.{rid}: position tightened ({b_rule.get('position')} -> {a_rule.get('position')})")
        elif b_rule.get("pageOwner") is False and a_rule.get("pageOwner") is True:
            reasons.append(f"{path}:rules.{rid}: pageOwner enabled")
        else:
            return "raises", [f"{path}:rules.{rid}: modified ({b_rule!r} -> {a_rule!r})"]

    if not reasons:
        return "raises", [f"{path}: modified without tightening watchdog thresholds or rules"]
    return "lowers", reasons


def _classify_mcp_callers(
    path: str, before: dict[str, Any] | None, after: dict[str, Any] | None
) -> tuple[str, list[str]]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return "raises", [f"{path}: added, removed, or invalid YAML"]
    b_callers = before.get("callers") if isinstance(before.get("callers"), dict) else {}
    a_callers = after.get("callers") if isinstance(after.get("callers"), dict) else {}
    added = sorted(set(a_callers) - set(b_callers))
    if added:
        return "raises", [f"{path}:callers: added {', '.join(added)}"]
    reasons: list[str] = []
    for removed in sorted(set(b_callers) - set(a_callers)):
        reasons.append(f"{path}:callers.{removed}: caller removed")
    for cid in sorted(set(b_callers) & set(a_callers)):
        bc, ac = b_callers[cid], a_callers[cid]
        if bc == ac:
            continue
        if not isinstance(bc, dict) or not isinstance(ac, dict):
            return "raises", [f"{path}:callers.{cid}: invalid mapping"]
        for fixed_key in ("isPerson", "requireScope", "appRole", "adapter"):
            if bc.get(fixed_key) != ac.get(fixed_key):
                return "raises", [f"{path}:callers.{cid}.{fixed_key}: changed"]
        if bc.get("allowPropose") is False and ac.get("allowPropose") is True:
            return "raises", [f"{path}:callers.{cid}.allowPropose: enabled"]
        if bc.get("allowPropose") is True and ac.get("allowPropose") is False:
            reasons.append(f"{path}:callers.{cid}.allowPropose: disabled")
        for list_key in ("allowlists", "allowedPositions"):
            b_set = set(bc.get(list_key) or [])
            a_set = set(ac.get(list_key) or [])
            if a_set - b_set:
                return "raises", [f"{path}:callers.{cid}.{list_key}: added {', '.join(sorted(a_set - b_set))}"]
            if b_set - a_set:
                reasons.append(f"{path}:callers.{cid}.{list_key}: removed {', '.join(sorted(b_set - a_set))}")
    if not reasons:
        return "raises", [f"{path}: modified without tightening MCP caller permissions"]
    return "lowers", reasons


def _parse_runbook_front_matter(text: str | None) -> tuple[dict[str, Any] | None, str | None]:
    if text is None:
        return None, None
    if not text.startswith("---\n"):
        return None, "missing YAML front matter"
    front, sep, _ = text[4:].partition("\n---\n")
    if not sep and not text[4:].endswith("\n---"):
        return None, "unclosed YAML front matter"
    if not sep:
        front = text[4:-4]
    try:
        meta = yaml.safe_load(front)
    except yaml.YAMLError as err:
        return None, f"invalid YAML front matter ({err})"
    if not isinstance(meta, dict):
        return None, "front matter must be a mapping"
    return meta, None


def _classify_runbook(
    path: str, before_text: str | None, after_text: str | None
) -> dict[str, Any] | None:
    """Evaluates whether a runbook change touches protected fields (runMode: propose, proposes, reads, budget.tokensUsd)
    per core-and-adapters.md section 3.2, section 5, and red team P-1 (WP8-2). Returns None when the change is an
    unprotected read-only runbook edit."""
    b_meta, b_err = _parse_runbook_front_matter(before_text)
    a_meta, a_err = _parse_runbook_front_matter(after_text)
    if b_err or a_err:
        return {"path": path, "protected": True, "effect": "raises", "reasons": [f"{path}: {b_err or a_err}"]}

    def _mode(m: dict[str, Any] | None) -> str:
        if not isinstance(m, dict):
            return ""
        trig = m.get("trigger") if isinstance(m.get("trigger"), dict) else {}
        return str(trig.get("runMode") or "")

    def _proposes(m: dict[str, Any] | None) -> str:
        return str(m.get("proposes") or "") if isinstance(m, dict) else ""

    def _reads(m: dict[str, Any] | None) -> set[str]:
        if not isinstance(m, dict) or not isinstance(m.get("reads"), list):
            return set()
        return {json.dumps(r, sort_keys=True) for r in m["reads"] if isinstance(r, dict)}

    def _budget(m: dict[str, Any] | None) -> float | None:
        if not isinstance(m, dict) or not isinstance(m.get("budget"), dict):
            return None
        val = m["budget"].get("tokensUsd")
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            return None
        return float(val)

    b_propose = _mode(b_meta) == "propose" or bool(_proposes(b_meta))
    a_propose = _mode(a_meta) == "propose" or bool(_proposes(a_meta))

    if b_meta is None and a_meta is not None:
        if a_propose:
            return {
                "path": path,
                "protected": True,
                "effect": "raises",
                "reasons": [f"{path}: new propose-mode runbook ({_proposes(a_meta) or 'propose'})"],
            }
        return None

    if b_meta is not None and a_meta is None:
        if b_propose:
            return {
                "path": path,
                "protected": True,
                "effect": "lowers",
                "reasons": [f"{path}: propose-mode runbook removed"],
            }
        return None

    assert b_meta is not None and a_meta is not None
    reasons_raise: list[str] = []
    reasons_lower: list[str] = []

    if not b_propose and a_propose:
        reasons_raise.append(f"{path}: runMode raised to propose ({_proposes(a_meta) or 'propose'})")
    elif b_propose and not a_propose:
        reasons_lower.append(f"{path}: runMode lowered from propose to read-only")
    elif b_propose and a_propose and _proposes(b_meta) != _proposes(a_meta):
        reasons_raise.append(f"{path}: proposes changed ({_proposes(b_meta)!r} -> {_proposes(a_meta)!r})")

    if b_meta.get("agent") != a_meta.get("agent"):
        reasons_raise.append(f"{path}: agent changed ({b_meta.get('agent')!r} -> {a_meta.get('agent')!r})")

    added_reads = _reads(a_meta) - _reads(b_meta)
    removed_reads = _reads(b_meta) - _reads(a_meta)
    if added_reads:
        reasons_raise.append(f"{path}: added {len(added_reads)} read(s)")
    elif removed_reads and (b_propose or a_propose):
        reasons_lower.append(f"{path}: removed {len(removed_reads)} read(s)")

    b_bud, a_bud = _budget(b_meta), _budget(a_meta)
    if b_bud is not None and a_bud is not None:
        if a_bud > b_bud:
            reasons_raise.append(f"{path}: budget.tokensUsd raised from {b_bud} to {a_bud}")
        elif a_bud < b_bud and a_bud > 0 and (b_propose or a_propose):
            reasons_lower.append(f"{path}: budget.tokensUsd lowered from {b_bud} to {a_bud}")
        elif a_bud <= 0:
            reasons_raise.append(f"{path}: invalid non-positive budget.tokensUsd ({a_bud})")

    if reasons_raise:
        return {"path": path, "protected": True, "effect": "raises", "reasons": reasons_raise}
    if reasons_lower:
        return {"path": path, "protected": True, "effect": "lowers", "reasons": reasons_lower}
    if b_propose or a_propose:
        return {
            "path": path,
            "protected": True,
            "effect": "raises",
            "reasons": [f"{path}: propose-mode runbook modified without lowering capability"],
        }
    return None


def classify_change(
    path: str,
    before_text: str | None,
    after_text: str | None,
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Classifies a single file change as `unprotected`, `lowers`, or `raises`."""
    if path.startswith("runbooks/") and path.endswith(".md") and path != "runbooks/README.md":
        rb_result = _classify_runbook(path, before_text, after_text)
        if rb_result is not None:
            return rb_result
    if not is_protected_path(path, policy):
        return {"path": path, "protected": False, "effect": "unprotected", "reasons": []}

    before_data = None
    after_data = None
    if path.endswith((".yml", ".yaml")):
        try:
            before_data = yaml.safe_load(before_text) if before_text is not None else None
            after_data = yaml.safe_load(after_text) if after_text is not None else None
        except yaml.YAMLError as err:
            return {
                "path": path,
                "protected": True,
                "effect": "raises",
                "reasons": [f"{path}: invalid YAML ({err})"],
            }

    if path.startswith("catalog/actions/") and path.endswith(".yml"):
        effect, reasons = _classify_action_manifest(path, before_data, after_data)
    elif path == "governance/gateway.yml":
        effect, reasons = _classify_gateway(path, before_data, after_data)
    elif path == "governance/compliance.yml":
        effect, reasons = _classify_compliance(path, before_data, after_data)
    elif path == "governance/watchdog.yml":
        effect, reasons = _classify_watchdog(path, before_data, after_data)
    elif path == "governance/mcp-callers.yml":
        effect, reasons = _classify_mcp_callers(path, before_data, after_data)
    elif path.startswith("governance/allowlists/") and path.endswith(".yml"):
        effect, reasons = _classify_allowlist(path, before_data, after_data)
    elif path == "governance/protected-paths.yml":
        effect, reasons = _classify_protected_paths(path, before_data, after_data)
    else:
        effect = "raises"
        reasons = [f"{path}: protected path change counts as capability-raising"]

    return {"path": path, "protected": True, "effect": effect, "reasons": reasons}


def evaluate_timelock(
    changes: list[dict[str, Any]],
    *,
    approvers: list[str],
    last_commit_utc: datetime,
    now_utc: datetime,
    reviews: list[dict[str, Any]] | None = None,
    pr_author: str = "",
    pinned_issue_open: bool = True,
) -> dict[str, Any]:
    """Evaluates whether a PR passes solo-mode or team-mode guardrail requirements (governance.md section 2.6)."""
    clean_approvers = [a.strip() for a in approvers if a and a.strip()]
    mode = "solo" if len(clean_approvers) <= 1 else "team"
    protected_changes = [c for c in changes if c.get("protected")]
    raising_changes = [c for c in protected_changes if c.get("effect") == "raises"]
    lowering_changes = [c for c in protected_changes if c.get("effect") == "lowers"]

    if raising_changes:
        overall = "raises"
    elif lowering_changes:
        overall = "lowers"
    else:
        overall = "none"

    valid_approvers: set[str] = set()
    ignored_reviews: list[str] = []
    approver_set = {a.lower() for a in clean_approvers}
    author_norm = pr_author.strip().lower()

    for rev in reviews or []:
        if (rev.get("state") or "").upper() != "APPROVED":
            continue
        user = rev.get("user") if isinstance(rev.get("user"), dict) else {}
        login = (rev.get("login") or user.get("login") or "").strip()
        user_type = rev.get("type") or user.get("type") or "User"
        if not is_human_reviewer(login, user_type):
            ignored_reviews.append(f"{login or 'unknown'} (non-human/AI review ignored)")
            continue
        if login.lower() == author_norm:
            ignored_reviews.append(f"{login} (PR author cannot approve own PR)")
            continue
        if approver_set and login.lower() not in approver_set:
            ignored_reviews.append(f"{login} (not in approvers team)")
            continue
        valid_approvers.add(login.lower())

    elapsed = now_utc - last_commit_utc
    elapsed_hours = round(elapsed.total_seconds() / 3600.0, 2)
    remaining_hours = max(0.0, round(TIMELOCK_HOURS - (elapsed.total_seconds() / 3600.0), 2))
    unlock_at = (last_commit_utc + timedelta(hours=TIMELOCK_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")

    if overall == "none":
        return {
            "mode": mode,
            "overallEffect": overall,
            "passed": True,
            "status": "pass",
            "timelockRequired": False,
            "requiredApprovals": 0,
            "validApprovals": len(valid_approvers),
            "ignoredReviews": ignored_reviews,
            "elapsedHours": elapsed_hours,
            "remainingHours": 0.0,
            "unlockAtUtc": None,
            "pinnedIssueOpen": pinned_issue_open,
            "changes": changes,
            "reason": "No protected paths changed.",
        }

    if mode == "solo":
        if overall == "lowers":
            return {
                "mode": mode,
                "overallEffect": overall,
                "passed": True,
                "status": "pass",
                "timelockRequired": False,
                "requiredApprovals": 0,
                "validApprovals": len(valid_approvers),
                "ignoredReviews": ignored_reviews,
                "elapsedHours": elapsed_hours,
                "remainingHours": 0.0,
                "unlockAtUtc": None,
                "pinnedIssueOpen": pinned_issue_open,
                "changes": changes,
                "reason": "Guardrail change strictly lowers capability in solo mode; no 72-hour time-lock required.",
            }
        timelock_met = elapsed >= timedelta(hours=TIMELOCK_HOURS)
        passed = bool(timelock_met and pinned_issue_open)
        if not pinned_issue_open:
            reason = "Solo-mode capability-raising change requires an open pinned issue for the 72-hour window."
        elif not timelock_met:
            reason = f"72-hour solo-mode time-lock pending ({elapsed_hours}h elapsed, {remaining_hours}h remaining until {unlock_at})."
        else:
            reason = f"72-hour solo-mode time-lock satisfied ({elapsed_hours}h elapsed since last commit) and pinned issue is open."
        return {
            "mode": mode,
            "overallEffect": overall,
            "passed": passed,
            "status": "pass" if passed else "pending",
            "timelockRequired": True,
            "requiredApprovals": 0,
            "validApprovals": len(valid_approvers),
            "ignoredReviews": ignored_reviews,
            "elapsedHours": elapsed_hours,
            "remainingHours": remaining_hours,
            "unlockAtUtc": unlock_at,
            "pinnedIssueOpen": pinned_issue_open,
            "changes": changes,
            "reason": reason,
        }

    required_approvals = 2 if overall == "raises" else 1
    passed = len(valid_approvers) >= required_approvals
    reason = (
        f"Team-mode {overall} change requires {required_approvals} human code-owner approval(s); "
        f"found {len(valid_approvers)}."
    )
    return {
        "mode": mode,
        "overallEffect": overall,
        "passed": passed,
        "status": "pass" if passed else "pending",
        "timelockRequired": False,
        "requiredApprovals": required_approvals,
        "validApprovals": len(valid_approvers),
        "ignoredReviews": ignored_reviews,
        "elapsedHours": elapsed_hours,
        "remainingHours": 0.0,
        "unlockAtUtc": None,
        "pinnedIssueOpen": pinned_issue_open,
        "changes": changes,
        "reason": reason,
    }


def format_summary_markdown(result: dict[str, Any]) -> str:
    """Formats the deterministic guard summary posted on PRs touching protected paths."""
    lines = [
        "## `swarm-guard/timelock` deterministic summary",
        "",
        f"- **Mode:** `{result['mode']}`",
        f"- **Capability effect:** `{result['overallEffect']}`",
        f"- **Status:** `{result['status']}`",
        f"- **Decision:** {result['reason']}",
    ]
    if result.get("timelockRequired"):
        lines.append(f"- **Unlock at (UTC):** `{result['unlockAtUtc']}`")
        lines.append(f"- **Pinned issue open:** `{result['pinnedIssueOpen']}`")
    if result["mode"] == "team":
        lines.append(
            f"- **Human code-owner approvals:** `{result['validApprovals']}/{result['requiredApprovals']}`"
        )
    if result.get("ignoredReviews"):
        lines.append(f"- **Ignored advisory/ineligible reviews:** {', '.join(result['ignoredReviews'])}")

    protected = [c for c in result.get("changes", []) if c.get("protected")]
    if protected:
        lines.extend(["", "### Protected path changes", ""])
        for change in protected:
            details = "; ".join(change.get("reasons") or []) or change["effect"]
            lines.append(f"- `{change['path']}` (`{change['effect']}`): {details}")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python3 scripts/check_timelock.py INPUT_JSON")
        return 2
    root = Path(__file__).resolve().parents[1]
    policy = yaml.safe_load((root / "governance/protected-paths.yml").read_text(encoding="utf-8")) or {}
    payload = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    classified = [
        classify_change(item["path"], item.get("before"), item.get("after"), policy)
        for item in payload.get("files", [])
    ]
    last_commit = datetime.fromisoformat(payload["lastCommitUtc"].replace("Z", "+00:00")).astimezone(timezone.utc)
    now_utc = (
        datetime.fromisoformat(payload["nowUtc"].replace("Z", "+00:00")).astimezone(timezone.utc)
        if "nowUtc" in payload
        else datetime.now(timezone.utc)
    )
    result = evaluate_timelock(
        classified,
        approvers=payload.get("approvers") or ["owner"],
        last_commit_utc=last_commit,
        now_utc=now_utc,
        reviews=payload.get("reviews") or [],
        pr_author=payload.get("prAuthor") or "",
        pinned_issue_open=payload.get("pinnedIssueOpen", True),
    )
    print(format_summary_markdown(result), end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
