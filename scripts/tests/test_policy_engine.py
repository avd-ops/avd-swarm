# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Unit tests for the WP7 offline policy engine (gate/avd_gate/policy.py, scripts/check_policy.py, governance/policy.yml)."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "gate") not in sys.path:
    sys.path.insert(0, str(ROOT / "gate"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from avd_gate import apply as gate_apply  # noqa: E402
from avd_gate import policy as gate_policy  # noqa: E402
import check_policy  # noqa: E402
import check_timelock  # noqa: E402


class PolicyEngineTests(unittest.TestCase):
    def setUp(self):
        self.policy = gate_policy.load_policy(ROOT)
        self.plan, self.state = check_policy._valid_plan_and_state()

    def test_repo_policy_and_three_rule_spike_pass(self):
        self.assertEqual(check_policy.check(ROOT), [])

    def test_rule1_allows_s2_catalog_actions_and_refuses_unknown_or_denied_or_v1_6(self):
        for action in ("host.drain", "host.restart-agent", "host.recycle-empty", "host.reregister", "host.stop", "host.resume"):
            self.assertEqual(
                gate_policy.rule_action_allowlist({**self.plan, "action": action}, self.state, self.policy),
                [],
                action,
            )

        unknown = gate_policy.rule_action_allowlist({**self.plan, "action": "host.delete-vm"}, self.state, self.policy)
        self.assertTrue(any("default-deny" in r for r in unknown), unknown)

        for denied_scope in (
            "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-avd-swarm-01/providers/Microsoft.App/containerApps/api",
            "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-avd-runstore/providers/Microsoft.Storage/storageAccounts/st",
            "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-avd-prod/providers/Microsoft.Authorization/roleAssignments/ra",
        ):
            refused = gate_policy.rule_action_allowlist(self.plan, {**self.state, "targetScope": denied_scope}, self.policy)
            self.assertTrue(any("deny-swarm-control-plane" in r for r in refused), (denied_scope, refused))

        for bad_feature in gate_policy.FORBIDDEN_V1_6_FEATURES:
            refused_feat = gate_policy.rule_action_allowlist({**self.plan, bad_feature: True}, self.state, self.policy)
            self.assertTrue(any("V1-6" in r for r in refused_feat), (bad_feature, refused_feat))

        ask_decision = gate_policy.rule_action_allowlist({**self.plan, "policyDecision": "ask"}, self.state, self.policy)
        self.assertTrue(any("V1-6" in r for r in ask_decision), ask_decision)

    def test_rule2_enforces_scope_and_blast_radius(self):
        tenant_wide = gate_policy.rule_scope_and_blast_radius({**self.plan, "tenantWide": True}, self.state, self.policy)
        self.assertTrue(any("allowTenantWide" in r for r in tenant_wide), tenant_wide)

        unlisted_rg = gate_policy.rule_scope_and_blast_radius(
            self.plan,
            {
                **self.state,
                "targetScope": "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-shared-network/providers/Microsoft.Network/virtualNetworks/vnet",
            },
            self.policy,
        )
        self.assertTrue(any("outside allowedTargetGlobs" in r for r in unlisted_rg), unlisted_rg)

        blast = gate_policy.rule_scope_and_blast_radius(
            self.plan,
            {**self.state, "hostsInPlan": 2, "hostsTargetedOnPool": 2, "usersChangedCount": 6},
            self.policy,
        )
        self.assertEqual(len(blast), 3, blast)

    def test_rule3_enforces_rates_budget_and_business_hours_with_stop_spend_exemption(self):
        rates = gate_policy.rule_rate_budget_and_window(
            self.plan,
            {**self.state, "totalPlansInLastHour": 6, "writesInLast24h": 10},
            self.policy,
        )
        self.assertEqual(len(rates), 2, rates)

        budget = gate_policy.rule_rate_budget_and_window(
            self.plan,
            {
                **self.state,
                "spendEstimate": {
                    "estimatedUsd": 25.0,
                    "monthToDateUsd": 140.0,
                    "pendingUsd": 5.0,
                    "unpricedCount": 2,
                    "openRunsOnSandbox": 1,
                },
            },
            self.policy,
        )
        self.assertEqual(len(budget), 4, budget)

        window_refused = gate_policy.rule_rate_budget_and_window(
            self.plan,
            {**self.state, "inBusinessHours": True},
            self.policy,
        )
        self.assertTrue(any("businessHoursReason" in r for r in window_refused), window_refused)

        window_with_reason = gate_policy.rule_rate_budget_and_window(
            {**self.plan, "businessHoursReason": "drain empty host with broken agent"},
            {**self.state, "inBusinessHours": True},
            self.policy,
        )
        self.assertEqual(window_with_reason, [])

        stop_spend_exempt = gate_policy.rule_rate_budget_and_window(
            {**self.plan, "action": "host.stop", "stopSpend": True},
            {**self.state, "inBusinessHours": True},
            self.policy,
        )
        self.assertEqual(stop_spend_exempt, [])

    def test_gate_apply_check_plan_wires_policy_refusals(self):
        bad_plan = {**self.plan, "tenantWide": True}
        checked = gate_apply.check_plan(bad_plan, live_state=self.state, root=ROOT)
        self.assertEqual(checked["status"], "refused")
        self.assertTrue(any("allowTenantWide" in r for r in checked["reasons"]), checked["reasons"])

    def test_schema_validation_rejects_v1_6_violations(self):
        bad = copy.deepcopy(self.policy)
        bad["rules"][0]["decision"] = "require_approval"
        errs = gate_policy.validate_policy_schema(bad)
        self.assertTrue(any("V1-6" in e for e in errs), errs)

        bad2 = copy.deepcopy(self.policy)
        bad2["limits"]["networkCallsAllowed"] = True
        errs2 = gate_policy.validate_policy_schema(bad2)
        self.assertTrue(any("networkCallsAllowed must be false" in e for e in errs2), errs2)

    def test_timelock_classifies_policy_tightening_as_lowers_and_loosening_as_raises(self):
        protected_policy = yaml.safe_load((ROOT / "governance/protected-paths.yml").read_text(encoding="utf-8"))
        raw = (ROOT / "governance/policy.yml").read_text(encoding="utf-8")

        tighter = raw.replace("maxPlansPerHour: 6", "maxPlansPerHour: 3").replace("      - host.resume\n", "")
        res_low = check_timelock.classify_change("governance/policy.yml", raw, tighter, protected_policy)
        self.assertTrue(res_low["protected"])
        self.assertEqual(res_low["effect"], "lowers", res_low)

        looser = raw.replace("maxPlansPerHour: 6", "maxPlansPerHour: 12")
        res_high = check_timelock.classify_change("governance/policy.yml", raw, looser, protected_policy)
        self.assertTrue(res_high["protected"])
        self.assertEqual(res_high["effect"], "raises", res_high)


if __name__ == "__main__":
    unittest.main()
