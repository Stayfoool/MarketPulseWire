"""Compatibility wrapper for the unified official-company content flow."""

from market_content_flow import (
    OFFICIAL_NEWS_SOURCES,
    OFFICIAL_SYSTEM_PROMPT as GATE_SYSTEM_PROMPT,
    OFFICIAL_USER_PROMPT as GATE_USER_PROMPT,
    analysis_lines_from_review,
    apply_official_hardline_override,
    apply_official_push_rule_override,
    is_official_news_source,
    normalize_official_review as normalize_review,
    normalized_official_item,
    official_news_enabled,
    process_official_review,
    review_official_news,
    rule_first_official_review,
    save_official_review as save_review,
)
from market_review_store import (
    ensure_official_news_table,
    mark_official_pushed as mark_pushed,
    official_review_exists as review_exists,
)


__all__ = [
    "GATE_SYSTEM_PROMPT",
    "GATE_USER_PROMPT",
    "OFFICIAL_NEWS_SOURCES",
    "analysis_lines_from_review",
    "apply_official_hardline_override",
    "apply_official_push_rule_override",
    "ensure_official_news_table",
    "is_official_news_source",
    "mark_pushed",
    "normalize_review",
    "normalized_official_item",
    "official_news_enabled",
    "process_official_review",
    "review_exists",
    "review_official_news",
    "rule_first_official_review",
    "save_review",
]
