#!/usr/bin/env python3
"""Preview or repair empty unified feedback decision snapshots."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from market_db import DEFAULT_DB_PATH
from market_feedback import repair_missing_feedback_snapshots


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.db.exists():
        parser.error(f"database does not exist: {args.db}")
    uri = f"file:{args.db}?mode={'rw' if args.apply else 'ro'}"
    conn = sqlite3.connect(uri, uri=True)
    try:
        if args.apply:
            conn.execute("BEGIN IMMEDIATE")
        result = repair_missing_feedback_snapshots(conn, apply=args.apply)
        if args.apply:
            if result["unresolved"]:
                conn.rollback()
                result["updated"] = 0
            else:
                conn.commit()
    finally:
        conn.close()
    result["mode"] = "apply" if args.apply else "preview"
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if result["unresolved"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
