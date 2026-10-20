#!/usr/bin/env python3
# avd-swarm spend guard. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""A run record for every lab run that costs money: estimate and caps before, teardown and actual cost after.

Commands (each prints one JSON line last, for scripts; everything before it is for the reader):

  open    Refuses the run, with reasons, when no cap is set, a line is unpriced (unless --allow-unpriced),
          the estimate is over the run cap, month to date plus the estimate is over the monthly cap, or
          the sandbox already has an open run. Otherwise writes an open run record with its due time.
  close   Records the teardown evidence (the leftovers Remove-Spike.ps1 found) and any token cost billed
          outside Azure. Leftovers close the run red.
  settle  Reads the run's actual Azure cost from Cost Management, by resource group and day. It is
          provisional until three days after the close, then settled; charges after the close day are
          reported as a tail.
  status  Lists a sandbox's runs, with open runs past their due time marked overdue.

Records are JSON files under the ledger folder (default ~/clouddrive/avdswarm-spend), one per run:
<ledger>/<sandbox>/<yyyy-mm>/<run id>.json. They hold amounts, dates, the resource group name and the
teardown result; never a tenant, subscription or card detail. Keep the folder private, or commit it to a
private repo of your own: avd-swarm doesn't need it to be anywhere in particular.

Azure has no hard stop on spend, so the caps are enforced before the run opens and the stop rule (due
time) is checked on every resume. Token spend has its own cap, enforced before each call
(spikes/s1a/agent_api/meter.py, docs/spend.md section 3.7), and the provider's workspace limit backs it up.

Only the standard library and the Azure CLI (az) are used, so it runs in Cloud Shell as is.
"""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCHEMA = "avd-swarm-spend-run/v1"
SETTLE_DAYS = 3
MAX_HOURS = 24
COST_API = "2023-11-01"


# ---------- Pure functions: data in, data out (tested in scripts/tests/test_spend_guard.py) ----------

def new_run_id(now: datetime, suffix: str) -> str:
    """Sortable and safe for resource names and tags: 20261020-0541-a1b2."""
    return f"{now:%Y%m%d-%H%M}-{suffix}"


def month_key(when: str | datetime) -> str:
    stamp = when if isinstance(when, datetime) else parse_time(when)
    return f"{stamp:%Y-%m}"


def parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pending_amount(runs: list[dict], month: str) -> float:
    """What this month's runs may still add that Cost Management doesn't show yet.

    Open runs count at their estimate; closed runs that aren't settled count at their estimate or their
    provisional cost, whichever is larger. Settled runs are already in Cost Management, so they add nothing.
    Counting both is deliberately conservative: a run's early charges can appear in both numbers.
    """
    total = 0.0
    for run in runs:
        if run.get("status") not in ("open", "closed") or month_key(run["startedAt"]) != month:
            continue
        cost = run.get("cost") or {}
        if cost.get("settled"):
            continue
        estimate = float(run["estimate"]["amount"])
        total += max(estimate, float(cost.get("total") or 0)) if run["status"] == "closed" else estimate
    return round(total, 2)


def decide_open(*, estimate: float, unpriced_lines: int, allow_unpriced: bool, run_cap: float,
                monthly_cap: float, month_to_date: float | None, open_runs: list[str], hours: float) -> dict:
    """The gate for a new run. Every reason is listed, so one refusal shows everything to fix."""
    reasons = []
    if run_cap <= 0 or monthly_cap <= 0:
        reasons.append("no cap set: pass a run cap and a monthly cap above 0")
    if run_cap > monthly_cap > 0:
        reasons.append(f"the run cap {run_cap:.2f} is above the monthly cap {monthly_cap:.2f}")
    if unpriced_lines > 0 and not allow_unpriced:
        reasons.append(f"{unpriced_lines} line(s) unpriced: price them by hand into the estimate, or allow unpriced lines on purpose")
    if estimate <= 0:
        reasons.append("no estimate: every run needs one")
    if run_cap > 0 and estimate > run_cap:
        reasons.append(f"the estimate {estimate:.2f} is over the run cap {run_cap:.2f}")
    if month_to_date is None:
        reasons.append("month to date unknown: Cost Management could not be read; pass the month to date by hand")
    elif monthly_cap > 0 and month_to_date + estimate > monthly_cap:
        reasons.append(f"month to date {month_to_date:.2f} plus the estimate {estimate:.2f} is over the monthly cap {monthly_cap:.2f}")
    if open_runs:
        reasons.append(f"the sandbox already has an open run ({', '.join(open_runs)}): close it first")
    if not 0 < hours <= MAX_HOURS:
        reasons.append(f"planned hours must be above 0 and at most {MAX_HOURS}")
    return {"allowed": not reasons, "reasons": reasons}


def new_record(*, run_id: str, sandbox: str, purpose: str, resource_group: str, now: datetime, hours: float,
               estimate: float, estimate_note: str, unpriced_lines: int, allow_unpriced: bool, run_cap: float,
               monthly_cap: float, month_to_date: float | None, month_to_date_source: str, decision: dict) -> dict:
    return {
        "schema": SCHEMA,
        "runId": run_id,
        "sandbox": sandbox,
        "purpose": purpose,
        "resourceGroup": resource_group,
        "status": "open" if decision["allowed"] else "refused",
        "startedAt": iso(now),
        "dueAt": iso(now + timedelta(hours=hours)),
        "hours": hours,
        "estimate": {"amount": round(estimate, 2), "note": estimate_note, "unpricedLines": unpriced_lines,
                     "allowUnpriced": allow_unpriced},
        "caps": {"run": run_cap, "monthly": monthly_cap},
        "monthToDate": {"amount": month_to_date, "source": month_to_date_source},
        "decision": decision,
    }


def close_record(record: dict, *, now: datetime, teardown: dict, token_cost: float | None, token_source: str) -> dict:
    """Closes an open run with its teardown evidence. Leftovers keep it red until a clean close."""
    if record["status"] not in ("open", "closed"):
        raise ValueError(f"run {record['runId']} is {record['status']}, so there is nothing to close")
    leftovers = list(teardown.get("leftovers") or [])
    closed = dict(record)
    closed["status"] = "closed"
    closed["teardown"] = {"closedAt": iso(now), "clean": not leftovers, "leftovers": leftovers,
                          "checkedBy": teardown.get("step", "unknown")}
    cost = dict(record.get("cost") or {})
    if token_cost is not None:
        cost["tokens"] = round(token_cost, 4)
        cost["tokensSource"] = token_source
    cost["total"] = round(float(cost.get("azure") or 0) + float(cost.get("tokens") or 0), 4)
    closed["cost"] = cost
    return closed


def sum_cost_rows(response: dict) -> dict:
    """Totals a Cost Management query response by day: {'total', 'currency', 'byDay': {'yyyy-mm-dd': amount}}."""
    props = response.get("properties") or {}
    columns = [c["name"] for c in props.get("columns") or []]
    by_day: dict[str, float] = {}
    total = 0.0
    currency = None
    for row in props.get("rows") or []:
        values = dict(zip(columns, row))
        amount = float(values.get("Cost", values.get("PreTaxCost", 0)) or 0)
        total += amount
        currency = values.get("Currency", currency)
        if "UsageDate" in values:
            day = str(values["UsageDate"])
            key = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
            by_day[key] = round(by_day.get(key, 0) + amount, 4)
    return {"total": round(total, 4), "currency": currency, "byDay": by_day}


def settle_record(record: dict, *, now: datetime, azure: dict) -> dict:
    """Adds the actual Azure cost. Settled once SETTLE_DAYS have passed since the close; a tail is any charge after the close day."""
    teardown = record.get("teardown")
    if record["status"] != "closed" or not teardown:
        raise ValueError(f"run {record['runId']} is {record['status']}: close it before settling")
    close_day = parse_time(teardown["closedAt"]).date()
    tail = round(sum(v for d, v in azure["byDay"].items() if date.fromisoformat(d) > close_day), 4)
    cost = dict(record.get("cost") or {})
    cost.update({"azure": azure["total"], "currency": azure["currency"], "byDay": azure["byDay"],
                 "tailAfterClose": tail, "readAt": iso(now),
                 "settled": now.date() >= close_day + timedelta(days=SETTLE_DAYS)})
    cost["total"] = round(float(cost["azure"]) + float(cost.get("tokens") or 0), 4)
    cost["versusEstimate"] = round(cost["total"] / record["estimate"]["amount"], 2) if record["estimate"]["amount"] else None
    settled = dict(record)
    settled["cost"] = cost
    return settled


def run_flags(record: dict, now: datetime) -> list[str]:
    flags = []
    if record["status"] == "open" and now > parse_time(record["dueAt"]):
        flags.append("overdue: past its planned hours, tear it down now")
    teardown = record.get("teardown") or {}
    if record["status"] == "closed" and not teardown.get("clean", True):
        flags.append(f"teardown left {len(teardown['leftovers'])} thing(s): clean up and close again")
    cost = record.get("cost") or {}
    if cost.get("tailAfterClose"):
        flags.append(f"charges after the close day: {cost['tailAfterClose']}")
    if record["status"] == "closed" and not cost.get("settled"):
        flags.append("cost not settled yet")
    return flags


def cost_query(*, start: date, end: date, resource_group: str | None, granularity: str = "Daily") -> dict:
    """The Cost Management query body: actual cost, optionally for one resource group, by day."""
    body = {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {"from": f"{start.isoformat()}T00:00:00Z", "to": f"{end.isoformat()}T23:59:59Z"},
        "dataset": {"granularity": granularity, "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}}},
    }
    if resource_group:
        # Cost Management reports resource group names in lower case.
        body["dataset"]["filter"] = {"dimensions": {"name": "ResourceGroupName", "operator": "In", "values": [resource_group.lower()]}}
    return body


# ---------- Ledger files ----------

def ledger_dir(ledger: Path, sandbox: str) -> Path:
    return ledger / sandbox


def load_runs(ledger: Path, sandbox: str) -> list[dict]:
    folder = ledger_dir(ledger, sandbox)
    if not folder.exists():
        return []
    return [json.loads(p.read_text()) for p in sorted(folder.glob("*/*.json"))]


def run_path(ledger: Path, record: dict) -> Path:
    return ledger_dir(ledger, record["sandbox"]) / month_key(record["startedAt"]) / f"{record['runId']}.json"


def save(ledger: Path, record: dict) -> Path:
    path = run_path(ledger, record)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n")
    tmp.replace(path)
    return path


def find_run(ledger: Path, sandbox: str, run_id: str) -> dict:
    for run in load_runs(ledger, sandbox):
        if run["runId"] == run_id:
            return run
    raise SystemExit(f"No run {run_id} in {ledger_dir(ledger, sandbox)}.")


# ---------- Azure reads (az only; nothing here writes to Azure) ----------

def az(*args: str) -> str:
    result = subprocess.run(["az", *args, "--only-show-errors", "-o", "json"], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"az {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout


def read_cost(body: dict) -> dict:
    subscription = json.loads(az("account", "show"))["id"]  # used for the call only, never written down
    url = f"https://management.azure.com/subscriptions/{subscription}/providers/Microsoft.CostManagement/query?api-version={COST_API}"
    return sum_cost_rows(json.loads(az("rest", "--method", "post", "--url", url, "--body", json.dumps(body))))


# ---------- Commands ----------

def emit(step: str, **values) -> None:
    print(json.dumps({"step": step, **values}, separators=(",", ":")))


def cmd_open(args) -> int:
    now = datetime.now(timezone.utc)
    runs = load_runs(args.ledger, args.sandbox)
    month = month_key(now)
    if args.month_to_date is not None:
        month_to_date, source = args.month_to_date, "given by hand"
    else:
        try:
            first = now.date().replace(day=1)
            billed = read_cost(cost_query(start=first, end=now.date(), resource_group=None, granularity="None"))["total"]
            pending = pending_amount(runs, month)
            month_to_date, source = round(billed + pending, 2), f"Cost Management {billed:.2f} + unsettled runs {pending:.2f}"
        except RuntimeError as error:
            print(f"Could not read the month to date: {error}", file=sys.stderr)
            month_to_date, source = None, "unavailable"
    decision = decide_open(estimate=args.estimate, unpriced_lines=args.unpriced_lines, allow_unpriced=args.allow_unpriced,
                           run_cap=args.run_cap, monthly_cap=args.monthly_cap, month_to_date=month_to_date,
                           open_runs=[r["runId"] for r in runs if r["status"] == "open"], hours=args.hours)
    record = new_record(run_id=new_run_id(now, secrets.token_hex(2)), sandbox=args.sandbox, purpose=args.purpose,
                        resource_group=args.resource_group, now=now, hours=args.hours, estimate=args.estimate,
                        estimate_note=args.estimate_note, unpriced_lines=args.unpriced_lines,
                        allow_unpriced=args.allow_unpriced, run_cap=args.run_cap, monthly_cap=args.monthly_cap,
                        month_to_date=month_to_date, month_to_date_source=source, decision=decision)
    path = save(args.ledger, record)
    print(f"Estimate {args.estimate:.2f}, run cap {args.run_cap:.2f}; month to date {month_to_date} ({source}) of {args.monthly_cap:.2f}.")
    if decision["allowed"]:
        print(f"Run {record['runId']} is open until {record['dueAt']}. Record: {path}")
    else:
        print("Refused, nothing may be deployed:\n  " + "\n  ".join(decision["reasons"]) + f"\nRecord: {path}")
    emit("spend-open", runId=record["runId"], status=record["status"], dueAt=record["dueAt"], reasons=decision["reasons"])
    return 0 if decision["allowed"] else 3


def cmd_close(args) -> int:
    record = find_run(args.ledger, args.sandbox, args.run_id)
    text = args.teardown.strip()
    teardown = json.loads(text if text.startswith("{") else Path(text).read_text())
    closed = close_record(record, now=datetime.now(timezone.utc), teardown=teardown,
                          token_cost=args.token_cost, token_source=args.token_source)
    path = save(args.ledger, closed)
    if closed["teardown"]["clean"]:
        print(f"Run {args.run_id} closed clean. In {SETTLE_DAYS} days, settle its cost:\n"
              f"  python3 {Path(__file__).resolve()} --ledger {args.ledger} --sandbox {args.sandbox} settle --run-id {args.run_id}")
    else:
        print(f"Run {args.run_id} closed RED, teardown left: {'; '.join(closed['teardown']['leftovers'])}. Clean up, then close again.")
    print(f"Record: {path}")
    emit("spend-close", runId=args.run_id, clean=closed["teardown"]["clean"], leftovers=closed["teardown"]["leftovers"])
    return 0 if closed["teardown"]["clean"] else 1


def cmd_settle(args) -> int:
    record = find_run(args.ledger, args.sandbox, args.run_id)
    now = datetime.now(timezone.utc)
    azure = read_cost(cost_query(start=parse_time(record["startedAt"]).date(), end=now.date(),
                                 resource_group=record["resourceGroup"]))
    settled = settle_record(record, now=now, azure=azure)
    save(args.ledger, settled)
    cost = settled["cost"]
    state = "settled" if cost["settled"] else "provisional"
    print(f"Run {args.run_id}: {cost['total']} {cost['currency'] or ''} ({state}; Azure {cost['azure']}, tokens outside Azure "
          f"{cost.get('tokens', 0)}) against an estimate of {record['estimate']['amount']}.")
    for flag in run_flags(settled, now):
        print(f"  {flag}")
    emit("spend-settle", runId=args.run_id, total=cost["total"], settled=cost["settled"], tailAfterClose=cost["tailAfterClose"])
    return 0


def cmd_status(args) -> int:
    now = datetime.now(timezone.utc)
    runs = load_runs(args.ledger, args.sandbox)
    rows = []
    for run in runs:
        flags = run_flags(run, now)
        rows.append({"runId": run["runId"], "status": run["status"], "estimate": run["estimate"]["amount"],
                     "cost": (run.get("cost") or {}).get("total"), "flags": flags})
        print(f"{run['runId']}  {run['status']:<8} estimate {run['estimate']['amount']:>7}  "
              f"cost {rows[-1]['cost'] if rows[-1]['cost'] is not None else '-':>7}  {'; '.join(flags)}")
    if not runs:
        print(f"No runs in {ledger_dir(args.ledger, args.sandbox)}.")
    emit("spend-status", runs=rows)
    return 1 if any("overdue" in f for r in rows for f in r["flags"]) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", type=Path, default=Path.home() / "clouddrive" / "avdswarm-spend")
    parser.add_argument("--sandbox", default="lab", help="a name for the subscription you run labs in; one open run at a time")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("open", help="check the estimate against the caps and open a run")
    p.add_argument("--purpose", required=True)
    p.add_argument("--resource-group", required=True, help="the one resource group the run deploys, for its actual cost")
    p.add_argument("--estimate", type=float, required=True)
    p.add_argument("--estimate-note", default="")
    p.add_argument("--unpriced-lines", type=int, default=0)
    p.add_argument("--allow-unpriced", action="store_true")
    p.add_argument("--run-cap", type=float, required=True)
    p.add_argument("--monthly-cap", type=float, required=True)
    p.add_argument("--month-to-date", type=float, help="skip the Cost Management read and use this amount")
    p.add_argument("--hours", type=float, default=6)
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("close", help="record the teardown evidence and close a run")
    p.add_argument("--run-id", required=True)
    p.add_argument("--teardown", required=True, help="the JSON line Remove-Spike.ps1 prints ({\"leftovers\": [...]}), or a file holding it")
    p.add_argument("--token-cost", type=float, help="token spend billed outside Azure, from the meter or the provider's usage page")
    p.add_argument("--token-source", default="metered by the agent service")
    p.set_defaults(func=cmd_close)

    p = sub.add_parser("settle", help="read the run's actual Azure cost from Cost Management")
    p.add_argument("--run-id", required=True)
    p.set_defaults(func=cmd_settle)

    p = sub.add_parser("status", help="list the sandbox's runs")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
