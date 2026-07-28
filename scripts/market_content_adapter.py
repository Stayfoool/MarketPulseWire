"""Interpret article and official market items for the unified runtime."""

from __future__ import annotations

import os
from typing import Any

from decision_engine import (
    attach_decision_result_to_article_review,
    attach_decision_result_to_official_review,
)
from llm_analysis import format_llm_analysis
from market_flow import evaluate_market_item
from market_item import (
    DecisionResult,
    InterpretationResult,
    MarketFlowResult,
    NormalizedMarketItem,
    article_item_id,
    item_from_article_mapping,
)
from market_interpreter import thin_system_prompt, thin_user_prompt_template
from source_profiles import runtime_source_profile


ARTICLE_SYSTEM_PROMPT = thin_system_prompt(task="为一条已完成规则决策的资讯/报告生成极简实时摘要。")
ARTICLE_USER_PROMPT = thin_user_prompt_template(
    intro="请解读以下资讯/报告",
    forbidden_mode="article",
    include_source_module=True,
)
OFFICIAL_SYSTEM_PROMPT = thin_system_prompt(
    task="为一条已完成规则决策的核心产业链公司官网新闻生成极简实时摘要。",
    subject_note="重点关注产品量产、客户认证、产能、资本开支、供需、价格、监管和平台路线图等硬变量。",
)
OFFICIAL_USER_PROMPT = thin_user_prompt_template(
    intro="请解读以下核心产业链公司官网新闻",
    forbidden_mode="official",
)

GATE_SYSTEM_PROMPT = ARTICLE_SYSTEM_PROMPT
GATE_USER_PROMPT = ARTICLE_USER_PROMPT

OFFICIAL_NEWS_SOURCES = {
    "openai_news",
    "nvidia_blog",
    "nvidia_developer_blog",
    "samsung_semiconductor_news",
    "samsung_global_semiconductor",
    "skhynix_newsroom",
    "micron_news_releases",
}

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
    source_category = str(
        item.get("source_category")
        or profile.get("category")
        or ARTICLE_COMPAT_SOURCE_CATEGORIES.get(source, "")
    )
    publisher_role = str(item.get("publisher_role") or profile.get("publisher_role") or "")
    if not publisher_role and source_category in {"news_media", "portfolio_stock_news"}:
        publisher_role = "news_media"
    return item_from_article_mapping(
        source,
        item,
        source_category=source_category,
        publisher_role=publisher_role,
        collector=str(item.get("collector") or profile.get("fetcher") or "market_content_adapter"),
        content_type=str(item.get("content_type") or default_content_type),
    )


def normalized_official_item(source: str, item: dict[str, Any]) -> NormalizedMarketItem:
    profile = _source_profile(source)
    return item_from_article_mapping(
        source,
        item,
        source_category=str(item.get("source_category") or profile.get("category") or "official_company"),
        publisher_role=str(item.get("publisher_role") or profile.get("publisher_role") or ""),
        collector=str(item.get("collector") or profile.get("fetcher") or "market_content_adapter"),
        content_type=str(item.get("content_type") or "official_news"),
    )


def article_gate_enabled() -> bool:
    return os.getenv("SURVEIL_ARTICLE_GATE", "1").strip() != "0"


def official_news_enabled() -> bool:
    return True


def is_official_news_source(source: str) -> bool:
    return source in OFFICIAL_NEWS_SOURCES


def _target_labels(decision: DecisionResult, interpretation: InterpretationResult) -> list[str]:
    labels: list[str] = []
    for target in interpretation.related_targets:
        name = str(target.get("name") or "").strip()
        code = str(target.get("code") or "").strip()
        label = " ".join(part for part in (name, code) if part)
        if label:
            labels.append(label)
    for rule in decision.rule_hits:
        labels.extend(str(target or "").strip() for target in rule.get("affected_targets") or [])
        for target in rule.get("related_targets") or []:
            if isinstance(target, dict):
                name = str(target.get("name") or "").strip()
                code = str(target.get("code") or "").strip()
                label = " ".join(part for part in (name, code) if part)
                if label:
                    labels.append(label)
    return [label for label in dict.fromkeys(labels) if label][:5]


def _interpretation_content(source: str, item: dict[str, Any]) -> str:
    del source
    text = str(item.get("full_text") or item.get("content") or item.get("summary") or "").strip()
    return text[:12000]


def _source_enrichment_interpretation(normalized: NormalizedMarketItem) -> InterpretationResult | None:
    if not normalized.source.startswith("value_directory_"):
        return None
    preview = normalized.raw.get("value_directory_preview")
    if not isinstance(preview, dict):
        return None
    facts = preview.get("facts") if isinstance(preview.get("facts"), dict) else {}
    if facts.get("status") != "ok":
        return None
    return InterpretationResult(
        core_content=str(facts.get("core_content") or normalized.summary or normalized.title),
        model=str(facts.get("model") or "value_directory_preview"),
        prompt_version="value_directory_preview_v1",
    )


def _evaluate_content_item(
    source: str,
    item: dict[str, Any],
    normalized: NormalizedMarketItem,
    *,
    official: bool = False,
    decision: DecisionResult,
) -> MarketFlowResult:
    source_interpretation = _source_enrichment_interpretation(normalized)
    value_directory_source = normalized.source.startswith("value_directory_")
    return evaluate_market_item(
        normalized,
        decision=decision,
        source_interpretation=source_interpretation,
        content=_interpretation_content(source, item),
        task=(
            "为一条已完成规则决策的核心产业链公司官网新闻生成极简实时摘要。"
            if official
            else "为一条已完成规则决策的资讯/报告生成极简实时摘要。"
        ),
        intro="请解读以下核心产业链公司官网新闻" if official else "请解读以下资讯/报告",
        forbidden_mode="official" if official else "article",
        extra_notes=["只可围绕 DecisionResult 的规则上下文解释，不得输出或改写推送开关。"],
        user_agent="surveil-official-content-flow/0.1" if official else "surveil-article-content-flow/0.1",
        force_interpretation=not value_directory_source,
        storage_ref={
            "store_kind": "market_reviews",
            "item_kind": "official" if official else "article",
            "source": source,
            "item_id": article_item_id(item),
        },
    )


def _attach_article_flow_audit(review: dict[str, Any], flow_result: MarketFlowResult) -> dict[str, Any]:
    updated = dict(review)
    raw = dict(updated.get("raw") or {})
    raw["_market_flow_result"] = flow_result.audit_payload()
    source_enrichment = {
        key: flow_result.item.raw[key]
        for key in ("value_directory_preview", "value_directory_policy", "cls_metadata")
        if key in flow_result.item.raw
    }
    if source_enrichment:
        raw["_source_enrichment"] = source_enrichment
    updated["raw"] = raw
    return updated


def _attach_official_flow_audit(review: dict[str, Any], flow_result: MarketFlowResult) -> dict[str, Any]:
    updated = dict(review)
    analysis = dict(updated.get("analysis") or {})
    analysis["_market_flow_result"] = flow_result.audit_payload()
    updated["analysis"] = analysis
    return updated


def _article_review_from_results(
    item: dict[str, Any],
    decision: DecisionResult,
    interpretation: InterpretationResult,
) -> dict[str, Any]:
    targets = _target_labels(decision, interpretation)
    rule_ids = [str(rule.get("rule_id") or "") for rule in decision.rule_hits if rule.get("rule_id")]
    protected_rule_ids = [
        str(rule.get("rule_id") or "")
        for rule in decision.rule_hits
        if rule.get("rule_id") and rule.get("protected_from_llm_downgrade")
    ]
    reason = decision.brief_reason or decision.reason
    return {
        "importance": decision.importance,
        "push_now": decision.should_push,
        "market_impact": "",
        "incremental_classification": "规则命中" if decision.rule_hits else "未命中确定性规则",
        "affected_targets": targets,
        "daily_summary": interpretation.core_content or str(item.get("title") or ""),
        "reason": reason,
        "brief_reason": reason,
        "confidence": "规则" if decision.rule_hits else "待确认",
        "model": interpretation.model,
        "raw": {
            **interpretation.to_dict(),
            "_interpretation_result": interpretation.to_dict(),
            "_decision_rule_ids": rule_ids,
            "_protected_decision_rule_ids": protected_rule_ids,
            "llm_mode": "thin" if interpretation.model != "interpretation_failed" else "failed",
        },
    }


def _official_review_from_results(
    item: dict[str, Any],
    decision: DecisionResult,
    interpretation: InterpretationResult,
) -> dict[str, Any]:
    reason = decision.brief_reason or decision.reason
    analysis = {
        **interpretation.to_dict(),
        "_interpretation_result": interpretation.to_dict(),
        "llm_mode": "thin" if interpretation.model != "interpretation_failed" else "failed",
    }
    return {
        "importance": decision.importance,
        "should_push_now": decision.should_push,
        "reason": reason,
        "daily_summary": interpretation.core_content or str(item.get("title") or ""),
        "analysis": analysis,
        "model": interpretation.model,
    }


def normalize_review(parsed: dict[str, Any]) -> dict[str, Any]:
    """Compatibility normalizer for callers/tests that still pass raw payloads."""
    importance = str(parsed.get("importance") or "low").strip().lower()
    if importance not in {"high", "medium", "low"}:
        importance = "low"
    related = parsed.get("related_targets") if isinstance(parsed.get("related_targets"), list) else []
    targets = []
    for target in related:
        if isinstance(target, dict):
            label = " ".join(str(target.get(key) or "").strip() for key in ("name", "code")).strip()
            if label:
                targets.append(label)
    core = str(parsed.get("core_content") or "").strip()
    reason = str(parsed.get("brief_reason") or parsed.get("reason") or "").strip()
    return {
        "importance": importance,
        "push_now": bool(parsed.get("push_now")) and importance == "high",
        "market_impact": str(parsed.get("market_impact") or "").strip(),
        "incremental_classification": str(parsed.get("incremental_classification") or "").strip(),
        "affected_targets": targets[:5],
        "daily_summary": str(parsed.get("daily_summary") or core).strip(),
        "reason": reason,
        "brief_reason": reason,
        "confidence": str(parsed.get("confidence") or "").strip(),
        "raw": {**parsed, "llm_mode": "thin"},
    }


def evaluate_article_review(
    conn,
    source: str,
    item: dict[str, Any],
    *,
    source_profile_id: str | None = None,
    normalized_item: NormalizedMarketItem | None = None,
    decision: DecisionResult,
) -> dict[str, Any]:
    """Run the production article/news spine without choosing a storage table."""
    del conn, source_profile_id
    normalized = normalized_item or normalized_article_item(source, item)
    flow_result = _evaluate_content_item(source, item, normalized, decision=decision)
    review = _article_review_from_results(item, flow_result.decision, flow_result.interpretation)
    review = _attach_article_flow_audit(review, flow_result)
    return attach_decision_result_to_article_review(flow_result.decision, review)


def failed_review(item: dict[str, Any], error: Exception) -> dict[str, Any]:
    reason = str(error).strip()[:500]
    return {
        "importance": "low",
        "push_now": False,
        "market_impact": "薄解读失败；既有 DecisionResult 保持不变。",
        "incremental_classification": "无法判断",
        "affected_targets": [],
        "daily_summary": str(item.get("title") or "薄解读失败条目"),
        "reason": f"薄解读失败：{reason}",
        "confidence": "低",
        "raw": {"error": reason},
        "model": "interpretation_failed",
    }


def gate_lines(review: dict[str, Any]) -> list[str]:
    lines = [
        f"重要性：{review.get('importance', 'low')}",
        f"是否即时推送：{'是' if review.get('push_now') else '否'}",
    ]
    reason = str(review.get("reason") or "").strip()
    if reason:
        lines.append(f"分流理由：{reason}")
    return lines


def normalize_official_review(parsed: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_review(parsed)
    return {
        "importance": normalized["importance"],
        "should_push_now": normalized["push_now"],
        "reason": normalized["reason"],
        "daily_summary": normalized["daily_summary"],
        "analysis": normalized["raw"],
    }


def evaluate_official_review(
    conn,
    source: str,
    item: dict[str, Any],
    *,
    source_profile_id: str | None = None,
    normalized_item: NormalizedMarketItem | None = None,
    decision: DecisionResult,
) -> dict[str, Any]:
    """Run the production official-news spine without choosing a storage table."""
    del conn, source_profile_id
    normalized = normalized_item or normalized_official_item(source, item)
    flow_result = _evaluate_content_item(source, item, normalized, official=True, decision=decision)
    review = _official_review_from_results(item, flow_result.decision, flow_result.interpretation)
    review = _attach_official_flow_audit(review, flow_result)
    return attach_decision_result_to_official_review(flow_result.decision, review)


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
    return [lines[0], *prefix, *lines[1:]] if lines else prefix
