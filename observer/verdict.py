# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Layer 3: Stateless HTTP `verdict` webhook for `plan.proposed` CloudEvents (docs/outside-observer-spec.md)."""

from __future__ import annotations

import re
from fnmatch import fnmatch
from typing import Any, Callable

from .models import ApprovalTelemetry, VerdictResult

FORBIDDEN_CONVERSATION_KEYS: frozenset[str] = frozenset(
    {
        "messages",
        "prompt",
        "prompts",
        "conversation",
        "conversationtext",
        "chathistory",
        "modeloutput",
        "rawtext",
        "transcript",
    }
)

DEFAULT_ALLOWED_ACTIONS: frozenset[str] = frozenset(
    {
        "host.drain",
        "host.restart-agent",
        "host.recycle-empty",
        "host.reregister",
        "host.stop",
        "host.resume",
        "host.reprovision",
        "host.deprovision",
        "host.replace-session-host",
        "group.add-user",
        "group.remove-user",
    }
)

DESTRUCTIVE_D1_ACTIONS: frozenset[str] = frozenset(
    {
        "host.reprovision",
        "host.deprovision",
        "host.replace-session-host",
        "reprovision",
        "deprovision",
        "replace-session-host",
    }
)

USGOV_UNSUPPORTED_CAPABILITIES: frozenset[str] = frozenset({"mhn", "frontline"})

DENIED_TARGET_GLOBS: tuple[str, ...] = (
    "/subscriptions/*/resourcegroups/rg-*-swarm*/*",
    "/subscriptions/*/resourcegroups/rg-*-runstore*/*",
    "/subscriptions/*/resourcegroups/rg-*-observer*/*",
    "*/providers/microsoft.authorization/roleassignments/*",
    "*/providers/microsoft.authorization/policyassignments/*",
    "*/providers/microsoft.authorization/policyexemptions/*",
    "*/providers/microsoft.security/*",
)

ALLOWED_TARGET_GLOBS: tuple[str, ...] = (
    "/subscriptions/*/resourcegroups/rg-avd-*/*",
    "/subscriptions/*/resourcegroups/rg-*-avd-*/*",
)

ARM_RESOURCE_ID_RE = re.compile(
    r"^/subscriptions/[0-9a-fA-F-]+/resourceGroups/[^/]+/providers/[^/]+/.+$",
    re.IGNORECASE,
)


def _find_forbidden_keys(obj: Any, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key_norm = str(k).strip().lower()
            cur_path = f"{path}.{k}" if path else str(k)
            if key_norm in FORBIDDEN_CONVERSATION_KEYS:
                hits.append(cur_path)
            hits.extend(_find_forbidden_keys(v, cur_path))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            hits.extend(_find_forbidden_keys(item, f"{path}[{idx}]"))
    return hits


def detect_approval_fatigue(
    recent_approvals: list[ApprovalTelemetry],
    *,
    min_fast_streak: int = 3,
    fast_latency_seconds: float = 3.0,
    min_zero_reject_streak: int = 10,
) -> tuple[bool, str]:
    """Detects sub-3-second rubber-stamp streaks or 0%-rejection streaks in recent C10 outcomes."""
    if not recent_approvals:
        return False, ""

    # Count trailing consecutive fast approvals (< 3.0s)
    fast_streak = 0
    for item in reversed(recent_approvals):
        if item.decision == "approved" and item.latency_seconds < fast_latency_seconds:
            fast_streak += 1
        else:
            break
    if fast_streak >= min_fast_streak:
        return True, f"approval-fatigue: {fast_streak} consecutive sub-{fast_latency_seconds:.0f}s approvals"

    # Count trailing consecutive approvals with 0 rejections
    approve_streak = 0
    for item in reversed(recent_approvals):
        if item.decision == "approved":
            approve_streak += 1
        else:
            break
    if approve_streak >= min_zero_reject_streak:
        return True, f"approval-fatigue: {approve_streak} consecutive approvals with 0 rejections"

    return False, ""


def _validate_cloudevent_schema(payload: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if payload.get("specversion") != "1.0":
        errors.append("schema: specversion must be '1.0'")
    if payload.get("type") != "plan.proposed":
        errors.append("schema: type must be 'plan.proposed'")
    for field_name in ("id", "source", "time"):
        val = payload.get(field_name)
        if not isinstance(val, str) or not val.strip():
            errors.append(f"schema: {field_name} must be a non-empty string")
    data = payload.get("data")
    if not isinstance(data, dict):
        errors.append("schema: data must be an object")
        return False, errors

    for field_name in ("planId", "planHash", "action"):
        val = data.get(field_name)
        if not isinstance(val, str) or not val.strip():
            errors.append(f"schema: data.{field_name} must be a non-empty string")

    targets = data.get("targets")
    if not isinstance(targets, list) or not targets or not all(isinstance(t, str) and t.strip() for t in targets):
        errors.append("schema: data.targets must be a non-empty list of resource IDs")
    else:
        for t in targets:
            if not ARM_RESOURCE_ID_RE.match(t.strip()):
                errors.append(f"schema: invalid ARM resource ID target {t!r}")

    cloud = data.get("cloud")
    if cloud not in ("commercial", "usgov"):
        errors.append("schema: data.cloud must be 'commercial' or 'usgov'")

    return len(errors) == 0, errors


def evaluate_verdict(
    payload: dict[str, Any],
    *,
    cloud_env: str = "commercial",
    allowed_actions: frozenset[str] = DEFAULT_ALLOWED_ACTIONS,
    recent_approvals: list[ApprovalTelemetry] | None = None,
    model_reviewer: Callable[[dict[str, Any], int], tuple[bool, str]] | None = None,
    max_tokens: int = 256,
    trip_callback: Callable[[str, str], None] | None = None,
) -> VerdictResult:
    """Evaluates a `plan.proposed` CloudEvent fail-closed:
    1. Rejects conversation text keys (`GQ1`).
    2. Validates strict CloudEvents 1.0 + plan schema.
    3. Runs deterministic checks (allowlist, scope, blast radius, C26, D1, business hours).
    4. Checks approval-fatigue streaks from recent C10 outcomes.
    5. Optionally calls a second-opinion model (capped max_tokens), failing closed on errors.
    """
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    plan_id = str(data.get("planId") or payload.get("id") or "unknown")

    # 1. No conversation text (GQ1)
    forbidden_hits = _find_forbidden_keys(payload)
    if forbidden_hits:
        return VerdictResult(
            plan_id=plan_id,
            verdict="deny",
            reasons=[f"forbidden conversation text field(s): {', '.join(forbidden_hits)}"],
            deterministic_passed=False,
        )

    # 2. Strict CloudEvents + data schema
    schema_ok, schema_errors = _validate_cloudevent_schema(payload)
    if not schema_ok:
        return VerdictResult(
            plan_id=plan_id,
            verdict="deny",
            reasons=schema_errors,
            deterministic_passed=False,
        )

    reasons: list[str] = []
    action = str(data["action"]).strip()
    targets = [str(t).strip() for t in data["targets"]]
    cloud = str(data["cloud"]).strip()

    # 3a. Action allowlist
    if action not in allowed_actions:
        reasons.append(f"deterministic: action {action!r} is not in observer allowlist")

    # 3b. Scope check
    for t in targets:
        t_low = t.lower()
        if any(fnmatch(t_low, g) for g in DENIED_TARGET_GLOBS):
            reasons.append(f"deterministic: target {t} matches denied control-plane/observer/RBAC scope")
        elif not any(fnmatch(t_low, g) for g in ALLOWED_TARGET_GLOBS):
            reasons.append(f"deterministic: target {t} is outside allowed AVD resource group scope")

    # 3c. Blast radius
    blast = data.get("blastRadius") if isinstance(data.get("blastRadius"), dict) else {}
    hosts_count = int(blast.get("hosts", len(targets)))
    users_count = int(blast.get("users", 0))
    if hosts_count > 1:
        reasons.append(f"deterministic: blastRadius.hosts {hosts_count} exceeds max 1")
    if users_count > 5:
        reasons.append(f"deterministic: blastRadius.users {users_count} exceeds max 5")

    # 3d. Cloud boundary (C26)
    if cloud != cloud_env:
        reasons.append(f"C26: plan cloud {cloud!r} disagrees with observer cloudEnv {cloud_env!r}")
    caps = data.get("capabilities") if isinstance(data.get("capabilities"), list) else []
    if cloud == "usgov":
        bad_caps = [str(c) for c in caps if str(c).strip().lower() in USGOV_UNSUPPORTED_CAPABILITIES]
        if bad_caps:
            reasons.append(f"C26: usgov does not support capabilities {', '.join(bad_caps)}")

    # 3e. Data-loss guard (D1)
    if action in DESTRUCTIVE_D1_ACTIONS:
        dlg = data.get("dataLossGuard") if isinstance(data.get("dataLossGuard"), dict) else {}
        restore_ok = bool(dlg.get("restorePointVerified")) and float(dlg.get("restorePointAgeHours", 999)) <= 24.0
        discard_ok = bool(dlg.get("discardLastCopyApproved"))
        if not (restore_ok or discard_ok):
            reasons.append("D1: destructive action requires restorePointVerified (<=24h) or discardLastCopyApproved")

    # 3f. Business hours window check
    if bool(data.get("duringBusinessHours", False)):
        why = data.get("why") if isinstance(data.get("why"), dict) else {}
        if not (str(why.get("decisionId") or "").strip() or str(why.get("reason") or "").strip()):
            reasons.append("deterministic: business-hours plan requires why.decisionId or why.reason")

    if reasons:
        return VerdictResult(
            plan_id=plan_id,
            verdict="deny",
            reasons=reasons,
            deterministic_passed=False,
        )

    # 4. Approval-fatigue streak detection
    fatigue, fatigue_reason = detect_approval_fatigue(recent_approvals or [])
    if fatigue:
        if trip_callback is not None:
            try:
                trip_callback("halt-actions", fatigue_reason)
            except Exception:
                pass
        return VerdictResult(
            plan_id=plan_id,
            verdict="deny",
            reasons=[fatigue_reason],
            deterministic_passed=True,
            fatigue_detected=True,
            trip_recommended="halt-actions",
        )

    # 5. Optional second-opinion model (capped max_tokens, fail-closed on error/timeout)
    if model_reviewer is not None:
        try:
            model_allow, model_reason = model_reviewer(data, max_tokens)
            if not model_allow:
                return VerdictResult(
                    plan_id=plan_id,
                    verdict="deny",
                    reasons=[model_reason or "second-opinion model denied plan"],
                    deterministic_passed=True,
                    model_used=True,
                )
            return VerdictResult(
                plan_id=plan_id,
                verdict="allow",
                reasons=[model_reason or "deterministic and second-opinion checks passed"],
                deterministic_passed=True,
                model_used=True,
            )
        except Exception as exc:
            return VerdictResult(
                plan_id=plan_id,
                verdict="deny",
                reasons=[f"fail-closed: second-opinion model error: {exc}"],
                deterministic_passed=True,
                model_used=True,
            )

    return VerdictResult(
        plan_id=plan_id,
        verdict="allow",
        reasons=["deterministic checks passed"],
        deterministic_passed=True,
        model_used=False,
    )
