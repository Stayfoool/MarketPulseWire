"""Unified deterministic decision wrapper for market items.

This module is the second migration step toward the three-layer market flow.
It is intentionally passive: it does not call LLMs, send Feishu cards, write
SQLite, or reserve delivery dedup keys. It only evaluates the existing
deterministic rule helpers and converts their legacy dict outputs into the
shared DecisionResult shape.
"""

from __future__ import annotations

from typing import Any

from attributed_research import EXTRACTION_KEY, attributed_research_rule
from industry_hardline import industry_topic_hard_variable_rule
from macro_policy import macro_policy_match
from market_item import VALID_ACTIONS, DecisionResult, NormalizedMarketItem, normalize_importance
from push_rules import first_matching_push_rule
from trade_friction import trade_friction_rule


ENGINE_VERSION = "decision_engine_v1"


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _item_source(item: NormalizedMarketItem | dict[str, Any], source: str | None) -> str:
    if source is not None:
        return _clean_text(source)
    if isinstance(item, NormalizedMarketItem):
        return item.source
    return _clean_text(item.get("source"))


def _legacy_item(item: NormalizedMarketItem | dict[str, Any]) -> dict[str, Any]:
    """Convert a normalized item to the dict shape expected by legacy rules."""
    if isinstance(item, NormalizedMarketItem):
        raw = dict(item.raw)
        legacy = dict(raw)
        legacy.update(
            {
                "title": item.title,
                "summary": item.summary,
                "source": item.source,
                "source_category": item.source_category,
                "publisher_role": item.publisher_role,
                "content": raw.get("content") or item.summary,
                "full_text": item.full_text,
                "url": item.url,
                "published_at": item.published_at,
                "first_seen_at": item.first_seen_at,
                "symbols": list(item.symbols),
                "themes": list(item.themes),
                "raw": raw,
                "dedupe_key": item.dedupe_key,
                "access_note": item.access_note,
            }
        )
        return legacy
    return dict(item)


def _symbol_set(
    item: NormalizedMarketItem | dict[str, Any],
    legacy_item: dict[str, Any],
    symbols: set[str] | list[str] | tuple[str, ...] | None,
) -> set[str]:
    if symbols is not None:
        return {str(symbol).upper() for symbol in symbols if str(symbol).strip()}
    if isinstance(item, NormalizedMarketItem):
        return {symbol.upper() for symbol in item.symbols if symbol}
    raw_symbols = legacy_item.get("symbols") or legacy_item.get("related_symbols") or []
    if isinstance(raw_symbols, list):
        return {str(symbol).upper() for symbol in raw_symbols if str(symbol).strip()}
    return set()


def _audit_base(source: str, item: NormalizedMarketItem | dict[str, Any], legacy_item: dict[str, Any]) -> dict[str, Any]:
    base: dict[str, Any] = {
        "engine_version": ENGINE_VERSION,
        "source": source,
        "deterministic_push_match": False,
        "legacy_entrypoints_wrapped": [
            "push_rules.first_matching_push_rule",
            "attributed_research.attributed_research_rule",
            "industry_hardline.industry_topic_hard_variable_rule",
            "trade_friction.trade_friction_rule",
            "macro_policy.macro_policy_match",
        ],
    }
    if isinstance(item, NormalizedMarketItem):
        base.update(
            {
                "content_type": item.content_type,
                "source_category": item.source_category,
                "publisher_role": item.publisher_role,
                "collector": item.collector,
                "dedupe_key": item.dedupe_key,
            }
        )
        attribution = item.raw.get(EXTRACTION_KEY)
        if isinstance(attribution, dict):
            base["attributed_research_extraction"] = dict(attribution)
    else:
        base.update(
            {
                "content_type": str(legacy_item.get("content_type") or legacy_item.get("event_type") or ""),
                "dedupe_key": str(legacy_item.get("dedupe_key") or ""),
            }
        )
    return base


def _rule_dedup(rule: dict[str, Any]) -> dict[str, Any]:
    dedup_key = str(rule.get("dedup_key") or "").strip()
    if not dedup_key:
        return {}
    return {
        "rule_alert_reservation_required": True,
        "rule_id": str(rule.get("rule_id") or ""),
        "dedup_key": dedup_key,
        "dedup_lookback_days": rule.get("dedup_lookback_days"),
        "note": "decision_engine only reports dedup metadata; reservation stays in the delivery layer.",
    }


def decision_metadata(decision: DecisionResult, *, final_fields: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "decision_engine_version": ENGINE_VERSION,
        "decision_passthrough": True,
        "decision_result": decision.to_dict(),
        "decision_audit": dict(decision.audit_json),
        "decision_final_fields": dict(final_fields or {}),
    }


def _article_final_fields(review: dict[str, Any], push_key: str) -> dict[str, Any]:
    return {
        "importance": review.get("importance"),
        push_key: bool(review.get(push_key)),
        "reason": review.get("reason"),
    }


def _official_final_fields(review: dict[str, Any]) -> dict[str, Any]:
    return {
        "importance": review.get("importance"),
        "should_push_now": bool(review.get("should_push_now")),
        "reason": review.get("reason"),
    }


def _event_final_fields(analysis: dict[str, Any]) -> dict[str, Any]:
    push_decision = analysis.get("push_decision") if isinstance(analysis.get("push_decision"), dict) else {}
    return {
        "importance": analysis.get("importance"),
        "should_push": bool(push_decision.get("should_push") if push_decision else analysis.get("should_push")),
        "reason": push_decision.get("reason") if push_decision else analysis.get("brief_reason"),
    }


def _has_article_decision(review: dict[str, Any]) -> bool:
    raw = review.get("raw") if isinstance(review.get("raw"), dict) else {}
    return isinstance(raw.get("decision_result"), dict)


def attach_decision_to_article_review(
    source: str,
    item: NormalizedMarketItem | dict[str, Any],
    review: dict[str, Any],
    *,
    holdings: list[dict[str, Any]] | None = None,
    symbols: set[str] | list[str] | tuple[str, ...] | None = None,
    push_key: str = "push_now",
) -> dict[str, Any]:
    """Attach DecisionResult audit metadata without changing legacy fields."""
    decision = decide_market_item(item, source=source, holdings=holdings or [], symbols=symbols)
    return attach_decision_result_to_article_review(decision, review, push_key=push_key)


def attach_decision_result_to_article_review(
    decision: DecisionResult,
    review: dict[str, Any],
    *,
    push_key: str = "push_now",
) -> dict[str, Any]:
    """Attach an already-finalized decision without recomputing the rules."""
    updated = dict(review)
    raw = dict(updated.get("raw") or {})
    raw.update(
        decision_metadata(
            decision,
            final_fields=_article_final_fields(updated, push_key),
        )
    )
    updated["raw"] = raw
    return updated


def ensure_article_decision_audit(
    source: str,
    item: NormalizedMarketItem | dict[str, Any],
    review: dict[str, Any],
    *,
    push_key: str = "push_now",
) -> dict[str, Any]:
    if _has_article_decision(review):
        updated = dict(review)
        raw = dict(updated.get("raw") or {})
        raw["decision_final_fields"] = _article_final_fields(updated, push_key)
        updated["raw"] = raw
        return updated
    return attach_decision_to_article_review(source, item, review, holdings=[], push_key=push_key)


def _prefixed_metadata(decision: DecisionResult, *, final_fields: dict[str, Any] | None = None) -> dict[str, Any]:
    return {f"_{key}": value for key, value in decision_metadata(decision, final_fields=final_fields).items()}


def _has_payload_decision(payload: dict[str, Any]) -> bool:
    return isinstance(payload.get("_decision_result"), dict)


def attach_decision_to_official_review(
    source: str,
    item: NormalizedMarketItem | dict[str, Any],
    review: dict[str, Any],
    *,
    holdings: list[dict[str, Any]] | None = None,
    symbols: set[str] | list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    decision = decide_market_item(item, source=source, holdings=holdings or [], symbols=symbols)
    return attach_decision_result_to_official_review(decision, review)


def attach_decision_result_to_official_review(
    decision: DecisionResult,
    review: dict[str, Any],
) -> dict[str, Any]:
    """Attach an already-finalized decision without recomputing the rules."""
    updated = dict(review)
    analysis = updated.get("analysis") if isinstance(updated.get("analysis"), dict) else {}
    analysis = dict(analysis)
    analysis.update(
        _prefixed_metadata(
            decision,
            final_fields=_official_final_fields(updated),
        )
    )
    updated["analysis"] = analysis
    return updated


def ensure_official_decision_audit(
    source: str,
    item: NormalizedMarketItem | dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    analysis = review.get("analysis") if isinstance(review.get("analysis"), dict) else {}
    if _has_payload_decision(analysis):
        updated = dict(review)
        refreshed = dict(analysis)
        refreshed["_decision_final_fields"] = _official_final_fields(updated)
        updated["analysis"] = refreshed
        return updated
    return attach_decision_to_official_review(source, item, review, holdings=[])


def attach_decision_to_event_analysis(
    source: str,
    item: NormalizedMarketItem | dict[str, Any],
    analysis: dict[str, Any],
    *,
    holdings: list[dict[str, Any]] | None = None,
    symbols: set[str] | list[str] | tuple[str, ...] | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    if _has_payload_decision(analysis) and not refresh:
        updated = dict(analysis)
        updated["_decision_final_fields"] = _event_final_fields(updated)
        return updated
    decision = decide_market_item(item, source=source, holdings=holdings or [], symbols=symbols)
    updated = dict(analysis)
    updated.update(
        _prefixed_metadata(
            decision,
            final_fields={
                "importance": decision.importance,
                "should_push": decision.should_push,
                "reason": decision.brief_reason or decision.reason,
            },
        )
    )
    return updated


def _decision_from_rule(
    rule: dict[str, Any],
    *,
    audit_json: dict[str, Any],
    source_stage: str,
) -> DecisionResult:
    audit = dict(audit_json)
    explicit_action = str(rule.get("decision_action") or "").strip().lower()
    action = (
        explicit_action
        if explicit_action in VALID_ACTIONS
        else "push" if rule.get("push_now") or rule.get("should_push") else "archive"
    )
    audit["deterministic_push_match"] = action == "push"
    audit["source_stage"] = source_stage
    audit["legacy_rule"] = dict(rule)
    return DecisionResult(
        action=action,
        importance=normalize_importance(rule.get("importance"), default="high"),
        reason=str(rule.get("reason") or ""),
        brief_reason=str(rule.get("brief_reason") or rule.get("reason") or ""),
        rule_hits=[rule],
        dedup=_rule_dedup(rule),
        need_llm_interpretation=action == "push",
        need_limited_llm_judgement=action == "daily",
        audit_json=audit,
    )


def _decision_from_rules(
    rules: list[dict[str, Any]],
    *,
    audit_json: dict[str, Any],
    source_stage: str,
    dedup_rule: dict[str, Any] | None = None,
) -> DecisionResult:
    audit = dict(audit_json)
    audit["deterministic_push_match"] = True
    audit["source_stage"] = source_stage
    audit["legacy_rules"] = [dict(rule) for rule in rules]
    reasons = list(
        dict.fromkeys(
            str(rule.get("brief_reason") or rule.get("reason") or "").strip()
            for rule in rules
            if str(rule.get("brief_reason") or rule.get("reason") or "").strip()
        )
    )
    reason = "\n".join(reasons)
    return DecisionResult(
        action="push",
        importance="high",
        reason=reason,
        brief_reason=reason,
        rule_hits=rules,
        dedup=_rule_dedup(dedup_rule or {}),
        need_llm_interpretation=True,
        need_limited_llm_judgement=False,
        audit_json=audit,
    )


def _macro_rule(source: str, match: dict[str, Any]) -> dict[str, Any]:
    reason = str(match.get("reason") or "美国核心宏观/Fed 政策线命中。")
    full_reason = f"宏观政策线规则：{reason}"
    return {
        "matched": True,
        "rule_id": "macro_policy_line",
        "importance": "high" if match.get("tier") == "primary" else "medium",
        "push_now": match.get("tier") == "primary",
        "should_push": match.get("tier") == "primary",
        "reason": full_reason,
        "brief_reason": full_reason,
        "affected_targets": ["美债收益率/美元", "A股风险偏好", "成长股估值"],
        "related_targets": [
            {"name": "美债收益率/美元", "code": "", "relation": "美国宏观/Fed 政策线", "direction": "uncertain"},
            {"name": "A股风险偏好", "code": "", "relation": "美国宏观/Fed 政策线", "direction": "uncertain"},
            {"name": "成长股估值", "code": "", "relation": "美国宏观/Fed 政策线", "direction": "uncertain"},
        ],
        "macro_policy_line": dict(match),
        "source": source,
    }


def apply_deterministic_source_controls(
    item: NormalizedMarketItem,
    decision: DecisionResult,
) -> DecisionResult:
    """Apply trusted collector policy without introducing another decision authority."""
    if not item.source.startswith("value_directory_") or not decision.should_push:
        return decision
    policy = item.raw.get("value_directory_policy")
    preview = item.raw.get("value_directory_preview")
    if not isinstance(policy, dict) or not isinstance(preview, dict):
        return decision
    facts = preview.get("facts") if isinstance(preview.get("facts"), dict) else {}
    status = str(facts.get("status") or "").strip()
    if (
        not policy.get("preview_enabled")
        or policy.get("push_on_preview_failure")
        or not status
        or status == "ok"
    ):
        return decision

    control_reason = "第一页预览提取失败，按生产配置不发送标题兜底推送。"
    reason = f"{decision.reason}\n{control_reason}".strip()
    audit = dict(decision.audit_json)
    audit["deterministic_source_control"] = {
        "control_id": "value_directory_preview_failure_block",
        "initial_action": decision.action,
        "final_action": "archive",
        "preview_status": status,
        "preview_error": str(facts.get("error") or "")[:500],
        "policy": {
            "preview_enabled": True,
            "push_on_preview_failure": False,
        },
    }
    return DecisionResult(
        action="archive",
        importance=decision.importance,
        reason=reason,
        brief_reason=control_reason,
        rule_hits=list(decision.rule_hits),
        candidate_rules=list(decision.candidate_rules),
        skeptic=dict(decision.skeptic),
        dedup=dict(decision.dedup),
        need_llm_interpretation=False,
        need_limited_llm_judgement=False,
        audit_json=audit,
    )


def decide_market_item(
    item: NormalizedMarketItem | dict[str, Any],
    *,
    source: str | None = None,
    holdings: list[dict[str, Any]] | None = None,
    symbols: set[str] | list[str] | tuple[str, ...] | None = None,
) -> DecisionResult:
    """Evaluate existing deterministic rules and return a unified decision.

    The returned DecisionResult is an audit-friendly wrapper. Production
    article/event/official entrypoints are not changed by importing this
    function; callers must explicitly opt in.
    """
    resolved_source = _item_source(item, source)
    legacy_item = _legacy_item(item)
    holdings = holdings or []
    symbol_set = _symbol_set(item, legacy_item, symbols)
    audit = _audit_base(resolved_source, item, legacy_item)

    attributed_rule = attributed_research_rule(item)
    push_rule = first_matching_push_rule(
        source=resolved_source,
        item=legacy_item,
        holdings=holdings,
        symbols=symbol_set,
    )
    industry_rule = industry_topic_hard_variable_rule(resolved_source, legacy_item)
    trade_rule = trade_friction_rule(item)
    matched_rules = [rule for rule in (push_rule, attributed_rule, industry_rule, trade_rule) if rule]
    if len(matched_rules) > 1:
        dedup_rule = next((rule for rule in matched_rules if rule.get("dedup_key")), None)
        return _decision_from_rules(
            matched_rules,
            audit_json=audit,
            source_stage="combined_content_rules",
            dedup_rule=dedup_rule,
        )
    if push_rule:
        return _decision_from_rule(push_rule, audit_json=audit, source_stage="push_rules_first_match")
    if attributed_rule:
        return _decision_from_rule(
            attributed_rule,
            audit_json=audit,
            source_stage="attributed_research_hard_variable",
        )
    if industry_rule:
        return _decision_from_rule(
            industry_rule,
            audit_json=audit,
            source_stage="industry_topic_hard_variable",
        )
    if trade_rule:
        return _decision_from_rule(
            trade_rule,
            audit_json=audit,
            source_stage="trade_friction_escalation",
        )

    macro = macro_policy_match(legacy_item)
    if macro.get("matched"):
        rule = _macro_rule(resolved_source, macro)
        if rule["should_push"]:
            return _decision_from_rule(rule, audit_json=audit, source_stage="macro_policy_match")
        audit["source_stage"] = "macro_policy_candidate"
        audit["legacy_rule"] = dict(rule)
        return DecisionResult(
            action="daily",
            importance="medium",
            reason=str(rule.get("reason") or ""),
            brief_reason=str(rule.get("brief_reason") or rule.get("reason") or ""),
            candidate_rules=[rule],
            need_llm_interpretation=False,
            need_limited_llm_judgement=True,
            audit_json=audit,
        )

    audit["source_stage"] = "no_deterministic_match"
    return DecisionResult(
        action="archive",
        importance="unknown",
        reason="未命中当前确定性规则快判；保持既有 article/event/official 入口继续处理。",
        brief_reason="未命中确定性规则。",
        need_llm_interpretation=False,
        need_limited_llm_judgement=True,
        audit_json=audit,
    )
