# avd-swarm gateway: token spend meter. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""A cap on what one run spends on a model, metered from each response's usage (moved from the S1a spike).

Cost Management doesn't see Anthropic's API at all, and its own data arrives hours late, so the cap is
enforced here, before every call (spend.md section 3.7, red team R22). Before a call the meter reserves
the worst case: the estimated input at the model's dearest input price plus max_tokens of output. It
refuses when the booked spend plus all open reservations would pass the cap. After the call it books the
cost from the response's usage and releases the reservation. A stop (governance section 2.3 layer 6)
refuses every later call until the process restarts.

Prices come from governance/model-prices.yml through a PriceBook; an unpriced model is refused. The
day, month and per-caller caps across processes are the Ledger's (ledger.py); this meter is one run's.
Stdlib only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .prices import PriceBook, SpendRefused

CHARS_PER_TOKEN = 3  # deliberately low, so the input estimate overstates rather than understates

__all__ = ["CHARS_PER_TOKEN", "Meter", "SpendRefused", "estimate_input_tokens"]


def estimate_input_tokens(request_chars: int) -> int:
    return math.ceil(request_chars / CHARS_PER_TOKEN)


@dataclass
class Meter:
    cap_usd: float
    prices: PriceBook
    provider: str = "anthropic"
    spent_usd: float = 0.0
    reserved: dict = field(default_factory=dict)
    stopped: str | None = None
    calls: int = 0
    _next: int = 0

    def worst_case_usd(self, model: str, input_tokens: int, max_tokens: int) -> float:
        return self.prices.worst_case_usd(self.provider, model, input_tokens, max_tokens)

    def cost_usd(self, model: str, usage: dict) -> float:
        return self.prices.cost_usd(self.provider, model, usage)

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
        cost = self.cost_usd(model, usage)
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
