"""Canonical SQLite storage for normalized items, reviews and delivery audits."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db_utils import connect_sqlite
from market_db import DEFAULT_DB_PATH, init_db
from market_item import AdmissionResult, MarketFlowResult, NormalizedMarketItem


ROOT = Path(__file__).resolve().parents[1]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def application_revision() -> str:
    explicit = os.getenv("SURVEIL_REVISION", "").strip()
    if explicit:
        return explicit
    try:
        for line in (ROOT / "REVISION").read_text(encoding="utf-8").splitlines():
            if line.startswith("commit="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def source_item_id(item: NormalizedMarketItem) -> str:
    value = str(item.raw.get("source_event_id") or item.raw.get("id") or "").strip()
    if value:
        return value
    if item.url:
        return item.url
    return item.dedupe_key.split(":", 1)[-1] if ":" in item.dedupe_key else item.dedupe_key


def _content_hash(item: NormalizedMarketItem) -> str:
    value = "\n".join(
        (item.source, source_item_id(item), item.title, item.summary, item.full_text, item.url)
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def upsert_market_item(
    conn: sqlite3.Connection,
    item: NormalizedMarketItem,
    *,
    collection_class: str = "live",
    processability_status: str = "succeeded",
    processability_reason: str = "",
    processing_status: str = "pending",
    processing_error: str = "",
    legacy_store_kind: str | None = None,
    legacy_store_id: str | None = None,
) -> int:
    now = utc_now()
    item_id = source_item_id(item)
    if not item.source or not item_id:
        raise ValueError("market item requires source and source_item_id")
    first_seen_at = item.first_seen_at or now
    conn.execute(
        """
        INSERT INTO market_items (
            source, source_item_id, dedupe_key, source_category, publisher_role,
            collector, content_type, title, summary, full_text, url, published_at,
            first_seen_at, symbols_json, themes_json, raw_json, access_note,
            content_hash, collection_class, processability_status,
            processability_reason, processing_status, processing_error,
            legacy_store_kind, legacy_store_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, source_item_id) DO UPDATE SET
            dedupe_key = excluded.dedupe_key,
            source_category = CASE WHEN excluded.source_category <> '' THEN excluded.source_category ELSE market_items.source_category END,
            publisher_role = CASE WHEN excluded.publisher_role <> '' THEN excluded.publisher_role ELSE market_items.publisher_role END,
            collector = CASE WHEN excluded.collector <> '' THEN excluded.collector ELSE market_items.collector END,
            content_type = CASE WHEN excluded.content_type <> 'unknown' THEN excluded.content_type ELSE market_items.content_type END,
            title = CASE WHEN excluded.title <> '' THEN excluded.title ELSE market_items.title END,
            summary = CASE WHEN length(COALESCE(excluded.summary, '')) >= length(COALESCE(market_items.summary, '')) THEN excluded.summary ELSE market_items.summary END,
            full_text = CASE WHEN length(COALESCE(excluded.full_text, '')) >= length(COALESCE(market_items.full_text, '')) THEN excluded.full_text ELSE market_items.full_text END,
            url = CASE WHEN COALESCE(excluded.url, '') <> '' THEN excluded.url ELSE market_items.url END,
            published_at = CASE WHEN COALESCE(excluded.published_at, '') <> '' THEN excluded.published_at ELSE market_items.published_at END,
            symbols_json = CASE WHEN excluded.symbols_json <> '[]' THEN excluded.symbols_json ELSE market_items.symbols_json END,
            themes_json = CASE WHEN excluded.themes_json <> '[]' THEN excluded.themes_json ELSE market_items.themes_json END,
            raw_json = CASE WHEN excluded.raw_json <> '{}' THEN excluded.raw_json ELSE market_items.raw_json END,
            access_note = CASE WHEN COALESCE(excluded.access_note, '') <> '' THEN excluded.access_note ELSE market_items.access_note END,
            content_hash = excluded.content_hash,
            collection_class = CASE
                WHEN market_items.collection_class = 'baseline' AND excluded.collection_class = 'live' THEN 'live'
                WHEN market_items.collection_class = 'legacy_unclassified' THEN excluded.collection_class
                ELSE market_items.collection_class
            END,
            processability_status = excluded.processability_status,
            processability_reason = excluded.processability_reason,
            processing_status = excluded.processing_status,
            processing_error = excluded.processing_error,
            legacy_store_kind = COALESCE(market_items.legacy_store_kind, excluded.legacy_store_kind),
            legacy_store_id = COALESCE(market_items.legacy_store_id, excluded.legacy_store_id),
            updated_at = excluded.updated_at
        """,
        (
            item.source,
            item_id,
            item.dedupe_key,
            item.source_category,
            item.publisher_role,
            item.collector,
            item.content_type,
            item.title,
            item.summary,
            item.full_text,
            item.url,
            item.published_at,
            first_seen_at,
            json_dumps(item.symbols),
            json_dumps(item.themes),
            json_dumps(item.raw),
            item.access_note,
            _content_hash(item),
            collection_class,
            processability_status,
            processability_reason,
            processing_status,
            processing_error,
            legacy_store_kind,
            legacy_store_id,
            now,
            now,
        ),
    )
    row = conn.execute(
        "SELECT id FROM market_items WHERE source = ? AND source_item_id = ?",
        (item.source, item_id),
    ).fetchone()
    if not row:
        raise RuntimeError("market item upsert did not return an identity")
    return int(row[0])


def begin_market_review(
    conn: sqlite3.Connection,
    market_item_id: int,
    admission: AdmissionResult,
    *,
    task: str = "production",
    legacy_store_kind: str | None = None,
    legacy_store_id: str | None = None,
) -> int:
    now = utc_now()
    conn.execute(
        "UPDATE market_reviews SET is_current = 0 WHERE market_item_id = ? AND task = ? AND is_current = 1",
        (market_item_id, task),
    )
    review_status = "admitted_pending" if admission.status == "admitted" else admission.status
    cur = conn.execute(
        """
        INSERT INTO market_reviews (
            market_item_id, task, run_key, is_current, review_status,
            admission_status, admission_reason, admission_matched_families_json,
            admission_evidence_json, admission_config_version,
            admission_rule_contract_version, admission_json, application_revision,
            legacy_store_kind, legacy_store_id, created_at, completed_at
        ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            market_item_id,
            task,
            uuid.uuid4().hex,
            review_status,
            admission.status,
            admission.reason_code,
            json_dumps(list(admission.matched_families)),
            json_dumps([evidence.to_dict() for evidence in admission.evidence]),
            admission.config_version,
            admission.rule_contract_version,
            json_dumps(admission.to_dict()),
            application_revision(),
            legacy_store_kind,
            legacy_store_id,
            now,
            now if admission.status != "admitted" else None,
        ),
    )
    return int(cur.lastrowid)


def record_production_admission(
    item: NormalizedMarketItem,
    admission: AdmissionResult,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    collection_class: str = "live",
    task: str = "production",
) -> tuple[int, int]:
    init_db(db_path).close()
    with connect_sqlite(db_path) as conn:
        item_id = upsert_market_item(
            conn,
            item,
            collection_class=collection_class,
            processing_status="pending" if admission.status == "admitted" else "not_applicable",
        )
        review_id = begin_market_review(conn, item_id, admission, task=task)
        conn.commit()
    return item_id, review_id


def record_baseline_item(
    item: NormalizedMarketItem,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    legacy_store_kind: str | None = None,
    legacy_store_id: str | None = None,
) -> int:
    init_db(db_path).close()
    with connect_sqlite(db_path) as conn:
        item_id = upsert_market_item(
            conn,
            item,
            collection_class="baseline",
            processing_status="not_applicable",
            legacy_store_kind=legacy_store_kind,
            legacy_store_id=legacy_store_id,
        )
        conn.commit()
    return item_id


def complete_market_review(
    market_review_id: int,
    flow_result: MarketFlowResult,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    legacy_store_kind: str | None = None,
    legacy_store_id: str | None = None,
) -> None:
    now = utc_now()
    with connect_sqlite(db_path) as conn:
        row = conn.execute(
            "SELECT market_item_id, admission_status FROM market_reviews WHERE id = ?",
            (market_review_id,),
        ).fetchone()
        if not row:
            raise RuntimeError(f"market review does not exist: {market_review_id}")
        if str(row[1]) != "admitted":
            raise ValueError("only an admitted market review can contain DecisionResult")
        conn.execute(
            """
            UPDATE market_reviews
            SET review_status = 'succeeded', decision_action = ?, importance = ?,
                decision_json = ?, interpretation_json = ?, completed_at = ?,
                legacy_store_kind = COALESCE(?, legacy_store_kind),
                legacy_store_id = COALESCE(?, legacy_store_id)
            WHERE id = ?
            """,
            (
                flow_result.decision.action,
                flow_result.decision.importance,
                json_dumps(flow_result.decision.to_dict()),
                json_dumps(flow_result.interpretation.to_dict()),
                now,
                legacy_store_kind,
                legacy_store_id,
                market_review_id,
            ),
        )
        conn.execute(
            "UPDATE market_items SET processing_status = 'succeeded', processing_error = '', updated_at = ? WHERE id = ?",
            (now, int(row[0])),
        )
        conn.commit()


def fail_market_review(
    market_review_id: int,
    error: BaseException,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    now = utc_now()
    message = f"{type(error).__name__}: {str(error)[:400]}"
    with connect_sqlite(db_path) as conn:
        row = conn.execute("SELECT market_item_id FROM market_reviews WHERE id = ?", (market_review_id,)).fetchone()
        if not row:
            return
        conn.execute(
            "UPDATE market_reviews SET review_status = 'failed_retryable', completed_at = ? WHERE id = ?",
            (now, market_review_id),
        )
        conn.execute(
            "UPDATE market_items SET processing_status = 'failed_retryable', processing_error = ?, updated_at = ? WHERE id = ?",
            (message, now, int(row[0])),
        )
        conn.commit()


def record_article_delivery(
    market_item_id: int,
    market_review_id: int,
    *,
    status: str,
    decision_action: str,
    payload: dict[str, Any] | None = None,
    error: str = "",
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    now = utc_now()
    with connect_sqlite(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO deliveries (
                event_id, market_item_id, market_review_id, channel, status,
                decision_action, attempted_at, sent_at, error, payload_json
            ) VALUES (NULL, ?, ?, 'feishu', ?, ?, ?, ?, ?, ?)
            """,
            (
                market_item_id,
                market_review_id,
                status,
                decision_action,
                now,
                now if status == "sent" else "",
                error,
                json_dumps(payload or {}),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def link_latest_event_delivery(
    event_id: int,
    market_item_id: int,
    market_review_id: int,
    *,
    decision_action: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    with connect_sqlite(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM deliveries WHERE event_id = ? ORDER BY id DESC LIMIT 1",
            (event_id,),
        ).fetchone()
        if not row:
            return
        conn.execute(
            """
            UPDATE deliveries
            SET market_item_id = ?, market_review_id = ?, decision_action = ?,
                attempted_at = COALESCE(NULLIF(attempted_at, ''), sent_at, ?)
            WHERE id = ?
            """,
            (market_item_id, market_review_id, decision_action, utc_now(), int(row[0])),
        )
        conn.commit()


def market_ids_for_review(market_review_id: int, *, db_path: Path = DEFAULT_DB_PATH) -> tuple[int, int]:
    with connect_sqlite(db_path) as conn:
        row = conn.execute(
            "SELECT market_item_id, id FROM market_reviews WHERE id = ?",
            (market_review_id,),
        ).fetchone()
    if not row:
        raise RuntimeError(f"market review does not exist: {market_review_id}")
    return int(row[0]), int(row[1])
