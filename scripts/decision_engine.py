"""Single production boundary for the reviewed degree decision.

两个可切换引擎共用这一条生产边界：``LLM_DECISION_ENGINE`` 缺省或为 ``llm``
时走既有生成模型规则决策；为 ``jev`` 时走 Jev 类型化决策引擎（无证据契约，
顶部选择直接生效）。切换只改变决策引擎，不改变准入、下游解释、投递和
`DecisionResult.action` 唯一权威。
"""

from __future__ import annotations

import os
from typing import Any

from market_item import AdmissionResult, DecisionResult, NormalizedMarketItem


DECISION_ENGINE_ENV = "LLM_DECISION_ENGINE"
DECISION_ENGINE_LLM = "llm"
DECISION_ENGINE_JEV = "jev"


def resolve_decision_engine(values: dict[str, str] | None = None) -> str:
    """Return the configured production decision engine; unknown values fail closed."""
    env = os.environ if values is None else values
    engine = str(env.get(DECISION_ENGINE_ENV) or "").strip().lower()
    if not engine:
        return DECISION_ENGINE_LLM
    if engine not in {DECISION_ENGINE_LLM, DECISION_ENGINE_JEV}:
        raise ValueError(f"不支持的 {DECISION_ENGINE_ENV}: {engine}")
    return engine


def decide_market_item_with_llm(
    item: NormalizedMarketItem,
    *,
    admission: AdmissionResult,
    portfolio: Any,
    market_item_id: int,
    market_review_id: int,
) -> DecisionResult:
    """Invoke the only production degree/action decision implementation."""
    engine = resolve_decision_engine()
    if engine == DECISION_ENGINE_JEV:
        from llm_jev_decision import decide_production_market_item_with_jev

        return decide_production_market_item_with_jev(
            item,
            admission=admission,
            portfolio=portfolio,
            market_item_id=market_item_id,
            market_review_id=market_review_id,
        )
    from llm_production_decision import decide_production_market_item

    decision = decide_production_market_item(
        item,
        admission=admission,
        portfolio=portfolio,
        market_item_id=market_item_id,
        market_review_id=market_review_id,
    )
    run_jev_shadow_for_production(
        item,
        admission=admission,
        portfolio=portfolio,
        production_decision=decision,
        market_item_id=market_item_id,
        market_review_id=market_review_id,
    )
    return decision


def run_jev_shadow_for_production(
    item: NormalizedMarketItem,
    *,
    admission: AdmissionResult,
    portfolio: Any,
    production_decision: DecisionResult,
    market_item_id: int,
    market_review_id: int,
) -> None:
    """Best-effort Jev shadow comparison next to the generative production decision.

    run_jev_shadow_comparison 自身吞掉全部异常并保证不修改生产决策；未启用时直接返回。
    """
    from llm_jev_decision import run_jev_shadow_comparison

    run_jev_shadow_comparison(
        item=item,
        admission=admission,
        portfolio=portfolio,
        production_decision=production_decision,
        market_item_id=market_item_id,
        market_review_id=market_review_id,
    )
