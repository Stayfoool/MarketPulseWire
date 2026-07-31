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
from market_db import DEFAULT_DB_PATH
from market_item import AdmissionResult, MarketFlowResult, NormalizedMarketItem


ROOT = Path(__file__).resolve().parents[1]
INSUFFICIENT_EVIDENCE_STATUS = "insufficient_evidence"


class InsufficientEvidenceError(RuntimeError):
    """Signals a terminal review that has no DecisionResult because evidence is insufficient."""

    review_status = INSUFFICIENT_EVIDENCE_STATUS
    processing_status = INSUFFICIENT_EVIDENCE_STATUS


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_dict(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


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
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ELSE market_items.collection_class
            END,
            processability_status = excluded.processability_status,
            processability_reason = excluded.processability_reason,
            processing_status = excluded.processing_status,
            processing_error = excluded.processing_error,
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
            created_at, completed_at
        ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            now,
            now if admission.status != "admitted" else None,
        ),
    )
    return int(cur.lastrowid)


def ensure_market_item_alias(
    conn: sqlite3.Connection,
    market_item_id: int,
    *,
    item_kind: str,
    source: str,
    legacy_item_id: str,
    legacy_store_kind: str,
) -> None:
    conn.execute(
        """
        INSERT INTO market_item_aliases (
            market_item_id, item_kind, source, legacy_item_id,
            legacy_store_kind, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_kind, source, legacy_item_id) DO UPDATE SET
            market_item_id = excluded.market_item_id,
            legacy_store_kind = excluded.legacy_store_kind
        """,
        (market_item_id, item_kind, source, legacy_item_id, legacy_store_kind, utc_now()),
    )


def record_production_admission(
    item: NormalizedMarketItem,
    admission: AdmissionResult,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    collection_class: str = "live",
    task: str = "production",
    force_new: bool = False,
) -> tuple[int, int]:
    with connect_sqlite(db_path) as conn:
        item_source_id = source_item_id(item)
        existing = conn.execute(
            """
            SELECT m.id,r.id,r.review_status,m.processing_status,r.admission_json
            FROM market_items m
            JOIN market_reviews r ON r.market_item_id=m.id AND r.task=? AND r.is_current=1
            WHERE m.source=? AND m.source_item_id=?
            LIMIT 1
            """,
            (task, item.source, item_source_id),
        ).fetchone()
        existing_status = str(existing[2]) if existing else ""
        same_admission = bool(existing and json_dict(existing[4]) == admission.to_dict())
        if existing and (
            existing_status == INSUFFICIENT_EVIDENCE_STATUS
            or (
                not force_new
                and (
                    existing_status == "succeeded"
                    or (
                        existing_status
                        in {
                            "admitted_pending",
                            "excluded",
                            "not_applicable",
                            "failed_retryable",
                            "failed_terminal",
                        }
                        and same_admission
                    )
                )
            )
        ):
            upsert_market_item(
                conn,
                item,
                collection_class=collection_class,
                processing_status=str(existing[3] or "pending"),
            )
            conn.commit()
            return int(existing[0]), int(existing[1])
        item_id = upsert_market_item(
            conn,
            item,
            collection_class=collection_class,
            processing_status="pending" if admission.status == "admitted" else "not_applicable",
        )
        review_id = begin_market_review(conn, item_id, admission, task=task)
        conn.commit()
    return item_id, review_id


def market_review_snapshot(
    market_review_id: int,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    with connect_sqlite(db_path) as conn:
        row = conn.execute(
            """
            SELECT r.market_item_id,r.review_status,r.admission_status,r.decision_action,
                   r.importance,r.decision_json,r.interpretation_json,
                   EXISTS(SELECT 1 FROM deliveries d
                          WHERE d.market_item_id=r.market_item_id AND d.status='sent')
            FROM market_reviews r WHERE r.id=?
            """,
            (market_review_id,),
        ).fetchone()
    if not row:
        return None
    payload: dict[str, Any] = {}
    decision = json_dict(row[5])
    interpretation = json_dict(row[6])
    if decision:
        payload["decision_result"] = decision
    if interpretation:
        payload["interpretation_result"] = interpretation
    return {
        "market_item_id": int(row[0]),
        "market_review_id": market_review_id,
        "review_status": str(row[1] or ""),
        "admission_status": str(row[2] or ""),
        "decision_action": str(row[3] or ""),
        "importance": str(row[4] or ""),
        "payload": payload,
        "delivered": bool(row[7]),
    }


def source_item_review_snapshot(
    source: str,
    item_id: str,
    *,
    task: str = "production",
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    """Return the current unified review for one collector identity."""
    with connect_sqlite(db_path) as conn:
        row = conn.execute(
            """
            SELECT r.id
            FROM market_items m
            JOIN market_reviews r
              ON r.market_item_id=m.id AND r.task=? AND r.is_current=1
            WHERE m.source=? AND m.source_item_id=?
            LIMIT 1
            """,
            (task, source, item_id),
        ).fetchone()
    return market_review_snapshot(int(row[0]), db_path=db_path) if row else None


def record_baseline_item(
    item: NormalizedMarketItem,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    with connect_sqlite(db_path) as conn:
        item_id = upsert_market_item(
            conn,
            item,
            collection_class="baseline",
            processing_status="not_applicable",
        )
        conn.commit()
    return item_id


def _complete_market_review_in_conn(
    conn: sqlite3.Connection,
    market_review_id: int,
    flow_result: MarketFlowResult,
) -> int:
    now = utc_now()
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
            decision_json = ?, interpretation_json = ?, completed_at = ?
        WHERE id = ?
        """,
        (
            flow_result.decision.action,
            flow_result.decision.importance,
            json_dumps(flow_result.decision.to_dict()),
            json_dumps(flow_result.interpretation.to_dict()),
            now,
            market_review_id,
        ),
    )
    conn.execute(
        "UPDATE market_items SET processing_status = 'succeeded', processing_error = '', updated_at = ? WHERE id = ?",
        (now, int(row[0])),
    )
    return int(row[0])


def complete_market_review(
    market_review_id: int,
    flow_result: MarketFlowResult,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    alias: tuple[str, str, str, str] | None = None,
) -> None:
    with connect_sqlite(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            market_item_id = _complete_market_review_in_conn(
                conn,
                market_review_id,
                flow_result,
            )
            if alias:
                ensure_market_item_alias(
                    conn,
                    market_item_id,
                    item_kind=alias[0],
                    source=alias[1],
                    legacy_item_id=alias[2],
                    legacy_store_kind=alias[3],
                )
        except BaseException:
            conn.rollback()
            raise
        conn.commit()


def fail_market_review(
    market_review_id: int,
    error: BaseException,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    now = utc_now()
    message = f"{type(error).__name__}: {str(error)[:400]}"
    status = processing_failure_status(error)
    with connect_sqlite(db_path) as conn:
        row = conn.execute("SELECT market_item_id FROM market_reviews WHERE id = ?", (market_review_id,)).fetchone()
        if not row:
            return
        conn.execute(
            "UPDATE market_reviews SET review_status = ?, completed_at = ? WHERE id = ?",
            (status, now, market_review_id),
        )
        conn.execute(
            "UPDATE market_items SET processing_status = ?, processing_error = ?, updated_at = ? WHERE id = ?",
            (status, message, now, int(row[0])),
        )
        conn.commit()


def processing_failure_status(error: BaseException) -> str:
    """Map a processing exception to the shared retryable or terminal lifecycle status."""
    if (
        getattr(error, "review_status", "") == INSUFFICIENT_EVIDENCE_STATUS
        and getattr(error, "processing_status", "") == INSUFFICIENT_EVIDENCE_STATUS
    ):
        return INSUFFICIENT_EVIDENCE_STATUS
    return "failed_retryable"


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
                market_item_id, market_review_id, channel, status, decision_action,
                attempted_at, sent_at, error, payload_json
            ) VALUES (?, ?, 'feishu', ?, ?, ?, ?, ?, ?)
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
        delivery_id = int(cur.lastrowid)
    return delivery_id


def record_event_delivery(
    channel: str,
    status: str,
    payload: dict[str, Any],
    *,
    error: str = "",
    market_item_id: int | None = None,
    market_review_id: int | None = None,
    decision_action: str = "",
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Persist an event-shaped item's delivery using only unified identities."""
    now = utc_now()
    with connect_sqlite(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO deliveries (
                market_item_id, market_review_id, channel, status, decision_action,
                attempted_at, sent_at, error, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                market_item_id,
                market_review_id,
                channel,
                status,
                decision_action or None,
                now,
                now if status == "sent" else "",
                error,
                json_dumps(payload),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def market_ids_for_review(market_review_id: int, *, db_path: Path = DEFAULT_DB_PATH) -> tuple[int, int]:
    with connect_sqlite(db_path) as conn:
        row = conn.execute(
            "SELECT market_item_id, id FROM market_reviews WHERE id = ?",
            (market_review_id,),
        ).fetchone()
    if not row:
        raise RuntimeError(f"market review does not exist: {market_review_id}")
    return int(row[0]), int(row[1])
