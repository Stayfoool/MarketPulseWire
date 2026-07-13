"""Legacy payload and store adapter for article and official market items."""

from __future__ import annotations

import os
from typing import Any

from decision_engine import (
    attach_decision_result_to_article_review,
    attach_decision_result_to_official_review,
    attach_decision_to_article_review,
    attach_decision_to_official_review,
)
from industry_hardline import apply_hardline_review_override, explain_hardline
from llm_analysis import format_llm_analysis
from macro_policy import apply_macro_review_override, macro_prompt_note
from market_flow import evaluate_market_item, finalize_market_flow_result
from market_flow_adapters import store_article_flow_review, store_official_flow_review
from market_item import DecisionResult, InterpretationResult, MarketFlowResult, NormalizedMarketItem, item_from_article_mapping
from market_interpreter import thin_system_prompt, thin_user_prompt_template
from market_review_store import (
    article_item_id,
    article_review_exists,
    ensure_article_reviews_table,
    ensure_official_news_table,
    mark_article_pushed,
    mark_official_pushed,
    official_review_exists,
)
from push_rules import apply_article_push_rules, first_matching_push_rule, load_enabled_holdings_for_rules, review_from_push_rule
from skeptic_evaluator import apply_skeptic_review, skeptic_lines
from source_profiles import runtime_source_profile


ARTICLE_SYSTEM_PROMPT = thin_system_prompt(task="为一条已完成规则决策的资讯/报告生成极简实时摘要。")
ARTICLE_USER_PROMPT = thin_user_prompt_template(
    intro="请解读以下资讯/报告",
    mode="targets",
    forbidden_mode="article",
    include_source_module=True,
)
OFFICIAL_SYSTEM_PROMPT = thin_system_prompt(
    task="为一条已完成规则决策的核心产业链公司官网新闻生成极简实时摘要。",
    subject_note="重点关注产品量产、客户认证、产能、资本开支、供需、价格、监管和平台路线图等硬变量。",
)
OFFICIAL_USER_PROMPT = thin_user_prompt_template(
    intro="请解读以下核心产业链公司官网新闻",
    mode="targets",
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
    text = str(item.get("full_text") or item.get("content") or item.get("summary") or "").strip()
    notes: list[str] = []
    macro_note = macro_prompt_note(item)
    hardline_note = explain_hardline(source, (item.get("title"), item.get("summary"), text))
    if macro_note:
        notes.append(f"【宏观政策线提示】{macro_note}")
    if hardline_note:
        notes.append(f"【产业硬变量线提示】{hardline_note}")
    notes.append(text)
    return "\n\n".join(note for note in notes if note)[:12000]


def _source_enrichment_interpretation(normalized: NormalizedMarketItem) -> InterpretationResult | None:
    if not normalized.source.startswith("value_directory_"):
        return None
    preview = normalized.raw.get("value_directory_preview")
    if not isinstance(preview, dict):
        return None
    facts = preview.get("facts") if isinstance(preview.get("facts"), dict) else {}
    if facts.get("status") != "ok":
        return None
    targets = [
        {"name": str(target), "code": "", "relation": "价值目录第一页可见信息", "direction": "uncertain"}
        for target in facts.get("targets") or []
        if str(target).strip()
    ]
    notes = [str(point) for point in facts.get("key_points") or [] if str(point).strip()]
    preview_basis = str(facts.get("preview_basis") or "").strip()
    if preview_basis:
        notes.append(f"可见范围：{preview_basis}")
    return InterpretationResult(
        core_content=str(facts.get("core_content") or normalized.summary or normalized.title),
        related_targets=targets[:5],
        notes=notes,
        llm_judgement="not_needed",
        model=str(facts.get("model") or "value_directory_preview"),
        prompt_version="value_directory_preview_v1",
    )


def _evaluate_content_item(
    source: str,
    item: dict[str, Any],
    normalized: NormalizedMarketItem,
    holdings: list[dict[str, Any]],
    *,
    official: bool = False,
) -> MarketFlowResult:
    source_interpretation = _source_enrichment_interpretation(normalized)
    value_directory_source = normalized.source.startswith("value_directory_")
    return evaluate_market_item(
        normalized,
        holdings=holdings,
        source_interpretation=source_interpretation,
        content=_interpretation_content(source, item),
        task=(
            "为一条已完成规则决策的核心产业链公司官网新闻生成极简实时摘要。"
            if official
            else "为一条已完成规则决策的资讯/报告生成极简实时摘要。"
        ),
        intro="请解读以下核心产业链公司官网新闻" if official else "请解读以下资讯/报告",
        mode="targets",
        forbidden_mode="official" if official else "article",
        extra_notes=["只可围绕 DecisionResult 的规则上下文解释，不得输出或改写推送开关。"],
        user_agent="surveil-official-content-flow/0.1" if official else "surveil-article-content-flow/0.1",
        force_interpretation=not value_directory_source,
        storage_ref={
            "store_kind": "official_news_reviews" if official else "article_reviews",
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
        for key in ("value_directory_preview", "value_directory_policy")
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
    interpretation_failed = interpretation.model == "interpretation_failed"
    reason = (
        decision.brief_reason or decision.reason
        if interpretation_failed
        else interpretation.brief_reason or decision.brief_reason or decision.reason
    )
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
    interpretation_failed = interpretation.model == "interpretation_failed"
    reason = (
        decision.brief_reason or decision.reason
        if interpretation_failed
        else interpretation.brief_reason or decision.brief_reason or decision.reason
    )
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


def review_article(source: str, item: dict[str, Any]) -> dict[str, Any]:
    holdings = load_enabled_holdings_for_rules()
    normalized = normalized_article_item(source, item)
    flow_result = _evaluate_content_item(source, item, normalized, holdings)
    review = _article_review_from_results(item, flow_result.decision, flow_result.interpretation)
    review = _attach_article_flow_audit(review, flow_result)
    return attach_decision_result_to_article_review(flow_result.decision, review)


def process_article_review(
    conn,
    source: str,
    item: dict[str, Any],
    *,
    source_profile_id: str | None = None,
    normalized_item: NormalizedMarketItem | None = None,
) -> dict[str, Any]:
    """Run the production article/news spine and persist its compatibility review."""
    holdings = load_enabled_holdings_for_rules()
    normalized = normalized_item or normalized_article_item(source, item)
    flow_result = _evaluate_content_item(source, item, normalized, holdings)
    review = _article_review_from_results(item, flow_result.decision, flow_result.interpretation)
    review = apply_skeptic_review(
        conn,
        source=source,
        source_profile_id=source_profile_id,
        item=item,
        review=review,
        push_key="push_now",
    )
    hard_variable_protected = bool(review.get("industry_hard_variable_override"))
    blocked = bool(review.get("skeptic_blocked"))
    downgraded = bool(review.get("skeptic_downgraded")) and not hard_variable_protected
    flow_result = finalize_market_flow_result(
        flow_result,
        final_push=False if blocked or downgraded else None,
        importance=str(review.get("importance") or ""),
        reason=str(review.get("reason") or ""),
        brief_reason=str(review.get("brief_reason") or review.get("reason") or ""),
        skeptic=dict(review.get("skeptic") or {}),
        downgraded=downgraded,
        blocked=blocked,
    )
    review = _attach_article_flow_audit(review, flow_result)
    review = attach_decision_result_to_article_review(flow_result.decision, review)
    store_article_flow_review(conn, source, item, review, normalized)
    return review


def failed_review(item: dict[str, Any], error: Exception) -> dict[str, Any]:
    reason = str(error).strip()[:500]
    return {
        "importance": "low",
        "push_now": False,
        "market_impact": "薄解读失败，确定性规则仍可在后续 override 中生效。",
        "incremental_classification": "无法判断",
        "affected_targets": [],
        "daily_summary": str(item.get("title") or "薄解读失败条目"),
        "reason": f"薄解读失败：{reason}",
        "confidence": "低",
        "raw": {"error": reason},
        "model": "interpretation_failed",
    }


def save_review(conn, source: str, item: dict[str, Any], review: dict[str, Any]) -> None:
    store_article_flow_review(conn, source, item, review, normalized_article_item(source, item))


def apply_hardline_override(source: str, item: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    return apply_hardline_review_override(source, item, review)


def apply_macro_override(item: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    return apply_macro_review_override(review, item)


def rule_first_review(source: str, item: dict[str, Any], *, push_key: str = "push_now") -> dict[str, Any] | None:
    holdings = load_enabled_holdings_for_rules()
    normalized = normalized_article_item(source, item)
    rule = first_matching_push_rule(source=source, item=item, holdings=holdings)
    if not rule:
        return None
    review = review_from_push_rule(rule, item, push_key=push_key)
    return attach_decision_to_article_review(source, normalized, review, holdings=holdings, push_key=push_key)


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
        f"重要性：{review.get('importance', 'low')}",
        f"是否即时推送：{'是' if review.get('push_now') else '否'}",
    ]
    reason = str(review.get("reason") or "").strip()
    if reason:
        lines.append(f"分流理由：{reason}")
    if targets:
        lines.append("相关标的：" + "、".join(str(item) for item in targets[:5]))
    lines.extend(skeptic_lines(review))
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


def review_official_news(source: str, item: dict[str, Any]) -> dict[str, Any]:
    holdings = load_enabled_holdings_for_rules()
    normalized = normalized_official_item(source, item)
    flow_result = _evaluate_content_item(source, item, normalized, holdings, official=True)
    review = _official_review_from_results(item, flow_result.decision, flow_result.interpretation)
    review = _attach_official_flow_audit(review, flow_result)
    return attach_decision_result_to_official_review(flow_result.decision, review)


def process_official_review(
    conn,
    source: str,
    item: dict[str, Any],
    *,
    source_profile_id: str | None = None,
    normalized_item: NormalizedMarketItem | None = None,
) -> dict[str, Any]:
    """Run the production official-news spine and persist its compatibility review."""
    holdings = load_enabled_holdings_for_rules()
    normalized = normalized_item or normalized_official_item(source, item)
    flow_result = _evaluate_content_item(source, item, normalized, holdings, official=True)
    review = _official_review_from_results(item, flow_result.decision, flow_result.interpretation)
    review = apply_skeptic_review(
        conn,
        source=source,
        source_profile_id=source_profile_id,
        item=item,
        review=review,
        push_key="should_push_now",
    )
    hard_variable_protected = bool(review.get("industry_hard_variable_override"))
    blocked = bool(review.get("skeptic_blocked"))
    downgraded = bool(review.get("skeptic_downgraded")) and not hard_variable_protected
    flow_result = finalize_market_flow_result(
        flow_result,
        final_push=False if blocked or downgraded else None,
        importance=str(review.get("importance") or ""),
        reason=str(review.get("reason") or ""),
        brief_reason=str(review.get("brief_reason") or review.get("reason") or ""),
        skeptic=dict(review.get("skeptic") or {}),
        downgraded=downgraded,
        blocked=blocked,
    )
    review = _attach_official_flow_audit(review, flow_result)
    review = attach_decision_result_to_official_review(flow_result.decision, review)
    store_official_flow_review(conn, source, item, review, normalized)
    return review


def save_official_review(conn, source: str, item: dict[str, Any], review: dict[str, Any]) -> None:
    store_official_flow_review(conn, source, item, review, normalized_official_item(source, item))


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
    return [lines[0], *prefix, *lines[1:]] if lines else prefix


review_exists = article_review_exists
mark_pushed = mark_article_pushed
