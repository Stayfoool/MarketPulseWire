"""Compatibility exports for the event-shaped market adapter."""

from market_event_adapter import (
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


__all__ = [
    "EVENT_SOURCE_CONTEXT",
    "analysis_record_fields",
    "analyze_event",
    "apply_event_rules_to_analysis",
    "build_portfolio_event_input",
    "content_hash",
    "event_mapping_from_row",
    "event_source_context",
    "event_with_normalized_market_item_audit",
    "infer_importance",
    "load_enabled_holdings",
    "maybe_deliver_event",
    "normalize_importance",
    "normalized_event_audit_payload",
    "normalized_event_item",
    "should_push_analysis",
    "upsert_event",
]
