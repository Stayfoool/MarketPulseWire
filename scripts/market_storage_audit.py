#!/usr/bin/env python3
"""Read-only comparison of unified results and retained compatibility tables."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from market_db import DEFAULT_DB_PATH, MARKET_RESULTS_MIGRATION_VERSION
from market_item import decision_result_from_payload


def _json_dict(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def _action_differences(
    conn: sqlite3.Connection,
    *,
    store_kind: str,
    legacy_table: str,
    payload_column: str,
    since: str,
    until: str,
) -> int:
    rows = conn.execute(
        f"""
        SELECT r.decision_action,l.{payload_column}
        FROM market_reviews r
        JOIN {legacy_table} l ON r.legacy_store_id=l.source||':'||l.item_id
        WHERE r.legacy_store_kind=? AND r.created_at>=? AND r.created_at<?
          AND COALESCE(r.decision_action,'')<>''
        """,
        (store_kind, since, until),
    )
    differences = 0
    for expected, payload_json in rows:
        decision = decision_result_from_payload(_json_dict(payload_json))
        differences += int(decision is None or decision.action != str(expected or ""))
    return differences


def _event_action_differences(
    conn: sqlite3.Connection,
    *,
    since: str,
    until: str,
) -> int:
    rows = conn.execute(
        """
        SELECT r.decision_action,l.analysis_json
        FROM market_reviews r
        JOIN event_analyses l ON r.legacy_store_id=CAST(l.id AS TEXT)
        WHERE r.legacy_store_kind='event_analyses'
          AND r.created_at>=? AND r.created_at<? AND COALESCE(r.decision_action,'')<>''
        """,
        (since, until),
    )
    differences = 0
    for expected, payload_json in rows:
        decision = decision_result_from_payload(_json_dict(payload_json))
        differences += int(decision is None or decision.action != str(expected or ""))
    return differences


def audit_storage(conn: sqlite3.Connection, *, since: str, until: str) -> dict[str, Any]:
    article_params = (since, until)
    checks = {
        "article_alias_missing": _scalar(
            conn,
            """
            SELECT COUNT(*) FROM article_reviews l
            LEFT JOIN market_item_aliases a
              ON a.item_kind='article' AND a.source=l.source AND a.legacy_item_id=l.item_id
            WHERE l.created_at>=? AND l.created_at<? AND a.market_item_id IS NULL
            """,
            article_params,
        ),
        "article_result_missing": _scalar(
            conn,
            """
            SELECT COUNT(*) FROM article_reviews l
            LEFT JOIN market_reviews r
              ON r.legacy_store_kind='article_reviews'
             AND r.legacy_store_id=l.source||':'||l.item_id
            WHERE l.created_at>=? AND l.created_at<? AND r.id IS NULL
            """,
            article_params,
        ),
        "official_alias_missing": _scalar(
            conn,
            """
            SELECT COUNT(*) FROM official_news_reviews l
            LEFT JOIN market_item_aliases a
              ON a.item_kind='official' AND a.source=l.source AND a.legacy_item_id=l.item_id
            WHERE l.created_at>=? AND l.created_at<? AND a.market_item_id IS NULL
            """,
            article_params,
        ),
        "official_result_missing": _scalar(
            conn,
            """
            SELECT COUNT(*) FROM official_news_reviews l
            LEFT JOIN market_reviews r
              ON r.legacy_store_kind='official_news_reviews'
             AND r.legacy_store_id=l.source||':'||l.item_id
            WHERE l.created_at>=? AND l.created_at<? AND r.id IS NULL
            """,
            article_params,
        ),
        "event_alias_missing": _scalar(
            conn,
            """
            SELECT COUNT(*) FROM events e
            LEFT JOIN market_item_aliases a
              ON a.item_kind='event' AND a.source=e.source
             AND a.legacy_item_id=CAST(e.id AS TEXT)
            WHERE e.first_seen_at>=? AND e.first_seen_at<? AND a.market_item_id IS NULL
            """,
            article_params,
        ),
        "event_result_missing": _scalar(
            conn,
            """
            SELECT COUNT(*) FROM event_analyses l
            LEFT JOIN market_reviews r
              ON r.legacy_store_kind='event_analyses'
             AND r.legacy_store_id=CAST(l.id AS TEXT)
            WHERE l.created_at>=? AND l.created_at<? AND r.id IS NULL
            """,
            article_params,
        ),
        "completed_result_without_compatibility": _scalar(
            conn,
            """
            SELECT COUNT(*) FROM market_reviews
            WHERE created_at>=? AND created_at<? AND admission_status='admitted'
              AND review_status='succeeded'
              AND (legacy_store_kind IS NULL OR legacy_store_id IS NULL)
            """,
            article_params,
        ),
        "action_without_decision": _scalar(
            conn,
            """
            SELECT COUNT(*) FROM market_reviews
            WHERE created_at>=? AND created_at<? AND COALESCE(decision_action,'')<>''
              AND COALESCE(decision_json,'') IN ('','{}')
            """,
            article_params,
        ),
        "invalid_action": _scalar(
            conn,
            """
            SELECT COUNT(*) FROM market_reviews
            WHERE created_at>=? AND created_at<? AND COALESCE(decision_action,'')<>''
              AND decision_action NOT IN ('push','daily','archive','ignore')
            """,
            article_params,
        ),
        "decision_json_action_mismatch": _scalar(
            conn,
            """
            SELECT COUNT(*) FROM market_reviews
            WHERE created_at>=? AND created_at<? AND COALESCE(decision_action,'')<>''
              AND decision_action<>COALESCE(json_extract(decision_json,'$.action'),'')
            """,
            article_params,
        ),
        "article_compatibility_action_mismatch": _action_differences(
            conn,
            store_kind="article_reviews",
            legacy_table="article_reviews",
            payload_column="gate_json",
            since=since,
            until=until,
        ),
        "official_compatibility_action_mismatch": _action_differences(
            conn,
            store_kind="official_news_reviews",
            legacy_table="official_news_reviews",
            payload_column="analysis_json",
            since=since,
            until=until,
        ),
        "event_compatibility_action_mismatch": _event_action_differences(
            conn, since=since, until=until
        ),
        "duplicate_current_result_groups": _scalar(
            conn,
            """
            SELECT COUNT(*) FROM (
              SELECT market_item_id,task FROM market_reviews
              WHERE is_current=1 GROUP BY market_item_id,task HAVING COUNT(*)>1
            )
            """,
        ),
        "delivery_missing_item": _scalar(
            conn,
            """
            SELECT COUNT(*) FROM deliveries
            WHERE COALESCE(NULLIF(attempted_at,''),sent_at,'')>=?
              AND COALESCE(NULLIF(attempted_at,''),sent_at,'')<?
              AND market_item_id IS NULL
            """,
            article_params,
        ),
        "delivery_missing_result": _scalar(
            conn,
            """
            SELECT COUNT(*) FROM deliveries
            WHERE COALESCE(NULLIF(attempted_at,''),sent_at,'')>=?
              AND COALESCE(NULLIF(attempted_at,''),sent_at,'')<?
              AND market_review_id IS NULL
            """,
            article_params,
        ),
        "delivery_compatibility_projection_error": _scalar(
            conn,
            """
            SELECT COUNT(*) FROM deliveries
            WHERE COALESCE(NULLIF(attempted_at,''),sent_at,'')>=?
              AND COALESCE(NULLIF(attempted_at,''),sent_at,'')<?
              AND error LIKE 'compatibility projection failed:%'
            """,
            article_params,
        ),
        "orphan_alias": _scalar(
            conn,
            """
            SELECT COUNT(*) FROM market_item_aliases a
            LEFT JOIN market_items m ON m.id=a.market_item_id WHERE m.id IS NULL
            """,
        ),
        "orphan_result": _scalar(
            conn,
            """
            SELECT COUNT(*) FROM market_reviews r
            LEFT JOIN market_items m ON m.id=r.market_item_id WHERE m.id IS NULL
            """,
        ),
        "foreign_key_violation": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
    }
    counts = {
        "article_compatibility_rows": _scalar(
            conn, "SELECT COUNT(*) FROM article_reviews WHERE created_at>=? AND created_at<?", article_params
        ),
        "official_compatibility_rows": _scalar(
            conn, "SELECT COUNT(*) FROM official_news_reviews WHERE created_at>=? AND created_at<?", article_params
        ),
        "event_compatibility_rows": _scalar(
            conn, "SELECT COUNT(*) FROM events WHERE first_seen_at>=? AND first_seen_at<?", article_params
        ),
        "event_analysis_compatibility_rows": _scalar(
            conn, "SELECT COUNT(*) FROM event_analyses WHERE created_at>=? AND created_at<?", article_params
        ),
        "unified_results": _scalar(
            conn, "SELECT COUNT(*) FROM market_reviews WHERE created_at>=? AND created_at<?", article_params
        ),
        "deliveries": _scalar(
            conn,
            """
            SELECT COUNT(*) FROM deliveries
            WHERE COALESCE(NULLIF(attempted_at,''),sent_at,'')>=?
              AND COALESCE(NULLIF(attempted_at,''),sent_at,'')<?
            """,
            article_params,
        ),
    }
    return {
        "migration_version": MARKET_RESULTS_MIGRATION_VERSION,
        "window": {"since": since, "until": until},
        "counts": counts,
        "checks": checks,
        "ok": all(value == 0 for value in checks.values()),
        "quick_check": str(conn.execute("PRAGMA quick_check").fetchone()[0]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--since", default="")
    parser.add_argument("--until", default="9999-12-31T23:59:59+00:00")
    parser.add_argument("--fail-on-difference", action="store_true")
    args = parser.parse_args()
    if not args.db.exists():
        parser.error(f"database does not exist: {args.db}")
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        marker = conn.execute(
            "SELECT updated_at FROM source_state WHERE source=?",
            (MARKET_RESULTS_MIGRATION_VERSION,),
        ).fetchone()
        if marker is None:
            parser.error("unified result migration marker is absent")
        since = str(args.since or marker[0])
        report = audit_storage(conn, since=since, until=str(args.until))
    finally:
        conn.close()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if args.fail_on_difference and (not report["ok"] or report["quick_check"] != "ok") else 0


if __name__ == "__main__":
    raise SystemExit(main())
