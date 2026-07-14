"""Atomic cross-source delivery deduplication for deterministic alert facts."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from market_db import DEFAULT_DB_PATH, init_db
from market_item import decision_result_from_payload


RULE_IDS = {
    "international_bank_fed_rate_path_revision",
    "international_bank_theme_strategy",
    "attributed_research_hard_variable",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rule_hit(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
    structured_decision = decision_result_from_payload(payload)
    decision = payload.get("_decision_result")
    if not isinstance(decision, dict):
        decision = payload.get("decision_result") if isinstance(payload.get("decision_result"), dict) else {}
    candidates = (
        list(structured_decision.rule_hits if structured_decision else [])
        + list(decision.get("rule_hits") or [])
        + list(raw.get("rule_hits") or [])
        + list(payload.get("rule_hits") or [])
    )
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("rule_id") in RULE_IDS and candidate.get("dedup_key"):
            return candidate
    return None


def reserve_rule_alert(
    review_or_analysis: dict[str, Any],
    *,
    source: str,
    item_id: str,
    title: str,
    published_at: str,
    delivery_hit: dict[str, Any] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Reserve a single delivery fact before Feishu delivery.

    Explicit rule identities take precedence. ``delivery_hit`` permits another
    deterministic execution-only identity after a DecisionResult already made
    the item eligible to push. A reservation makes concurrent collectors
    deterministic; expired records are reused after the configured window.
    """
    hit = rule_hit(review_or_analysis) or delivery_hit
    if not hit:
        return {"reserved": False, "applicable": False}
    dedup_key = str(hit.get("dedup_key") or "")
    rule_id = str(hit.get("rule_id") or "")
    if not dedup_key or not rule_id:
        return {"reserved": False, "applicable": False}
    lookback_minutes = hit.get("dedup_lookback_minutes")
    if lookback_minutes is None:
        lookback_minutes = max(1, min(int(hit.get("dedup_lookback_days") or 14), 90)) * 24 * 60
    lookback_minutes = max(1, min(int(lookback_minutes), 90 * 24 * 60))
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(minutes=lookback_minutes)).isoformat()
    init_db(db_path).close()
    conn = sqlite3.connect(db_path, timeout=60, isolation_level="IMMEDIATE")
    try:
        conn.execute("PRAGMA busy_timeout = 60000")
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT status, first_source, first_item_id, first_title, first_published_at, updated_at
            FROM rule_alert_dedup
            WHERE dedup_key = ? AND created_at >= ?
            """,
            (dedup_key, cutoff),
        ).fetchone()
        if row:
            conn.rollback()
            return {
                "reserved": False,
                "applicable": True,
                "duplicate": True,
                "dedup_key": dedup_key,
                "rule_id": rule_id,
                "first": {
                    "status": row[0],
                    "source": row[1],
                    "item_id": row[2],
                    "title": row[3],
                    "published_at": row[4],
                    "updated_at": row[5],
                },
            }
        now_text = now.isoformat()
        conn.execute(
            """
            INSERT INTO rule_alert_dedup (
                dedup_key, rule_id, status, first_source, first_item_id, first_title,
                first_published_at, metadata_json, created_at, updated_at
            ) VALUES (?, ?, 'reserved', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dedup_key) DO UPDATE SET
                rule_id = excluded.rule_id,
                status = 'reserved',
                first_source = excluded.first_source,
                first_item_id = excluded.first_item_id,
                first_title = excluded.first_title,
                first_published_at = excluded.first_published_at,
                metadata_json = excluded.metadata_json,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
            """,
            (
                dedup_key,
                rule_id,
                source,
                item_id,
                title,
                published_at,
                json.dumps({"delivery_hit": hit}, ensure_ascii=False),
                now_text,
                now_text,
            ),
        )
        conn.commit()
        return {"reserved": True, "applicable": True, "dedup_key": dedup_key, "rule_id": rule_id}
    finally:
        conn.close()


def confirm_rule_alert(reservation: dict[str, Any], *, db_path: Path = DEFAULT_DB_PATH) -> None:
    if not reservation.get("reserved") or not reservation.get("dedup_key"):
        return
    init_db(db_path).close()
    with sqlite3.connect(db_path, timeout=60) as conn:
        conn.execute(
            "UPDATE rule_alert_dedup SET status = 'sent', updated_at = ? WHERE dedup_key = ?",
            (utc_now(), str(reservation["dedup_key"])),
        )
        conn.commit()


def release_rule_alert(reservation: dict[str, Any], *, db_path: Path = DEFAULT_DB_PATH) -> None:
    if not reservation.get("reserved") or not reservation.get("dedup_key"):
        return
    init_db(db_path).close()
    with sqlite3.connect(db_path, timeout=60) as conn:
        conn.execute(
            "DELETE FROM rule_alert_dedup WHERE dedup_key = ? AND status = 'reserved'",
            (str(reservation["dedup_key"]),),
        )
        conn.commit()
