"""Explicit ingestion and compatibility-store adapters for the shared market flow."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from market_db import DEFAULT_DB_PATH
from market_item import NormalizedMarketItem
from market_review_store import (
    insert_event_analysis,
    save_article_review,
    save_official_review,
    update_event_analysis,
    upsert_event_record,
)


def normalized_item_audit_payload(item: NormalizedMarketItem) -> dict[str, Any]:
    raw_keys = sorted(str(key) for key in item.raw if key != "_normalized_market_item")
    return {
        "schema": "NormalizedMarketItem/v1",
        "source": item.source,
        "source_category": item.source_category,
        "collector": item.collector,
        "content_type": item.content_type,
        "title": item.title,
        "summary": item.summary,
        "url": item.url,
        "published_at": item.published_at,
        "first_seen_at": item.first_seen_at,
        "symbols": list(item.symbols),
        "themes": list(item.themes),
        "dedupe_key": item.dedupe_key,
        "source_event_id": str(item.raw.get("source_event_id") or ""),
        "access_note": item.access_note,
        "full_text_chars": len(item.full_text),
        "raw_keys": raw_keys,
    }


def event_with_ingestion_audit(event: dict[str, Any], item: NormalizedMarketItem) -> dict[str, Any]:
    updated = dict(event)
    raw = dict(updated.get("raw") or {})
    raw.pop("_normalized_market_item", None)
    raw["_normalized_market_item"] = normalized_item_audit_payload(item)
    updated["raw"] = raw
    return updated


def ingest_event_item(
    event: dict[str, Any],
    item: NormalizedMarketItem,
    db_path: Path = DEFAULT_DB_PATH,
) -> tuple[int, bool]:
    """Persist one raw event for idempotency and return (event_id, inserted)."""
    return upsert_event_record(event_with_ingestion_audit(event, item), db_path)


def store_article_flow_review(
    conn: sqlite3.Connection,
    source: str,
    item: dict[str, Any],
    review: dict[str, Any],
    normalized_item: NormalizedMarketItem,
) -> None:
    save_article_review(conn, source, item, review, decision_item=normalized_item)


def store_official_flow_review(
    conn: sqlite3.Connection,
    source: str,
    item: dict[str, Any],
    review: dict[str, Any],
    normalized_item: NormalizedMarketItem,
) -> None:
    save_official_review(conn, source, item, review, decision_item=normalized_item)


def store_event_flow_analysis(
    event_id: int,
    task: str,
    model: str,
    analysis: dict[str, Any],
    *,
    importance: str,
    classification: str,
    direction: str,
    impact_duration: str,
    should_push: int,
    existing_analysis_id: int | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    if existing_analysis_id is not None:
        update_event_analysis(
            existing_analysis_id,
            importance=importance,
            classification=classification,
            direction=direction,
            impact_duration=impact_duration,
            should_push=should_push,
            analysis=analysis,
            db_path=db_path,
        )
        return
    insert_event_analysis(
        event_id,
        task,
        model,
        importance=importance,
        classification=classification,
        direction=direction,
        impact_duration=impact_duration,
        should_push=should_push,
        analysis=analysis,
        db_path=db_path,
    )
