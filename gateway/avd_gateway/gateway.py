# avd-swarm gateway: one metered, flag-checking path to every model. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""The gateway every model-calling process uses (core-and-adapters.md section 3.4; spend.md section 3.7).

Before a call, in this order: the kill switch flag, the provider (known, its adapter on, the model
priced), the run's own cap, then the day, month and caller caps in the counter outside the agents.
Any refusal raises SpendRefused and nothing is called. After the call it books the real cost to the run
and settles the counter. Each call and each refusal becomes an event with counts only.

A library first, inside the agent API, the only process that calls a model in build steps 1 to 3. A
caller is the app role of its Entra identity, never a key.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from . import flag as flags
from .ledger import Caps, Ledger
from .meter import Meter
from .prices import PriceBook, SpendRefused
from .providers import PROVIDERS, Provider, usable
from .usage import refused_event, usage_event


@dataclass
class Run:
    caller: str
    run_id: str
    provider: str
    meter: Meter


@dataclass
class Ticket:
    run: Run
    model: str
    reservation: int
    worst_usd: float
    when: datetime


@dataclass
class Gateway:
    prices: PriceBook
    config: dict                      # governance/gateway.yml
    ledger: Ledger
    read_flag: flags.FlagReader
    enabled_adapters: frozenset = frozenset()
    providers: dict[str, Provider] = field(default_factory=lambda: dict(PROVIDERS))
    emit: Callable[[dict], None] = lambda event: None
    settle_failures: int = 0
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))

    @classmethod
    def from_files(cls, prices_path, config_path, store, read_flag, **kwargs) -> "Gateway":
        import yaml
        from pathlib import Path
        config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
        return cls(PriceBook.load(prices_path), config, Ledger(store, Caps.from_config(config)), read_flag, **kwargs)

    def __post_init__(self):
        self.refuse_at = list(flags.refusals(self.config))  # raises on a config that would fail open

    def open_run(self, caller: str, run_id: str | None = None, provider: str = "anthropic",
                 budget_usd: float | None = None) -> Run:
        """A run's own meter. Its cap is the run's budget (a runbook's), or perRunUsd; never more than the daily cap."""
        caps = self.ledger.caps
        if caller not in caps.callers:
            raise SpendRefused(f"caller {caller!r} has no cap in governance/gateway.yml; refusing")
        cap = caps.per_run_usd if budget_usd is None else budget_usd
        if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not math.isfinite(cap) or cap <= 0:
            raise SpendRefused("a run's budget must be a positive, finite number")
        meter = Meter(cap_usd=min(float(cap), caps.daily_usd), prices=self.prices, provider=provider)
        return Run(caller, run_id or f"run-{next(self._ids)}", provider, meter)

    def before_call(self, run: Run, model: str, input_tokens: int, max_tokens: int) -> Ticket:
        try:
            flags.check(self.read_flag, self.refuse_at)
            usable(run.provider, model, self.enabled_adapters, self.providers)
            reservation = run.meter.reserve(model, input_tokens, max_tokens)
            worst = run.meter.reserved[reservation]
            try:
                when = self.ledger.reserve(run.caller, worst)
            except SpendRefused:
                run.meter.release(reservation)
                raise
        except SpendRefused as refused:
            self.emit(refused_event(run.caller, run.run_id, str(refused)))
            raise
        return Ticket(run, model, reservation, worst, when)

    def after_call(self, ticket: Ticket, usage: dict) -> float:
        run = ticket.run
        cost = run.meter.book(ticket.reservation, ticket.model, usage)
        self._settle(ticket, cost)
        self.emit(usage_event(run.caller, run.run_id, run.provider, ticket.model, usage, cost))
        return cost

    def failed(self, ticket: Ticket) -> None:
        """The call failed before a response: nothing is booked."""
        ticket.run.meter.release(ticket.reservation)
        self._settle(ticket, 0.0)

    def _settle(self, ticket: Ticket, actual: float) -> None:
        # The call already happened. If the counter can't be settled it keeps the worst case, which
        # overstates spend (the safe side); the failure is counted for the watchdog, never raised.
        try:
            self.ledger.settle(ticket.run.caller, ticket.when, ticket.worst_usd, actual)
        except SpendRefused:
            self.settle_failures += 1

    def stop(self, run: Run, reason: str) -> None:
        run.meter.stop(reason)
