# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Tests for the S1a spike's token spend meter, spikes/s1a/agent_api/meter.py (spend.md section 3.7; red team R21, R22)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "spikes/s1a/agent_api"))
import meter  # noqa: E402


class Prices(unittest.TestCase):
    def test_cost_from_usage(self):
        usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
                 "cache_read_input_tokens": 1_000_000, "cache_creation_input_tokens": 1_000_000}
        self.assertAlmostEqual(meter.cost_usd("claude-opus-5-5", usage), 4 + 20 + 0.20 + 5)
        self.assertAlmostEqual(meter.cost_usd("claude-sonnet-5-5", {"input_tokens": 2_000, "output_tokens": 500}),
                               (2_000 * 2 + 500 * 10) / 1e6)

    def test_haiku_long_prompt_tier(self):
        short = meter.cost_usd("claude-haiku-5-5", {"input_tokens": 100_000, "output_tokens": 0})
        long = meter.cost_usd("claude-haiku-5-5", {"input_tokens": 100_001, "output_tokens": 0})
        self.assertAlmostEqual(short, 0.01)
        self.assertAlmostEqual(long, 100_001 * 0.50 / 1e6)

    def test_unpriced_model_refused(self):
        with self.assertRaises(meter.SpendRefused):
            meter.cost_usd("claude-unknown", {})
        with self.assertRaises(meter.SpendRefused):
            meter.Meter(cap_usd=10).reserve("claude-unknown", 10, 10)


class Cap(unittest.TestCase):
    def test_refuses_before_the_call_that_would_cross_the_cap(self):
        m = meter.Meter(cap_usd=1.00)
        # Worst case of one Opus call with 10K input and 16K output: 10K * $5 + 16K * $20 per MTok = $0.37.
        first = m.reserve("claude-opus-5-5", 10_000, 16_000)
        second = m.reserve("claude-opus-5-5", 10_000, 16_000)
        with self.assertRaises(meter.SpendRefused):
            m.reserve("claude-opus-5-5", 10_000, 16_000)  # 0.74 reserved + 0.37 > 1.00
        m.book(first, "claude-opus-5-5", {"input_tokens": 10_000, "output_tokens": 1_000})  # $0.06
        m.release(second)
        self.assertAlmostEqual(m.spent_usd, 0.06)
        m.reserve("claude-opus-5-5", 10_000, 16_000)  # room again once the real cost is booked

    def test_booked_spend_counts_without_any_delayed_data(self):
        m = meter.Meter(cap_usd=0.10)
        r = m.reserve("claude-sonnet-5-5", 1_000, 1_000)
        m.book(r, "claude-sonnet-5-5", {"input_tokens": 1_000, "output_tokens": 9_000})  # $0.092
        with self.assertRaises(meter.SpendRefused):
            m.reserve("claude-sonnet-5-5", 1_000, 1_000)

    def test_stop_refuses_every_later_call(self):
        m = meter.Meter(cap_usd=100)
        m.stop("halt-agents drill")
        with self.assertRaises(meter.SpendRefused) as raised:
            m.reserve("claude-haiku-5-5", 10, 10)
        self.assertIn("halt-agents drill", str(raised.exception))
        self.assertEqual(m.status()["stopped"], "halt-agents drill")

    def test_input_estimate_overstates(self):
        self.assertEqual(meter.estimate_input_tokens(30_000), 10_000)


if __name__ == "__main__":
    unittest.main()
