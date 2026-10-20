# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Tests for the public spend guard, spend/spend_guard.py (docs/spend.md section 2). Offline: no az calls."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "spend"))
import spend_guard as g  # noqa: E402

NOW = datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc)
OK = dict(estimate=14.5, unpriced_lines=0, allow_unpriced=False, run_cap=20, monthly_cap=150,
          month_to_date=30.0, open_runs=[], hours=6)


def record(**changes):
    decision = g.decide_open(**OK)
    base = g.new_record(run_id="20261020-0600-abcd", sandbox="lab", purpose="S1a", resource_group="rg-avdswarm-spike",
                        now=NOW, hours=6, estimate=14.5, estimate_note="", unpriced_lines=0, allow_unpriced=False,
                        run_cap=20, monthly_cap=150, month_to_date=30.0, month_to_date_source="test", decision=decision)
    base.update(changes)
    return base


class Decide(unittest.TestCase):
    def test_allows_a_run_inside_both_caps(self):
        self.assertEqual(g.decide_open(**OK), {"allowed": True, "reasons": []})

    def test_refuses_each_rule_and_lists_every_reason(self):
        cases = {
            "no cap set": dict(run_cap=0),
            "unpriced": dict(unpriced_lines=2),
            "over the run cap": dict(estimate=25),
            "over the monthly cap": dict(month_to_date=140),
            "month to date unknown": dict(month_to_date=None),
            "already has an open run": dict(open_runs=["20261019-1200-ffff"]),
            "planned hours": dict(hours=30),
            "no estimate": dict(estimate=0),
            "above the monthly cap": dict(run_cap=200),
        }
        for words, change in cases.items():
            with self.subTest(words):
                decision = g.decide_open(**{**OK, **change})
                self.assertFalse(decision["allowed"])
                self.assertTrue(any(words in r for r in decision["reasons"]), decision["reasons"])
        both = g.decide_open(**{**OK, "estimate": 25, "open_runs": ["x"]})
        self.assertEqual(len(both["reasons"]), 2)

    def test_unpriced_allowed_on_purpose(self):
        self.assertTrue(g.decide_open(**{**OK, "unpriced_lines": 1, "allow_unpriced": True})["allowed"])

    def test_monthly_cap_boundary_is_inclusive(self):
        self.assertTrue(g.decide_open(**{**OK, "month_to_date": 135.5})["allowed"])
        self.assertFalse(g.decide_open(**{**OK, "month_to_date": 135.51})["allowed"])


class Records(unittest.TestCase):
    def test_new_record_holds_no_subscription_or_tenant(self):
        text = json.dumps(record())
        for key in ("subscription", "tenant", "card"):
            self.assertNotIn(key, text.lower())
        self.assertEqual(record()["dueAt"], "2026-10-20T12:00:00Z")
        self.assertEqual(record(status="refused")["status"], "refused")

    def test_run_id_is_safe_for_tags_and_names(self):
        run_id = g.new_run_id(NOW, "a1b2")
        self.assertEqual(run_id, "20261020-0600-a1b2")
        self.assertRegex(run_id, r"^[A-Za-z0-9._-]+$")  # Start-Spike.ps1's ValidatePattern

    def test_close_clean_and_red(self):
        later = NOW + timedelta(hours=3)
        clean = g.close_record(record(), now=later, teardown={"step": "remove-spike", "leftovers": []},
                               token_cost=4.21, token_source="meter")
        self.assertTrue(clean["teardown"]["clean"])
        self.assertEqual(clean["cost"]["tokens"], 4.21)
        red = g.close_record(record(), now=later, teardown={"leftovers": ["resource group rg-avdswarm-spike"]},
                             token_cost=None, token_source="")
        self.assertFalse(red["teardown"]["clean"])
        self.assertTrue(any("teardown left 1" in f for f in g.run_flags(red, later)))
        again = g.close_record(red, now=later, teardown={"leftovers": []}, token_cost=None, token_source="")
        self.assertTrue(again["teardown"]["clean"])

    def test_refused_run_cannot_be_closed(self):
        with self.assertRaises(ValueError):
            g.close_record(record(status="refused"), now=NOW, teardown={}, token_cost=None, token_source="")

    def test_overdue_flag(self):
        self.assertEqual(g.run_flags(record(), NOW + timedelta(hours=5)), [])
        self.assertTrue(g.run_flags(record(), NOW + timedelta(hours=7))[0].startswith("overdue"))


class Cost(unittest.TestCase):
    RESPONSE = {"properties": {"columns": [{"name": "Cost"}, {"name": "UsageDate"}, {"name": "Currency"}],
                               "rows": [[1.25, 20261020, "USD"], [0.5, 20261020, "USD"], [0.1, 20261023, "USD"]]}}

    def test_sum_rows_by_day(self):
        cost = g.sum_cost_rows(self.RESPONSE)
        self.assertEqual(cost, {"total": 1.85, "currency": "USD", "byDay": {"2026-10-20": 1.75, "2026-10-23": 0.1}})
        self.assertEqual(g.sum_cost_rows({"properties": {}}), {"total": 0.0, "currency": None, "byDay": {}})

    def test_settle_marks_tail_and_settled_after_three_days(self):
        closed = g.close_record(record(), now=NOW + timedelta(hours=4), teardown={"leftovers": []},
                                token_cost=2.0, token_source="meter")
        azure = g.sum_cost_rows(self.RESPONSE)
        early = g.settle_record(closed, now=NOW + timedelta(days=1), azure=azure)
        self.assertFalse(early["cost"]["settled"])
        self.assertEqual(early["cost"]["tailAfterClose"], 0.1)
        self.assertEqual(early["cost"]["total"], 3.85)
        late = g.settle_record(closed, now=NOW + timedelta(days=3), azure=azure)
        self.assertTrue(late["cost"]["settled"])
        with self.assertRaises(ValueError):
            g.settle_record(record(), now=NOW, azure=azure)

    def test_query_filters_one_resource_group_lower_case(self):
        body = g.cost_query(start=NOW.date(), end=NOW.date(), resource_group="RG-AvdSwarm-Spike")
        self.assertEqual(body["dataset"]["filter"]["dimensions"]["values"], ["rg-avdswarm-spike"])
        self.assertNotIn("filter", g.cost_query(start=NOW.date(), end=NOW.date(), resource_group=None)["dataset"])

    def test_pending_counts_unsettled_runs_of_the_month_only(self):
        open_run = record()
        closed = g.close_record(record(runId="b"), now=NOW, teardown={"leftovers": []}, token_cost=20, token_source="")
        settled = dict(closed, cost={**closed["cost"], "settled": True})
        last_month = record(runId="c", startedAt="2026-09-30T23:00:00Z")
        refused = record(runId="d", status="refused")
        self.assertEqual(g.pending_amount([open_run, closed, settled, last_month, refused], "2026-10"), 14.5 + 20)


class Ledger(unittest.TestCase):
    def test_open_refuses_a_second_run_and_writes_both_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = ["--ledger", tmp, "open", "--purpose", "t", "--resource-group", "rg-x", "--estimate", "5",
                    "--run-cap", "20", "--monthly-cap", "150", "--month-to-date", "0"]
            self.assertEqual(g.main(args), 0)
            self.assertEqual(g.main(args), 3)
            runs = g.load_runs(Path(tmp), "lab")
            self.assertEqual(sorted(r["status"] for r in runs), ["open", "refused"])
            opened = next(r for r in runs if r["status"] == "open")
            teardown = Path(tmp) / "teardown.json"
            teardown.write_text('{"step":"remove-spike","leftovers":[]}')
            self.assertEqual(g.main(["--ledger", tmp, "close", "--run-id", opened["runId"], "--teardown", str(teardown)]), 0)
            self.assertEqual(g.main(args), 0)


if __name__ == "__main__":
    unittest.main()
