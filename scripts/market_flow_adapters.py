"""Audit helpers shared by event-shaped market item adapters."""

from __future__ import annotations

from typing import Any

from market_item import NormalizedMarketItem


def normalized_item_audit_payload(item: NormalizedMarketItem) -> dict[str, Any]:
    raw_keys = sorted(str(key) for key in item.raw if key != "_normalized_market_item")
    return {
        "schema": "NormalizedMarketItem/v1",
        "source": item.source,
        "source_category": item.source_category,
        "publisher_role": item.publisher_role,
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
