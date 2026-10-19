# avd-swarm S1a spike: token spend meter. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""A per-run cap on what the agent service spends on Claude, metered from each response's usage.

Cost Management doesn't see Anthropic's API at all, and its own data arrives hours late, so the cap is
enforced here, before every call (avd-swarm spend.md section 3.7, red team R22). Before a call the meter
reserves the worst case: the estimated input at the model's input price plus max_tokens of output. It
refuses when the booked spend plus all open reservations would pass the cap. After the call it books
the cost from the response's usage and releases the reservation. A stop (the spike's kill switch,
governance section 2.3 layer 6) refuses every later call until the service restarts.

Stdlib only, so the guard's unit tests run it without the spike's dependencies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# USD per million tokens: input, output, cache read, cache write (5-minute TTL, 1.25x input).
# Anthropic first-party prices as of 2026-10-06 (platform.claude.com pricing). Claude Haiku 5.5
# doubles its input and output prices past a 100K-token prompt (HAIKU_LONG). Unknown models are refused.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}
HAIKU_LONG = {"threshold": 100_000, "prices": (0.50, 2.50, 0.05, 0.625)}
CHARS_PER_TOKEN = 3  # deliberately low, so the input estimate overstates rather than understates


class SpendRefused(Exception):
    """A model call the meter won't allow: stopped, over the cap, or an unpriced model."""


def prices_for(model: str, prompt_tokens: int) -> tuple[float, float, float, float]:
    if model not in PRICES:
        raise SpendRefused(f"{model} has no price in meter.PRICES; refusing to call it")
    if model == "claude-haiku-5-5" and prompt_tokens > HAIKU_LONG["threshold"]:
        return HAIKU_LONG["prices"]
    return PRICES[model]


def cost_usd(model: str, usage: dict) -> float:
    """What one response cost, from its usage (input_tokens is the uncached remainder)."""
    fresh = usage.get("input_tokens") or 0
    read = usage.get("cache_read_input_tokens") or 0
    write = usage.get("cache_creation_input_tokens") or 0
    out = usage.get("output_tokens") or 0
    p_in, p_out, p_read, p_write = prices_for(model, fresh + read + write)
    return (fresh * p_in + out * p_out + read * p_read + write * p_write) / 1_000_000


def estimate_input_tokens(request_chars: int) -> int:
    return math.ceil(request_chars / CHARS_PER_TOKEN)


@dataclass
class Meter:
    cap_usd: float
    spent_usd: float = 0.0
    reserved: dict = field(default_factory=dict)
    stopped: str | None = None
    calls: int = 0
    _next: int = 0

    def worst_case_usd(self, model: str, input_tokens: int, max_tokens: int) -> float:
        # Priced as if every input token were a cache write, the dearest kind of input.
        p_in, p_out, _, p_write = prices_for(model, input_tokens)
        return (input_tokens * max(p_in, p_write) + max_tokens * p_out) / 1_000_000

    def reserve(self, model: str, input_tokens: int, max_tokens: int) -> int:
        """Reserves a call's worst case or raises SpendRefused. No await inside, so it is atomic in asyncio."""
        if self.stopped:
            raise SpendRefused(f"stopped: {self.stopped}")
        worst = self.worst_case_usd(model, input_tokens, max_tokens)
        committed = self.spent_usd + sum(self.reserved.values())
        if committed + worst > self.cap_usd:
            raise SpendRefused(f"spend cap: ${committed:.4f} booked or reserved + ${worst:.4f} worst case "
                               f"for this call > cap ${self.cap_usd:.2f}")
        self._next += 1
        self.reserved[self._next] = worst
        return self._next

    def book(self, reservation: int, model: str, usage: dict) -> float:
        self.reserved.pop(reservation, None)
        cost = cost_usd(model, usage)
        self.spent_usd += cost
        self.calls += 1
        return cost

    def release(self, reservation: int) -> None:
        """A call that failed before a response: nothing is booked (Anthropic doesn't bill a failed request)."""
        self.reserved.pop(reservation, None)

    def stop(self, reason: str) -> None:
        self.stopped = reason or "stopped by the operator"

    def status(self) -> dict:
        return {"capUsd": self.cap_usd, "spentUsd": round(self.spent_usd, 4), "calls": self.calls,
                "openReservations": len(self.reserved), "stopped": self.stopped}
