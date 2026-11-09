#!/usr/bin/env python3
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Tests for section 4a deterministic code claim check and post-merge sentence check (docs/spec.md section 4a)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import claim_check as cc  # noqa: E402

REGISTER = cc.load_fact_register(ROOT)


class ClaimCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tool_outputs = {
            "call-mcp-hosts": {
                "hostPool": "hp-contoso-prod",
                "maxSessionLimit": 8,
                "sessionHosts": [
                    {"name": "sh-pooled-01", "status": "Available", "sessions": 4},
                    {"name": "sh-pooled-02", "status": "Available", "sessions": 0},
                ],
            },
            "call-cost-01": {
                "spentUsd": 142.368,
                "unit": "USD",
                "generatedUtc": "2026-11-08T08:00:00Z",
            },
            "c11.hostPool.maxSessionLimit": {
                "id": "c11.hostPool.maxSessionLimit",
                "value": 8,
                "generatedUtc": "2026-11-08T06:00:00Z",
            },
            "c12.sizing.p95RamGb@hp-contoso-prod": {
                "id": "c12.sizing.p95RamGb@hp-contoso-prod",
                "value": 14.2,
                "unit": "GB",
                "windowEndUtc": "2026-06-01T00:00:00Z",
            },
        }

    def test_supported_exact_and_rounded_values_pass(self) -> None:
        verdicts = cc.check_claims(
            [
                {
                    "claim": "hp-contoso-prod has maxSessionLimit 8",
                    "value": 8,
                    "unit": "sessions",
                    "citations": ["call-mcp-hosts", "c11.hostPool.maxSessionLimit"],
                },
                {
                    "claim": "sh-pooled-01 is Available",
                    "value": "sh-pooled-01",
                    "unit": "",
                    "citations": ["call-mcp-hosts"],
                },
                {
                    "claim": "Month-to-date spend is $142.37",
                    "value": 142.37,
                    "unit": "USD",
                    "citations": ["call-cost-01"],
                },
            ],
            self.tool_outputs,
            REGISTER,
            now_utc="2026-11-08T12:00:00Z",
        )
        self.assertTrue(all(v.status == cc.SUPPORTED for v in verdicts))

    def test_inference_claim_without_value_is_marked_unchecked(self) -> None:
        verdict = cc.check_claim(
            {
                "claim": "sh-pooled-02 is idle because breadth-first routing saturated sh-pooled-01 first",
                "value": None,
                "unit": "",
                "citations": ["call-mcp-hosts"],
            },
            self.tool_outputs,
            REGISTER,
        )
        self.assertEqual(verdict.status, cc.UNCHECKED)
        self.assertIn("inference-needs-verifier", verdict.reasons)

    def test_catches_all_six_auditor_seeded_fault_kinds(self) -> None:
        # 1. number-changed: 8 changed to 12
        v_num = cc.check_claim(
            {"claim": "maxSessionLimit is 12", "value": 12, "unit": "sessions", "citations": ["call-mcp-hosts"]},
            self.tool_outputs,
            REGISTER,
        )
        self.assertEqual(v_num.status, cc.UNSUPPORTED)
        self.assertIn("value-not-in-output", v_num.reasons)

        # 2. name-swapped: sh-pooled-01 swapped to sh-pooled-99
        v_name = cc.check_claim(
            {"claim": "sh-pooled-99 is Available", "value": "sh-pooled-99", "citations": ["call-mcp-hosts"]},
            self.tool_outputs,
            REGISTER,
        )
        self.assertEqual(v_name.status, cc.UNSUPPORTED)
        self.assertIn("value-not-in-output", v_name.reasons)

        # 3. claim-added (uncited claim)
        v_uncited = cc.check_claim(
            {"claim": "Autoscale is disabled", "value": "disabled", "citations": []},
            self.tool_outputs,
            REGISTER,
        )
        self.assertEqual(v_uncited.status, cc.UNSUPPORTED)
        self.assertIn("uncited", v_uncited.reasons)

        # 4. fact-deleted (citation points to a tool call or C15 fact that does not exist or is not built)
        v_missing_call = cc.check_claim(
            {"claim": "Deleted tool call", "value": 8, "citations": ["call-deleted-99"]},
            self.tool_outputs,
            REGISTER,
        )
        self.assertEqual(v_missing_call.status, cc.UNSUPPORTED)
        self.assertIn("missing-tool-call:call-deleted-99", v_missing_call.reasons)

        v_unregistered_fact = cc.check_claim(
            {"claim": "Unregistered C11 fact", "value": 8, "citations": ["c11.hostPool.doesNotExist"]},
            {**self.tool_outputs, "c11.hostPool.doesNotExist": {"value": 8}},
            REGISTER,
        )
        self.assertEqual(v_unregistered_fact.status, cc.UNSUPPORTED)
        self.assertIn("unregistered-fact:c11.hostPool.doesNotExist", v_unregistered_fact.reasons)

        # 5. unit-swapped: USD swapped to percent when output specifies USD
        v_unit = cc.check_claim(
            {"claim": "Spend is 142.37%", "value": 142.37, "unit": "%", "citations": ["call-cost-01"]},
            self.tool_outputs,
            REGISTER,
        )
        self.assertEqual(v_unit.status, cc.UNSUPPORTED)
        self.assertIn("unit-mismatch", v_unit.reasons)

        # 6. stale-snapshot-cited: windowEndUtc > 90 days before now_utc
        # Use a mock register entry where c12.sizing.p95RamGb is built so we isolate the staleness check
        mock_reg = {**REGISTER, "c12.sizing.p95RamGb": {"id": "c12.sizing.p95RamGb", "status": "built", "contract": "C12"}}
        v_stale = cc.check_claim(
            {
                "claim": "p95 RAM is 14.2 GB",
                "value": 14.2,
                "unit": "GB",
                "citations": ["c12.sizing.p95RamGb@hp-contoso-prod"],
            },
            self.tool_outputs,
            mock_reg,
            now_utc="2026-11-08T12:00:00Z",
            max_snapshot_age_days=90,
        )
        self.assertEqual(v_stale.status, cc.UNSUPPORTED)
        self.assertIn("stale-snapshot-cited:c12.sizing.p95RamGb@hp-contoso-prod", v_stale.reasons)


class MergeCheckTests(unittest.TestCase):
    def test_merged_answer_requires_supported_citations_on_factual_sentences(self) -> None:
        tool_outputs = {
            "call-mcp-01": {"hostPool": "hp-contoso-prod", "maxSessionLimit": 8},
            "call-bad-01": {"hostPool": "hp-contoso-prod", "maxSessionLimit": 8},
        }
        verdicts = cc.check_claims(
            [
                {"claim": "maxSessionLimit is 8", "value": 8, "citations": ["call-mcp-01"]},
                {"claim": "maxSessionLimit is 16", "value": 16, "citations": ["call-bad-01"]},
            ],
            tool_outputs,
        )

        # Clean merge: states supported fact with [call-mcp-01] and lists unconfirmed item with "Not confirmed:"
        good_answer = (
            "Here is what I found. "
            "Host pool `hp-contoso-prod` has a `maxSessionLimit` of 8 [call-mcp-01]. "
            "Not confirmed: whether autoscale is enabled."
        )
        res_good = cc.check_merged_answer(good_answer, verdicts)
        self.assertTrue(res_good["valid"])
        self.assertEqual(res_good["failingSentences"], [])

        # Failing merge 1: states a factual sentence with no citation
        uncited_answer = "Host pool `hp-contoso-prod` has a `maxSessionLimit` of 8."
        res_uncited = cc.check_merged_answer(uncited_answer, verdicts)
        self.assertFalse(res_uncited["valid"])
        self.assertEqual(len(res_uncited["failingSentences"]), 1)

        # Failing merge 2: cites an unsupported claim [call-bad-01]
        bad_cite_answer = "Host pool `hp-contoso-prod` has a `maxSessionLimit` of 16 [call-bad-01]."
        res_bad_cite = cc.check_merged_answer(bad_cite_answer, verdicts)
        self.assertFalse(res_bad_cite["valid"])
        self.assertEqual(len(res_bad_cite["failingSentences"]), 1)


if __name__ == "__main__":
    unittest.main()
