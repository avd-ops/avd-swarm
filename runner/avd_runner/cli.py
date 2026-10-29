# avd-swarm check runner. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""The check runner's command line, for the workflow template in templates/workflows/runbooks.yml
(core-and-adapters.md section 3.3; spec section 3c).

  select --schedule CRON          the runbook IDs whose trigger.schedule is this cron (one per line)
  flag --subscription S --resource-group RG
                                  reads the kill switch tag through ARM; exit 0 when agents may run,
                                  3 at halt-agents or above, 4 when it can't be read (both refuse; governance
                                  section 2.3), so a broken setup shows as a failed job, not a quiet skip
  title --id ID                   the runbook's issue title; exit 2 for an ID that isn't a runbook here
  run --id ID --out FILE          asks the agent API to run one runbook and writes the finding; a transport
                                  failure or a reply that isn't a finding is written as could-not-run, never clear
  alert-job                       the alert path's Container Apps Job (avd_runner/alert.py), configured by its
                                  environment
  issue --finding FILE [--open-outcome O] --summary FILE
                                  prints the action (open, comment, close or none) and the outcome on one line,
                                  and writes the summary Markdown
  orphans --issues FILE           the numbers of open runbook issues (gh issue list --json number,title) whose
                                  runbook is no longer in this repo, one per line, so the workflow closes them

The runner never calls a model and holds no model credential: the agent API runs the runbook and its
gateway enforces the runbook's budget. It signs in with the read-only runner identity (docs/identities.md)
through the Azure CLI that azure/login set up; no token is ever printed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))
from avd_gateway import flag as flags  # noqa: E402

from . import report  # noqa: E402
from .api import load_all  # noqa: E402
from .execute import STARTED_BY  # noqa: E402

WORKFLOW_STARTED_BY = tuple(s for s in STARTED_BY if s != "alert")  # alert runs come from the alert job only

REFUSED = 3
UNREADABLE = 4
UNKNOWN_RUNBOOK = 2
TAG_DEFAULT = "avd-swarm-switch"
TIMEOUT_SECONDS = 300
RETRY_STATUSES = (502, 503)  # a proxy or a cold start; never 500 or 504, which may mean the runbook already ran
RETRY_SECONDS = 10


def select(root: Path, cron: str) -> list[str]:
    wanted = " ".join(cron.split())
    return [rid for rid, rb in load_all(root).items()
            if " ".join(str(rb.meta["trigger"].get("schedule", "")).split()) == wanted]


def az(*args: str) -> str:
    done = subprocess.run(["az", *args], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        # The CLI's own error, cut short; it carries the status and code, not the token.
        raise RuntimeError(f"az {args[0]} failed ({done.returncode}): {done.stderr.strip()[:300]}")
    return done.stdout


def arm_get_json(url: str) -> dict:
    return json.loads(az("rest", "--method", "get", "--url", url))


def flag_position(subscription: str, resource_group: str, tag: str, refuse_at: list[str]) -> tuple[int, str]:
    return flag_position_with(arm_get_json, subscription, resource_group, tag, refuse_at)


def flag_position_with(get_json, subscription: str, resource_group: str, tag: str, refuse_at: list[str]) -> tuple[int, str]:
    """(0, position) when agents may run, (REFUSED, why) at a refusing position, (UNREADABLE, why) otherwise.
    get_json signs in as the caller: the Azure CLI here, the managed identity in the alert job."""
    read = flags.arm_flag_reader(get_json, subscription, resource_group, tag)
    try:
        position = read()
    except Exception as error:
        return UNREADABLE, f"kill switch flag unreadable, refusing: {type(error).__name__}: {error}"[:300]
    try:
        return 0, flags.check(lambda: position, refuse_at)
    except flags.SpendRefused as refused:
        return REFUSED, str(refused)


def az_token(resource: str) -> str:
    return az("account", "get-access-token", "--resource", resource, "--query", "accessToken", "-o", "tsv").strip()


def az_scope_token(scope: str) -> str:
    """A token for one delegated scope (AGENT_API_SCOPE), as an operator's manual run asks for it."""
    return az("account", "get-access-token", "--scope", scope, "--query", "accessToken", "-o", "tsv").strip()


def post_runbook(url: str, resource: str, runbook: str, started_by: str, token=az_token, sleep=time.sleep) -> dict:
    """One retry after a 502 or 503, so a transient blip doesn't open an issue that the next run closes."""
    token = token(resource)
    request = urllib.request.Request(f"{url.rstrip('/')}/runbook", method="POST",
                                     data=json.dumps({"runbook": runbook, "startedBy": started_by}).encode(),
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        return _post(request)
    except urllib.error.HTTPError as error:
        if error.code not in RETRY_STATUSES:
            raise
        print(f"Agent API answered {error.code}; retrying once in {RETRY_SECONDS} seconds", flush=True)
    sleep(RETRY_SECONDS)
    return _post(request)


def _post(request: urllib.request.Request) -> dict:
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read())


def checked_finding(finding: object, runbook: str) -> dict:
    """The agent API's reply, refused unless it is a finding for this runbook with a known outcome."""
    if not isinstance(finding, dict) or finding.get("runbook") != runbook or finding.get("outcome") not in report.OUTCOMES:
        raise ValueError("the agent API's reply isn't a finding for this runbook")
    for key, empty in (("version", 0), ("facts", []), ("failedReads", []), ("readErrors", {}),
                       ("investigation", {"status": "not-available"})):
        finding.setdefault(key, empty)
    return finding


def error_detail(error: Exception) -> str:
    """The type and, for HTTP, the status and reason: enough to tell setup errors from outages, no names."""
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTPError {error.code} {error.reason}"[:80]
    return type(error).__name__


def unreachable(runbook: str, started_by: str, error: Exception) -> dict:
    return {"runbook": runbook, "version": 0, "outcome": "could-not-run", "facts": [],
            "failedReads": ["agent-api"], "readErrors": {"agent-api": error_detail(error)},
            "investigation": {"status": "not-available"}, "threadStartedBy": started_by}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="avd_runner")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("select")
    s.add_argument("--schedule", required=True)
    s.add_argument("--root", default=str(ROOT))
    f = sub.add_parser("flag")
    f.add_argument("--subscription", required=True)
    f.add_argument("--resource-group", required=True)
    f.add_argument("--tag", default=TAG_DEFAULT)
    t = sub.add_parser("title")
    t.add_argument("--id", required=True)
    r = sub.add_parser("run")
    r.add_argument("--id", required=True)
    r.add_argument("--started-by", default="schedule", choices=WORKFLOW_STARTED_BY)
    r.add_argument("--out", required=True)
    sub.add_parser("alert-job")
    i = sub.add_parser("issue")
    i.add_argument("--finding", required=True)
    i.add_argument("--open-outcome", default=None)
    i.add_argument("--summary", required=True)
    o = sub.add_parser("orphans")
    o.add_argument("--issues", required=True)
    args = parser.parse_args(argv)

    if args.command == "select":
        print("\n".join(select(Path(args.root), args.schedule)))
        return 0
    if args.command == "flag":
        import yaml
        config = yaml.safe_load((ROOT / "governance/gateway.yml").read_text(encoding="utf-8"))
        code, detail = flag_position(args.subscription, args.resource_group, args.tag, list(flags.refusals(config)))
        print({0: f"switch: {detail}", REFUSED: f"skipped: {detail}"}.get(code, f"::error::{detail}"))
        return code
    if args.command == "alert-job":
        from .alert import main as alert_main
        return alert_main()
    if args.command == "orphans":
        issues = json.loads(Path(args.issues).read_text(encoding="utf-8"))
        print("\n".join(str(n) for n in report.orphan_issues(issues, set(load_all(ROOT)))))
        return 0
    if args.command in ("title", "run") and args.id not in load_all(ROOT):
        print(f"::error::{args.id!r} isn't a runbook in this repo", file=sys.stderr)
        return UNKNOWN_RUNBOOK
    if args.command == "title":
        print(report.issue_title(args.id))
        return 0
    if args.command == "run":
        url, resource = os.environ.get("AGENT_API_URL", ""), os.environ.get("AGENT_API_RESOURCE", "")
        try:
            if not url or not resource:
                raise RuntimeError("AGENT_API_URL and AGENT_API_RESOURCE must be set")
            print(f"Asking the agent API to run {args.id} (up to {TIMEOUT_SECONDS} seconds)", flush=True)
            scope = os.environ.get("AGENT_API_SCOPE", "")  # set for an operator's run: their token for that scope
            token = (lambda _resource: az_scope_token(scope)) if scope else az_token
            finding = checked_finding(post_runbook(url, resource, args.id, args.started_by, token=token), args.id)
        except Exception as error:
            finding = unreachable(args.id, args.started_by, error)
        Path(args.out).write_text(json.dumps(finding, indent=2), encoding="utf-8")
        print(f"{finding['runbook']}: {finding['outcome']}")
        return 0
    finding = json.loads(Path(args.finding).read_text(encoding="utf-8"))
    Path(args.summary).write_text(report.summary(finding), encoding="utf-8")
    print(report.issue_action(args.open_outcome or None, finding["outcome"]), finding["outcome"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
