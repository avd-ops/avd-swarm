#!/usr/bin/env python3
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Automated 0-token smoke test for avd-assistant (buddy/server.py)."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from buddy import server as buddy_server  # noqa: E402


def run_smoke(*, offline: bool = False) -> int:
    state = buddy_server.BuddyState(sim_mode=False, learn_search=not offline)
    srv = buddy_server.ThreadingHTTPServer(("127.0.0.1", 0), buddy_server.make_handler(state))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/") as resp:
            assert resp.status == 200, f"GET / returned {resp.status}"
            assert "Content-Security-Policy" in resp.headers, "Missing Content-Security-Policy header"
        with urllib.request.urlopen(f"{base}/api/status") as resp:
            assert resp.status == 200, f"GET /api/status returned {resp.status}"

        cases = [
            ("guru", "FSLogix 0x0000052e", "sm-guru-1"),
            ("guru", "Accelerated Networking", "sm-guru-2"),
            ("guru", "RDP Shortpath UDP 3390", "sm-guru-3"),
            ("buddy", "FSLogix Kerberos", "sm-buddy-1"),
        ]
        for mode, question, key in cases:
            req = urllib.request.Request(
                f"{base}/api/turns",
                data=json.dumps({"question": question, "mode": mode, "idempotencyKey": key}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                turn = json.loads(resp.read().decode("utf-8"))["turn"]
            docs = (turn.get("modeBlock") or {}).get("docs") or []
            learn = turn.get("learnLinks") or []
            sources = sorted({d.get("source") for d in docs})
            badge = turn.get("sourceBadge") or ""
            print(f"[{mode}] {question!r} -> badge={badge!r} docs={len(docs)} sources={sources} learnLinks={len(learn)}")
            assert "0 tokens" in badge, f"Expected 0 tokens in {turn}"
            assert len(docs) >= 1, f"Expected docs for {question}"
            if not offline:
                assert len(learn) >= 1, f"Expected Microsoft Learn links for {question}"

        print("SMOKE TEST PASSED")
        return 0
    finally:
        srv.shutdown()
        srv.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run automated 0-token smoke test against avd-assistant.")
    parser.add_argument("--offline", action="store_true", help="Skip live Microsoft Learn Search API assertions.")
    args = parser.parse_args(argv)
    return run_smoke(offline=args.offline)


if __name__ == "__main__":
    raise SystemExit(main())
