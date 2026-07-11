"""Production compatibility wrapper for official-news reviews."""

from __future__ import annotations

import os
from typing import Any

from llm_analysis import call_chat_completion_with_prompts, format_llm_analysis, llm_config
from industry_hardline import apply_hardline_review_override, explain_hardline
from decision_engine import attach_decision_to_official_review
from market_item import NormalizedMarketItem, item_from_article_mapping
from market_interpreter import thin_system_prompt, thin_user_prompt_template
from market_review_store import (
    ensure_official_news_table,
    mark_official_pushed as mark_pushed,
    official_review_exists as review_exists,
    save_official_review as _save_official_review,
)
from push_rules import (
    apply_article_push_rules,
    first_matching_push_rule,
    load_enabled_holdings_for_rules,
    review_from_push_rule,
)
from skeptic_evaluator import skeptic_lines
from source_profiles import runtime_source_profile


OFFICIAL_NEWS_SOURCES = {
    "openai_news",
    "nvidia_blog",
    "nvidia_developer_blog",
    "samsung_semiconductor_news",
    "samsung_global_semiconductor",
    "skhynix_newsroom",
    "micron_news_releases",
}


GATE_SYSTEM_PROMPT = thin_system_prompt(
    task="为一条核心公司官网新闻生成极简实时摘要。",
    subject_note="核心公司包括 OpenAI、NVIDIA、Samsung Semiconductor、SK hynix、Micron 等。",
)


GATE_USER_PROMPT = thin_user_prompt_template(
    intro="请分析以下官网新闻",
    mode="targets",
    forbidden_mode="official",
    extra_notes=[
        "新一代 GPU/ASIC/CPU/互联/液冷/服务器平台、HBM/DRAM/NAND、样品/量产/客户资格认证、大客户采购、资本开支、建厂、先进封装和数据中心扩张等，只能围绕原文证据和规则上下文摘要。",
    ],
)


def official_news_enabled() -> bool:
    return llm_config() is not None


def is_official_news_source(source: str) -> bool:
    return source in OFFICIAL_NEWS_SOURCES


def _source_profile(source: str) -> dict[str, Any]:
    try:
        return runtime_source_profile(source) or {}
    except Exception:
        return {}


def normalized_official_item(source: str, item: dict[str, Any]) -> NormalizedMarketItem:
    profile = _source_profile(source)
    return item_from_article_mapping(
        source,
        item,
        source_category=str(item.get("source_category") or profile.get("category") or "official_company"),
        collector=str(item.get("collector") or profile.get("fetcher") or "official_news_gate"),
        content_type=str(item.get("content_type") or "official_news"),
    )


def normalize_review(parsed: dict[str, Any]) -> dict[str, Any]:
    importance = str(parsed.get("importance") or "low").strip().lower()
    if importance not in {"high", "medium", "low"}:
        importance = "low"
    should_push_now = bool(parsed.get("should_push_now")) and importance == "high"
    analysis = parsed.get("analysis") if isinstance(parsed.get("analysis"), dict) else parsed
    if isinstance(analysis, dict):
        analysis = {**analysis, "llm_mode": "thin"}
    core_content = str(parsed.get("core_content") or (analysis.get("core_content") if isinstance(analysis, dict) else "") or "").strip()
    brief_reason = str(parsed.get("brief_reason") or parsed.get("reason") or "").strip()
    related_targets = parsed.get("related_targets")
    if isinstance(related_targets, list) and isinstance(analysis, dict):
        analysis = {**analysis, "related_targets": related_targets}
    return {
        "importance": importance,
        "should_push_now": should_push_now,
        "reason": brief_reason,
        "brief_reason": brief_reason,
        "industry_impact": str(parsed.get("industry_impact") or "").strip(),
        "a_share_relevance": str(parsed.get("a_share_relevance") or "").strip(),
        "daily_summary": str(parsed.get("daily_summary") or core_content or "").strip(),
        "analysis": analysis,
    }


def review_official_news(source: str, item: dict[str, Any]) -> dict[str, Any]:
    text = str(item.get("full_text") or item.get("content") or item.get("summary") or "").strip()
    title = str(item.get("title") or "").strip()
    hardline_note = explain_hardline(source, (title, text, item.get("source_module")))
    if hardline_note:
        text = f"【产业硬变量线提示】{hardline_note}\n\n{text}"
    user_prompt = (
        GATE_USER_PROMPT.replace("{source}", source)
        .replace("{title}", title)
        .replace("{published_at}", str(item.get("published_at") or ""))
        .replace("{content}", text[:12000])
    )
    parsed, model = call_chat_completion_with_prompts(
        GATE_SYSTEM_PROMPT,
        user_prompt,
        user_agent="surveil-official-news-gate/0.1",
        truncate_user_prompt=False,
        thinking_override=os.getenv("LLM_GATE_THINKING_TYPE", "enabled"),
        max_tokens_override=int(os.getenv("LLM_GATE_MAX_OUTPUT_TOKENS", "1400")),
    )
    review = normalize_review(parsed)
    review["model"] = model
    return review


def save_review(conn, source: str, item: dict[str, Any], review: dict[str, Any]) -> None:
    _save_official_review(conn, source, item, review, decision_item=normalized_official_item(source, item))


def apply_official_hardline_override(source: str, item: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    updated = apply_hardline_review_override(source, item, review)
    if updated.get("push_now"):
        updated["should_push_now"] = True
    return updated


def rule_first_official_review(source: str, item: dict[str, Any]) -> dict[str, Any] | None:
    holdings = load_enabled_holdings_for_rules()
    rule = first_matching_push_rule(source=source, item=item, holdings=holdings)
    if not rule:
        return None
    review = review_from_push_rule(rule, item, push_key="should_push_now")
    review["analysis"] = {
        "core_content": str(item.get("summary") or item.get("title") or "").strip(),
        "related_targets": rule.get("related_targets") or [],
        "llm_mode": "rule_only",
    }
    return attach_decision_to_official_review(source, normalized_official_item(source, item), review, holdings=holdings)


def apply_official_push_rule_override(source: str, item: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    holdings = load_enabled_holdings_for_rules()
    updated = apply_article_push_rules(source, item, review, holdings=holdings, push_key="should_push_now")
    return attach_decision_to_official_review(source, normalized_official_item(source, item), updated, holdings=holdings)


def analysis_lines_from_review(review: dict[str, Any]) -> list[str]:
    parsed = review.get("analysis") if isinstance(review.get("analysis"), dict) else review
    model = str(review.get("model") or "LLM")
    lines = format_llm_analysis(parsed, model)
    prefix = [
        f"官网新闻重要性：{review.get('importance', 'low')}",
        f"是否即时推送：{'是' if review.get('should_push_now') else '否'}",
    ]
    reason = str(review.get("reason") or "").strip()
    if reason:
        prefix.append(f"分流理由：{reason}")
    prefix.extend(skeptic_lines(review))
    return [lines[0], *prefix, *lines[1:]]
