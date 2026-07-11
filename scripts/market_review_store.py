"""Compatibility review storage adapters for current production tables."""

from __future__ import annotations

import json
import sqlite3
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db_utils import connect_sqlite
from decision_engine import ensure_article_decision_audit, ensure_official_decision_audit
from market_db import DEFAULT_DB_PATH, init_db
from market_item import NormalizedMarketItem


def json_loads_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def event_content_hash(*parts: str) -> str:
    joined = "\n".join(part.strip() for part in parts if part and part.strip())
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def load_enabled_holdings(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    init_db(db_path).close()
    with connect_sqlite(db_path) as conn:
        rows = conn.execute(
            """
            SELECT symbol, name, full_name, aliases_json, raw_json
            FROM portfolio_holdings
            WHERE enabled = 1
            ORDER BY symbol
            """
        ).fetchall()
    holdings = []
    for symbol, name, full_name, aliases_json, raw_json in rows:
        try:
            aliases = json.loads(aliases_json or "[]")
        except json.JSONDecodeError:
            aliases = []
        try:
            raw = json.loads(raw_json or "{}")
        except json.JSONDecodeError:
            raw = {}
        holdings.append(
            {
                "symbol": symbol,
                "name": name,
                "full_name": full_name or "",
                "aliases": aliases,
                "news_keywords": raw.get("news_keywords") if isinstance(raw.get("news_keywords"), list) else [],
                "news_exclude_keywords": raw.get("news_exclude_keywords")
                if isinstance(raw.get("news_exclude_keywords"), list)
                else [],
                "business_summary": str(raw.get("business_summary") or ""),
                "raw": raw,
            }
        )
    return holdings


def upsert_event_record(event: dict[str, Any], db_path: Path = DEFAULT_DB_PATH) -> tuple[int, bool]:
    init_db(db_path).close()
    now = utc_now()
    source = str(event["source"])
    source_event_id = str(event["source_event_id"])
    title = str(event.get("title") or "").strip()
    summary = str(event.get("summary") or "").strip()
    full_text = str(event.get("full_text") or "").strip()
    digest = event.get("content_hash") or event_content_hash(source, source_event_id, title, summary, full_text)
    payload = (
        source,
        source_event_id,
        str(event.get("event_type") or "unknown"),
        title,
        summary,
        full_text,
        str(event.get("url") or ""),
        str(event.get("published_at") or ""),
        now,
        json_dumps(event.get("symbols") or []),
        json_dumps(event.get("themes") or []),
        json_dumps(event.get("raw") or {}),
        digest,
        1 if event.get("baseline_only") else 0,
    )
    with connect_sqlite(db_path) as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO events (
                    source, source_event_id, event_type, title, summary, full_text, url,
                    published_at, first_seen_at, symbols_json, themes_json, raw_json,
                    content_hash, baseline_only
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
            conn.commit()
            return int(cur.lastrowid), True
        except sqlite3.IntegrityError:
            row = conn.execute(
                "SELECT id, full_text FROM events WHERE source = ? AND source_event_id = ?",
                (source, source_event_id),
            ).fetchone()
            if not row:
                raise
            event_id = int(row[0])
            existing_full_text = str(row[1] or "")
            if full_text and len(full_text) > len(existing_full_text):
                conn.execute(
                    """
                    UPDATE events
                    SET summary = ?, full_text = ?, raw_json = ?, content_hash = ?
                    WHERE id = ?
                    """,
                    (summary, full_text, json_dumps(event.get("raw") or {}), digest, event_id),
                )
                conn.commit()
            return event_id, False


def event_row_by_id(event_id: int, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any] | None:
    with connect_sqlite(db_path) as conn:
        row = conn.execute(
            """
            SELECT source, event_type, title, summary, full_text, url, published_at, symbols_json, raw_json
            FROM events
            WHERE id = ?
            """,
            (event_id,),
        ).fetchone()
    if not row:
        return None
    source, event_type, title, summary, full_text, url, published_at, symbols_json, raw_json = row
    return {
        "source": source,
        "event_type": event_type,
        "title": title,
        "summary": summary,
        "full_text": full_text,
        "url": url,
        "published_at": published_at,
        "symbols_json": symbols_json,
        "raw_json": raw_json,
    }


def latest_event_analysis(event_id: int, task: str, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any] | None:
    with connect_sqlite(db_path) as conn:
        row = conn.execute(
            """
            SELECT id, analysis_json
            FROM event_analyses
            WHERE event_id = ? AND task = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (event_id, task),
        ).fetchone()
    if not row:
        return None
    parsed = json.loads(row[1])
    return {"id": int(row[0]), "analysis": parsed if isinstance(parsed, dict) else {}}


def update_event_analysis(
    analysis_id: int,
    *,
    importance: str,
    classification: str,
    direction: str,
    impact_duration: str,
    should_push: int,
    analysis: dict[str, Any],
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    with connect_sqlite(db_path) as conn:
        conn.execute(
            """
            UPDATE event_analyses
            SET importance = ?, classification = ?, direction = ?, impact_duration = ?,
                should_push = ?, analysis_json = ?
            WHERE id = ?
            """,
            (
                importance,
                classification,
                direction,
                impact_duration,
                should_push,
                json_dumps(analysis),
                analysis_id,
            ),
        )
        conn.commit()


def insert_event_analysis(
    event_id: int,
    task: str,
    model: str,
    *,
    importance: str,
    classification: str,
    direction: str,
    impact_duration: str,
    should_push: int,
    analysis: dict[str, Any],
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    with connect_sqlite(db_path) as conn:
        conn.execute(
            """
            INSERT INTO event_analyses (
                event_id, task, model, importance, classification, direction,
                impact_duration, should_push, analysis_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                task,
                model,
                importance,
                classification,
                direction,
                impact_duration,
                should_push,
                json_dumps(analysis),
                utc_now(),
            ),
        )
        conn.commit()


def record_event_delivery(
    event_id: int,
    channel: str,
    status: str,
    payload: dict[str, Any],
    *,
    error: str = "",
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    with connect_sqlite(db_path) as conn:
        conn.execute(
            """
            INSERT INTO deliveries (event_id, channel, status, sent_at, error, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_id, channel, status, utc_now() if status == "sent" else "", error, json_dumps(payload)),
        )
        conn.commit()


def article_item_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("url") or item.get("title") or "")


def ensure_article_reviews_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS article_reviews (
            source TEXT NOT NULL,
            item_id TEXT NOT NULL,
            url TEXT,
            title TEXT NOT NULL,
            source_module TEXT,
            published_at TEXT,
            importance TEXT NOT NULL,
            push_now INTEGER NOT NULL DEFAULT 0,
            market_impact TEXT,
            incremental_classification TEXT,
            affected_targets_json TEXT NOT NULL,
            reason TEXT,
            daily_summary TEXT,
            confidence TEXT,
            gate_json TEXT NOT NULL,
            skeptic_json TEXT,
            pre_skeptic_importance TEXT,
            pushed_at TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (source, item_id)
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(article_reviews)").fetchall()}
    if "skeptic_json" not in columns:
        conn.execute("ALTER TABLE article_reviews ADD COLUMN skeptic_json TEXT")
    if "pre_skeptic_importance" not in columns:
        conn.execute("ALTER TABLE article_reviews ADD COLUMN pre_skeptic_importance TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_article_reviews_created ON article_reviews(created_at)")
    conn.commit()


def save_article_review(
    conn: sqlite3.Connection,
    source: str,
    item: dict[str, Any],
    review: dict[str, Any],
    *,
    decision_item: NormalizedMarketItem | dict[str, Any] | None = None,
) -> None:
    ensure_article_reviews_table(conn)
    review = ensure_article_decision_audit(source, decision_item or item, review, push_key="push_now")
    now = datetime.now(timezone.utc).isoformat()
    item_id = article_item_id(item)
    conn.execute(
        """
        INSERT INTO article_reviews (
            source, item_id, url, title, source_module, published_at,
            importance, push_now, market_impact, incremental_classification,
            affected_targets_json, reason, daily_summary, confidence,
            gate_json, skeptic_json, pre_skeptic_importance, pushed_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, item_id) DO UPDATE SET
            source_module = excluded.source_module,
            published_at = excluded.published_at,
            importance = excluded.importance,
            push_now = excluded.push_now,
            market_impact = excluded.market_impact,
            incremental_classification = excluded.incremental_classification,
            affected_targets_json = excluded.affected_targets_json,
            reason = excluded.reason,
            daily_summary = excluded.daily_summary,
            confidence = excluded.confidence,
            gate_json = excluded.gate_json,
            skeptic_json = excluded.skeptic_json,
            pre_skeptic_importance = excluded.pre_skeptic_importance
        """,
        (
            source,
            item_id,
            str(item.get("url") or ""),
            str(item.get("title") or ""),
            str(item.get("source_module") or item.get("source_display") or ""),
            str(item.get("published_at") or ""),
            str(review.get("importance") or "low"),
            1 if review.get("push_now") else 0,
            str(review.get("market_impact") or ""),
            str(review.get("incremental_classification") or ""),
            json.dumps(review.get("affected_targets") or [], ensure_ascii=False),
            str(review.get("reason") or ""),
            str(review.get("daily_summary") or ""),
            str(review.get("confidence") or ""),
            json.dumps(review, ensure_ascii=False),
            json.dumps(review.get("skeptic") or {}, ensure_ascii=False),
            str(review.get("pre_skeptic_importance") or ""),
            "",
            now,
        ),
    )
    conn.commit()


def article_review_exists(conn: sqlite3.Connection, source: str, item_id: str) -> dict[str, Any] | None:
    ensure_article_reviews_table(conn)
    row = conn.execute(
        """
        SELECT importance, push_now, market_impact, incremental_classification,
               affected_targets_json, reason, daily_summary, confidence, gate_json,
               skeptic_json, pre_skeptic_importance, pushed_at
        FROM article_reviews
        WHERE source = ? AND item_id = ?
        """,
        (source, item_id),
    ).fetchone()
    if not row:
        return None
    (
        importance,
        push_now,
        market_impact,
        incremental,
        targets_json,
        reason,
        daily_summary,
        confidence,
        gate_json,
        skeptic_json,
        pre_skeptic_importance,
        pushed_at,
    ) = row
    raw = json_loads_dict(gate_json)
    try:
        targets = json.loads(targets_json or "[]")
    except json.JSONDecodeError:
        targets = []
    return {
        "importance": importance,
        "push_now": bool(push_now),
        "market_impact": market_impact or "",
        "incremental_classification": incremental or "",
        "affected_targets": targets if isinstance(targets, list) else [],
        "reason": reason or "",
        "daily_summary": daily_summary or "",
        "confidence": confidence or "",
        "raw": raw,
        "skeptic": json_loads_dict(skeptic_json) if skeptic_json else raw.get("skeptic", {}),
        "pre_skeptic_importance": pre_skeptic_importance or raw.get("pre_skeptic_importance", ""),
        "pushed_at": pushed_at or "",
    }


def mark_article_pushed(conn: sqlite3.Connection, source: str, item_id: str) -> None:
    ensure_article_reviews_table(conn)
    conn.execute(
        "UPDATE article_reviews SET pushed_at = ? WHERE source = ? AND item_id = ?",
        (datetime.now(timezone.utc).isoformat(), source, item_id),
    )
    conn.commit()


def official_news_item_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("url") or item.get("title") or "")


def ensure_official_news_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS official_news_reviews (
            source TEXT NOT NULL,
            item_id TEXT NOT NULL,
            url TEXT,
            title TEXT NOT NULL,
            published_at TEXT,
            importance TEXT NOT NULL,
            should_push_now INTEGER NOT NULL DEFAULT 0,
            reason TEXT,
            daily_summary TEXT,
            analysis_json TEXT NOT NULL,
            skeptic_json TEXT,
            pre_skeptic_importance TEXT,
            pushed_at TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (source, item_id)
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(official_news_reviews)").fetchall()}
    if "skeptic_json" not in columns:
        conn.execute("ALTER TABLE official_news_reviews ADD COLUMN skeptic_json TEXT")
    if "pre_skeptic_importance" not in columns:
        conn.execute("ALTER TABLE official_news_reviews ADD COLUMN pre_skeptic_importance TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_official_news_created ON official_news_reviews(created_at)")
    conn.commit()


def official_review_exists(conn: sqlite3.Connection, source: str, item_id: str) -> dict[str, Any] | None:
    ensure_official_news_table(conn)
    row = conn.execute(
        """
        SELECT importance, should_push_now, reason, daily_summary, analysis_json,
               skeptic_json, pre_skeptic_importance, pushed_at
        FROM official_news_reviews
        WHERE source = ? AND item_id = ?
        """,
        (source, item_id),
    ).fetchone()
    if not row:
        return None
    importance, should_push_now, reason, daily_summary, analysis_json, skeptic_json, pre_skeptic_importance, pushed_at = row
    parsed = json.loads(analysis_json)
    review = {
        "importance": importance,
        "should_push_now": bool(should_push_now),
        "reason": reason or "",
        "daily_summary": daily_summary or "",
        "analysis": parsed,
        "pushed_at": pushed_at or "",
    }
    try:
        skeptic = json.loads(skeptic_json or "{}")
    except json.JSONDecodeError:
        skeptic = {}
    if isinstance(skeptic, dict) and skeptic:
        review["skeptic"] = skeptic
        review["pre_skeptic_importance"] = pre_skeptic_importance or ""
    elif isinstance(parsed, dict) and isinstance(parsed.get("_skeptic"), dict):
        review["skeptic"] = parsed["_skeptic"]
        review["pre_skeptic_importance"] = parsed.get("_pre_skeptic_importance", "")
    return review


def save_official_review(
    conn: sqlite3.Connection,
    source: str,
    item: dict[str, Any],
    review: dict[str, Any],
    *,
    decision_item: NormalizedMarketItem | dict[str, Any] | None = None,
) -> None:
    ensure_official_news_table(conn)
    review = ensure_official_decision_audit(source, decision_item or item, review)
    now = datetime.now(timezone.utc).isoformat()
    analysis_payload = review.get("analysis") if isinstance(review.get("analysis"), dict) else dict(review)
    analysis_payload = dict(analysis_payload)
    if review.get("skeptic"):
        analysis_payload["_skeptic"] = review["skeptic"]
        analysis_payload["_pre_skeptic_importance"] = review.get("pre_skeptic_importance", "")
    conn.execute(
        """
        INSERT INTO official_news_reviews (
            source, item_id, url, title, published_at, importance, should_push_now,
            reason, daily_summary, analysis_json, skeptic_json,
            pre_skeptic_importance, pushed_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, item_id) DO UPDATE SET
            importance = excluded.importance,
            should_push_now = excluded.should_push_now,
            reason = excluded.reason,
            daily_summary = excluded.daily_summary,
            analysis_json = excluded.analysis_json,
            skeptic_json = excluded.skeptic_json,
            pre_skeptic_importance = excluded.pre_skeptic_importance
        """,
        (
            source,
            official_news_item_id(item),
            str(item.get("url") or ""),
            str(item.get("title") or ""),
            str(item.get("published_at") or ""),
            str(review.get("importance") or "low").lower(),
            1 if review.get("should_push_now") else 0,
            str(review.get("reason") or ""),
            str(review.get("daily_summary") or ""),
            json.dumps(analysis_payload, ensure_ascii=False),
            json.dumps(review.get("skeptic") or {}, ensure_ascii=False),
            str(review.get("pre_skeptic_importance") or ""),
            "",
            now,
        ),
    )
    conn.commit()


def mark_official_pushed(conn: sqlite3.Connection, source: str, item_id: str) -> None:
    ensure_official_news_table(conn)
    conn.execute(
        "UPDATE official_news_reviews SET pushed_at = ? WHERE source = ? AND item_id = ?",
        (datetime.now(timezone.utc).isoformat(), source, item_id),
    )
    conn.commit()
