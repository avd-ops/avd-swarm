# avd-swarm gate: deterministic offline policy engine (governance.md section 2.1; core-and-adapters.md WP7; red team V1-6).
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Evaluates the three declarative gate policy rules in `governance/policy.yml` 100% offline:
1. Rule 1 (`action_allowlist`): default-deny evaluation of action ID, target surface, and target scope globs against
   `governance/policy.yml` `rules`, where explicit `deny` rules always outrank `allow` rules, and only `allow` and
   `deny` decisions exist (`V1-6`: `require_approval`, `ask`, `quorum`, in-process kill switch, and MCP security
   gateway are forbidden).
2. Rule 2 (`scope_and_blast_radius`): target resource scope glob check (`allowedTargetGlobs` and `deniedTargetGlobs`,
   forbidding `rg-*-swarm*`, `rg-*-runstore*`, RBAC/Policy assignments, and `tenantWide: true`) and blast-radius
   caps (`maxHostsPerPool`, `maxHostsPerPlan`, `maxUsersPerGroupChange`).
3. Rule 3 (`rate_budget_and_window`): cross-action rate caps (`maxPlansPerHour`, `maxWritesPerDay`), spend budget
   gate (`perRunCapUsd`, `monthlyCapUsd`, `allowUnpricedResources: false`, `requireSingleOpenRun: true`), and
   business-hours window rule (host actions during business hours require `businessHoursReason`, while `stopSpend`
   is never blocked per spend.md section 3.3).
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_DECISIONS = ("allow", "deny")
FORBIDDEN_V1_6_FEATURES = (
    "require_approval",
    "ask",
    "quorum",
    "in_process_kill_switch",
    "mcp_security_gateway",
)
REQUIRED_TOP_KEYS = (
    "engine",
    "defaultDecision",
    "limits",
    "rules",
    "scope",
    "blastRadius",
    "rateLimits",
    "budgetGate",
    "window",
)


def load_policy(root: Path | None = None) -> dict[str, Any]:
    path = (root or REPO_ROOT) / "governance/policy.yml"
    if not path.is_file() and root is not None:
        path = REPO_ROOT / "governance/policy.yml"
    if not path.is_file():
        raise FileNotFoundError(f"missing governance/policy.yml at {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("governance/policy.yml must be a YAML mapping")
    return data


def _matches_any(value: str, patterns: list[str]) -> bool:
    val = value.strip().lower()
    return any(fnmatch.fnmatchcase(val, str(pat).strip().lower()) for pat in patterns)


def validate_policy_schema(policy: object, catalog_actions: set[str] | None = None) -> list[str]:
    """Validates `governance/policy.yml` structure and V1-6 boundaries."""
    if not isinstance(policy, dict):
        return ["policy.yml: must be a mapping"]
    errors: list[str] = []
    for req in REQUIRED_TOP_KEYS:
        if req not in policy:
            errors.append(f"policy.yml: missing required key {req!r}")
    extra = set(policy) - set(REQUIRED_TOP_KEYS)
    if extra:
        errors.append(f"policy.yml: unknown top-level key(s): {', '.join(sorted(extra))}")

    if policy.get("engine") != "avd-swarm-policy/v1":
        errors.append("policy.yml: engine must be 'avd-swarm-policy/v1'")
    if policy.get("defaultDecision") != "deny":
        errors.append("policy.yml: defaultDecision must be 'deny'")

    # V1-6 limits check
    limits = policy.get("limits")
    if not isinstance(limits, dict):
        errors.append("policy.yml: limits must be a mapping")
    else:
        decisions = limits.get("allowedDecisions")
        if decisions != ["allow", "deny"]:
            errors.append("policy.yml: limits.allowedDecisions must be ['allow', 'deny'] (V1-6)")
        if limits.get("offlineOnly") is not True:
            errors.append("policy.yml: limits.offlineOnly must be true (V1-6)")
        if limits.get("networkCallsAllowed") is not False:
            errors.append("policy.yml: limits.networkCallsAllowed must be false (V1-6)")
        forbidden = set(limits.get("forbiddenFeatures") or [])
        missing_forbidden = set(FORBIDDEN_V1_6_FEATURES) - forbidden
        if missing_forbidden:
            errors.append(
                f"policy.yml: limits.forbiddenFeatures missing {', '.join(sorted(missing_forbidden))} (V1-6)"
            )

    for forbidden_key in FORBIDDEN_V1_6_FEATURES:
        if forbidden_key in policy:
            errors.append(f"policy.yml: V1-6 forbids top-level feature {forbidden_key!r}")

    rules = policy.get("rules")
    if not isinstance(rules, list) or not rules:
        errors.append("policy.yml: rules must be a non-empty list")
    else:
        seen_ids: set[str] = set()
        for idx, rule in enumerate(rules):
            if not isinstance(rule, dict):
                errors.append(f"policy.yml: rules[{idx}] must be a mapping")
                continue
            rid = str(rule.get("id") or "").strip()
            if not rid:
                errors.append(f"policy.yml: rules[{idx}] missing id")
            elif rid in seen_ids:
                errors.append(f"policy.yml: duplicate rule id {rid!r}")
            else:
                seen_ids.add(rid)
            decision = rule.get("decision")
            if decision not in ALLOWED_DECISIONS:
                errors.append(
                    f"policy.yml: rule {rid or idx} has decision {decision!r}; V1-6 allows only {ALLOWED_DECISIONS}"
                )
            for forbidden_key in FORBIDDEN_V1_6_FEATURES:
                if forbidden_key in rule:
                    errors.append(f"policy.yml: rule {rid or idx} uses forbidden V1-6 feature {forbidden_key!r}")
            for list_key in ("actions", "surfaces", "targetGlobs"):
                val = rule.get(list_key)
                if not isinstance(val, list) or not val or not all(isinstance(x, str) and x.strip() for x in val):
                    errors.append(f"policy.yml: rule {rid or idx} must have non-empty string list {list_key!r}")
            if decision == "allow" and catalog_actions is not None and isinstance(rule.get("actions"), list):
                for act in rule["actions"]:
                    if act != "*" and act not in catalog_actions:
                        errors.append(f"policy.yml: allow rule {rid} references unknown catalog action {act!r}")

    scope = policy.get("scope")
    if not isinstance(scope, dict):
        errors.append("policy.yml: scope must be a mapping")
    else:
        if scope.get("allowTenantWide") is not False:
            errors.append("policy.yml: scope.allowTenantWide must be false")
        for key in ("allowedTargetGlobs", "deniedTargetGlobs"):
            val = scope.get(key)
            if not isinstance(val, list) or not val:
                errors.append(f"policy.yml: scope.{key} must be a non-empty list")

    blast = policy.get("blastRadius")
    if not isinstance(blast, dict):
        errors.append("policy.yml: blastRadius must be a mapping")
    else:
        if blast.get("maxHostsPerPool") != 1 or blast.get("maxHostsPerPlan") != 1:
            errors.append("policy.yml: blastRadius.maxHostsPerPool and maxHostsPerPlan must be 1 in S2")
        users_cap = blast.get("maxUsersPerGroupChange")
        if isinstance(users_cap, bool) or not isinstance(users_cap, int) or not (1 <= users_cap <= 5):
            errors.append("policy.yml: blastRadius.maxUsersPerGroupChange must be an integer between 1 and 5")

    rates = policy.get("rateLimits")
    if not isinstance(rates, dict):
        errors.append("policy.yml: rateLimits must be a mapping")
    else:
        for key in ("maxPlansPerHour", "maxWritesPerDay"):
            val = rates.get(key)
            if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
                errors.append(f"policy.yml: rateLimits.{key} must be a positive integer")

    budget = policy.get("budgetGate")
    if not isinstance(budget, dict):
        errors.append("policy.yml: budgetGate must be a mapping")
    else:
        for key in ("perRunCapUsd", "monthlyCapUsd"):
            val = budget.get(key)
            if isinstance(val, bool) or not isinstance(val, (int, float)) or val <= 0:
                errors.append(f"policy.yml: budgetGate.{key} must be a positive number")
        if budget.get("allowUnpricedResources") is not False:
            errors.append("policy.yml: budgetGate.allowUnpricedResources must be false")
        if budget.get("requireSingleOpenRun") is not True:
            errors.append("policy.yml: budgetGate.requireSingleOpenRun must be true")

    window = policy.get("window")
    if not isinstance(window, dict):
        errors.append("policy.yml: window must be a mapping")
    else:
        if window.get("requireReasonDuringBusinessHours") is not True:
            errors.append("policy.yml: window.requireReasonDuringBusinessHours must be true")
        if window.get("stopSpendAlwaysAllowed") is not True:
            errors.append("policy.yml: window.stopSpendAlwaysAllowed must be true (spend.md section 3.3)")

    return errors


def rule_action_allowlist(plan: dict[str, Any], state: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    """Rule 1: Default-deny action & surface evaluation against `governance/policy.yml` `rules` and `V1-6` limits."""
    out: list[str] = []
    for forbidden_key in FORBIDDEN_V1_6_FEATURES:
        if forbidden_key in plan or forbidden_key in state:
            out.append(f"policy.rule1 (V1-6): forbidden feature {forbidden_key!r} is not permitted in plans or state")
    if plan.get("policyDecision") in ("ask", "require_approval", "quorum"):
        out.append(f"policy.rule1 (V1-6): policyDecision {plan.get('policyDecision')!r} is forbidden; only allow/deny exist")

    action = str(plan.get("action") or "").strip()
    surface = str(plan.get("targetSurface") or "avd").strip()
    target_scope = str(
        state.get("targetScope")
        or plan.get("targetScope")
        or "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-avd-prod/providers/Microsoft.DesktopVirtualization/hostPools/hp"
    ).strip()

    rules = policy.get("rules") if isinstance(policy.get("rules"), list) else []
    matched_allow = False
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        decision = rule.get("decision")
        if decision not in ALLOWED_DECISIONS:
            out.append(f"policy.rule1 (V1-6): rule {rule.get('id')!r} uses invalid decision {decision!r}")
            continue
        actions = [str(a) for a in (rule.get("actions") or [])]
        surfaces = [str(s) for s in (rule.get("surfaces") or [])]
        globs = [str(g) for g in (rule.get("targetGlobs") or [])]
        if _matches_any(action, actions) and _matches_any(surface, surfaces) and _matches_any(target_scope, globs):
            if decision == "deny":
                reason = str(rule.get("reason") or f"denied by rule {rule.get('id')}")
                out.append(f"policy.rule1 ({rule.get('id')}): {reason}")
            elif decision == "allow":
                matched_allow = True

    if not matched_allow:
        out.append(
            f"policy.rule1 (default-deny): action {action!r} on surface {surface!r} is not allowed by governance/policy.yml"
        )
    return out


def rule_scope_and_blast_radius(plan: dict[str, Any], state: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    """Rule 2: Resource scope globs (`allowedTargetGlobs`, `deniedTargetGlobs`, no `tenantWide`) and blast-radius caps."""
    out: list[str] = []
    scope_cfg = policy.get("scope") if isinstance(policy.get("scope"), dict) else {}
    blast_cfg = policy.get("blastRadius") if isinstance(policy.get("blastRadius"), dict) else {}

    if plan.get("tenantWide") is True or state.get("tenantWide") is True:
        out.append("policy.rule2 (scope): tenant-wide actions are refused (allowTenantWide: false)")

    target_scope = state.get("targetScope") or plan.get("targetScope")
    if target_scope is not None:
        scope_str = str(target_scope).strip()
        denied_globs = [str(g) for g in (scope_cfg.get("deniedTargetGlobs") or [])]
        allowed_globs = [str(g) for g in (scope_cfg.get("allowedTargetGlobs") or [])]
        if not scope_str or _matches_any(scope_str, denied_globs):
            out.append("policy.rule2 (scope): targetScope matches a denied resource scope in governance/policy.yml")
        elif allowed_globs and not _matches_any(scope_str, allowed_globs):
            out.append("policy.rule2 (scope): targetScope is outside allowedTargetGlobs in governance/policy.yml")

    max_hosts_pool = int(blast_cfg.get("maxHostsPerPool", 1))
    max_hosts_plan = int(blast_cfg.get("maxHostsPerPlan", 1))
    max_users_group = int(blast_cfg.get("maxUsersPerGroupChange", 5))

    hosts_in_plan = state.get("hostsInPlan", plan.get("hostsInPlan", 1))
    if isinstance(hosts_in_plan, bool) or not isinstance(hosts_in_plan, int) or hosts_in_plan < 1:
        out.append("policy.rule2 (blastRadius): hostsInPlan must be a positive integer")
    elif hosts_in_plan > max_hosts_plan:
        out.append(f"policy.rule2 (blastRadius): hostsInPlan ({hosts_in_plan}) exceeds maxHostsPerPlan ({max_hosts_plan})")

    hosts_on_pool = state.get("hostsTargetedOnPool", plan.get("hostsTargetedOnPool", 1))
    if isinstance(hosts_on_pool, bool) or not isinstance(hosts_on_pool, int) or hosts_on_pool < 1:
        out.append("policy.rule2 (blastRadius): hostsTargetedOnPool must be a positive integer")
    elif hosts_on_pool > max_hosts_pool:
        out.append(
            f"policy.rule2 (blastRadius): hostsTargetedOnPool ({hosts_on_pool}) exceeds maxHostsPerPool ({max_hosts_pool})"
        )

    users_changed = state.get("usersChangedCount", plan.get("usersChangedCount", 0))
    if isinstance(users_changed, bool) or not isinstance(users_changed, int) or users_changed < 0:
        out.append("policy.rule2 (blastRadius): usersChangedCount must be a non-negative integer")
    elif users_changed > max_users_group:
        out.append(
            f"policy.rule2 (blastRadius): usersChangedCount ({users_changed}) exceeds maxUsersPerGroupChange ({max_users_group})"
        )

    return out


def rule_rate_budget_and_window(plan: dict[str, Any], state: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    """Rule 3: Cross-action rate limits, spend budget gate, and business-hours window rule."""
    out: list[str] = []
    rates_cfg = policy.get("rateLimits") if isinstance(policy.get("rateLimits"), dict) else {}
    budget_cfg = policy.get("budgetGate") if isinstance(policy.get("budgetGate"), dict) else {}
    window_cfg = policy.get("window") if isinstance(policy.get("window"), dict) else {}

    max_plans_hour = int(rates_cfg.get("maxPlansPerHour", 6))
    max_writes_day = int(rates_cfg.get("maxWritesPerDay", 10))

    total_plans_hour = state.get("totalPlansInLastHour", 0)
    if isinstance(total_plans_hour, bool) or not isinstance(total_plans_hour, int) or total_plans_hour < 0:
        out.append("policy.rule3 (rateLimits): totalPlansInLastHour must be a non-negative integer")
    elif total_plans_hour >= max_plans_hour:
        out.append(
            f"policy.rule3 (rateLimits): totalPlansInLastHour ({total_plans_hour}) reaches maxPlansPerHour ({max_plans_hour})"
        )

    writes_day = state.get("writesInLast24h", 0)
    if isinstance(writes_day, bool) or not isinstance(writes_day, int) or writes_day < 0:
        out.append("policy.rule3 (rateLimits): writesInLast24h must be a non-negative integer")
    elif writes_day >= max_writes_day:
        out.append(f"policy.rule3 (rateLimits): writesInLast24h ({writes_day}) reaches maxWritesPerDay ({max_writes_day})")

    # Budget gate check when spendEstimate is present on plan or state
    spend = state.get("spendEstimate") if isinstance(state.get("spendEstimate"), dict) else plan.get("spendEstimate")
    if isinstance(spend, dict):
        per_run_cap = float(budget_cfg.get("perRunCapUsd", 20.0))
        monthly_cap = float(budget_cfg.get("monthlyCapUsd", 150.0))
        est_usd = spend.get("estimatedUsd", 0.0)
        mtd_usd = spend.get("monthToDateUsd", 0.0)
        pending_usd = spend.get("pendingUsd", 0.0)
        unpriced = spend.get("unpricedCount", 0)
        open_runs = spend.get("openRunsOnSandbox", 0)

        if isinstance(est_usd, bool) or not isinstance(est_usd, (int, float)) or est_usd < 0:
            out.append("policy.rule3 (budgetGate): spendEstimate.estimatedUsd must be a non-negative number")
        elif float(est_usd) > per_run_cap:
            out.append(
                f"policy.rule3 (budgetGate): estimatedUsd (${float(est_usd):.2f}) exceeds perRunCapUsd (${per_run_cap:.2f})"
            )

        if (
            not isinstance(mtd_usd, bool)
            and isinstance(mtd_usd, (int, float))
            and not isinstance(pending_usd, bool)
            and isinstance(pending_usd, (int, float))
            and not isinstance(est_usd, bool)
            and isinstance(est_usd, (int, float))
        ):
            projected = float(mtd_usd) + float(pending_usd) + float(est_usd)
            if projected > monthly_cap:
                out.append(
                    f"policy.rule3 (budgetGate): projected monthly spend (${projected:.2f}) exceeds monthlyCapUsd (${monthly_cap:.2f})"
                )

        if budget_cfg.get("allowUnpricedResources") is False and (
            isinstance(unpriced, bool) or not isinstance(unpriced, int) or unpriced > 0
        ):
            out.append(f"policy.rule3 (budgetGate): unpriced resource(s) ({unpriced}) refused")

        if budget_cfg.get("requireSingleOpenRun") is True and (
            isinstance(open_runs, bool) or not isinstance(open_runs, int) or open_runs > 0
        ):
            out.append(f"policy.rule3 (budgetGate): sandbox already has {open_runs} open run(s)")

    # Window rule: host actions during business hours need an explicit reason, except stop-spend (spend.md section 3.3)
    action = str(plan.get("action") or "")
    stop_spend = bool(plan.get("stopSpend") is True or action == "host.stop")
    in_business_hours = bool(state.get("inBusinessHours") is True or plan.get("inBusinessHours") is True)
    if window_cfg.get("requireReasonDuringBusinessHours") is True and in_business_hours and not stop_spend:
        reason = str(plan.get("businessHoursReason") or state.get("businessHoursReason") or "").strip()
        if not reason:
            out.append(
                "policy.rule3 (window): action during business hours requires businessHoursReason (stop-spend is exempt)"
            )

    return out


def policy_refusals(plan: object, state: object = None, root: Path | None = None) -> list[str]:
    """Evaluates all three gate policy rules in `governance/policy.yml` against `plan` and `state`."""
    if not isinstance(plan, dict):
        return ["policy: plan must be a mapping"]
    st = state if isinstance(state, dict) else {}
    try:
        policy = load_policy(root)
    except Exception as exc:
        return [f"policy: failed to load governance/policy.yml ({type(exc).__name__})"]
    schema_errors = validate_policy_schema(policy)
    if schema_errors:
        return schema_errors
    return [
        *rule_action_allowlist(plan, st, policy),
        *rule_scope_and_blast_radius(plan, st, policy),
        *rule_rate_budget_and_window(plan, st, policy),
    ]


def classify_policy_change(
    path: str, before: dict[str, Any] | None, after: dict[str, Any] | None
) -> tuple[str, list[str]]:
    """Classifies changes to `governance/policy.yml` as `lowers` or `raises` capability for `check_timelock.py`."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return "raises", [f"{path}: added, removed, or invalid YAML"]
    schema_errs = validate_policy_schema(after)
    if schema_errs:
        return "raises", schema_errs

    for fixed_key in ("engine", "defaultDecision", "limits", "window"):
        if before.get(fixed_key) != after.get(fixed_key):
            return "raises", [f"{path}:{fixed_key}: changed"]

    reasons: list[str] = []

    # Compare scope globs
    b_scope = before.get("scope") if isinstance(before.get("scope"), dict) else {}
    a_scope = after.get("scope") if isinstance(after.get("scope"), dict) else {}
    if b_scope != a_scope:
        if b_scope.get("allowTenantWide") != a_scope.get("allowTenantWide"):
            return "raises", [f"{path}:scope.allowTenantWide: changed"]
        b_allow = set(b_scope.get("allowedTargetGlobs") or [])
        a_allow = set(a_scope.get("allowedTargetGlobs") or [])
        if a_allow - b_allow:
            return "raises", [f"{path}:scope.allowedTargetGlobs: added {', '.join(sorted(a_allow - b_allow))}"]
        if b_allow - a_allow:
            reasons.append(f"{path}:scope.allowedTargetGlobs: removed {', '.join(sorted(b_allow - a_allow))}")
        b_deny = set(b_scope.get("deniedTargetGlobs") or [])
        a_deny = set(a_scope.get("deniedTargetGlobs") or [])
        if b_deny - a_deny:
            return "raises", [f"{path}:scope.deniedTargetGlobs: removed {', '.join(sorted(b_deny - a_deny))}"]
        if a_deny - b_deny:
            reasons.append(f"{path}:scope.deniedTargetGlobs: added {', '.join(sorted(a_deny - b_deny))}")

    # Compare numeric caps in blastRadius, rateLimits, and budgetGate
    for section in ("blastRadius", "rateLimits", "budgetGate"):
        b_sec = before.get(section) if isinstance(before.get(section), dict) else {}
        a_sec = after.get(section) if isinstance(after.get(section), dict) else {}
        if b_sec == a_sec:
            continue
        for k in sorted(set(b_sec) | set(a_sec)):
            if b_sec.get(k) == a_sec.get(k):
                continue
            ov, nv = b_sec.get(k), a_sec.get(k)
            if isinstance(ov, bool) or isinstance(nv, bool) or not isinstance(ov, (int, float)) or not isinstance(nv, (int, float)):
                return "raises", [f"{path}:{section}.{k}: changed ({ov!r} -> {nv!r})"]
            if nv > ov or nv <= 0:
                return "raises", [f"{path}:{section}.{k}: raised or invalid ({ov} -> {nv})"]
            reasons.append(f"{path}:{section}.{k}: lowered from {ov} to {nv}")

    # Compare rules
    b_rules = {r["id"]: r for r in (before.get("rules") or []) if isinstance(r, dict) and "id" in r}
    a_rules = {r["id"]: r for r in (after.get("rules") or []) if isinstance(r, dict) and "id" in r}
    if b_rules != a_rules:
        for rid in sorted(set(b_rules) | set(a_rules)):
            br, ar = b_rules.get(rid), a_rules.get(rid)
            if br == ar:
                continue
            if br is None and ar is not None:
                if ar.get("decision") == "deny":
                    reasons.append(f"{path}:rules.{rid}: added deny rule")
                    continue
                return "raises", [f"{path}:rules.{rid}: added allow rule"]
            if br is not None and ar is None:
                if br.get("decision") == "allow":
                    reasons.append(f"{path}:rules.{rid}: removed allow rule")
                    continue
                return "raises", [f"{path}:rules.{rid}: removed deny rule"]
            assert br is not None and ar is not None
            if br.get("decision") != ar.get("decision"):
                if br.get("decision") == "allow" and ar.get("decision") == "deny":
                    reasons.append(f"{path}:rules.{rid}: changed allow to deny")
                    continue
                return "raises", [f"{path}:rules.{rid}: decision changed to allow"]
            if br.get("decision") == "allow":
                for list_key in ("actions", "surfaces", "targetGlobs"):
                    b_set, a_set = set(br.get(list_key) or []), set(ar.get(list_key) or [])
                    if a_set - b_set:
                        return "raises", [f"{path}:rules.{rid}.{list_key}: added {', '.join(sorted(a_set - b_set))}"]
                    if b_set - a_set:
                        reasons.append(f"{path}:rules.{rid}.{list_key}: removed {', '.join(sorted(b_set - a_set))}")
            else:
                return "raises", [f"{path}:rules.{rid}: modified deny rule"]

    if not reasons:
        return "raises", [f"{path}: modified without tightening policy"]
    return "lowers", reasons
