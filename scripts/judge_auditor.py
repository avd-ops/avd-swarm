#!/usr/bin/env python3
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""The judge auditor's decision rules (spec section 6b, red team R24): pure functions, stdlib only.

The auditor that runs them is built with the eval runs (build order step 3); the rules and their error
rates are fixed now so the thresholds in governance/judge-auditor.yml can be checked offline.
"""

from __future__ import annotations

from math import comb


def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    if k < 0:
        return 0.0
    return sum(comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(0, min(k, n) + 1))


def upper_bound(successes: int, n: int, confidence: float = 0.95) -> float:
    """One-sided Clopper-Pearson upper bound of a binomial rate."""
    if successes >= n:
        return 1.0
    alpha, lo, hi = 1 - confidence, successes / n, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if binom_cdf(successes, n, mid) > alpha:
            lo = mid
        else:
            hi = mid
    return hi


def lower_bound(successes: int, n: int, confidence: float = 0.95) -> float:
    """One-sided Clopper-Pearson lower bound of a binomial rate."""
    if successes <= 0:
        return 0.0
    return 1 - upper_bound(n - successes, n, confidence)


def seeded_fault_alarm(caught: int, seeded: int, settings: dict) -> bool | None:
    """True: the judge's catch rate is credibly under the floor. None: not the fixed count, so no verdict."""
    if seeded != settings["seeded_faults_per_window"]:
        return None
    return upper_bound(caught, seeded, settings["confidence"]) < settings["seeded_catch_rate_floor"]


def rate_alarm(events: int, n: int, ceiling: float, minimum: int, confidence: float = 0.95) -> bool | None:
    """True: the rate is credibly over the ceiling. None: under the minimum count, so the window rolls over."""
    if n < minimum:
        return None
    return lower_bound(events, n, confidence) > ceiling


def raises_alarm(measure: str, alarm: bool | None, settings: dict) -> bool:
    """Only measures outside advisory_measures can alarm; advisory ones are reported with their counts."""
    return bool(alarm) and measure not in settings["advisory_measures"]


def sign_test_p(b: int, c: int) -> float:
    """Exact one-sided p-value that b (second judge right, Claude judge wrong) beats c (the reverse) by chance."""
    n = b + c
    return 1 - binom_cdf(b - 1, n, 0.5) if n else 1.0


def shadow_promote(b: int, c: int, windows: int, settings: dict, clean_flags_worse: bool) -> bool:
    """Paired comparison: promote the shadow judge only when it is credibly better and not noisier on clean answers."""
    if windows < settings["shadow_min_windows"] or b + c < settings["shadow_min_discordant"]:
        return False
    return sign_test_p(b, c) < settings["shadow_alpha"] and not clean_flags_worse
