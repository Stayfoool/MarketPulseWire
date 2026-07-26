"""Shared decision and interpretation orchestration for normalized market items."""

from __future__ import annotations

from typing import Any, Literal

from market_item import DecisionResult, InterpretationResult, MarketFlowResult, NormalizedMarketItem
from market_interpreter import interpret_market_item
from market_runtime import (
    MarketItemProcessingError,
    MarketProcessOutcome,
    is_official_news_source,
    normalize_market_item,
    process_market_item,
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
    decision: DecisionResult,
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
    """Interpret one normalized item after its authoritative decision exists."""
    decision_item = item
    resolved_decision = decision
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
        except Exception as exc:  # noqa: BLE001 - interpretation must not erase the authoritative decision
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
