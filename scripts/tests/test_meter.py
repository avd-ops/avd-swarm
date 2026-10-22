# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Tests for the gateway's token spend meter, gateway/avd_gateway/meter.py, priced from governance/model-prices.yml
(spend.md section 3.7; red team R21, R22)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))
from avd_gateway import meter  # noqa: E402
from avd_gateway.prices import PriceBook  # noqa: E402

BOOK = PriceBook.load(ROOT / "governance/model-prices.yml")


def new_meter(cap_usd):
    return meter.Meter(cap_usd=cap_usd, prices=BOOK)


def cost_usd(model, usage):
    return BOOK.cost_usd("anthropic", model, usage)


class Prices(unittest.TestCase):
    def test_cost_from_usage(self):
        usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
                 "cache_read_input_tokens": 1_000_000, "cache_creation_input_tokens": 1_000_000}
        self.assertAlmostEqual(cost_usd("claude-opus-5-5", usage), 4 + 20 + 0.20 + 5)
        self.assertAlmostEqual(cost_usd("claude-sonnet-5-5", {"input_tokens": 2_000, "output_tokens": 500}),
                               (2_000 * 2 + 500 * 10) / 1e6)

    def test_haiku_long_prompt_tier(self):
        short = cost_usd("claude-haiku-5-5", {"input_tokens": 100_000, "output_tokens": 0})
        long = cost_usd("claude-haiku-5-5", {"input_tokens": 100_001, "output_tokens": 0})
        self.assertAlmostEqual(short, 0.01)
        self.assertAlmostEqual(long, 100_001 * 0.50 / 1e6)

    def test_unpriced_model_refused(self):
        with self.assertRaises(meter.SpendRefused):
            cost_usd("claude-unknown", {})
        with self.assertRaises(meter.SpendRefused):
            new_meter(10).reserve("claude-unknown", 10, 10)


class Cap(unittest.TestCase):
    def test_refuses_before_the_call_that_would_cross_the_cap(self):
        m = new_meter(1.00)
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
        m = new_meter(0.10)
        r = m.reserve("claude-sonnet-5-5", 1_000, 1_000)
        m.book(r, "claude-sonnet-5-5", {"input_tokens": 1_000, "output_tokens": 9_000})  # $0.092
        with self.assertRaises(meter.SpendRefused):
            m.reserve("claude-sonnet-5-5", 1_000, 1_000)

    def test_stop_refuses_every_later_call(self):
        m = new_meter(100)
        m.stop("halt-agents drill")
        with self.assertRaises(meter.SpendRefused) as raised:
            m.reserve("claude-haiku-5-5", 10, 10)
        self.assertIn("halt-agents drill", str(raised.exception))
        self.assertEqual(m.status()["stopped"], "halt-agents drill")

    def test_input_estimate_overstates(self):
        self.assertEqual(meter.estimate_input_tokens(30_000), 10_000)


if __name__ == "__main__":
    unittest.main()
