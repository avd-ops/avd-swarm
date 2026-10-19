# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""The judge auditor's rules hold the error rates spec section 6b states (red team R24)."""

import random
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import judge_auditor as ja  # noqa: E402

SETTINGS = yaml.safe_load((ROOT / "governance/judge-auditor.yml").read_text(encoding="utf-8"))
N = SETTINGS["seeded_faults_per_window"]


def alarm_probability(true_rate: float) -> float:
    """Exact probability that one window raises the seeded-fault alarm for a judge with this catch rate."""
    worst_alarming = max(x for x in range(N + 1) if ja.seeded_fault_alarm(x, N, SETTINGS))
    return ja.binom_cdf(worst_alarming, N, true_rate)


class SeededFaults(unittest.TestCase):
    def test_old_rule_false_alarm_rate_reproduced(self):
        # The finding: 40 faults, a 95% line, a 97% judge falls under the line about 12% of the time.
        self.assertAlmostEqual(1 - ja.binom_cdf(2, 40, 0.03), 0.118, places=3)

    def test_alarm_at_109_or_fewer_caught_of_120(self):
        self.assertEqual(N, 20 * len(SETTINGS["fault_kinds"]))
        self.assertTrue(ja.seeded_fault_alarm(109, N, SETTINGS))
        self.assertFalse(ja.seeded_fault_alarm(110, N, SETTINGS))

    def test_healthy_judge_rarely_alarms(self):
        per_window = alarm_probability(0.97)
        self.assertLess(per_window, 0.002)
        self.assertLess(1 - (1 - per_window) ** 52, 0.06)  # about 5% a year, one check a week

    def test_degraded_judge_is_caught(self):
        self.assertGreater(alarm_probability(0.85), 0.95)

    def test_no_verdict_without_the_fixed_count(self):
        self.assertIsNone(ja.seeded_fault_alarm(30, 40, SETTINGS))


class Rates(unittest.TestCase):
    def test_minimum_counts_roll_over(self):
        self.assertIsNone(ja.rate_alarm(3, 19, SETTINGS["ground_truth_miss_ceiling"], SETTINGS["ground_truth_min_answers"]))
        self.assertIsNone(ja.rate_alarm(5, 9, SETTINGS["overturn_ceiling"], SETTINGS["overturn_min_cases"]))

    def test_rate_alarm_needs_the_lower_bound_over_the_ceiling(self):
        ceiling, minimum = SETTINGS["ground_truth_miss_ceiling"], SETTINGS["ground_truth_min_answers"]
        self.assertFalse(ja.rate_alarm(3, 20, ceiling, minimum))   # 15% observed, but could be 10%
        self.assertTrue(ja.rate_alarm(8, 20, ceiling, minimum))    # 40% observed, credibly over 10%

    def test_advisory_measures_never_alarm(self):
        self.assertFalse(ja.raises_alarm("ground_truth", True, SETTINGS))
        self.assertFalse(ja.raises_alarm("overturns", True, SETTINGS))
        self.assertTrue(ja.raises_alarm("seeded_faults", True, SETTINGS))
        self.assertFalse(ja.raises_alarm("seeded_faults", None, SETTINGS))


class Shadow(unittest.TestCase):
    def test_old_rule_promotes_an_equal_judge_almost_always(self):
        # "Promote if the second judge caught anything the first missed", both 97%, 240 faults.
        self.assertGreater(1 - (1 - 0.03 * 0.97) ** 240, 0.99)

    def test_paired_rule_rarely_promotes_an_equal_judge(self):
        rng = random.Random(20261019)
        trials, promoted = 4000, 0
        for _ in range(trials):
            b = c = 0
            for _ in range(2 * N):
                first, second = rng.random() < 0.97, rng.random() < 0.97
                b += second and not first
                c += first and not second
            promoted += ja.shadow_promote(b, c, windows=2, settings=SETTINGS, clean_flags_worse=False)
        self.assertLess(promoted / trials, 0.05)

    def test_paired_rule_promotes_a_clearly_better_judge(self):
        self.assertTrue(ja.shadow_promote(14, 2, windows=2, settings=SETTINGS, clean_flags_worse=False))
        self.assertFalse(ja.shadow_promote(14, 2, windows=2, settings=SETTINGS, clean_flags_worse=True))
        self.assertFalse(ja.shadow_promote(14, 2, windows=1, settings=SETTINGS, clean_flags_worse=False))
        self.assertFalse(ja.shadow_promote(6, 0, windows=2, settings=SETTINGS, clean_flags_worse=False))


if __name__ == "__main__":
    unittest.main()
