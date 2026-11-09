# avd-swarm gate: pre-execution live re-read, preconditions, and approved write runner (spec section 5, 5a, 6d, 6f).
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""The governance gate's pre-approval check and post-approval `apply` step (avd-swarm#43):
1. Checks the kill switch first (`halt-actions` or `halt-all` refuses; an unreadable flag refuses).
2. Validates the plan's evidence provenance (V1-4), Desktop DevOps data-loss guard (D1), and `why` block (C18).
3. Validates every host precondition from spec section 5a (`notDrainedByOthers`, `noActiveSessions`,
   `notPowerLocked`, `notScalingExcluded`, `powerState != 'deallocated'`, `hostPoolAvailableHostsAbove`,
   `noInFlightPlanOnPool`, `serviceHealthClear`).
4. In `apply_approved_plan`, re-reads live target state before any write, rebuilds the canonical plan digest
   from the live state, and refuses if the live digest differs from the approved plan hash or if any live
   precondition fails (spec section 5 step 7 and section 6d).
5. Applies the reversible change via the caller's action runner, checks positive post-action verification
   (missing verification is a failure, never a pass; brain H5), and returns a redacted execution record
   carrying only `planId`, `planHash`, `action`, `passedCount`, `failedCount`, and `status` — never a target,
   parameter, resource ID, or host name (red team R17; spec section 5 step 5).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import yaml

from .evidence import (
    CLOUD_ENVIRONMENTS,
    CLOUDS,
    cloud_refusals,
    data_loss_guard_refusals,
    refusals as evidence_refusals,
    resolve_cloud_environment,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
WHY_KINDS = ("build", "configure", "expand", "contract", "fix", "recover", "retire")
KILL_SWITCH_REFUSE_ACTIONS = ("halt-actions", "halt-agents", "halt-all")
HOST_ACTIONS = ("host.drain", "host.restart-agent", "host.recycle-empty", "host.reregister", "host.stop", "host.resume")
FORBIDDEN_PLAN_SECRET_KEYS = ("registrationToken", "token", "secret", "password", "keyVaultSecretValue")


def load_action_catalog(root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Loads all action manifests from catalog/actions/*.yml."""
    base = (root or REPO_ROOT) / "catalog/actions"
    if not base.is_dir():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(base.glob("*.yml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("id"), str):
            out[data["id"]] = data
    return out


def load_compliance_policy(root: Path | None = None) -> dict[str, Any]:
    """Loads Defender for Cloud / Azure Policy compliance gate settings from governance/compliance.yml."""
    path = (root or REPO_ROOT) / "governance/compliance.yml"
    if not path.is_file():
        return {"floor": 70.0, "verifyMinutes": 30, "standards": ["microsoft-cloud-security-benchmark"], "allowedMayLowerControls": []}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def why_refusals(plan: object) -> list[str]:
    """Why the gate refuses this plan's `why` block (contract C18, spec section 6f); empty when it passes."""
    if not isinstance(plan, dict):
        return ["C18: a plan must be a mapping"]
    why = plan.get("why")
    if not isinstance(why, dict):
        return ["C18: a plan needs a why block"]
    out: list[str] = []
    kind = why.get("kind")
    if kind not in WHY_KINDS:
        out.append(f"C18: why.kind {kind!r} isn't in {WHY_KINDS}")
    decision = why.get("decision")
    draft = why.get("draft")
    if not decision and not isinstance(draft, dict):
        out.append("C18: why needs an accepted decision ID or a draft decision")
    if isinstance(draft, dict):
        alternatives = draft.get("alternatives")
        if not isinstance(alternatives, list) or len(alternatives) == 0:
            out.append("C18: why.draft needs at least one alternative")
    return out


def catalog_action_refusals(
    plan: object,
    state: object,
    runner_env: dict | None = None,
    root: Path | None = None,
) -> list[str]:
    """Evaluates catalog membership, section 5a rate limits & 24h circuit breaker, Run Command script hash &
    dedicated identity, secret-in-plan refusal, and governance section 2.5 compliance gate rules."""
    if not isinstance(plan, dict):
        return ["5a: plan must be a mapping"]
    base_root = root or REPO_ROOT
    catalog = load_action_catalog(base_root)
    action = str(plan.get("action") or "")
    if not catalog or action not in catalog:
        return [f"5a: action {action!r} is not in catalog/actions/"]

    manifest = catalog[action]
    out: list[str] = []
    st = state if isinstance(state, dict) else {}

    # 1. Secrets never enter the plan (host.reregister and all catalog actions)
    for key in FORBIDDEN_PLAN_SECRET_KEYS:
        if key in plan or key in st:
            out.append(f"5a: secretsInActionOnly: plan or state must never carry secret field {key!r}")

    # 2. Rate limits and 24-hour circuit breaker from the action manifest
    limits = manifest.get("limits") if isinstance(manifest.get("limits"), dict) else {}
    per_hour_cap = limits.get("perHour", 1)
    per_host_day_cap = limits.get("perHostPerDay", 1)
    cb_cap = limits.get("circuitBreakerFailures24h", 3)

    plans_last_hour = st.get("plansInLastHour", 0)
    if isinstance(plans_last_hour, bool) or not isinstance(plans_last_hour, int) or plans_last_hour < 0:
        out.append("5a: limits.perHour: plansInLastHour must be a non-negative integer")
    elif plans_last_hour >= per_hour_cap:
        out.append(f"5a: limits.perHour: {plans_last_hour} plan(s) in the last hour reaches cap {per_hour_cap} for {action}")

    plans_host_day = st.get("plansForHostInLast24h", 0)
    if isinstance(plans_host_day, bool) or not isinstance(plans_host_day, int) or plans_host_day < 0:
        out.append("5a: limits.perHostPerDay: plansForHostInLast24h must be a non-negative integer")
    elif plans_host_day >= per_host_day_cap:
        out.append(
            f"5a: limits.perHostPerDay: {plans_host_day} plan(s) on target host in 24h reaches cap {per_host_day_cap} for {action}"
        )

    failures_24h = st.get("failuresInLast24h", 0)
    if isinstance(failures_24h, bool) or not isinstance(failures_24h, int) or failures_24h < 0:
        out.append("5a: limits.circuitBreakerFailures24h: failuresInLast24h must be a non-negative integer")
    elif failures_24h >= cb_cap:
        out.append(
            f"5a: circuit breaker tripped for {action}: {failures_24h} failed verification(s)/rollback(s) in 24h (cap {cb_cap})"
        )

    # 3. Run Command fixed script SHA-256, no parameters, and dedicated identity (spec section 5a)
    cat_rc = manifest.get("runCommand")
    plan_rc = plan.get("runCommand")
    if plan_rc is not None and not isinstance(cat_rc, dict):
        out.append(f"5a: action {action} does not permit Run Command")
    if isinstance(cat_rc, dict):
        for forbidden_rc_key in ("inlineScript", "parameters", "args"):
            if forbidden_rc_key in plan or (isinstance(plan_rc, dict) and forbidden_rc_key in plan_rc):
                out.append(f"5a: Run Command takes no {forbidden_rc_key} from the agent")
        script_rel = str(cat_rc.get("script") or "")
        pinned_sha = str(cat_rc.get("sha256") or "")
        dedicated_id = str(cat_rc.get("identity") or "")
        script_path = base_root / script_rel
        if not script_path.is_file():
            out.append(f"5a: Run Command script {script_rel!r} is missing")
        else:
            actual_sha = hashlib.sha256(script_path.read_bytes()).hexdigest()
            if actual_sha != pinned_sha:
                out.append(f"5a: Run Command script {script_rel} sha256 {actual_sha} does not match catalog pin {pinned_sha}")
        if isinstance(plan_rc, dict):
            if "script" in plan_rc and plan_rc.get("script") != script_rel:
                out.append(f"5a: plan runCommand.script {plan_rc.get('script')!r} does not match catalog {script_rel!r}")
            if "sha256" in plan_rc and plan_rc.get("sha256") != pinned_sha:
                out.append(f"5a: plan runCommand.sha256 {plan_rc.get('sha256')!r} does not match catalog {pinned_sha!r}")
            if "identity" in plan_rc and plan_rc.get("identity") != dedicated_id:
                out.append(
                    f"5a: plan runCommand.identity {plan_rc.get('identity')!r} does not match dedicated identity {dedicated_id!r}"
                )
            if plan_rc.get("deleteRunCommandAfter", True) is not True:
                out.append("5a: Run Command must set deleteRunCommandAfter: true")
        if "runCommandSha256" in st and st.get("runCommandSha256") != pinned_sha:
            out.append("5a: live Run Command script sha256 does not match catalog pin")
        if isinstance(runner_env, dict) and "approveIdentity" in runner_env:
            if runner_env.get("approveIdentity") != dedicated_id:
                out.append(
                    f"5a: runner approveIdentity {runner_env.get('approveIdentity')!r} does not match dedicated {dedicated_id!r}"
                )

    # 4. Compliance gate (governance.md section 2.5)
    compliance_policy = load_compliance_policy(base_root)
    effect = str(plan.get("complianceEffect") or manifest.get("complianceEffect") or "none").strip()
    if effect.startswith("may-lower"):
        raw_controls = effect[len("may-lower") :].strip()
        controls = [c.strip() for c in raw_controls.split(",") if c.strip()]
        allowed_controls = set(compliance_policy.get("allowedMayLowerControls") or [])
        disallowed = [c for c in controls if c not in allowed_controls]
        if not controls or disallowed:
            out.append(
                f"2.5: complianceEffect {effect!r} refused: control(s) {disallowed or ['unspecified']} not allowed in governance/compliance.yml"
            )
        if st.get("observerVerdict") != "allow":
            out.append("2.5: may-lower plan requires an explicit observer verdict 'allow' (governance section 2.2, 2.5)")
        stop_spend = bool(plan.get("stopSpend") is True or action == "host.stop")
        floor = float(compliance_policy.get("floor", 70.0))
        score = st.get("secureScore")
        if not stop_spend:
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                out.append(f"2.5: may-lower plan requires current Defender secureScore (floor {floor})")
            elif float(score) < floor:
                out.append(f"2.5: secureScore {score} is below compliance floor {floor} in governance/compliance.yml")

    return out


def host_precondition_refusals(action: str, state: object, plan_id: str = "") -> list[str]:
    """Evaluates spec section 5a host preconditions against a snapshot or live state dict.
    Any missing or unreadable field fails closed."""
    if action not in HOST_ACTIONS:
        return []
    if not isinstance(state, dict):
        return ["5a: host state is missing or unreadable"]
    out: list[str] = []

    # 1. Never undrain a host someone else drained (notDrainedByOthers)
    allow_new_session = state.get("allowNewSession")
    drained_by_plan = state.get("drainedByPlanId")
    if allow_new_session is None or not isinstance(allow_new_session, bool):
        out.append("5a: allowNewSession is unknown (notDrainedByOthers fails closed)")
    elif allow_new_session is False and drained_by_plan not in (plan_id, None if action == "host.drain" and state.get("initialAllowNewSession") is False else plan_id):
        if not (action == "host.drain" and state.get("preserveInitialDrainState") is True):
            out.append("5a: notDrainedByOthers: host is already in drain mode set by another actor")

    # 2. Unknown sessions count as occupied (noActiveSessions)
    if action in ("host.restart-agent", "host.recycle-empty", "host.reregister", "host.stop"):
        heartbeat_fresh = state.get("heartbeatFresh")
        active_sessions = state.get("activeSessions")
        recent_connections = state.get("recentConnectionsInWindow")
        if heartbeat_fresh is not True or not isinstance(active_sessions, int) or isinstance(active_sessions, bool) or recent_connections is not False:
            out.append("5a: noActiveSessions: agent heartbeat must be fresh, activeSessions == 0, and no recent WVDConnections")
        elif active_sessions > 0:
            out.append(f"5a: noActiveSessions: host has {active_sessions} active session(s)")

    # 3. Not power-locked (notPowerLocked)
    power_locked = state.get("powerLocked")
    if power_locked is not False:
        out.append("5a: notPowerLocked: host carries power-locked tag or lock state is unknown")

    # 4. Not excluded from the scaling plan (notScalingExcluded)
    scaling_excluded = state.get("scalingExcluded")
    if scaling_excluded is not False:
        out.append("5a: notScalingExcluded: host carries scaling exclusion tag or state is unknown")

    # 5. Deallocated is not unhealthy
    power_state = state.get("powerState")
    if action != "host.resume" and power_state == "deallocated":
        out.append("5a: deallocated host is stopped on purpose and cannot be targeted by health/drain actions")
    elif not isinstance(power_state, str) or not power_state:
        out.append("5a: powerState is unknown")

    # 6. Availability floor (hostPoolAvailableHostsAbove)
    available_after = state.get("availableHostsAfterAction")
    required_floor = state.get("requiredAvailableHostsFloor")
    if (
        not isinstance(available_after, int)
        or isinstance(available_after, bool)
        or not isinstance(required_floor, int)
        or isinstance(required_floor, bool)
        or available_after < required_floor
    ):
        out.append("5a: hostPoolAvailableHostsAbove: available hosts after action would fall below demand + 1 or pool minimum")

    # 7. One host per pool at a time
    in_flight = state.get("inFlightPlanOnPool")
    if in_flight not in (None, False, "", plan_id):
        out.append("5a: another plan is already in flight on the same host pool")

    # 8. Service Health clear (serviceHealthClear: AVD)
    service_health = state.get("serviceHealthClear")
    if service_health is not True:
        out.append("5a: serviceHealthClear: active or unknown Azure Service Health incident in region")

    return out


def canonical_plan_hash(plan: dict, live_state: dict | None = None) -> str:
    """Computes the SHA-256 digest over the security-critical plan fields and resolved target state
    (spec section 5 steps 3 and 7)."""
    state = live_state if isinstance(live_state, dict) else (plan.get("preconditionState") if isinstance(plan.get("preconditionState"), dict) else {})
    why = plan.get("why") if isinstance(plan.get("why"), dict) else {}
    resolved_cloud = str(state.get("cloud") or why.get("cloud") or plan.get("cloud") or "commercial")
    material = {
        "planId": str(plan.get("planId", "")),
        "action": str(plan.get("action", "")),
        "cloud": resolved_cloud,
        "targetSurface": plan.get("targetSurface", "avd"),
        "desktopDevOpsAction": plan.get("desktopDevOpsAction"),
        "targetDigest": str(state.get("targetDigest") or plan.get("targetDigest") or ""),
        "initialAllowNewSession": state.get("allowNewSession"),
        "rollbackRestoreAllowNewSession": (plan.get("rollback") or {}).get("restoreAllowNewSession")
        if isinstance(plan.get("rollback"), dict)
        else None,
        "why": plan.get("why"),
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def check_plan(
    plan: object,
    live_state: dict | None = None,
    runner_env: dict | None = None,
    root: Path | None = None,
) -> dict:
    """Runs all gate rules on a plan and returns a redacted summary dict (never target names or IDs)."""
    if not isinstance(plan, dict):
        return {
            "planId": "unknown",
            "planHash": "",
            "action": "unknown",
            "passedCount": 0,
            "failedCount": 1,
            "status": "refused",
            "reasons": ["gate: plan must be a mapping"],
        }
    plan_id = str(plan.get("planId") or "unknown")
    action = str(plan.get("action") or "unknown")
    state = live_state if live_state is not None else plan.get("preconditionState")

    rule_groups = [
        evidence_refusals(plan),
        data_loss_guard_refusals(plan),
        [*why_refusals(plan), *cloud_refusals(plan, state, runner_env=runner_env)],
        [
            *host_precondition_refusals(action, state, plan_id=plan_id),
            *catalog_action_refusals(plan, state, runner_env=runner_env, root=root),
        ],
    ]
    reasons = [r for group in rule_groups for r in group]
    passed_count = sum(1 for group in rule_groups if not group)
    failed_count = len(reasons)
    digest = canonical_plan_hash(plan, state if isinstance(state, dict) else None)
    return {
        "planId": plan_id,
        "planHash": digest,
        "action": action,
        "passedCount": passed_count,
        "failedCount": failed_count,
        "status": "ready" if failed_count == 0 else "refused",
        "reasons": reasons,
    }


def apply_approved_plan(
    plan: dict,
    approved_hash: str,
    read_flag: Callable[[], str],
    read_live_state: Callable[[], dict],
    apply_action: Callable[[dict, dict], dict],
    verify_action: Callable[[dict, dict, dict], bool],
    runner_env: dict | None = None,
    root: Path | None = None,
) -> dict:
    """Executes an approved plan inside the `approve` environment after re-reading the kill switch and live state
    (spec section 5 step 7, section 5a, section 6d). Fails closed on any drift or missing verification."""
    plan_id = str(plan.get("planId") or "unknown")
    action = str(plan.get("action") or "unknown")

    # 1. Kill switch check before any live read or write
    try:
        position = str(read_flag()).strip()
    except Exception:
        return {
            "planId": plan_id,
            "planHash": approved_hash,
            "action": action,
            "passedCount": 0,
            "failedCount": 1,
            "status": "refused",
            "reasons": ["kill-switch: flag unreadable, refusing write"],
        }
    if position != "run":
        return {
            "planId": plan_id,
            "planHash": approved_hash,
            "action": action,
            "passedCount": 0,
            "failedCount": 1,
            "status": "refused",
            "reasons": [f"kill-switch: position {position!r} refuses writes"],
        }

    # 2. Live state re-read & gate re-evaluation (spec section 5 step 7)
    try:
        live_state = read_live_state()
    except Exception:
        return {
            "planId": plan_id,
            "planHash": approved_hash,
            "action": action,
            "passedCount": 0,
            "failedCount": 1,
            "status": "refused",
            "reasons": ["live-reread: failed to read live target state"],
        }

    gate_check = check_plan(plan, live_state=live_state, runner_env=runner_env, root=root)
    if gate_check["failedCount"] > 0:
        return {
            "planId": plan_id,
            "planHash": gate_check["planHash"],
            "action": action,
            "passedCount": gate_check["passedCount"],
            "failedCount": gate_check["failedCount"],
            "status": "refused",
            "reasons": gate_check["reasons"],
        }

    if gate_check["planHash"] != approved_hash:
        return {
            "planId": plan_id,
            "planHash": gate_check["planHash"],
            "action": action,
            "passedCount": gate_check["passedCount"],
            "failedCount": 1,
            "status": "refused",
            "reasons": ["live-reread: live plan hash differs from approved hash; a new approval is required"],
        }

    # 3. Apply action and require positive post-action verification + no compliance regression (spec section 5a, governance 2.5)
    try:
        effect = apply_action(plan, live_state)
        compliance_regressed = bool(
            (isinstance(effect, dict) and effect.get("complianceRegressed") is True)
            or (isinstance(live_state, dict) and live_state.get("complianceRegressed") is True)
        )
        verified = (not compliance_regressed) and (verify_action(plan, live_state, effect) is True)
    except Exception:
        compliance_regressed = False
        verified = False

    if not verified:
        reason = (
            "verify: compliance.regressed on target resource; rolling back"
            if compliance_regressed
            else "verify: positive post-action evidence missing or failed"
        )
        return {
            "planId": plan_id,
            "planHash": approved_hash,
            "action": action,
            "passedCount": gate_check["passedCount"],
            "failedCount": 1,
            "status": "verification-failed",
            "reasons": [reason],
        }

    return {
        "planId": plan_id,
        "planHash": approved_hash,
        "action": action,
        "passedCount": gate_check["passedCount"],
        "failedCount": 0,
        "status": "applied-and-verified",
        "reasons": [],
    }


def redacted_summary_markdown(result: dict) -> str:
    """Formats the workflow log and step summary (spec section 5 step 5, R17):
    carries only plan ID, hash, action name, gate rule counts, and status — never targets or names."""
    lines = [
        f"### Governance gate: `{result.get('planId', 'unknown')}`",
        "",
        f"- **Action:** `{result.get('action', 'unknown')}`",
        f"- **Plan hash:** `{result.get('planHash', '')}`",
        f"- **Status:** `{result.get('status', 'unknown')}`",
        f"- **Gate rules passed:** {int(result.get('passedCount', 0))}",
        f"- **Gate rules failed:** {int(result.get('failedCount', 0))}",
    ]
    return "\n".join(lines) + "\n"
