"""Single production boundary for the reviewed LLM degree decision."""

from __future__ import annotations

from typing import Any

from market_item import AdmissionResult, DecisionResult, NormalizedMarketItem


def decide_market_item_with_llm(
    item: NormalizedMarketItem,
    *,
    admission: AdmissionResult,
    portfolio: Any,
    market_item_id: int,
    market_review_id: int,
) -> DecisionResult:
    """Invoke the only production degree/action decision implementation."""
    from llm_production_decision import decide_production_market_item

    return decide_production_market_item(
        item,
        admission=admission,
        portfolio=portfolio,
        market_item_id=market_item_id,
        market_review_id=market_review_id,
    )
