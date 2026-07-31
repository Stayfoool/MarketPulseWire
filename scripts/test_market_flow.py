#!/usr/bin/env python3
"""Regression checks for the shared normalized market-flow core."""

from __future__ import annotations

from dataclasses import replace
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import market_flow
from llm_production_decision import ProductionLLMInsufficientEvidence
from market_flow import evaluate_market_item
from market_db import init_db
from market_item import AdmissionEvidence, AdmissionResult, DecisionResult, InterpretationResult, MarketFlowResult, NormalizedMarketItem
from market_store import processing_failure_status, record_article_delivery, record_production_admission


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
    return InterpretationResult(
        core_content="统一市场流解读。",
        model="fake-model",
        prompt_version="market_interpreter_v2",
    )


def test_five_content_types_share_one_decision_and_interpretation_contract() -> None:
    original_interpreter = market_flow.interpret_market_item
    calls = {"interpretation": 0}
    decision = DecisionResult(
        action="push",
        importance="high",
        reason="大模型程度决策命中。",
        brief_reason="大模型程度决策命中。",
        rule_hits=[{"rule_id": "canonical_rule"}],
        need_llm_interpretation=True,
    )

    def fake_interpreter(*args, **kwargs):
        calls["interpretation"] += 1
        return fake_interpretation(*args, **kwargs)

    try:
        market_flow.interpret_market_item = fake_interpreter
        results = [evaluate_market_item(item, decision=decision) for item in canonical_items()]
    finally:
        market_flow.interpret_market_item = original_interpreter

    assert calls == {"interpretation": 5}
    assert all(isinstance(result, MarketFlowResult) for result in results)
    assert all(result.decision.action == "push" for result in results)
    assert all(result.delivery_intent["should_deliver"] is True for result in results)
    assert all("should_push" not in result.interpretation.to_dict() for result in results)
    sina = next(result for result in results if result.item.source == "sina_flash")
    assert sina.item.source_category == "news_media"
    assert sina.item.content_type == "flash"


def test_interpretation_failure_preserves_decision_action() -> None:
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


def test_value_directory_enrichment_is_preserved_in_normalized_item() -> None:
    raw_item = {
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
    }
    original_interpreter = market_flow.interpret_market_item
    try:
        market_flow.interpret_market_item = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("preview facts should supply the interpretation")
        )
        flow_result = market_flow.evaluate_content_item(
            market_flow.normalize_market_item(
                "value_directory_ib_industry_macro",
                raw_item,
                store_kind="article",
            ),
            raw_item,
            DecisionResult(action="daily", importance="medium", reason="模型判断为日报。"),
            official=False,
            storage_ref={},
        )
    finally:
        market_flow.interpret_market_item = original_interpreter
    enrichment = flow_result.item.raw
    facts = enrichment["value_directory_preview"]["facts"]
    assert facts["research_action"] == "overweight"
    assert facts["ocr"]["text"] == "Agentic AI to carry Semis further"
    assert flow_result.audit_json["source_interpretation_supplied"] is True



def admitted() -> AdmissionResult:
    return AdmissionResult(
        status="admitted",
        reason_code="semiconductor_ai_match",
        matched_families=("semiconductor_ai",),
        evidence=(AdmissionEvidence("semiconductor_ai", "term", "HBM"),),
        config_version="test-v1",
    )


def test_production_content_runtime_uses_unified_result_for_existing_and_delivery() -> None:
    original_deliver = market_flow.deliver_article_review
    original_interpreter = market_flow.interpret_market_item
    original_prepare = market_flow.prepare_item_for_decision
    original_decider = market_flow.decide_market_item_with_llm
    calls = {"evaluate": 0, "deliver": 0}
    decision = DecisionResult(action="push", importance="high", reason="HBM扩产")

    try:
        def fake_decide(*_args, **_kwargs):
            calls["evaluate"] += 1
            return decision

        market_flow.interpret_market_item = lambda *_args, **_kwargs: InterpretationResult(
            core_content="HBM扩产"
        )
        market_flow.prepare_item_for_decision = lambda value: value
        market_flow.decide_market_item_with_llm = fake_decide

        def fake_deliver(*_args, **kwargs):
            calls["deliver"] += 1
            assert kwargs["already_sent"] is False
            record_article_delivery(
                kwargs["market_item_id"],
                kwargs["market_review_id"],
                status="sent",
                decision_action="push",
                db_path=kwargs["db_path"],
            )
            return "sent"

        market_flow.deliver_article_review = fake_deliver
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "runtime.sqlite3"
            init_db(db_path).close()
            item = NormalizedMarketItem(
                source="test_news",
                source_category="news_media",
                content_type="article",
                title="HBM扩产",
                url="https://example.com/hbm",
                raw={"id": "hbm-1"},
            )
            raw_item = {"id": "hbm-1", "title": item.title, "url": item.url}
            item_id, review_id = record_production_admission(item, admitted(), db_path=db_path)
            first = market_flow.process_market_item(
                item,
                raw_item,
                store_kind="article",
                db_path=db_path,
                production_admission=admitted(),
                production_portfolio=object(),
                market_item_id=item_id,
                market_review_id=review_id,
            )
            repeated_ids = record_production_admission(item, admitted(), db_path=db_path)
            second = market_flow.process_market_item(
                item,
                raw_item,
                store_kind="article",
                db_path=db_path,
                production_admission=admitted(),
                production_portfolio=object(),
                market_item_id=repeated_ids[0],
                market_review_id=repeated_ids[1],
            )
            with sqlite3.connect(db_path) as conn:
                assert conn.execute("SELECT COUNT(*) FROM market_reviews").fetchone()[0] == 1
                assert conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='article_reviews'"
                ).fetchone()[0] == 0
                assert conn.execute("SELECT COUNT(*) FROM market_item_aliases").fetchone()[0] == 1
                delivery = conn.execute(
                    "SELECT market_item_id,market_review_id,status,decision_action FROM deliveries"
                ).fetchone()
            assert delivery == (item_id, review_id, "sent", "push")
            assert first.inserted is True
            assert second.inserted is False
            assert second.delivery_status == "existing"
    finally:
        market_flow.deliver_article_review = original_deliver
        market_flow.interpret_market_item = original_interpreter
        market_flow.prepare_item_for_decision = original_prepare
        market_flow.decide_market_item_with_llm = original_decider
    assert calls == {"evaluate": 1, "deliver": 1}


def test_production_event_runtime_completes_only_unified_result() -> None:
    original_interpreter = market_flow.interpret_market_item
    original_prepare = market_flow.prepare_item_for_decision
    original_decider = market_flow.decide_market_item_with_llm
    calls = {"analyze": 0}
    decision = DecisionResult(action="daily", importance="medium", reason="公告跟踪")

    def fake_decide(*_args, **_kwargs):
        calls["analyze"] += 1
        return decision

    try:
        market_flow.interpret_market_item = lambda *_args, **_kwargs: InterpretationResult(
            core_content="公告跟踪"
        )
        market_flow.prepare_item_for_decision = lambda value: value
        market_flow.decide_market_item_with_llm = fake_decide
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "event-runtime.sqlite3"
            init_db(db_path).close()
            raw_event = {
                "source": "sina_flash",
                "source_event_id": "event-unified-1",
                "event_type": "flash",
                "title": "公告跟踪",
                "summary": "公告内容",
                "raw": {"source_event_id": "event-unified-1"},
            }
            item = market_flow.normalize_market_item("sina_flash", raw_event, store_kind="event")
            item_id, review_id = record_production_admission(
                item, admitted(), db_path=db_path, task="portfolio_event"
            )
            first = market_flow.process_market_item(
                item,
                raw_event,
                store_kind="event",
                db_path=db_path,
                deliver=False,
                production_admission=admitted(),
                production_portfolio=object(),
                market_item_id=item_id,
                market_review_id=review_id,
            )
            second = market_flow.process_market_item(
                item,
                raw_event,
                store_kind="event",
                db_path=db_path,
                deliver=False,
                production_admission=admitted(),
                production_portfolio=object(),
                market_item_id=item_id,
                market_review_id=review_id,
            )
            with sqlite3.connect(db_path) as conn:
                unified = conn.execute(
                    "SELECT review_status,decision_action,legacy_store_kind FROM market_reviews"
                ).fetchone()
                assert conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='event_analyses'"
                ).fetchone()[0] == 0
                assert conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='events'"
                ).fetchone()[0] == 0
                assert conn.execute("SELECT COUNT(*) FROM market_item_aliases").fetchone()[0] == 1
            assert unified == ("succeeded", "daily", None)
            assert first.inserted is True
            assert second.inserted is False
            assert second.delivery_status == "existing"
    finally:
        market_flow.interpret_market_item = original_interpreter
        market_flow.prepare_item_for_decision = original_prepare
        market_flow.decide_market_item_with_llm = original_decider
    assert calls == {"analyze": 1}


def test_production_official_runtime_uses_only_unified_result() -> None:
    original_interpreter = market_flow.interpret_market_item
    original_prepare = market_flow.prepare_item_for_decision
    original_decider = market_flow.decide_market_item_with_llm
    decision = DecisionResult(action="archive", importance="low", reason="例行官网更新")

    try:
        market_flow.interpret_market_item = lambda *_args, **_kwargs: InterpretationResult(
            core_content="例行官网更新"
        )
        market_flow.prepare_item_for_decision = lambda value: value
        market_flow.decide_market_item_with_llm = lambda *_args, **_kwargs: decision
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "official-runtime.sqlite3"
            init_db(db_path).close()
            item = NormalizedMarketItem(
                source="nvidia_blog",
                source_category="official_company",
                content_type="official_news",
                title="官网例行更新",
                url="https://example.com/official",
                raw={"id": "official-1"},
            )
            raw_item = {"id": "official-1", "title": item.title, "url": item.url}
            item_id, review_id = record_production_admission(item, admitted(), db_path=db_path)
            outcome = market_flow.process_market_item(
                item,
                raw_item,
                store_kind="official",
                db_path=db_path,
                deliver=False,
                production_admission=admitted(),
                production_portfolio=object(),
                market_item_id=item_id,
                market_review_id=review_id,
            )
            with sqlite3.connect(db_path) as conn:
                unified = conn.execute(
                    "SELECT review_status,decision_action,legacy_store_kind FROM market_reviews"
                ).fetchone()
                assert conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='official_news_reviews'"
                ).fetchone()[0] == 0
                alias = conn.execute(
                    "SELECT item_kind,source,legacy_item_id FROM market_item_aliases"
                ).fetchone()
            assert unified == ("succeeded", "archive", None)
            assert alias == ("official", "nvidia_blog", "official-1")
            assert outcome.inserted is True
    finally:
        market_flow.interpret_market_item = original_interpreter
        market_flow.prepare_item_for_decision = original_prepare
        market_flow.decide_market_item_with_llm = original_decider


def test_production_llm_failure_retries_same_review_without_delivery() -> None:
    original_interpreter = market_flow.interpret_market_item
    original_decider = market_flow.decide_market_item_with_llm
    calls = {"evaluate": 0}
    decision = DecisionResult(
        action="daily",
        importance="medium",
        reason="模型固定响应",
        audit_json={"production_authority": True},
    )

    def successful_decide(*_args, **_kwargs):
        calls["evaluate"] += 1
        return decision

    try:
        market_flow.interpret_market_item = lambda *_args, **_kwargs: InterpretationResult(
            core_content="模型固定响应"
        )
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "llm-retry.sqlite3"
            init_db(db_path).close()
            item = NormalizedMarketItem(
                source="test_news",
                source_category="news_media",
                content_type="article",
                title="模型失败后重试",
                url="https://example.com/retry",
                raw={"id": "llm-retry-1"},
            )
            raw_item = {"id": "llm-retry-1", "title": item.title, "url": item.url}
            item_id, review_id = record_production_admission(item, admitted(), db_path=db_path)
            market_flow.decide_market_item_with_llm = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("model unavailable")
            )
            try:
                market_flow.process_market_item(
                    item,
                    raw_item,
                    store_kind="article",
                    db_path=db_path,
                    deliver=False,
                    production_admission=admitted(),
                    production_portfolio=object(),
                    market_item_id=item_id,
                    market_review_id=review_id,
                )
            except RuntimeError as exc:
                assert "model unavailable" in str(exc)
            else:
                raise AssertionError("production model failure must fail the review")
            failed_ids = record_production_admission(item, admitted(), db_path=db_path)
            assert failed_ids == (item_id, review_id)
            with sqlite3.connect(db_path) as conn:
                assert conn.execute("SELECT review_status FROM market_reviews WHERE id=?", (review_id,)).fetchone()[0] == "failed_retryable"
                assert conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 0
                assert conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='article_reviews'"
                ).fetchone()[0] == 0

            market_flow.decide_market_item_with_llm = successful_decide
            outcome = market_flow.process_market_item(
                item,
                raw_item,
                store_kind="article",
                db_path=db_path,
                deliver=False,
                production_admission=admitted(),
                production_portfolio=object(),
                market_item_id=item_id,
                market_review_id=review_id,
            )
            with sqlite3.connect(db_path) as conn:
                assert conn.execute("SELECT review_status FROM market_reviews WHERE id=?", (review_id,)).fetchone()[0] == "succeeded"
                assert conn.execute("SELECT COUNT(*) FROM market_reviews").fetchone()[0] == 1
            assert outcome.market_review_id == review_id
            assert outcome.flow_result.decision.action == "daily"
    finally:
        market_flow.interpret_market_item = original_interpreter
        market_flow.decide_market_item_with_llm = original_decider
    assert calls == {"evaluate": 1}


def test_production_uncertain_terminates_review_without_delivery() -> None:
    original_decider = market_flow.decide_market_item_with_llm
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "llm-insufficient.sqlite3"
        init_db(db_path).close()
        item = NormalizedMarketItem(
            source="test_news",
            source_category="news_media",
            content_type="article",
            title="证据不足的文章",
            url="https://example.com/insufficient",
            raw={"id": "llm-insufficient-1"},
        )
        raw_item = {"id": "llm-insufficient-1", "title": item.title, "url": item.url}
        item_id, review_id = record_production_admission(item, admitted(), db_path=db_path)
        market_flow.decide_market_item_with_llm = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ProductionLLMInsufficientEvidence("valid uncertain result")
        )
        try:
            market_flow.process_market_item(
                item,
                raw_item,
                store_kind="article",
                db_path=db_path,
                deliver=True,
                production_admission=admitted(),
                production_portfolio=object(),
                market_item_id=item_id,
                market_review_id=review_id,
            )
        except ProductionLLMInsufficientEvidence:
            pass
        else:
            raise AssertionError("valid uncertain must stop before interpretation and delivery")
        finally:
            market_flow.decide_market_item_with_llm = original_decider
        assert record_production_admission(item, admitted(), db_path=db_path) == (item_id, review_id)
        changed_admission = AdmissionResult(
            status="admitted",
            reason_code="holding_match",
            matched_families=("holding",),
            evidence=(AdmissionEvidence("holding", "entity", "测试公司"),),
            config_version="changed-v2",
        )
        assert record_production_admission(item, changed_admission, db_path=db_path) == (item_id, review_id)
        assert record_production_admission(
            item,
            changed_admission,
            db_path=db_path,
            force_new=True,
        ) == (item_id, review_id)
        with sqlite3.connect(db_path) as conn:
            assert conn.execute(
                "SELECT review_status,decision_action,decision_json,interpretation_json FROM market_reviews WHERE id=?",
                (review_id,),
            ).fetchone() == ("insufficient_evidence", None, None, None)
            assert conn.execute(
                "SELECT processing_status FROM market_items WHERE id=?", (item_id,)
            ).fetchone()[0] == "insufficient_evidence"
            assert conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 0
        repeated_calls = 0

        def unexpected_decider(*_args, **_kwargs):
            nonlocal repeated_calls
            repeated_calls += 1
            raise AssertionError("terminal evidence insufficiency must not call the model again")

        market_flow.decide_market_item_with_llm = unexpected_decider
        try:
            market_flow.process_market_item(
                item,
                raw_item,
                store_kind="article",
                db_path=db_path,
                deliver=False,
                production_admission=admitted(),
                production_portfolio=object(),
                market_item_id=item_id,
                market_review_id=review_id,
                reprocess_existing=True,
            )
        except Exception as exc:
            assert processing_failure_status(exc) == "insufficient_evidence"
        else:
            raise AssertionError("terminal review must remain closed")
        finally:
            market_flow.decide_market_item_with_llm = original_decider
        assert repeated_calls == 0


def test_event_uncertain_preserves_terminal_status_through_processing_wrapper() -> None:
    original_decider = market_flow.decide_market_item_with_llm
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "event-insufficient.sqlite3"
        init_db(db_path).close()
        item = NormalizedMarketItem(
            source="sina_flash",
            source_category="news_media",
            content_type="flash",
            title="证据不足的快讯",
            raw={"source_event_id": "flash-insufficient-1"},
        )
        raw_item = {
            "source": "sina_flash",
            "source_event_id": "flash-insufficient-1",
            "event_type": "flash",
            "title": item.title,
        }
        item_id, review_id = record_production_admission(
            item,
            admitted(),
            db_path=db_path,
            task="sina_flash_portfolio",
        )
        market_flow.decide_market_item_with_llm = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ProductionLLMInsufficientEvidence("valid uncertain result")
        )
        try:
            market_flow.process_market_item(
                item,
                raw_item,
                store_kind="event",
                task="sina_flash_portfolio",
                db_path=db_path,
                production_admission=admitted(),
                production_portfolio=object(),
                market_item_id=item_id,
                market_review_id=review_id,
            )
        except market_flow.MarketItemProcessingError as exc:
            assert processing_failure_status(exc) == "insufficient_evidence"
        else:
            raise AssertionError("event uncertain must retain the terminal processing status")
        finally:
            market_flow.decide_market_item_with_llm = original_decider
        with sqlite3.connect(db_path) as conn:
            assert conn.execute(
                "SELECT review_status,decision_action FROM market_reviews WHERE id=?", (review_id,)
            ).fetchone() == ("insufficient_evidence", None)
            assert conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 0


def main() -> int:
    test_five_content_types_share_one_decision_and_interpretation_contract()
    test_interpretation_failure_preserves_decision_action()
    test_supplied_source_interpretation_skips_second_llm_call()
    test_value_directory_enrichment_is_preserved_in_normalized_item()
    test_production_content_runtime_uses_unified_result_for_existing_and_delivery()
    test_production_event_runtime_completes_only_unified_result()
    test_production_official_runtime_uses_only_unified_result()
    test_production_llm_failure_retries_same_review_without_delivery()
    test_production_uncertain_terminates_review_without_delivery()
    test_event_uncertain_preserves_terminal_status_through_processing_wrapper()
    print("market flow checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
