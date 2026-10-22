# avd-swarm gateway: pinned model prices. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Prices from governance/model-prices.yml, never from code (spend.md section 3.7 item 2).

A model with no row is refused. A row is looked up by provider and model ID, so the same model served
by two providers (Anthropic's API, Claude in Foundry) is priced by whoever serves the call.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

FIELDS = ("input", "output", "cacheRead", "cacheWrite")


class SpendRefused(Exception):
    """A model call the gateway won't allow: stopped, over a cap, an unknown caller or an unpriced model."""


@dataclass(frozen=True)
class Row:
    input: float
    output: float
    cache_read: float
    cache_write: float

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.input, self.output, self.cache_read, self.cache_write)


@dataclass(frozen=True)
class Price:
    base: Row
    long_threshold: int | None = None
    long: Row | None = None

    def row_for(self, prompt_tokens: int) -> Row:
        if self.long and prompt_tokens > self.long_threshold:
            return self.long
        return self.base


def _row(data: dict, where: str) -> Row:
    values = []
    for name in FIELDS:
        value = data.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"{where}: {name} must be a price of zero or more")
        values.append(float(value))
    return Row(*values)


class PriceBook:
    """Prices per (provider, model). Build it with load() or from_dict()."""

    def __init__(self, prices: dict[tuple[str, str], Price]):
        self._prices = dict(prices)

    @classmethod
    def from_dict(cls, data: dict) -> "PriceBook":
        if data.get("currency") != "USD" or data.get("unit") != "per-million-tokens":
            raise ValueError("model prices must be USD per-million-tokens")
        prices = {}
        for provider, block in (data.get("providers") or {}).items():
            for model, row in ((block or {}).get("models") or {}).items():
                where = f"{provider}/{model}"
                long = row.get("longContext")
                if long is None:
                    prices[(provider, model)] = Price(_row(row, where))
                else:
                    threshold = long.get("threshold")
                    if not isinstance(threshold, int) or threshold <= 0:
                        raise ValueError(f"{where}: longContext.threshold must be a positive integer")
                    prices[(provider, model)] = Price(_row(row, where), threshold, _row(long, where + " longContext"))
        return cls(prices)

    @classmethod
    def load(cls, path: str | Path) -> "PriceBook":
        import yaml  # only the loader needs PyYAML; the rest of the package is stdlib
        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")))

    def has(self, provider: str, model: str) -> bool:
        return (provider, model) in self._prices

    def models(self, provider: str) -> list[str]:
        return sorted(m for p, m in self._prices if p == provider)

    def row(self, provider: str, model: str, prompt_tokens: int) -> Row:
        price = self._prices.get((provider, model))
        if price is None:
            raise SpendRefused(f"{provider}/{model} has no price in governance/model-prices.yml; refusing to call it")
        return price.row_for(prompt_tokens)

    def cost_usd(self, provider: str, model: str, usage: dict) -> float:
        """What one response cost, from its usage (input_tokens is the uncached remainder)."""
        fresh = usage.get("input_tokens") or 0
        read = usage.get("cache_read_input_tokens") or 0
        write = usage.get("cache_creation_input_tokens") or 0
        out = usage.get("output_tokens") or 0
        r = self.row(provider, model, fresh + read + write)
        return (fresh * r.input + out * r.output + read * r.cache_read + write * r.cache_write) / 1_000_000

    def worst_case_usd(self, provider: str, model: str, input_tokens: int, max_tokens: int) -> float:
        """Priced as if every input token were a cache write, the dearest kind of input."""
        r = self.row(provider, model, input_tokens)
        return (input_tokens * max(r.input, r.cache_write) + max_tokens * r.output) / 1_000_000
