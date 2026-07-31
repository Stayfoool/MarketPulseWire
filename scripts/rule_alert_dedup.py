"""Atomic cross-source delivery deduplication for deterministic alert facts."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from market_db import DEFAULT_DB_PATH
from market_item import DecisionResult


RULE_IDS = {
    "ai_compute_supply_demand",
    "international_bank_fed_rate_path_revision",
    "international_bank_theme_strategy",
    "attributed_research_hard_variable",
}
LEGACY_ALIAS_LOOKBACK_DAYS = 30


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rule_hit(decision: DecisionResult) -> dict[str, Any] | None:
    for candidate in decision.rule_hits:
        if isinstance(candidate, dict) and candidate.get("rule_id") in RULE_IDS and candidate.get("dedup_key"):
            return candidate
    return None


def _lookback_minutes(hit: dict[str, Any]) -> int:
    value = hit.get("dedup_lookback_minutes")
    if value is None:
        value = max(1, min(int(hit.get("dedup_lookback_days") or 14), 90)) * 24 * 60
    return max(1, min(int(value), 90 * 24 * 60))


def _existing_reservation(
    conn: sqlite3.Connection,
    *,
    dedup_key: str,
    alias_keys: list[str],
    now: datetime,
    lookback_minutes: int,
) -> sqlite3.Row | tuple[Any, ...] | None:
    cutoff = (now - timedelta(minutes=lookback_minutes)).isoformat()
    row = conn.execute(
        """
        SELECT dedup_key, status, first_source, first_item_id, first_title,
               first_published_at, updated_at
        FROM rule_alert_dedup
        WHERE dedup_key = ? AND created_at >= ?
        LIMIT 1
        """,
        (dedup_key, cutoff),
    ).fetchone()
    if row or not alias_keys:
        return row
    alias_cutoff = (
        now - timedelta(minutes=min(lookback_minutes, LEGACY_ALIAS_LOOKBACK_DAYS * 24 * 60))
    ).isoformat()
    placeholders = ",".join("?" for _ in alias_keys)
    return conn.execute(
        f"""
        SELECT dedup_key, status, first_source, first_item_id, first_title,
               first_published_at, updated_at
        FROM rule_alert_dedup
        WHERE dedup_key IN ({placeholders}) AND created_at >= ?
        ORDER BY created_at
        LIMIT 1
        """,
        (*alias_keys, alias_cutoff),
    ).fetchone()


def reserve_rule_alert(
    decision: DecisionResult | None,
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
    hit = rule_hit(decision) if decision is not None else None
    hit = hit or delivery_hit
    if not hit:
        return {"reserved": False, "applicable": False}
    dedup_key = str(hit.get("dedup_key") or "")
    rule_id = str(hit.get("rule_id") or "")
    if not dedup_key or not rule_id:
        return {"reserved": False, "applicable": False}
    alias_keys = [
        str(value)
        for value in hit.get("dedup_alias_keys") or []
        if str(value).strip() and str(value) != dedup_key
    ][:16]
    lookback_minutes = _lookback_minutes(hit)
    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(db_path, timeout=60, isolation_level="IMMEDIATE")
    try:
        conn.execute("PRAGMA busy_timeout = 60000")
        conn.execute("BEGIN IMMEDIATE")
        row = _existing_reservation(
            conn,
            dedup_key=dedup_key,
            alias_keys=alias_keys,
            now=now,
            lookback_minutes=lookback_minutes,
        )
        if row:
            matched_key = str(row[0])
            if matched_key != dedup_key and row[1] == "sent":
                now_text = now.isoformat()
                conn.execute(
                    """
                    INSERT INTO rule_alert_dedup (
                        dedup_key, rule_id, status, first_source, first_item_id, first_title,
                        first_published_at, metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(dedup_key) DO NOTHING
                    """,
                    (
                        dedup_key,
                        rule_id,
                        row[1],
                        row[2],
                        row[3],
                        row[4],
                        row[5],
                        json.dumps({"migrated_from_alias_key": matched_key, "delivery_hit": hit}, ensure_ascii=False),
                        now_text,
                        now_text,
                    ),
                )
                conn.commit()
            else:
                conn.rollback()
            return {
                "reserved": False,
                "applicable": True,
                "duplicate": True,
                "dedup_key": dedup_key,
                "rule_id": rule_id,
                "matched_dedup_key": matched_key,
                "first": {
                    "status": row[1],
                    "source": row[2],
                    "item_id": row[3],
                    "title": row[4],
                    "published_at": row[5],
                    "updated_at": row[6],
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


def reserve_rule_alert_set(
    delivery_hits: list[dict[str, Any]],
    *,
    source: str,
    item_id: str,
    title: str,
    published_at: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Atomically reserve every new identity in one delivery fact set.

    A delivery is a duplicate only when every applicable identity is already
    covered. If at least one identity is new, all new identities are reserved
    in the same immediate transaction and confirmed or released together.
    """
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in delivery_hits:
        if not isinstance(candidate, dict):
            continue
        dedup_key = str(candidate.get("dedup_key") or "").strip()
        rule_id = str(candidate.get("rule_id") or "").strip()
        if not dedup_key or not rule_id or dedup_key in seen:
            continue
        seen.add(dedup_key)
        hits.append(candidate)
    if not hits:
        return {"reserved": False, "applicable": False, "reservations": [], "covered": []}

    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(db_path, timeout=60, isolation_level="IMMEDIATE")
    try:
        conn.execute("PRAGMA busy_timeout = 60000")
        conn.execute("BEGIN IMMEDIATE")
        covered: list[dict[str, Any]] = []
        new_hits: list[dict[str, Any]] = []
        for hit in hits:
            dedup_key = str(hit["dedup_key"])
            alias_keys = [
                str(value)
                for value in hit.get("dedup_alias_keys") or []
                if str(value).strip() and str(value) != dedup_key
            ][:16]
            lookback_minutes = _lookback_minutes(hit)
            row = _existing_reservation(
                conn,
                dedup_key=dedup_key,
                alias_keys=alias_keys,
                now=now,
                lookback_minutes=lookback_minutes,
            )
            if not row:
                new_hits.append(hit)
                continue
            matched_key = str(row[0])
            first = {
                "status": row[1],
                "source": row[2],
                "item_id": row[3],
                "title": row[4],
                "published_at": row[5],
                "updated_at": row[6],
            }
            covered.append(
                {
                    "dedup_key": dedup_key,
                    "matched_dedup_key": matched_key,
                    "rule_id": str(hit["rule_id"]),
                    "first": first,
                }
            )
            if matched_key != dedup_key and row[1] == "sent":
                now_text = now.isoformat()
                conn.execute(
                    """
                    INSERT INTO rule_alert_dedup (
                        dedup_key, rule_id, status, first_source, first_item_id, first_title,
                        first_published_at, metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(dedup_key) DO NOTHING
                    """,
                    (
                        dedup_key,
                        str(hit["rule_id"]),
                        row[1],
                        row[2],
                        row[3],
                        row[4],
                        row[5],
                        json.dumps({"migrated_from_alias_key": matched_key, "delivery_hit": hit}, ensure_ascii=False),
                        now_text,
                        now_text,
                    ),
                )

        reservations: list[dict[str, Any]] = []
        now_text = now.isoformat()
        for hit in new_hits:
            dedup_key = str(hit["dedup_key"])
            rule_id = str(hit["rule_id"])
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
                    json.dumps({"delivery_hit": hit, "fact_set_size": len(hits)}, ensure_ascii=False),
                    now_text,
                    now_text,
                ),
            )
            reservations.append({"reserved": True, "dedup_key": dedup_key, "rule_id": rule_id})
        conn.commit()
        duplicate = not reservations
        first_covered = covered[0] if covered else {}
        return {
            "reserved": bool(reservations),
            "applicable": True,
            "duplicate": duplicate,
            "dedup_key": str(first_covered.get("dedup_key") or hits[0]["dedup_key"]),
            "dedup_keys": [str(hit["dedup_key"]) for hit in hits],
            "rule_id": str(hits[0]["rule_id"]),
            "first": first_covered.get("first") or {},
            "matched_dedup_key": first_covered.get("matched_dedup_key") or "",
            "reservations": reservations,
            "covered": covered,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _reserved_keys(reservation: dict[str, Any]) -> list[str]:
    nested = reservation.get("reservations")
    if isinstance(nested, list):
        keys = [
            str(item.get("dedup_key") or "")
            for item in nested
            if isinstance(item, dict) and item.get("reserved") and item.get("dedup_key")
        ]
        if keys:
            return list(dict.fromkeys(keys))
    if reservation.get("reserved") and reservation.get("dedup_key"):
        return [str(reservation["dedup_key"])]
    return []


def confirm_rule_alert(reservation: dict[str, Any], *, db_path: Path = DEFAULT_DB_PATH) -> None:
    keys = _reserved_keys(reservation)
    if not keys:
        return
    with sqlite3.connect(db_path, timeout=60) as conn:
        placeholders = ",".join("?" for _ in keys)
        conn.execute(
            f"UPDATE rule_alert_dedup SET status = 'sent', updated_at = ? WHERE dedup_key IN ({placeholders})",
            (utc_now(), *keys),
        )
        conn.commit()


def release_rule_alert(reservation: dict[str, Any], *, db_path: Path = DEFAULT_DB_PATH) -> None:
    keys = _reserved_keys(reservation)
    if not keys:
        return
    with sqlite3.connect(db_path, timeout=60) as conn:
        placeholders = ",".join("?" for _ in keys)
        conn.execute(
            f"DELETE FROM rule_alert_dedup WHERE dedup_key IN ({placeholders}) AND status = 'reserved'",
            keys,
        )
        conn.commit()
