# avd-swarm gateway: the spend counter outside the agents. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Day, month and per-caller caps across processes (spend.md section 3.7 item 3).

The counter is a table that only the gateway's identity writes. Each call reserves its worst case on
three rows at once (the UTC day, the UTC month and the caller's day) with optimistic concurrency, so two
processes can't both spend the last dollar; after the response it settles the row to the real cost. The
three rows share a partition (the month), so the write is one transaction. If the counter can't be read
or written, the call is refused. The watchdog reads the same rows on its own (governance section 2.4).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

from .prices import SpendRefused

RETRIES = 3


class Conflict(Exception):
    """Another writer changed a row since it was read."""


class CounterStore(Protocol):
    def read(self, partition: str, keys: list[str]) -> dict[str, tuple[float, str | None]]:
        """Each key's value and etag; a missing row is (0.0, None)."""

    def write(self, partition: str, rows: dict[str, tuple[float, str | None]]) -> None:
        """Writes every row or none. A row whose etag is None must not exist yet. Raises Conflict."""


class MemoryStore:
    """For tests and a single process that has no table yet. Never the counter of a deployed gateway."""

    def __init__(self):
        self.rows: dict[tuple[str, str], tuple[float, int]] = {}
        self.fail: str | None = None  # set to make every call raise, as a broken table would

    def read(self, partition, keys):
        if self.fail:
            raise RuntimeError(self.fail)
        out = {}
        for key in keys:
            value, version = self.rows.get((partition, key), (0.0, None))
            out[key] = (value, None if version is None else str(version))
        return out

    def write(self, partition, rows):
        if self.fail:
            raise RuntimeError(self.fail)
        for key, (_, etag) in rows.items():
            current = self.rows.get((partition, key))
            if (current is None) != (etag is None) or (current and str(current[1]) != etag):
                raise Conflict(key)
        for key, (value, _) in rows.items():
            version = self.rows.get((partition, key), (0.0, 0))[1]
            self.rows[(partition, key)] = (value, version + 1)


class TableStore:
    """Azure Table Storage through azure-data-tables, signed in with the gateway's managed identity.
    One entity per row, PartitionKey the month, RowKey the key, `usd` the value; a batch is one
    entity group transaction, so all three rows change together or not at all."""

    def __init__(self, table_client):
        self.table = table_client

    def read(self, partition, keys):
        # One round trip: the month's partition holds a row per day and per caller-day, a few hundred at most.
        found = {e["RowKey"]: (float(e["usd"]), e.metadata["etag"])
                 for e in self.table.query_entities("PartitionKey eq @p", parameters={"p": partition})}
        return {key: found.get(key, (0.0, None)) for key in keys}

    def write(self, partition, rows):
        from azure.core import MatchConditions
        from azure.data.tables import TableTransactionError, UpdateMode
        operations = []
        for key, (value, etag) in rows.items():
            entity = {"PartitionKey": partition, "RowKey": key, "usd": float(value)}
            if etag is None:
                operations.append(("create", entity))
            else:
                operations.append(("update", entity, {"mode": UpdateMode.REPLACE, "etag": etag,
                                                      "match_condition": MatchConditions.IfNotModified}))
        try:
            self.table.submit_transaction(operations)
        except TableTransactionError as error:
            raise Conflict(str(error)[:200]) from error


@dataclass(frozen=True)
class Caps:
    per_run_usd: float
    daily_usd: float
    monthly_usd: float
    callers: dict[str, float]  # caller -> daily cap

    @classmethod
    def from_config(cls, config: dict) -> "Caps":
        caps = config.get("caps") or {}
        raw = {name: (c or {}).get("dailyUsd") for name, c in (config.get("callers") or {}).items()}
        values = (caps.get("perRunUsd"), caps.get("dailyUsd"), caps.get("monthlyUsd"), *raw.values())
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError("every cap in governance/gateway.yml must be a positive, finite number")
        return cls(float(caps["perRunUsd"]), float(caps["dailyUsd"]), float(caps["monthlyUsd"]),
                   {name: float(v) for name, v in raw.items()})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Ledger:
    def __init__(self, store: CounterStore, caps: Caps, clock: Callable[[], datetime] = utc_now):
        self.store, self.caps, self.clock = store, caps, clock

    def _rows(self, caller: str, when: datetime) -> tuple[str, dict[str, float]]:
        day, month = when.strftime("%Y-%m-%d"), when.strftime("%Y-%m")
        return month, {f"day-{day}": self.caps.daily_usd, f"month-{month}": self.caps.monthly_usd,
                       f"caller-{caller}-{day}": self.caps.callers[caller]}

    def _apply(self, caller: str, when: datetime, delta: float, enforce: bool) -> None:
        partition, limits = self._rows(caller, when)
        for _ in range(RETRIES):
            try:
                current = self.store.read(partition, list(limits))
            except Exception as error:
                raise SpendRefused(f"spend counter unreadable, refusing: {type(error).__name__}") from error
            if enforce:
                for key, cap in limits.items():
                    if current[key][0] + delta > cap:
                        raise SpendRefused(f"{key.split('-')[0]} cap: ${current[key][0]:.4f} committed + "
                                           f"${delta:.4f} worst case > cap ${cap:.2f}")
            rows = {key: (max(0.0, value + delta), etag) for key, (value, etag) in current.items()}
            try:
                self.store.write(partition, rows)
                return
            except Conflict:
                continue
            except Exception as error:
                raise SpendRefused(f"spend counter unwritable, refusing: {type(error).__name__}") from error
        raise SpendRefused(f"spend counter busy after {RETRIES} tries, refusing")

    def reserve(self, caller: str, worst_usd: float) -> datetime:
        """Commits the worst case on the day, month and caller rows; returns when, to settle against the same rows."""
        if caller not in self.caps.callers:
            raise SpendRefused(f"caller {caller!r} has no cap in governance/gateway.yml; refusing")
        when = self.clock()
        self._apply(caller, when, worst_usd, enforce=True)
        return when

    def settle(self, caller: str, when: datetime, reserved_usd: float, actual_usd: float) -> None:
        """Replaces the reservation with the real cost (0 for a call that failed before a response)."""
        self._apply(caller, when, actual_usd - reserved_usd, enforce=False)
