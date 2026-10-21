# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Tests for the gateway package, gateway/avd_gateway (core-and-adapters.md section 3.4; spend.md section 3.7;
governance section 2.3 layer 6). Offline: the flag reader, the counter store and the table client are fakes."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))
from avd_gateway import flag, ledger, providers  # noqa: E402
from avd_gateway.gateway import Gateway  # noqa: E402
from avd_gateway.prices import PriceBook, SpendRefused  # noqa: E402

PRICES = ROOT / "governance/model-prices.yml"
CONFIG = ROOT / "governance/gateway.yml"
BOOK = PriceBook.load(PRICES)
NOON = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def config(daily=5.0, monthly=50.0, per_run=1.0, callers=None):
    return {"flag": {"tag": "avd-swarm-switch", "refuseModelCallsAt": ["halt-agents", "halt-all"]},
            "caps": {"perRunUsd": per_run, "dailyUsd": daily, "monthlyUsd": monthly},
            "callers": callers or {"agent-api": {"dailyUsd": daily}}}


def gateway(position="run", store=None, cfg=None, adapters=frozenset(), clock=lambda: NOON):
    cfg = cfg or config()
    state = {"position": position}

    def read():
        if isinstance(state["position"], Exception):
            raise state["position"]
        return state["position"]

    events = []
    gw = Gateway(BOOK, cfg, ledger.Ledger(store or ledger.MemoryStore(), ledger.Caps.from_config(cfg), clock),
                 read, enabled_adapters=adapters, emit=events.append)
    return gw, state, events


class GovernanceFiles(unittest.TestCase):
    def test_the_repo_files_load(self):
        caps = ledger.Caps.from_config(yaml.safe_load(CONFIG.read_text()))
        self.assertGreater(caps.monthly_usd, caps.daily_usd)
        self.assertIn("agent-api", caps.callers)

    def test_the_default_provider_has_a_price_for_every_model(self):
        self.assertEqual(providers.missing_prices(providers.PROVIDERS["anthropic"], BOOK), [])

    def test_prices_must_be_usd_per_million(self):
        with self.assertRaises(ValueError):
            PriceBook.from_dict({"currency": "EUR", "unit": "per-million-tokens", "providers": {}})
        with self.assertRaises(ValueError):
            PriceBook.from_dict({"currency": "USD", "unit": "per-million-tokens", "providers": {
                "anthropic": {"models": {"m": {"input": -1, "output": 1, "cacheRead": 1, "cacheWrite": 1}}}}})

    def test_same_model_is_priced_by_whoever_serves_it(self):
        self.assertTrue(BOOK.has("anthropic", "claude-opus-5-5"))
        self.assertFalse(BOOK.has("foundry", "claude-opus-5-5"))
        with self.assertRaises(SpendRefused):
            BOOK.cost_usd("foundry", "claude-opus-5-5", {"input_tokens": 1})


class Flag(unittest.TestCase):
    def test_positions_from_tags(self):
        self.assertEqual(flag.position_from_tags({"avd-swarm-switch": "run"}, "avd-swarm-switch"), "run")
        for tags in (None, {}, {"avd-swarm-switch": "paused"}):
            with self.assertRaises(ValueError):
                flag.position_from_tags(tags, "avd-swarm-switch")

    def test_refuses_at_halt_agents_and_above_and_when_unreadable(self):
        refuse = ["halt-agents", "halt-all"]
        self.assertEqual(flag.check(lambda: "halt-actions", refuse), "halt-actions")
        for position in ("halt-agents", "halt-all", "nonsense"):
            with self.assertRaises(SpendRefused):
                flag.check(lambda p=position: p, refuse)

        def broken():
            raise TimeoutError("ARM timed out")
        with self.assertRaises(SpendRefused) as raised:
            flag.check(broken, refuse)
        self.assertIn("unreadable", str(raised.exception))

    def test_arm_reader_reads_the_resource_group_tag(self):
        urls = []
        read = flag.arm_flag_reader(lambda url: urls.append(url) or {"tags": {"avd-swarm-switch": "halt-all"}},
                                    "sub", "rg-swarm", "avd-swarm-switch")
        self.assertEqual(read(), "halt-all")
        self.assertIn("/subscriptions/sub/resourcegroups/rg-swarm?api-version=", urls[0])
        # ARM omits an empty tags object: no tag is an unreadable flag, so a refusal.
        no_tags = flag.arm_flag_reader(lambda url: {}, "sub", "rg", "avd-swarm-switch")
        with self.assertRaises(SpendRefused):
            flag.check(no_tags, ["halt-agents"])


class Ledger(unittest.TestCase):
    def test_reserve_and_settle_on_day_month_and_caller(self):
        store = ledger.MemoryStore()
        led = ledger.Ledger(store, ledger.Caps.from_config(config()), lambda: NOON)
        when = led.reserve("agent-api", 0.40)
        led.settle("agent-api", when, 0.40, 0.10)
        rows = {k: v[0] for (p, k), v in store.rows.items()}
        self.assertEqual(set(rows), {"day-2026-10-21", "month-2026-10", "caller-agent-api-2026-10-21"})
        for value in rows.values():
            self.assertAlmostEqual(value, 0.10)

    def test_daily_cap_refuses_across_processes(self):
        store = ledger.MemoryStore()
        caps = ledger.Caps.from_config(config(daily=1.0))
        first, second = ledger.Ledger(store, caps, lambda: NOON), ledger.Ledger(store, caps, lambda: NOON)
        first.reserve("agent-api", 0.60)
        with self.assertRaises(SpendRefused) as raised:
            second.reserve("agent-api", 0.60)
        self.assertIn("cap", str(raised.exception))

    def test_monthly_cap_counts_every_day(self):
        store = ledger.MemoryStore()
        caps = ledger.Caps.from_config(config(daily=1.0, monthly=1.5))
        ledger.Ledger(store, caps, lambda: NOON).reserve("agent-api", 0.9)
        next_day = ledger.Ledger(store, caps, lambda: NOON.replace(day=22))
        with self.assertRaises(SpendRefused) as raised:
            next_day.reserve("agent-api", 0.9)
        self.assertIn("month", str(raised.exception))

    def test_unknown_caller_refused(self):
        with self.assertRaises(SpendRefused):
            ledger.Ledger(ledger.MemoryStore(), ledger.Caps.from_config(config()), lambda: NOON).reserve("judge", 0.01)

    def test_broken_counter_refuses(self):
        store = ledger.MemoryStore()
        store.fail = "table unreachable"
        with self.assertRaises(SpendRefused) as raised:
            ledger.Ledger(store, ledger.Caps.from_config(config()), lambda: NOON).reserve("agent-api", 0.01)
        self.assertIn("unreadable", str(raised.exception))

    def test_conflict_retries_then_refuses(self):
        class Busy(ledger.MemoryStore):
            def write(self, partition, rows):
                raise ledger.Conflict("someone else wrote")
        with self.assertRaises(SpendRefused) as raised:
            ledger.Ledger(Busy(), ledger.Caps.from_config(config()), lambda: NOON).reserve("agent-api", 0.01)
        self.assertIn("busy", str(raised.exception))

    def test_a_lost_race_rereads_and_respects_the_cap(self):
        store = ledger.MemoryStore()
        caps = ledger.Caps.from_config(config(daily=1.0))
        other = ledger.Ledger(store, caps, lambda: NOON)

        class Racing(ledger.MemoryStore):
            raced = False

            def read(self, partition, keys):
                return store.read(partition, keys)

            def write(self, partition, rows):
                if not Racing.raced:  # another process spends between our read and our write
                    Racing.raced = True
                    other.reserve("agent-api", 0.7)
                return store.write(partition, rows)

        with self.assertRaises(SpendRefused):
            ledger.Ledger(Racing(), caps, lambda: NOON).reserve("agent-api", 0.5)

    def test_caps_must_be_positive_finite_numbers(self):
        for bad in (0, float("nan"), float("inf"), True, "5"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                ledger.Caps.from_config(config(daily=bad))
        with self.assertRaises(ValueError):
            ledger.Caps.from_config(config(callers={"agent-api": {"dailyUsd": True}}))


class FakeTable:
    """Enough of azure-data-tables' TableClient for TableStore: entities with etags, one transaction at a time."""

    def __init__(self):
        self.entities = {}
        self.queries = 0

    def query_entities(self, query_filter, parameters):
        assert query_filter == "PartitionKey eq @p"
        self.queries += 1
        for (partition, key), (value, etag) in self.entities.items():
            if partition == parameters["p"]:
                entity = type("Entity", (dict,), {"metadata": {"etag": etag}})(RowKey=key, usd=value)
                yield entity

    def submit_transaction(self, operations):
        from azure.data.tables import TableTransactionError
        for op in operations:
            key = (op[1]["PartitionKey"], op[1]["RowKey"])
            if op[0] == "create" and key in self.entities:
                raise TableTransactionError("exists")
            if op[0] == "update" and self.entities.get(key, (None, None))[1] != op[2]["etag"]:
                raise TableTransactionError("etag")
        for op in operations:
            key = (op[1]["PartitionKey"], op[1]["RowKey"])
            self.entities[key] = (op[1]["usd"], f"W/{len(self.entities)}-{op[1]['usd']}")


try:
    import azure.data.tables  # noqa: F401
    HAVE_TABLES = True
except ImportError:
    HAVE_TABLES = False


@unittest.skipUnless(HAVE_TABLES, "azure-data-tables isn't installed; the guard's CI installs only PyYAML")
class TableStoreShape(unittest.TestCase):
    def test_create_then_update_with_etag(self):
        table = FakeTable()
        store = ledger.TableStore(table)
        led = ledger.Ledger(store, ledger.Caps.from_config(config()), lambda: NOON)
        when = led.reserve("agent-api", 0.3)
        self.assertEqual(table.queries, 1)  # one round trip reads all three rows
        led.settle("agent-api", when, 0.3, 0.2)
        self.assertAlmostEqual(store.read("2026-10", ["day-2026-10-21"])["day-2026-10-21"][0], 0.2)
        self.assertEqual(store.read("2026-11", ["day-2026-11-01"]), {"day-2026-11-01": (0.0, None)})


class GatewayFlow(unittest.TestCase):
    USAGE = {"input_tokens": 1_000, "output_tokens": 500}

    def test_a_call_reserves_books_and_emits_counts_only(self):
        gw, _, events = gateway()
        run = gw.open_run("agent-api", "r1")
        ticket = gw.before_call(run, "claude-sonnet-5-5", 1_000, 1_000)
        cost = gw.after_call(ticket, self.USAGE)
        self.assertAlmostEqual(cost, (1_000 * 2 + 500 * 10) / 1e6)
        self.assertEqual(events[-1]["type"], "model.usage")
        self.assertEqual(events[-1]["output_tokens"], 500)
        self.assertNotIn("text", events[-1])
        self.assertAlmostEqual(gw.ledger.store.rows[("2026-10", "day-2026-10-21")][0], cost)

    def test_the_flag_is_read_before_every_call(self):
        gw, state, events = gateway()
        run = gw.open_run("agent-api")
        gw.after_call(gw.before_call(run, "claude-haiku-5-5", 10, 10), self.USAGE)
        state["position"] = "halt-agents"
        with self.assertRaises(SpendRefused):
            gw.before_call(run, "claude-haiku-5-5", 10, 10)
        self.assertEqual(events[-1]["type"], "model.refused")
        state["position"] = ConnectionError("no route")
        with self.assertRaises(SpendRefused):
            gw.before_call(run, "claude-haiku-5-5", 10, 10)

    def test_adapter_providers_are_off_by_default(self):
        gw, _, _ = gateway()
        run = gw.open_run("agent-api", provider="foundry")
        with self.assertRaises(SpendRefused) as raised:
            gw.before_call(run, "claude-haiku-5-5", 10, 10)
        self.assertIn("adapter", str(raised.exception))
        # Turned on but unpriced: still refused.
        gw, _, _ = gateway(adapters=frozenset({"foundry"}))
        with self.assertRaises(SpendRefused) as raised:
            gw.before_call(gw.open_run("agent-api", provider="foundry"), "claude-haiku-5-5", 10, 10)
        self.assertIn("no price", str(raised.exception))

    def test_a_runbook_budget_caps_the_run(self):
        gw, _, _ = gateway()
        run = gw.open_run("agent-api", budget_usd=0.05)
        with self.assertRaises(SpendRefused) as raised:
            gw.before_call(run, "claude-opus-5-5", 10_000, 16_000)  # worst case $0.37
        self.assertIn("spend cap", str(raised.exception))
        self.assertEqual(gw.ledger.store.rows, {})  # nothing reached the counter

    def test_a_budget_never_exceeds_the_daily_cap(self):
        gw, _, _ = gateway(cfg=config(daily=2.0))
        self.assertEqual(gw.open_run("agent-api", budget_usd=100).meter.cap_usd, 2.0)

    def test_counter_refusal_releases_the_run_reservation(self):
        gw, _, _ = gateway(cfg=config(daily=0.5, per_run=0.5))
        a, b = gw.open_run("agent-api"), gw.open_run("agent-api")
        gw.before_call(a, "claude-opus-5-5", 10_000, 16_000)  # $0.37 on the day
        with self.assertRaises(SpendRefused):
            gw.before_call(b, "claude-opus-5-5", 10_000, 16_000)
        self.assertEqual(b.meter.reserved, {})

    def test_failed_call_books_nothing(self):
        gw, _, _ = gateway()
        run = gw.open_run("agent-api")
        gw.failed(gw.before_call(run, "claude-haiku-5-5", 10, 10))
        self.assertEqual(run.meter.spent_usd, 0)
        self.assertEqual(gw.ledger.store.rows[("2026-10", "day-2026-10-21")][0], 0)

    def test_unsettled_counter_keeps_the_worst_case_and_counts_it(self):
        gw, _, _ = gateway()
        run = gw.open_run("agent-api")
        ticket = gw.before_call(run, "claude-haiku-5-5", 10, 10)
        gw.ledger.store.fail = "table gone"
        gw.after_call(ticket, self.USAGE)
        self.assertEqual(gw.settle_failures, 1)

    def test_unknown_caller_refused(self):
        gw, _, _ = gateway()
        with self.assertRaises(SpendRefused):
            gw.open_run("improve")

    def test_a_switch_config_that_would_fail_open_is_refused(self):
        for value in ("halt-agents", ["halt-agent", "halt-all"], ["halt-all"], None, ["halt-agents", "halt-all", "stop"]):
            cfg = config()
            cfg["flag"]["refuseModelCallsAt"] = value
            with self.assertRaises(ValueError, msg=repr(value)):
                gateway(cfg=cfg)
        cfg = config()
        cfg["flag"]["refuseModelCallsAt"] = ["halt-actions", "halt-agents", "halt-all"]
        gw, _, _ = gateway(position="halt-actions", cfg=cfg)
        with self.assertRaises(SpendRefused):  # stricter is allowed
            gw.before_call(gw.open_run("agent-api"), "claude-haiku-5-5", 10, 10)

    def test_a_budget_must_be_a_finite_positive_number(self):
        gw, _, _ = gateway()
        for bad in (float("nan"), float("inf"), 0, -1, True):
            with self.assertRaises(SpendRefused, msg=repr(bad)):
                gw.open_run("agent-api", budget_usd=bad)

    def test_from_files(self):
        gw = Gateway.from_files(PRICES, CONFIG, ledger.MemoryStore(), lambda: "run")
        self.assertEqual(gw.refuse_at, ["halt-agents", "halt-all"])


if __name__ == "__main__":
    unittest.main()
