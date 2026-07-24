"""Shared decision and interpretation orchestration for normalized market items."""

from __future__ import annotations

from typing import Any, Literal

from attributed_research import prepare_item_for_decision
from decision_engine import apply_deterministic_source_controls, decide_market_item
from market_item import DecisionResult, InterpretationResult, MarketFlowResult, NormalizedMarketItem
from market_interpreter import interpret_market_item
from market_runtime import (
    MarketItemProcessingError,
    MarketProcessOutcome,
    is_official_news_source,
    normalize_market_item,
    process_market_item,
    record_rule_comparison,
)


FLOW_VERSION = "market_flow_v1"
RelationMode = Literal["targets", "holdings"]
ForbiddenFieldMode = Literal["article", "official", "event"]


def interpretation_failure(error: Exception) -> InterpretationResult:
    reason = str(error).strip()[:500]
    return InterpretationResult(
        brief_reason=f"薄解读失败：{reason}",
        notes=[reason] if reason else [],
        llm_judgement="failed",
        model="interpretation_failed",
        prompt_version="market_interpreter_v1",
    )


def rule_only_interpretation(item: NormalizedMarketItem, decision: DecisionResult) -> InterpretationResult:
    return InterpretationResult(
        core_content=item.summary or item.title,
        brief_reason=decision.brief_reason or decision.reason,
        related_targets=[],
        llm_judgement="not_needed",
        model="rule_only",
        prompt_version="market_interpreter_v1",
    )


def evaluate_market_item(
    item: NormalizedMarketItem,
    *,
    holdings: list[dict[str, Any]] | None = None,
    symbols: set[str] | list[str] | tuple[str, ...] | None = None,
    decision: DecisionResult | None = None,
    source_interpretation: InterpretationResult | None = None,
    content: str = "",
    task: str = "为一条已完成规则决策的市场信息生成极简实时摘要。",
    intro: str = "请解读以下市场信息",
    mode: RelationMode = "targets",
    forbidden_mode: ForbiddenFieldMode = "article",
    extra_notes: list[str] | None = None,
    user_agent: str = "surveil-market-flow/0.1",
    force_interpretation: bool = False,
    storage_ref: dict[str, Any] | None = None,
) -> MarketFlowResult:
    """Evaluate one normalized item without persistence or delivery side effects."""
    decision_item = item if decision is not None else prepare_item_for_decision(item)
    resolved_decision = decision or decide_market_item(
        decision_item,
        holdings=holdings or [],
        symbols=symbols,
    )
    if not (decision is not None and resolved_decision.audit_json.get("production_authority") is True):
        resolved_decision = apply_deterministic_source_controls(decision_item, resolved_decision)
    should_interpret = bool(
        source_interpretation is None
        and (
            force_interpretation
            or resolved_decision.need_llm_interpretation
            or resolved_decision.need_limited_llm_judgement
        )
    )
    interpretation_error = ""
    if source_interpretation is not None:
        interpretation = source_interpretation
    elif should_interpret:
        try:
            interpretation = interpret_market_item(
                decision_item,
                resolved_decision,
                content=content,
                task=task,
                intro=intro,
                mode=mode,
                forbidden_mode=forbidden_mode,
                extra_notes=extra_notes,
                user_agent=user_agent,
            )
        except Exception as exc:  # noqa: BLE001 - interpretation must not erase deterministic intent
            interpretation = interpretation_failure(exc)
            interpretation_error = str(exc).strip()[:500]
    else:
        interpretation = rule_only_interpretation(decision_item, resolved_decision)
    return MarketFlowResult(
        item=decision_item,
        decision=resolved_decision,
        interpretation=interpretation,
        storage_ref=dict(storage_ref or {}),
        delivery_intent={
            "action": resolved_decision.action,
            "should_deliver": resolved_decision.should_push,
            "dedup": dict(resolved_decision.dedup),
        },
        audit_json={
            "flow_version": FLOW_VERSION,
            "decision_supplied": decision is not None,
            "source_interpretation_supplied": source_interpretation is not None,
            "interpreter_called": should_interpret,
            "interpretation_failed": bool(interpretation_error),
            "interpretation_error": interpretation_error,
        },
    )


def finalize_market_flow_result(
    result: MarketFlowResult,
    *,
    final_push: bool | None = None,
    importance: str = "",
    reason: str = "",
    brief_reason: str = "",
    skeptic: dict[str, Any] | None = None,
    downgraded: bool = False,
    blocked: bool = False,
    storage_ref: dict[str, Any] | None = None,
) -> MarketFlowResult:
    """Return a result whose DecisionResult reflects deterministic post-decision controls."""
    initial = result.decision
    action = initial.action
    promotion_rejected = final_push is True and not initial.should_push
    if final_push is False and initial.should_push:
        action = "ignore" if blocked else "daily" if downgraded else "archive"
    audit = dict(initial.audit_json)
    audit["market_flow_finalization"] = {
        "initial_action": initial.action,
        "final_action": action,
        "final_push": final_push,
        "downgraded": downgraded,
        "blocked": blocked,
        "promotion_rejected": promotion_rejected,
    }
    final_decision = DecisionResult(
        action=action,
        importance=importance or initial.importance,
        reason=reason or initial.reason,
        brief_reason=brief_reason or initial.brief_reason or reason,
        rule_hits=list(initial.rule_hits),
        candidate_rules=list(initial.candidate_rules),
        skeptic=dict(skeptic or initial.skeptic),
        dedup=dict(initial.dedup),
        need_llm_interpretation=initial.need_llm_interpretation,
        need_limited_llm_judgement=initial.need_limited_llm_judgement,
        audit_json=audit,
    )
    flow_audit = dict(result.audit_json)
    flow_audit["finalized"] = True
    return MarketFlowResult(
        item=result.item,
        decision=final_decision,
        interpretation=result.interpretation,
        storage_ref=dict(storage_ref if storage_ref is not None else result.storage_ref),
        delivery_intent={
            "action": final_decision.action,
            "should_deliver": final_decision.should_push,
            "dedup": dict(final_decision.dedup),
        },
        audit_json=flow_audit,
    )
