"""Resend event-shaped unified market items to Feishu using current settings."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from db_utils import connect_sqlite
from env_utils import load_env
from market_db import DEFAULT_DB_PATH, init_db
from market_event_adapter import analyze_event, maybe_deliver_event
from market_item import NormalizedMarketItem, decision_result_from_payload


def _json_dict(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> tuple[str, ...]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, json.JSONDecodeError):
        return ()
    return tuple(str(item) for item in parsed if str(item).strip()) if isinstance(parsed, list) else ()


def load_event_item(
    market_item_id: int,
    task: str,
    db_path: Path,
) -> tuple[NormalizedMarketItem, int, str, dict[str, Any]] | None:
    with connect_sqlite(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT m.*,r.id AS review_id,r.task AS review_task,r.review_status,r.decision_json,
                   r.interpretation_json,r.legacy_payload_json
            FROM market_items m
            JOIN market_reviews r
              ON r.market_item_id=m.id AND r.is_current=1
            WHERE m.id=?
              AND r.review_status='succeeded'
              AND (?='' OR r.task=?)
              AND EXISTS (
                  SELECT 1 FROM market_item_aliases a
                  WHERE a.market_item_id=m.id AND a.item_kind='event'
              )
            ORDER BY r.id DESC
            LIMIT 1
            """,
            (market_item_id, task, task),
        ).fetchone()
    if not row:
        return None
    raw = _json_dict(row["raw_json"])
    raw.setdefault("source_event_id", str(row["source_item_id"] or ""))
    item = NormalizedMarketItem(
        source=str(row["source"] or ""),
        source_category=str(row["source_category"] or ""),
        publisher_role=str(row["publisher_role"] or ""),
        collector=str(row["collector"] or ""),
        content_type=str(row["content_type"] or "event"),
        title=str(row["title"] or ""),
        summary=str(row["summary"] or ""),
        full_text=str(row["full_text"] or ""),
        url=str(row["url"] or ""),
        published_at=str(row["published_at"] or ""),
        first_seen_at=str(row["first_seen_at"] or ""),
        symbols=_json_list(row["symbols_json"]),
        themes=_json_list(row["themes_json"]),
        raw=raw,
        dedupe_key=str(row["dedupe_key"] or ""),
        access_note=str(row["access_note"] or ""),
    )
    analysis = _json_dict(row["legacy_payload_json"])
    decision = _json_dict(row["decision_json"])
    interpretation = _json_dict(row["interpretation_json"])
    if decision:
        analysis["_decision_result"] = decision
    if interpretation:
        analysis["_interpretation_result"] = interpretation
        analysis.setdefault("core_content", str(interpretation.get("core_content") or ""))
    return item, int(row["review_id"]), str(row["review_task"] or "production"), analysis


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("market_item_ids", nargs="+", type=int, help="market_items.id values to resend.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite database path.")
    parser.add_argument(
        "--task",
        default="",
        help="Optional market_reviews task name; default uses the latest current succeeded review.",
    )
    parser.add_argument(
        "--reanalyze",
        action="store_true",
        help="Generate a fresh thin interpretation from the stored DecisionResult before sending.",
    )
    return parser.parse_args()


def main() -> int:
    load_env()
    args = parse_args()
    db_path = Path(args.db)
    init_db(db_path).close()
    exit_code = 0
    for market_item_id in args.market_item_ids:
        loaded = load_event_item(market_item_id, args.task, db_path)
        if loaded is None:
            print(f"market item #{market_item_id}: missing succeeded event review", file=sys.stderr)
            exit_code = 1
            continue
        item, review_id, review_task, analysis = loaded
        decision = decision_result_from_payload(analysis)
        if decision is None:
            print(f"market item #{market_item_id}: missing DecisionResult", file=sys.stderr)
            exit_code = 1
            continue
        try:
            if args.reanalyze:
                analysis = analyze_event(
                    item,
                    task=f"{review_task}_manual_resend",
                    db_path=db_path,
                    decision=decision,
                )
            status = maybe_deliver_event(
                item,
                analysis,
                db_path=db_path,
                decision=decision,
                market_item_id=market_item_id,
                market_review_id=review_id,
            )
        except Exception as exc:  # noqa: BLE001 - manual ops should continue with other IDs.
            print(f"market item #{market_item_id}: resend_failed: {exc}", file=sys.stderr)
            exit_code = 1
            continue
        print(f"market item #{market_item_id}: {status}")
        if status != "sent":
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
