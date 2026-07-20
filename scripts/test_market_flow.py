#!/usr/bin/env python3
"""Regression checks for the shared normalized market-flow core."""

from __future__ import annotations

import market_content_adapter
import market_event_adapter
import market_flow
import market_runtime
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


def test_five_content_types_share_one_decision_and_interpretation_contract() -> None:
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

    assert calls == {"decision": 5, "interpretation": 5}
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


def test_supplied_source_interpretation_skips_second_llm_call() -> None:
    original_interpreter = market_flow.interpret_market_item
    try:
        market_flow.interpret_market_item = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("source enrichment must not trigger a second interpretation LLM")
        )
        result = evaluate_market_item(
            NormalizedMarketItem(
                source="value_directory_ib_industry_macro",
                source_category="research_industry_media",
                collector="value_directory_monitor",
                content_type="research_index",
                title="瑞银亚太科技策略",
            ),
            decision=DecisionResult(action="push", importance="high", reason="硬规则命中。"),
            source_interpretation=InterpretationResult(
                core_content="瑞银认为智能体 AI 将继续推动半导体与硬件上行。",
                model="preview-model",
                prompt_version="value_directory_preview_v1",
            ),
            force_interpretation=True,
        )
    finally:
        market_flow.interpret_market_item = original_interpreter
    assert result.interpretation.model == "preview-model"
    assert result.audit_json["source_interpretation_supplied"] is True
    assert result.audit_json["interpreter_called"] is False


def test_value_directory_preview_failure_policy_finalizes_decision_action() -> None:
    result = evaluate_market_item(
        NormalizedMarketItem(
            source="value_directory_ib_stocks",
            source_category="research_industry_media",
            collector="value_directory_monitor",
            content_type="research_index",
            title="高盛-交易思路：做多中国人工智能价值链",
            raw={
                "value_directory_preview": {
                    "facts": {"status": "failed", "error": "OCR unavailable"},
                },
                "value_directory_policy": {
                    "preview_enabled": True,
                    "push_on_preview_failure": False,
                },
            },
        ),
        decision=DecisionResult(
            action="push",
            importance="high",
            reason="国际投行主题策略规则命中。",
            rule_hits=[{"rule_id": "international_bank_theme_strategy"}],
        ),
    )
    assert result.decision.action == "archive"
    assert result.decision.importance == "high"
    assert result.decision.rule_hits[0]["rule_id"] == "international_bank_theme_strategy"
    control = result.decision.audit_json["deterministic_source_control"]
    assert control["control_id"] == "value_directory_preview_failure_block"
    assert result.delivery_intent["should_deliver"] is False


def test_value_directory_enrichment_is_preserved_in_review_audit() -> None:
    original_interpreter = market_flow.interpret_market_item
    try:
        market_flow.interpret_market_item = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("preview facts should supply the interpretation")
        )
        review = market_content_adapter.review_article(
            "value_directory_ib_industry_macro",
            {
                "id": "value-flow-1",
                "title": "瑞银-亚太科技策略：Agentic AI to carry Semis&Hardware further",
                "summary": "瑞银认为智能体 AI 将继续推动半导体与硬件上行。",
                "raw": {
                    "value_directory_preview": {
                        "facts": {
                            "status": "ok",
                            "core_content": "瑞银认为智能体 AI 将继续推动半导体与硬件上行。",
                            "research_action": "overweight",
                            "targets": ["半导体", "AI 硬件"],
                            "key_points": ["半导体景气上行"],
                            "preview_basis": "visible_first_page_ocr",
                            "model": "preview-model",
                            "ocr": {"status": "ok", "text": "Agentic AI to carry Semis further"},
                        }
                    },
                    "value_directory_policy": {
                        "preview_enabled": True,
                        "push_on_preview_failure": True,
                    },
                },
            },
        )
    finally:
        market_flow.interpret_market_item = original_interpreter
    enrichment = review["raw"]["_source_enrichment"]
    facts = enrichment["value_directory_preview"]["facts"]
    assert facts["research_action"] == "overweight"
    assert facts["ocr"]["text"] == "Agentic AI to carry Semis further"
    assert review["raw"]["_market_flow_result"]["audit"]["source_interpretation_supplied"] is True


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


def test_post_decision_finalization_cannot_promote_non_push_action() -> None:
    result = evaluate_market_item(
        canonical_items()[0],
        decision=DecisionResult(action="archive", importance="low", reason="no deterministic match"),
    )
    finalized = finalize_market_flow_result(result, final_push=True, importance="high", reason="legacy flag")
    assert finalized.decision.action == "archive"
    audit = finalized.decision.audit_json["market_flow_finalization"]
    assert audit["promotion_rejected"] is True
    assert finalized.delivery_intent["should_deliver"] is False


class _DummyContext:
    def __enter__(self):
        return object()

    def __exit__(self, *_args):
        return False


def test_reprocessing_existing_review_preserves_pushed_marker() -> None:
    original_connect = market_runtime.connect_sqlite
    original_existing = market_runtime.article_review_exists
    original_module = market_runtime._selected_module
    original_deliver = market_runtime.deliver_article_review
    calls = {"processed": 0, "delivered": 0}

    class FakeModule:
        @staticmethod
        def process_article_review(*_args, **_kwargs):
            calls["processed"] += 1
            return {
                "importance": "high",
                "push_now": True,
                "reason": "规则重算命中。",
                "raw": {
                    "decision_result": DecisionResult(
                        action="push",
                        importance="high",
                        reason="规则重算命中。",
                    ).to_dict()
                },
            }

        @staticmethod
        def gate_lines(_review):
            return []

    try:
        market_runtime.connect_sqlite = lambda *_args, **_kwargs: _DummyContext()
        market_runtime.article_review_exists = lambda *_args, **_kwargs: {
            "pushed_at": "2026-07-13T00:00:00+00:00",
            "raw": {},
        }
        market_runtime._selected_module = lambda _kind: FakeModule

        def fake_deliver(_source, _item, review, **_kwargs):
            calls["delivered"] += 1
            assert review["pushed_at"] == "2026-07-13T00:00:00+00:00"
            assert _kwargs["decision"].action == "push"
            return "skipped"

        market_runtime.deliver_article_review = fake_deliver
        item = NormalizedMarketItem(
            source="value_directory_ib_stocks",
            source_category="research_industry_media",
            collector="value_directory_monitor",
            content_type="research_index",
            title="高盛研报",
            raw={"id": "reprocess-pushed"},
        )
        outcome = market_runtime.process_market_item(
            item,
            {"id": "reprocess-pushed", "title": "高盛研报"},
            store_kind="article",
            reprocess_existing=True,
        )
    finally:
        market_runtime.connect_sqlite = original_connect
        market_runtime.article_review_exists = original_existing
        market_runtime._selected_module = original_module
        market_runtime.deliver_article_review = original_deliver
    assert calls == {"processed": 1, "delivered": 1}
    assert outcome.inserted is False
    assert outcome.delivery_status == "skipped"


def test_existing_legacy_review_without_decision_fails_closed() -> None:
    original_connect = market_runtime.connect_sqlite
    original_existing = market_runtime.article_review_exists
    try:
        market_runtime.connect_sqlite = lambda *_args, **_kwargs: _DummyContext()
        market_runtime.article_review_exists = lambda *_args, **_kwargs: {
            "importance": "high",
            "push_now": True,
            "reason": "legacy push flag",
            "raw": {},
            "pushed_at": "",
        }
        item = NormalizedMarketItem(
            source="cls_telegraph_api",
            source_category="news_media",
            collector="news_collector",
            content_type="article",
            title="旧记录",
            raw={"id": "legacy-no-decision"},
        )
        outcome = market_runtime.process_market_item(
            item,
            {"id": "legacy-no-decision", "title": "旧记录"},
            store_kind="article",
        )
    finally:
        market_runtime.connect_sqlite = original_connect
        market_runtime.article_review_exists = original_existing
    assert outcome.flow_result.decision.action == "archive"
    assert outcome.flow_result.decision.audit_json["contract_error"] == "missing_decision_result"
    assert outcome.delivery_status == "missing_decision"


def test_runtime_comparison_receives_the_exact_item_before_delivery() -> None:
    original_connect = market_runtime.connect_sqlite
    original_existing = market_runtime.article_review_exists
    original_module = market_runtime._selected_module
    original_record = market_runtime._record_rule_comparison
    original_deliver = market_runtime.deliver_article_review
    order: list[str] = []
    observed: list[NormalizedMarketItem] = []

    class FakeModule:
        @staticmethod
        def process_article_review(*_args, **_kwargs):
            return {
                "raw": {
                    "decision_result": DecisionResult(
                        action="daily",
                        importance="medium",
                        reason="现有规则结果",
                    ).to_dict()
                }
            }

        @staticmethod
        def gate_lines(_review):
            return []

    item = NormalizedMarketItem(
        source="wallstreetcn_news",
        source_category="news_media",
        collector="news_collector",
        content_type="article",
        title="DRAM价格上涨",
        full_text="完整生产正文。",
        raw={"id": "same-item-1"},
    )
    try:
        market_runtime.connect_sqlite = lambda *_args, **_kwargs: _DummyContext()
        market_runtime.article_review_exists = lambda *_args, **_kwargs: None
        market_runtime._selected_module = lambda _kind: FakeModule

        def fake_record(normalized, flow_result, storage_ref):
            order.append("comparison")
            observed.append(normalized)
            assert flow_result.decision.action == "daily"
            assert storage_ref["item_id"] == "same-item-1"

        def fake_deliver(*_args, **_kwargs):
            order.append("delivery")
            return "skipped"

        market_runtime._record_rule_comparison = fake_record
        market_runtime.deliver_article_review = fake_deliver
        outcome = market_runtime.process_market_item(
            item,
            {"id": "same-item-1", "title": item.title, "full_text": item.full_text},
            store_kind="article",
        )
    finally:
        market_runtime.connect_sqlite = original_connect
        market_runtime.article_review_exists = original_existing
        market_runtime._selected_module = original_module
        market_runtime._record_rule_comparison = original_record
        market_runtime.deliver_article_review = original_deliver
    assert observed == [item]
    assert order == ["comparison", "delivery"]
    assert outcome.flow_result.item is item


def test_analyzed_event_compares_before_delivery_and_baseline_does_not() -> None:
    original_module = market_runtime._selected_module
    original_record = market_runtime._record_rule_comparison
    order: list[str] = []

    class FakeEventModule:
        next_id = 40

        @classmethod
        def upsert_event(cls, *_args, **_kwargs):
            cls.next_id += 1
            return cls.next_id, True

        @staticmethod
        def analyze_event(*_args, **_kwargs):
            return {
                "analysis": {
                    "decision_result": DecisionResult(
                        action="push",
                        importance="high",
                        reason="现有事件规则结果",
                    ).to_dict()
                }
            }

        @staticmethod
        def maybe_deliver_event(*_args, **_kwargs):
            order.append("delivery")
            return "skipped"

    item = NormalizedMarketItem(
        source="sina_flash",
        source_category="news_media",
        collector="sina_flash",
        content_type="flash",
        title="美国CPI低于预期",
        full_text="美国CPI低于预期，市场调整降息预期。",
        raw={"source_event_id": "event-1"},
    )
    try:
        market_runtime._selected_module = lambda _kind: FakeEventModule

        def fake_record(normalized, flow_result, _storage_ref):
            order.append("comparison")
            assert normalized is item
            assert flow_result.decision.action == "push"

        market_runtime._record_rule_comparison = fake_record
        baseline = market_runtime.process_market_item(
            item,
            {"source_event_id": "baseline-1", "title": item.title},
            store_kind="event",
            baseline_only=True,
        )
        assert baseline.delivery_status == "baseline"
        assert order == []

        analyzed = market_runtime.process_market_item(
            item,
            {"source_event_id": "event-1", "title": item.title},
            store_kind="event",
        )
    finally:
        market_runtime._selected_module = original_module
        market_runtime._record_rule_comparison = original_record
    assert analyzed.flow_result.item is item
    assert order == ["comparison", "delivery"]


def main() -> int:
    test_five_content_types_share_one_decision_and_interpretation_contract()
    test_interpretation_failure_preserves_deterministic_action()
    test_supplied_source_interpretation_skips_second_llm_call()
    test_value_directory_preview_failure_policy_finalizes_decision_action()
    test_value_directory_enrichment_is_preserved_in_review_audit()
    test_post_decision_finalization_updates_one_decision_result()
    test_post_decision_finalization_cannot_promote_non_push_action()
    test_reprocessing_existing_review_preserves_pushed_marker()
    test_existing_legacy_review_without_decision_fails_closed()
    test_runtime_comparison_receives_the_exact_item_before_delivery()
    test_analyzed_event_compares_before_delivery_and_baseline_does_not()
    print("market flow checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
