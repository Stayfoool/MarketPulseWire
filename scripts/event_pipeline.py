"""Compatibility wrapper for the unified event-family market flow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from market_db import DEFAULT_DB_PATH
from market_delivery import compact_event_analysis_lines, feishu_webhook_fingerprint, record_delivery, simple_event_card
from market_item import NormalizedMarketItem
from market_event_flow import (
    EVENT_SOURCE_CONTEXT,
    analysis_record_fields,
    apply_event_rules_to_analysis,
    build_portfolio_event_input,
    content_hash,
    event_mapping_from_row,
    event_source_context,
    event_with_normalized_market_item_audit,
    infer_importance,
    load_enabled_holdings,
    normalize_importance,
    normalized_event_audit_payload,
    normalized_event_item,
    should_push_analysis,
)
from market_event_flow import analyze_event as _analyze_event
from market_event_flow import maybe_deliver_event as _maybe_deliver_event
from market_event_flow import upsert_event as _upsert_event
from market_review_store import json_dumps, utc_now


def upsert_event(
    event: dict[str, Any],
    db_path: Path = DEFAULT_DB_PATH,
    *,
    normalized_item: NormalizedMarketItem | None = None,
) -> tuple[int, bool]:
    return _upsert_event(event, db_path, normalized_item=normalized_item)


def analyze_event(event_id: int, task: str = "portfolio_event", db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    return _analyze_event(event_id, task=task, db_path=db_path)


def maybe_deliver_event(event_id: int, analysis: dict[str, Any], db_path: Path = DEFAULT_DB_PATH) -> str:
    return _maybe_deliver_event(event_id, analysis, db_path=db_path)
