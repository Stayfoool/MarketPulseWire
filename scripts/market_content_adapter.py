"""Interpret article and official market items for the unified runtime."""

from __future__ import annotations

from typing import Any

from decision_engine import (
    attach_decision_result_to_article_review,
    attach_decision_result_to_official_review,
)
from llm_analysis import format_llm_analysis
from market_item import (
    DecisionResult,
    InterpretationResult,
    MarketFlowResult,
)
from market_interpreter import thin_system_prompt, thin_user_prompt_template


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


def project_article_review(item: dict[str, Any], flow_result: MarketFlowResult) -> dict[str, Any]:
    """Project an authoritative flow result into the legacy article payload shape."""
    review = _article_review_from_results(item, flow_result.decision, flow_result.interpretation)
    review = _attach_article_flow_audit(review, flow_result)
    return attach_decision_result_to_article_review(flow_result.decision, review)


def gate_lines(review: dict[str, Any]) -> list[str]:
    lines = [
        f"重要性：{review.get('importance', 'low')}",
        f"是否即时推送：{'是' if review.get('push_now') else '否'}",
    ]
    reason = str(review.get("reason") or "").strip()
    if reason:
        lines.append(f"分流理由：{reason}")
    return lines


def project_official_review(item: dict[str, Any], flow_result: MarketFlowResult) -> dict[str, Any]:
    """Project an authoritative flow result into the legacy official-news payload shape."""
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
