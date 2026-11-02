# avd-swarm gate: the evidence provenance and data-loss guard rules. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""V1-4 and D1: a plan needs evidence from a thread an operator started (spec section 3a; red team V1-4),
and destructive Desktop DevOps actions need a verified restore point or explicit discard-last-copy approval
(avd-second-brain decisions 0016 and 0027; avd-ops decision 0012).

Every finding and every evidence item carries `threadStartedBy`. Only `avd-buddy` means an operator asked;
`schedule` and `manual` (the check runner) and `alert` (the alert path, core-and-adapters.md WP6) are runs
nobody in avd-buddy started, so they may support a plan but never carry it alone. A value outside the
vocabulary, or a missing one, counts as not operator-started: the rule fails closed.
"""

from __future__ import annotations

OPERATOR = "avd-buddy"
UNATTENDED = ("schedule", "manual", "alert")
VOCABULARY = (OPERATOR, *UNATTENDED)

TARGET_SURFACES = ("avd", "win365", "intune")
DESTRUCTIVE_ACTIONS = ("reprovision", "deprovision", "replace-session-host")
NON_DESTRUCTIVE_ACTIONS = ("restart", "resize", "restore-point", "policy-pr")
DESKTOP_DEVOPS_ACTIONS = (*DESTRUCTIVE_ACTIONS, *NON_DESTRUCTIVE_ACTIONS)
MAX_RESTORE_POINT_AGE_HOURS = 24


def refusals(plan: object) -> list[str]:
    """Why the gate refuses this plan under V1-4; empty when it passes. plan: {"evidence": [{...}, ...]}."""
    evidence = plan.get("evidence") if isinstance(plan, dict) else None
    if not isinstance(evidence, list) or not evidence:
        return ["V1-4: a plan needs evidence"]
    found, unknown = [], []
    for item in evidence:
        started = item.get("threadStartedBy") if isinstance(item, dict) else None
        found.append(started)
        if started not in VOCABULARY:
            unknown.append(repr(started))
    out = [f"V1-4: threadStartedBy {u} isn't in {VOCABULARY}" for u in sorted(set(unknown))]
    if OPERATOR not in found:
        kinds = sorted({str(s) for s in found})
        out.append(f"V1-4: no evidence from a thread an operator started in avd-buddy (only {', '.join(kinds)})")
    return out


def data_loss_guard_refusals(plan: object) -> list[str]:
    """Why the gate refuses this plan under D1 (Desktop DevOps dataLossGuard, C9); empty when it passes."""
    if not isinstance(plan, dict):
        return ["D1: a plan must be a mapping"]
    out: list[str] = []
    surface = plan.get("targetSurface")
    if surface is not None and surface not in TARGET_SURFACES:
        out.append(f"D1: targetSurface {surface!r} isn't in {TARGET_SURFACES}")
    action = plan.get("desktopDevOpsAction")
    if action is None:
        return out
    if action not in DESKTOP_DEVOPS_ACTIONS:
        out.append(f"D1: desktopDevOpsAction {action!r} isn't in {DESKTOP_DEVOPS_ACTIONS}")
        return out
    if action not in DESTRUCTIVE_ACTIONS:
        return out
    guard = plan.get("dataLossGuard")
    if not isinstance(guard, dict):
        out.append(f"D1: destructive action {action!r} needs a dataLossGuard block")
        return out
    discard = guard.get("discardLastCopyApproved") is True
    verified = guard.get("restorePointVerified") is True
    age = guard.get("restorePointAgeHours")
    valid_age = isinstance(age, (int, float)) and not isinstance(age, bool) and 0 <= age <= MAX_RESTORE_POINT_AGE_HOURS
    if not discard and not (verified and valid_age):
        out.append(
            f"D1: destructive action {action!r} requires restorePointVerified=True with "
            f"restorePointAgeHours <= {MAX_RESTORE_POINT_AGE_HOURS} or discardLastCopyApproved=True"
        )
    return out
