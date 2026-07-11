"""Production compatibility wrapper for article reviews."""

from __future__ import annotations

import os
from typing import Any

from llm_analysis import call_chat_completion_with_prompts, llm_config
from industry_hardline import apply_hardline_review_override, explain_hardline
from macro_policy import apply_macro_review_override, macro_prompt_note
from decision_engine import attach_decision_to_article_review
from market_item import NormalizedMarketItem, item_from_article_mapping
from market_review_store import (
    article_item_id,
    article_review_exists as review_exists,
    ensure_article_reviews_table,
    mark_article_pushed as mark_pushed,
    save_article_review as _save_article_review,
)
from market_interpreter import thin_system_prompt, thin_user_prompt_template
from push_rules import (
    apply_article_push_rules,
    first_matching_push_rule,
    load_enabled_holdings_for_rules,
    review_from_push_rule,
)
from skeptic_evaluator import skeptic_lines
from source_profiles import runtime_source_profile


GATE_SYSTEM_PROMPT = thin_system_prompt(task="为一条已通过规则预筛的资讯/报告生成极简实时摘要。")


GATE_USER_PROMPT = thin_user_prompt_template(
    intro="请分析以下资讯/报告",
    mode="targets",
    forbidden_mode="article",
    include_source_module=True,
)

ARTICLE_COMPAT_SOURCE_CATEGORIES = {
    "trendforce_page": "research_industry_media",
    "value_directory_ib_stocks": "research_industry_media",
    "value_directory_ib_industry_macro": "research_industry_media",
}


def _source_profile(source: str) -> dict[str, Any]:
    try:
        return runtime_source_profile(source) or {}
    except Exception:
        return {}


def normalized_article_item(source: str, item: dict[str, Any]) -> NormalizedMarketItem:
    profile = _source_profile(source)
    default_content_type = "research_index" if source.startswith("value_directory_") else "article"
    return item_from_article_mapping(
        source,
        item,
        source_category=str(
            item.get("source_category")
            or profile.get("category")
            or ARTICLE_COMPAT_SOURCE_CATEGORIES.get(source, "")
        ),
        collector=str(item.get("collector") or profile.get("fetcher") or "article_gate"),
        content_type=str(item.get("content_type") or default_content_type),
    )


def article_gate_enabled() -> bool:
    if os.getenv("SURVEIL_ARTICLE_GATE", "1").strip() == "0":
        return False
    return llm_config() is not None


def normalize_review(parsed: dict[str, Any]) -> dict[str, Any]:
    importance = str(parsed.get("importance") or "low").strip().lower()
    if importance not in {"high", "medium", "low"}:
        importance = "low"
    push_now = bool(parsed.get("push_now")) and importance == "high"
    targets = parsed.get("affected_targets")
    if not isinstance(targets, list):
        targets = []
    related_targets = parsed.get("related_targets")
    if isinstance(related_targets, list):
        for target in related_targets:
            if isinstance(target, dict):
                name = str(target.get("name") or "").strip()
                code = str(target.get("code") or "").strip()
                label = " ".join(part for part in (name, code) if part)
            else:
                label = str(target).strip()
            if label:
                targets.append(label)
    core_content = str(parsed.get("core_content") or "").strip()
    brief_reason = str(parsed.get("brief_reason") or parsed.get("reason") or "").strip()
    return {
        "importance": importance,
        "push_now": push_now,
        "market_impact": str(parsed.get("market_impact") or "").strip(),
        "incremental_classification": str(parsed.get("incremental_classification") or "").strip(),
        "affected_targets": [str(item).strip() for item in targets if str(item).strip()][:5],
        "daily_summary": str(parsed.get("daily_summary") or core_content or "").strip(),
        "reason": brief_reason,
        "brief_reason": brief_reason,
        "confidence": str(parsed.get("confidence") or "").strip(),
        "raw": {**parsed, "llm_mode": "thin"},
    }


def failed_review(item: dict[str, Any], error: Exception) -> dict[str, Any]:
    reason = str(error).strip()
    if len(reason) > 500:
        reason = reason[:497] + "..."
    return {
        "importance": "low",
        "push_now": False,
        "market_impact": "门控模型失败，无法判断是否显著影响股价。",
        "incremental_classification": "无法判断",
        "affected_targets": [],
        "daily_summary": str(item.get("title") or "门控失败条目"),
        "reason": f"门控模型失败：{reason}",
        "confidence": "低",
        "raw": {"error": reason},
        "model": "gate_failed",
    }


def review_article(source: str, item: dict[str, Any]) -> dict[str, Any]:
    text = str(item.get("full_text") or item.get("content") or item.get("summary") or "").strip()
    hardline_note = explain_hardline(source, (item.get("title"), item.get("summary"), text))
    macro_note = macro_prompt_note(item)
    content = text[:6000]
    if macro_note:
        content = f"【宏观政策线提示】{macro_note}\n\n{content}"
    if hardline_note:
        content = f"【产业硬变量线提示】{hardline_note}\n\n{content}"
    user_prompt = (
        GATE_USER_PROMPT.replace("{source}", source)
        .replace("{source_module}", str(item.get("source_module") or item.get("source_display") or ""))
        .replace("{title}", str(item.get("title") or ""))
        .replace("{published_at}", str(item.get("published_at") or ""))
        .replace("{content}", content)
    )
    parsed, model = call_chat_completion_with_prompts(
        GATE_SYSTEM_PROMPT,
        user_prompt,
        user_agent="surveil-article-gate/0.1",
        truncate_user_prompt=False,
        thinking_override=os.getenv("LLM_GATE_THINKING_TYPE", "enabled"),
        max_tokens_override=int(os.getenv("LLM_GATE_MAX_OUTPUT_TOKENS", "1400")),
    )
    review = normalize_review(parsed)
    review["model"] = model
    return review


def save_review(conn, source: str, item: dict[str, Any], review: dict[str, Any]) -> None:
    _save_article_review(conn, source, item, review, decision_item=normalized_article_item(source, item))


def apply_hardline_override(source: str, item: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    return apply_hardline_review_override(source, item, review)


def apply_macro_override(item: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    return apply_macro_review_override(review, item)


def rule_first_review(source: str, item: dict[str, Any], *, push_key: str = "push_now") -> dict[str, Any] | None:
    holdings = load_enabled_holdings_for_rules()
    rule = first_matching_push_rule(source=source, item=item, holdings=holdings)
    if not rule:
        return None
    review = review_from_push_rule(rule, item, push_key=push_key)
    return attach_decision_to_article_review(
        source,
        normalized_article_item(source, item),
        review,
        holdings=holdings,
        push_key=push_key,
    )


def apply_push_rule_override(
    source: str,
    item: dict[str, Any],
    review: dict[str, Any],
    *,
    push_key: str = "push_now",
) -> dict[str, Any]:
    holdings = load_enabled_holdings_for_rules()
    updated = apply_article_push_rules(source, item, review, holdings=holdings, push_key=push_key)
    return attach_decision_to_article_review(
        source,
        normalized_article_item(source, item),
        updated,
        holdings=holdings,
        push_key=push_key,
    )


def gate_lines(review: dict[str, Any]) -> list[str]:
    targets = review.get("affected_targets") or []
    lines = [
        f"重要性门控：{review.get('importance', 'low')}",
        f"是否即时推送：{'是' if review.get('push_now') else '否'}",
    ]
    if review.get("incremental_classification"):
        lines.append(f"门控增量判断：{review['incremental_classification']}")
    if review.get("market_impact"):
        lines.append(f"门控市场影响：{review['market_impact']}")
    if targets:
        lines.append("门控涉及标的/环节：" + "；".join(str(item) for item in targets[:5]))
    if review.get("reason"):
        lines.append(f"门控理由：{review['reason']}")
    lines.extend(skeptic_lines(review))
    return lines
