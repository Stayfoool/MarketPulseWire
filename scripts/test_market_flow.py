#!/usr/bin/env python3
"""Regression checks for the shared normalized market-flow core."""

from __future__ import annotations

import inspect

import market_content_flow
import market_event_flow
import market_flow
from market_flow import evaluate_market_item, finalize_market_flow_result
from market_item import DecisionResult, InterpretationResult, MarketFlowResult, NormalizedMarketItem


def canonical_items() -> list[NormalizedMarketItem]:
    return [
        NormalizedMarketItem(
            source="semianalysis",
            source_category="research_industry_media",
            collector="research_collector",
            content_type="article",
            title="AI infrastructure research update",
        ),
        NormalizedMarketItem(
            source="nvidia_blog",
            source_category="official_company",
            collector="official_collector",
            content_type="official_news",
            title="NVIDIA platform update",
        ),
        NormalizedMarketItem(
            source="sina_flash",
            source_category="news_media",
            collector="sina_flash",
            content_type="flash",
            title="新浪财经快讯",
        ),
        NormalizedMarketItem(
            source="sina_stock_news",
            source_category="portfolio_stock_news",
            collector="sina_stock_news",
            content_type="portfolio_news",
            title="持仓相关新闻",
        ),
        NormalizedMarketItem(
            source="ifind_notice",
            source_category="company_disclosures",
            collector="ifind_batch",
            content_type="notice",
            title="上市公司公告",
        ),
        NormalizedMarketItem(
            source="ifind_report",
            source_category="company_disclosures",
            collector="ifind_batch",
            content_type="report",
            title="研究报告",
        ),
    ]


def fake_interpretation(*args, **kwargs) -> InterpretationResult:
    decision = args[1]
    return InterpretationResult(
        core_content="统一市场流解读。",
        brief_reason=decision.brief_reason or decision.reason,
        related_targets=[{"name": "测试标的", "relation": "规则上下文"}],
        model="fake-model",
        prompt_version="market_interpreter_v1",
    )


def test_six_content_types_share_one_decision_and_interpretation_contract() -> None:
    original_decider = market_flow.decide_market_item
    original_interpreter = market_flow.interpret_market_item
    calls = {"decision": 0, "interpretation": 0}

    def fake_decider(item, *, holdings, symbols=None):
        calls["decision"] += 1
        return DecisionResult(
            action="push",
            importance="high",
            reason="canonical hard rule",
            brief_reason="canonical hard rule",
            rule_hits=[{"rule_id": "canonical_rule"}],
            need_llm_interpretation=True,
        )

    def fake_interpreter(*args, **kwargs):
        calls["interpretation"] += 1
        return fake_interpretation(*args, **kwargs)

    try:
        market_flow.decide_market_item = fake_decider
        market_flow.interpret_market_item = fake_interpreter
        results = [evaluate_market_item(item) for item in canonical_items()]
    finally:
        market_flow.decide_market_item = original_decider
        market_flow.interpret_market_item = original_interpreter

    assert calls == {"decision": 6, "interpretation": 6}
    assert all(isinstance(result, MarketFlowResult) for result in results)
    assert all(result.decision.action == "push" for result in results)
    assert all(result.delivery_intent["should_deliver"] is True for result in results)
    assert all("should_push" not in result.interpretation.to_dict() for result in results)
    sina = next(result for result in results if result.item.source == "sina_flash")
    assert sina.item.source_category == "news_media"
    assert sina.item.content_type == "flash"


def test_interpretation_failure_preserves_deterministic_action() -> None:
    original_interpreter = market_flow.interpret_market_item
    try:
        market_flow.interpret_market_item = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
        result = evaluate_market_item(
            canonical_items()[2],
            decision=DecisionResult(
                action="push",
                importance="high",
                reason="hard rule",
                need_llm_interpretation=True,
            ),
        )
    finally:
        market_flow.interpret_market_item = original_interpreter
    assert result.decision.action == "push"
    assert result.interpretation.llm_judgement == "failed"
    assert result.delivery_intent["should_deliver"] is True
    assert result.audit_json["interpretation_failed"] is True


def test_post_decision_finalization_updates_one_decision_result() -> None:
    result = evaluate_market_item(
        canonical_items()[0],
        decision=DecisionResult(action="push", importance="high", reason="hard rule"),
    )
    finalized = finalize_market_flow_result(
        result,
        final_push=False,
        importance="low",
        reason="Skeptic blocked",
        skeptic={"skeptic_verdict": "block"},
        blocked=True,
        storage_ref={"store_kind": "article_reviews", "item_id": "test-1"},
    )
    assert finalized.decision.action == "ignore"
    assert finalized.decision.skeptic["skeptic_verdict"] == "block"
    assert finalized.delivery_intent["should_deliver"] is False
    assert finalized.storage_ref["store_kind"] == "article_reviews"
    assert finalized.decision.audit_json["market_flow_finalization"]["initial_action"] == "push"


def test_existing_wrappers_delegate_to_shared_core() -> None:
    content_source = inspect.getsource(market_content_flow)
    event_source = inspect.getsource(market_event_flow)
    assert "from market_flow import" in content_source
    assert "from market_flow import" in event_source
    assert "from market_interpreter import interpret_market_item" not in content_source
    assert "from market_interpreter import interpret_market_item" not in event_source


def main() -> int:
    test_six_content_types_share_one_decision_and_interpretation_contract()
    test_interpretation_failure_preserves_deterministic_action()
    test_post_decision_finalization_updates_one_decision_result()
    test_existing_wrappers_delegate_to_shared_core()
    print("market flow checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
