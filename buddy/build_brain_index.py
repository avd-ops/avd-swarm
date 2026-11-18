#!/usr/bin/env python3
# Personal project, not for production, provided as is; not affiliated with the author's employer, Liquidware or Microsoft.
"""Build a read-only SQLite FTS5 index for avd-assistant from pinned avd-second-brain and avd-assess sources."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from buddy import assistant_router  # noqa: E402


def build_sqlite_index(
    *,
    second_brain_dir: Path,
    assess_dir: Path,
    remediate_dir: Path | None = None,
    learn_dir: Path | None = None,
    output_path: Path,
    pinned_sha: str = "0000000",
    assess_sha: str = "0000000",
    remediate_sha: str = "0000000",
    learn_sha: str = "0000000",
) -> int:
    docs = assistant_router.load_docs_from_sources(second_brain_dir, assess_dir, remediate_dir, learn_dir)
    if not docs:
        raise RuntimeError(f"No docs found in {second_brain_dir}, {assess_dir}, or {remediate_dir}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    conn = sqlite3.connect(str(output_path))
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE docs_fts USING fts5(doc_id, title, source, tags, summary, learn_url, snippets_json)"
        )
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        for doc in docs:
            conn.execute(
                "INSERT INTO docs_fts (doc_id, title, source, tags, summary, learn_url, snippets_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    doc["doc_id"],
                    doc["title"],
                    doc["source"],
                    doc.get("tags", ""),
                    doc["summary"],
                    doc.get("learn_url", ""),
                    json.dumps(doc.get("snippets", [])),
                ),
            )
        meta_rows = [
            ("pinned_sha", pinned_sha[:7]),
            ("assess_sha", assess_sha[:7]),
            ("remediate_sha", remediate_sha[:7]),
            ("learn_sha", learn_sha[:7]),
            ("doc_count", str(len(docs))),
            ("built_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        ]
        conn.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", meta_rows)
        conn.commit()
    finally:
        conn.close()
    return len(docs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build read-only FTS5 index for avd-assistant.")
    parser.add_argument("--second-brain", type=Path, default=ROOT.parent / "avd-second-brain")
    parser.add_argument("--assess", type=Path, default=ROOT.parent / "avd-assess")
    parser.add_argument("--remediate", type=Path, default=ROOT.parent / "avd-remediate")
    parser.add_argument("--learn", type=Path, default=ROOT.parent / "avd-learn")
    parser.add_argument("--output", type=Path, default=ROOT / "buddy" / "brain-index.sqlite")
    args = parser.parse_args(argv)

    cfg = assistant_router.load_yaml_safe(ROOT / "governance" / "assistant-index.yml")
    sources = cfg.get("sources") or {}
    brain_sha = str((sources.get("avd-second-brain") or {}).get("pinned_sha", "0000000"))
    assess_sha = str((sources.get("avd-assess") or {}).get("pinned_sha", "0000000"))
    remediate_sha = str((sources.get("avd-remediate") or {}).get("pinned_sha", "0000000"))
    learn_sha = str((sources.get("avd-learn") or {}).get("pinned_sha", "0000000"))

    count = build_sqlite_index(
        second_brain_dir=args.second_brain,
        assess_dir=args.assess,
        remediate_dir=args.remediate,
        learn_dir=args.learn,
        output_path=args.output,
        pinned_sha=brain_sha,
        assess_sha=assess_sha,
        remediate_sha=remediate_sha,
        learn_sha=learn_sha,
    )
    print(
        f"Built {args.output} with {count} docs "
        f"(brain @{brain_sha[:7]}, assess @{assess_sha[:7]}, remediate @{remediate_sha[:7]}, learn @{learn_sha[:7]})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
