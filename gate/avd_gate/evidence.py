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


CLOUDS = ("commercial", "usgov")

CLOUD_ENVIRONMENTS: dict[str, dict] = {
    "commercial": {
        "cloud": "commercial",
        "azCloudName": "AzureCloud",
        "mgGraphEnvironment": "Global",
        "loginEndpoint": "https://login.microsoftonline.com",
        "resourceManager": "https://management.azure.com",
        "graphEndpoint": "https://graph.microsoft.com",
        "logAnalyticsEndpoint": "https://api.loganalytics.io",
        "storageEndpoint": "https://storage.azure.com",
        "queueEndpointSuffix": "queue.core.windows.net",
        "capabilities": {
            "win365Supported": True,
            "win365MicrosoftHostedNetwork": True,
            "win365RequiresAnc": False,
            "win365FrontlineSupported": True,
        },
    },
    "usgov": {
        "cloud": "usgov",
        "azCloudName": "AzureUSGovernment",
        "mgGraphEnvironment": "USGov",
        "loginEndpoint": "https://login.microsoftonline.us",
        "resourceManager": "https://management.usgovcloudapi.net",
        "graphEndpoint": "https://graph.microsoft.us",
        "logAnalyticsEndpoint": "https://api.loganalytics.us",
        "storageEndpoint": "https://storage.azure.com",
        "queueEndpointSuffix": "queue.core.usgovcloudapi.net",
        "capabilities": {
            "win365Supported": True,
            "win365MicrosoftHostedNetwork": False,
            "win365RequiresAnc": True,
            "win365FrontlineSupported": False,
        },
    },
}


def resolve_cloud_environment(cloud: str = "commercial") -> dict:
    """Returns the canonical C26 cloud environment mapping for 'commercial' or 'usgov', failing closed on unknown values."""
    if not isinstance(cloud, str) or cloud not in CLOUD_ENVIRONMENTS:
        raise ValueError(f"C26: cloud {cloud!r} isn't in {CLOUDS}")
    return CLOUD_ENVIRONMENTS[cloud]


def cloud_refusals(plan: object, live_state: object = None, runner_env: dict | None = None) -> list[str]:
    """Why the gate refuses this plan under C26/C9/C10/C18 cloud boundary rules; empty when it passes.
    Verifies that the C9 plan's `cloud`, the C18 lifecycle/environment record (`why.cloud` and live `state.cloud`),
    any C10 outcome (`outcome.cloud`), and the runner's OIDC Azure/Graph cloud environment match before any token
    or write is permitted (govcloud-support-spec.md section 3.10)."""
    if not isinstance(plan, dict):
        return ["C26: a plan must be a mapping"]
    out: list[str] = []

    raw_plan_cloud = plan.get("cloud")
    if raw_plan_cloud is None:
        plan_cloud = "commercial"
    elif not isinstance(raw_plan_cloud, str) or raw_plan_cloud not in CLOUDS:
        out.append(f"C26: plan cloud {raw_plan_cloud!r} isn't in {CLOUDS}")
        return out
    else:
        plan_cloud = raw_plan_cloud

    expected = CLOUD_ENVIRONMENTS[plan_cloud]

    why = plan.get("why")
    if isinstance(why, dict) and "cloud" in why and why.get("cloud") is not None:
        why_cloud = why.get("cloud")
        if not isinstance(why_cloud, str) or why_cloud not in CLOUDS:
            out.append(f"C18: why.cloud {why_cloud!r} isn't in {CLOUDS}")
        elif why_cloud != plan_cloud:
            out.append(f"C18: target estate cloud {why_cloud!r} in C18 does not match approved C9 plan cloud {plan_cloud!r}")

    outcome = plan.get("outcome")
    if isinstance(outcome, dict) and "cloud" in outcome and outcome.get("cloud") is not None:
        outcome_cloud = outcome.get("cloud")
        if not isinstance(outcome_cloud, str) or outcome_cloud not in CLOUDS:
            out.append(f"C10: outcome.cloud {outcome_cloud!r} isn't in {CLOUDS}")
        elif outcome_cloud != plan_cloud:
            out.append(f"C10: outcome cloud {outcome_cloud!r} does not match approved C9 plan cloud {plan_cloud!r}")

    state = live_state if isinstance(live_state, dict) else (
        plan.get("preconditionState") if isinstance(plan.get("preconditionState"), dict) else {}
    )
    if "cloud" in state and state.get("cloud") is not None:
        state_cloud = state.get("cloud")
        if not isinstance(state_cloud, str) or state_cloud not in CLOUDS:
            out.append(f"C18: live estate cloud {state_cloud!r} isn't in {CLOUDS}")
        elif state_cloud != plan_cloud:
            out.append(f"C18: live estate cloud {state_cloud!r} does not match approved C9 plan cloud {plan_cloud!r}")

    # Runner OIDC Azure/Graph environment checks (from runner_env or live state)
    merged_runner: dict = {}
    if isinstance(state, dict):
        merged_runner.update(state)
    if isinstance(runner_env, dict):
        merged_runner.update(runner_env)

    runner_cloud = merged_runner.get("runnerCloud")
    if runner_cloud is not None and runner_cloud != "":
        if runner_cloud not in CLOUDS:
            out.append(f"C26: runnerCloud {runner_cloud!r} isn't in {CLOUDS}")
        elif runner_cloud != plan_cloud:
            out.append(f"C26: runnerCloud {runner_cloud!r} does not match plan cloud {plan_cloud!r}")

    az_env = merged_runner.get("azureEnvironment") or merged_runner.get("azCloudName")
    if az_env is not None and az_env != "":
        if az_env != expected["azCloudName"]:
            out.append(
                f"C26: runner OIDC Azure environment {az_env!r} does not match {expected['azCloudName']!r} for cloud {plan_cloud!r}"
            )

    graph_env = merged_runner.get("graphEnvironment") or merged_runner.get("mgGraphEnvironment")
    if graph_env is not None and graph_env != "":
        if graph_env != expected["mgGraphEnvironment"]:
            out.append(
                f"C26: runner OIDC Graph environment {graph_env!r} does not match {expected['mgGraphEnvironment']!r} for cloud {plan_cloud!r}"
            )

    arm_url = merged_runner.get("armEndpoint") or merged_runner.get("resourceManager")
    if arm_url is not None and arm_url != "":
        if str(arm_url).rstrip("/") != expected["resourceManager"]:
            out.append(
                f"C26: runner ARM endpoint {arm_url!r} does not match {expected['resourceManager']!r} for cloud {plan_cloud!r}"
            )

    graph_url = merged_runner.get("graphEndpoint")
    if graph_url is not None and graph_url != "":
        if str(graph_url).rstrip("/") != expected["graphEndpoint"]:
            out.append(
                f"C26: runner Graph endpoint {graph_url!r} does not match {expected['graphEndpoint']!r} for cloud {plan_cloud!r}"
            )

    # C26 capability guard for Windows 365 in usgov
    caps = expected["capabilities"]
    if plan.get("targetSurface") == "win365":
        net_kind = str(plan.get("networkConnectionType") or state.get("networkConnectionType") or "").lower()
        if net_kind == "mhn" and not caps.get("win365MicrosoftHostedNetwork", True):
            out.append(f"C26: Windows 365 Microsoft Hosted Network (MHN) is unsupported in cloud {plan_cloud!r}; ANC is required")
        prov_mode = str(plan.get("provisioningMode") or state.get("provisioningMode") or "").lower()
        if prov_mode == "frontline" and not caps.get("win365FrontlineSupported", True):
            out.append(f"C26: Windows 365 Frontline provisioning mode is unsupported in cloud {plan_cloud!r}")

    return out

