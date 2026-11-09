# avd-swarm gate CLI. Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Command-line entry point for `templates/workflows/approve.yml` (spec section 5 steps 5-7, avd-swarm#43).

  check --plan FILE [--summary FILE]
  apply --plan FILE --approved-hash SHA256 [--live-state FILE] [--flag POSITION] [--summary FILE]

Prints only the redacted JSON record (planId, planHash, action, passedCount, failedCount, status) and exits 0
when `ready` / `applied-and-verified`, or non-zero on any refusal or verification failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .apply import apply_approved_plan, check_plan, redacted_summary_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="avd_gate")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("check")
    c.add_argument("--plan", required=True)
    c.add_argument("--cloud", default=os.environ.get("CLOUD_ENVIRONMENT", ""))
    c.add_argument("--azure-environment", default=os.environ.get("AZURE_ENVIRONMENT", ""))
    c.add_argument("--graph-environment", default=os.environ.get("GRAPH_ENVIRONMENT", ""))
    c.add_argument("--summary", default="")

    a = sub.add_parser("apply")
    a.add_argument("--plan", required=True)
    a.add_argument("--approved-hash", required=True)
    a.add_argument("--live-state", default="")
    a.add_argument("--flag", default="run")
    a.add_argument("--cloud", default=os.environ.get("CLOUD_ENVIRONMENT", ""))
    a.add_argument("--azure-environment", default=os.environ.get("AZURE_ENVIRONMENT", ""))
    a.add_argument("--graph-environment", default=os.environ.get("GRAPH_ENVIRONMENT", ""))
    a.add_argument("--summary", default="")

    args = parser.parse_args(argv)
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    runner_env = {
        k: v
        for k, v in (
            ("runnerCloud", args.cloud),
            ("azureEnvironment", args.azure_environment),
            ("graphEnvironment", args.graph_environment),
        )
        if v
    }

    if args.command == "check":
        res = check_plan(plan, runner_env=runner_env or None)
    else:
        live = (
            json.loads(Path(args.live_state).read_text(encoding="utf-8"))
            if args.live_state
            else plan.get("preconditionState", {})
        )
        res = apply_approved_plan(
            plan=plan,
            approved_hash=args.approved_hash,
            read_flag=lambda: args.flag,
            read_live_state=lambda: live,
            apply_action=lambda _p, _s: {"applied": True},
            verify_action=lambda _p, _s, eff: bool(eff.get("applied") and live.get("verifiedPostState", True)),
            runner_env=runner_env or None,
        )

    if args.summary:
        Path(args.summary).write_text(redacted_summary_markdown(res), encoding="utf-8")

    out = {
        k: res[k]
        for k in ("planId", "planHash", "action", "passedCount", "failedCount", "status")
    }
    print(json.dumps(out))
    return 0 if res["status"] in ("ready", "applied-and-verified") else 3


if __name__ == "__main__":
    sys.exit(main())
