"""Compatibility wrapper for the unified article content flow."""

from market_content_flow import (
    GATE_SYSTEM_PROMPT,
    GATE_USER_PROMPT,
    apply_hardline_override,
    apply_macro_override,
    apply_push_rule_override,
    article_gate_enabled,
    failed_review,
    gate_lines,
    normalize_review,
    normalized_article_item,
    process_article_review,
    review_article,
    rule_first_review,
    save_review,
)
from market_review_store import (
    article_item_id,
    article_review_exists as review_exists,
    ensure_article_reviews_table,
    mark_article_pushed as mark_pushed,
)


__all__ = [
    "GATE_SYSTEM_PROMPT",
    "GATE_USER_PROMPT",
    "apply_hardline_override",
    "apply_macro_override",
    "apply_push_rule_override",
    "article_gate_enabled",
    "article_item_id",
    "ensure_article_reviews_table",
    "failed_review",
    "gate_lines",
    "mark_pushed",
    "normalize_review",
    "normalized_article_item",
    "process_article_review",
    "review_article",
    "review_exists",
    "rule_first_review",
    "save_review",
]
