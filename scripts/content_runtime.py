"""Runtime selector for article/news/official unified content flow."""

from __future__ import annotations

import os
from types import ModuleType
from typing import Any


DIRECT_PATH_ENV = "SURVEIL_CONTENT_DIRECT_PATH"


def content_direct_path_enabled() -> bool:
    return os.getenv(DIRECT_PATH_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


def selected_article_module() -> ModuleType:
    if content_direct_path_enabled():
        import market_content_flow

        return market_content_flow
    import article_gate

    return article_gate


def selected_official_module() -> ModuleType:
    if content_direct_path_enabled():
        import market_content_flow

        return market_content_flow
    import official_news_gate

    return official_news_gate


def runtime_path_name() -> str:
    return "direct" if content_direct_path_enabled() else "compat"


def process_article_review(conn, source: str, item: dict[str, Any], *, source_profile_id: str | None = None):
    return selected_article_module().process_article_review(
        conn,
        source,
        item,
        source_profile_id=source_profile_id,
    )


def process_official_review(conn, source: str, item: dict[str, Any], *, source_profile_id: str | None = None):
    return selected_official_module().process_official_review(
        conn,
        source,
        item,
        source_profile_id=source_profile_id,
    )


def article_gate_enabled() -> bool:
    return selected_article_module().article_gate_enabled()


def article_item_id(item: dict[str, Any]) -> str:
    return selected_article_module().article_item_id(item)


def article_review_exists(conn, source: str, item_id: str):
    return selected_article_module().review_exists(conn, source, item_id)


def review_article(source: str, item: dict[str, Any]) -> dict[str, Any]:
    return selected_article_module().review_article(source, item)


def failed_review(item: dict[str, Any], error: Exception) -> dict[str, Any]:
    return selected_article_module().failed_review(item, error)


def rule_first_review(source: str, item: dict[str, Any], *, push_key: str = "push_now"):
    return selected_article_module().rule_first_review(source, item, push_key=push_key)


def apply_article_hardline_override(source: str, item: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    return selected_article_module().apply_hardline_override(source, item, review)


def apply_macro_override(item: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    return selected_article_module().apply_macro_override(item, review)


def apply_push_rule_override(
    source: str, item: dict[str, Any], review: dict[str, Any], *, push_key: str = "push_now"
) -> dict[str, Any]:
    return selected_article_module().apply_push_rule_override(source, item, review, push_key=push_key)


def save_article_review(conn, source: str, item: dict[str, Any], review: dict[str, Any]) -> None:
    selected_article_module().save_review(conn, source, item, review)


def mark_article_pushed(conn, source: str, item_id: str) -> None:
    selected_article_module().mark_pushed(conn, source, item_id)


def gate_lines(review: dict[str, Any]) -> list[str]:
    return selected_article_module().gate_lines(review)


def official_news_enabled() -> bool:
    return selected_official_module().official_news_enabled()


def is_official_news_source(source: str) -> bool:
    return selected_official_module().is_official_news_source(source)


def official_review_exists(conn, source: str, item_id: str):
    module = selected_official_module()
    function = module.official_review_exists if content_direct_path_enabled() else module.review_exists
    return function(conn, source, item_id)


def review_official_news(source: str, item: dict[str, Any]) -> dict[str, Any]:
    return selected_official_module().review_official_news(source, item)


def rule_first_official_review(source: str, item: dict[str, Any]):
    return selected_official_module().rule_first_official_review(source, item)


def apply_official_hardline_override(source: str, item: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    return selected_official_module().apply_official_hardline_override(source, item, review)


def apply_official_push_rule_override(source: str, item: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    return selected_official_module().apply_official_push_rule_override(source, item, review)


def save_official_review(conn, source: str, item: dict[str, Any], review: dict[str, Any]) -> None:
    module = selected_official_module()
    function = module.save_official_review if content_direct_path_enabled() else module.save_review
    function(conn, source, item, review)


def mark_official_pushed(conn, source: str, item_id: str) -> None:
    module = selected_official_module()
    function = module.mark_official_pushed if content_direct_path_enabled() else module.mark_pushed
    function(conn, source, item_id)


def analysis_lines_from_review(review: dict[str, Any]) -> list[str]:
    return selected_official_module().analysis_lines_from_review(review)
