"""Legacy event API exports backed by the unified market runtime adapter."""

from market_delivery import compact_event_analysis_lines, feishu_webhook_fingerprint, record_delivery, simple_event_card
from market_event_flow import (
    EVENT_SOURCE_CONTEXT,
    analysis_record_fields,
    analyze_event,
    apply_event_rules_to_analysis,
    build_portfolio_event_input,
    content_hash,
    event_mapping_from_row,
    event_source_context,
    event_with_normalized_market_item_audit,
    infer_importance,
    load_enabled_holdings,
    maybe_deliver_event,
    normalize_importance,
    normalized_event_audit_payload,
    normalized_event_item,
    should_push_analysis,
    upsert_event,
)
from market_review_store import json_dumps, utc_now


__all__ = [
    "EVENT_SOURCE_CONTEXT",
    "analysis_record_fields",
    "analyze_event",
    "apply_event_rules_to_analysis",
    "build_portfolio_event_input",
    "compact_event_analysis_lines",
    "content_hash",
    "event_mapping_from_row",
    "event_source_context",
    "event_with_normalized_market_item_audit",
    "feishu_webhook_fingerprint",
    "infer_importance",
    "json_dumps",
    "load_enabled_holdings",
    "maybe_deliver_event",
    "normalize_importance",
    "normalized_event_audit_payload",
    "normalized_event_item",
    "record_delivery",
    "should_push_analysis",
    "simple_event_card",
    "upsert_event",
    "utc_now",
]
