#!/usr/bin/env python3
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Offline policy engine validator and three-rule CI spike (core-and-adapters.md WP7; governance.md section 2.1; red team V1-6).

Checks:
1. `governance/policy.yml` is covered by `governance/protected-paths.yml` and satisfies all `V1-6` limits (`allow`/`deny`
   only, `offlineOnly: true`, `networkCallsAllowed: false`, no `require_approval`/`ask`/`quorum`/in-process kill switch).
2. Every `allow` rule action in `governance/policy.yml` exists in `catalog/actions/*.yml`.
3. Evaluates the three gate rules offline against `governance/`:
   - Rule 1 (`action_allowlist`): allows catalog S2 host actions on `avd` in `rg-avd-*`, refuses unknown actions,
     refuses explicit `deny` rules (`rg-*-swarm*` / RBAC assignments), and refuses `V1-6` `ask`/`require_approval`.
   - Rule 2 (`scope_and_blast_radius`): refuses `tenantWide: true`, out-of-scope resource groups, `hostsInPlan > 1`,
     `hostsTargetedOnPool > 1`, and `usersChangedCount > 5`.
   - Rule 3 (`rate_budget_and_window`): refuses `totalPlansInLastHour >= maxPlansPerHour`,
     `writesInLast24h >= maxWritesPerDay`, `estimatedUsd > perRunCapUsd`, `unpricedCount > 0`,
     and business-hours host actions lacking `businessHoursReason` while allowing `stopSpend: true` / `host.stop`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "gate") not in sys.path:
    sys.path.insert(0, str(ROOT / "gate"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from avd_gate import apply as gate_apply  # noqa: E402
from avd_gate import policy as gate_policy  # noqa: E402
from check_agent_pr import matches  # noqa: E402


def _valid_plan_and_state() -> tuple[dict[str, Any], dict[str, Any]]:
    plan = {
        "planId": "plan-wp7-spike-01",
        "action": "host.drain",
        "cloud": "commercial",
        "targetSurface": "avd",
        "targetScope": "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-avd-prod/providers/Microsoft.DesktopVirtualization/hostPools/hp-01/sessionHosts/sh-01",
        "evidence": [{"threadStartedBy": "avd-buddy"}],
        "why": {"kind": "fix", "decision": "0001"},
        "rollback": {"restoreAllowNewSession": True},
    }
    state = {
        "cloud": "commercial",
        "targetDigest": "sha256:sh-01-v1",
        "targetScope": plan["targetScope"],
        "allowNewSession": True,
        "heartbeatFresh": True,
        "activeSessions": 0,
        "recentConnectionsInWindow": False,
        "powerLocked": False,
        "scalingExcluded": False,
        "powerState": "running",
        "availableHostsAfterAction": 2,
        "requiredAvailableHostsFloor": 2,
        "inFlightPlanOnPool": None,
        "serviceHealthClear": True,
        "hostsInPlan": 1,
        "hostsTargetedOnPool": 1,
        "usersChangedCount": 0,
        "totalPlansInLastHour": 1,
        "writesInLast24h": 2,
        "spendEstimate": {
            "estimatedUsd": 0.50,
            "monthToDateUsd": 12.00,
            "pendingUsd": 0.00,
            "unpricedCount": 0,
            "openRunsOnSandbox": 0,
        },
        "inBusinessHours": False,
    }
    return plan, state


def run_three_rule_spike(root: Path = ROOT) -> list[str]:
    """Runs the offline three-rule policy evaluation spike against `governance/policy.yml` and `gate_apply.check_plan`."""
    errors: list[str] = []
    policy = gate_policy.load_policy(root)

    # Baseline valid plan passes all three rules and full gate check
    base_plan, base_state = _valid_plan_and_state()
    if gate_policy.policy_refusals(base_plan, base_state, root=root):
        errors.append("spike baseline: valid S2 plan was unexpectedly refused by policy_refusals")
    checked = gate_apply.check_plan(base_plan, live_state=base_state, root=root)
    if checked["status"] != "ready" or checked["failedCount"] != 0:
        errors.append(f"spike baseline: gate check_plan failed on valid plan ({checked['reasons']})")

    # Rule 1 probe: default-deny unknown action, explicit deny on rg-*-swarm*, and V1-6 forbidden 'ask'
    r1_unknown = gate_policy.rule_action_allowlist({**base_plan, "action": "host.reimage"}, base_state, policy)
    if not any("default-deny" in r for r in r1_unknown):
        errors.append("spike rule 1: failed to refuse unallowlisted action 'host.reimage'")

    r1_swarm_rg = gate_policy.rule_action_allowlist(
        base_plan,
        {
            **base_state,
            "targetScope": "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-avd-swarm-prod/providers/Microsoft.App/containerApps/api",
        },
        policy,
    )
    if not any("deny-swarm-control-plane" in r for r in r1_swarm_rg):
        errors.append("spike rule 1: explicit deny rule failed to outrank allow on rg-*-swarm*")

    r1_v1_6 = gate_policy.rule_action_allowlist({**base_plan, "policyDecision": "ask"}, base_state, policy)
    if not any("V1-6" in r for r in r1_v1_6):
        errors.append("spike rule 1: failed to refuse V1-6 forbidden 'ask' decision")

    # Rule 2 probe: scope & blast radius (tenantWide, >1 host, >5 users)
    r2_tenant = gate_policy.rule_scope_and_blast_radius({**base_plan, "tenantWide": True}, base_state, policy)
    if not any("allowTenantWide" in r for r in r2_tenant):
        errors.append("spike rule 2: failed to refuse tenantWide: true")

    r2_blast = gate_policy.rule_scope_and_blast_radius(
        base_plan, {**base_state, "hostsInPlan": 2, "usersChangedCount": 8}, policy
    )
    if not (any("maxHostsPerPlan" in r for r in r2_blast) and any("maxUsersPerGroupChange" in r for r in r2_blast)):
        errors.append("spike rule 2: failed to refuse blastRadius violations (hostsInPlan=2, usersChangedCount=8)")

    # Rule 3 probe: rate limits, spend budget gate, and business-hours window (with stop-spend exemption)
    r3_rates = gate_policy.rule_rate_budget_and_window(
        base_plan, {**base_state, "totalPlansInLastHour": 6, "writesInLast24h": 10}, policy
    )
    if not (any("maxPlansPerHour" in r for r in r3_rates) and any("maxWritesPerDay" in r for r in r3_rates)):
        errors.append("spike rule 3: failed to refuse rateLimits violations")

    r3_budget = gate_policy.rule_rate_budget_and_window(
        base_plan,
        {
            **base_state,
            "spendEstimate": {
                "estimatedUsd": 25.0,
                "monthToDateUsd": 140.0,
                "pendingUsd": 0.0,
                "unpricedCount": 1,
                "openRunsOnSandbox": 1,
            },
        },
        policy,
    )
    if len(r3_budget) < 4:
        errors.append(f"spike rule 3: expected 4 budgetGate refusals, got {r3_budget}")

    r3_window_blocked = gate_policy.rule_rate_budget_and_window(
        base_plan, {**base_state, "inBusinessHours": True}, policy
    )
    if not any("businessHoursReason" in r for r in r3_window_blocked):
        errors.append("spike rule 3: failed to refuse business-hours host action without businessHoursReason")

    r3_stop_spend_allowed = gate_policy.rule_rate_budget_and_window(
        {**base_plan, "action": "host.stop", "stopSpend": True},
        {**base_state, "inBusinessHours": True},
        policy,
    )
    if r3_stop_spend_allowed:
        errors.append(f"spike rule 3: stop-spend action was blocked during business hours ({r3_stop_spend_allowed})")

    return errors


def check(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    policy_path = root / "governance/policy.yml"
    if not policy_path.is_file():
        return ["governance/policy.yml: missing file"]

    protected_path = root / "governance/protected-paths.yml"
    if protected_path.is_file():
        protected_cfg = yaml.safe_load(protected_path.read_text(encoding="utf-8")) or {}
        patterns = protected_cfg.get("protected") or []
        if not any(matches("governance/policy.yml", p) for p in patterns):
            errors.append("governance/policy.yml: not covered by governance/protected-paths.yml")

    try:
        policy = gate_policy.load_policy(root)
    except Exception as exc:
        return [f"governance/policy.yml: failed to parse ({exc})"]

    catalog = gate_apply.load_action_catalog(root)
    errors.extend(gate_policy.validate_policy_schema(policy, catalog_actions=set(catalog)))
    if not errors:
        errors.extend(run_three_rule_spike(root))
    return errors


def main() -> int:
    findings = check(ROOT)
    for item in findings:
        print(f"::error::{item}")
    print(f"Policy engine and 3 gate rules checked against governance/: {len(findings)} finding(s).")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
