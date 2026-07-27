#!/usr/bin/env python3
"""Preview or apply terminal status for current reviews with valid uncertain audits."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from db_utils import connect_sqlite
from llm_decision_web import DEFAULT_AUDIT_DIR, load_web_projections
from market_db import DEFAULT_DB_PATH
from market_store import INSUFFICIENT_EVIDENCE_STATUS


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def current_uncertain_candidates(
    conn: sqlite3.Connection,
    *,
    audit_dir: Path,
) -> list[tuple[int, int, str, str]]:
    projections = load_web_projections(audit_dir)
    rows = conn.execute(
        """
        SELECT r.id, r.market_item_id, m.source, m.source_item_id
        FROM market_reviews r
        JOIN market_items m ON m.id=r.market_item_id
        WHERE r.is_current=1
          AND r.review_status='failed_retryable'
          AND COALESCE(r.decision_action,'')=''
          AND COALESCE(r.decision_json,'')=''
        ORDER BY r.id
        """
    ).fetchall()
    result: list[tuple[int, int, str, str]] = []
    for row in rows:
        review_id = int(row[0])
        attempts = projections.get(review_id, [])
        if attempts and str(attempts[-1].get("evaluation_status") or "") == "uncertain":
            result.append((review_id, int(row[1]), str(row[2] or ""), str(row[3] or "")))
    return result


def apply_candidates(
    conn: sqlite3.Connection,
    candidates: list[tuple[int, int, str, str]],
) -> dict[str, int]:
    now = datetime.now(timezone.utc).isoformat()
    message = "ProductionLLMInsufficientEvidence: valid uncertain result has insufficient evidence"
    updated_reviews = 0
    updated_items = 0
    updated_seen_items = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for review_id, market_item_id, source, source_item_id in candidates:
            cursor = conn.execute(
                """
                UPDATE market_reviews
                SET review_status=?, completed_at=COALESCE(completed_at, ?)
                WHERE id=? AND is_current=1 AND review_status='failed_retryable'
                  AND COALESCE(decision_action,'')='' AND COALESCE(decision_json,'')=''
                """,
                (INSUFFICIENT_EVIDENCE_STATUS, now, review_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"review {review_id} changed after preview")
            updated_reviews += 1
            cursor = conn.execute(
                """
                UPDATE market_items
                SET processing_status=?, processing_error=?, updated_at=?
                WHERE id=?
                """,
                (INSUFFICIENT_EVIDENCE_STATUS, message, now, market_item_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"market item {market_item_id} is missing")
            updated_items += 1
            if table_exists(conn, "seen_items"):
                cursor = conn.execute(
                    """
                    UPDATE seen_items
                    SET processing_status=?, processing_error=?, processed_at=COALESCE(processed_at, ?),
                        lifecycle_updated_at=?
                    WHERE source=? AND item_id=?
                      AND processing_status IN ('pending','failed_retryable')
                    """,
                    (
                        INSUFFICIENT_EVIDENCE_STATUS,
                        message,
                        now,
                        now,
                        source,
                        source_item_id,
                    ),
                )
                updated_seen_items += max(0, cursor.rowcount)
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return {
        "updated_reviews": updated_reviews,
        "updated_market_items": updated_items,
        "updated_seen_items": updated_seen_items,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--audit-dir", type=Path, default=DEFAULT_AUDIT_DIR)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    connector = (
        connect_sqlite(args.db)
        if args.apply
        else sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    )
    with connector as conn:
        candidates = current_uncertain_candidates(conn, audit_dir=args.audit_dir)
        result = {
            "mode": "apply" if args.apply else "preview",
            "candidate_count": len(candidates),
            "review_id_min": min((item[0] for item in candidates), default=None),
            "review_id_max": max((item[0] for item in candidates), default=None),
        }
        if args.apply:
            result.update(apply_candidates(conn, candidates))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
