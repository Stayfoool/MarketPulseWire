#!/usr/bin/env python3
"""Regression checks for unified event decision and interpretation flow."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import market_event_adapter
import market_flow
from market_db import init_db
from market_item import DecisionResult, InterpretationResult, NormalizedMarketItem


def test_decision_result_action_precedes_legacy_push_fields() -> None:
    archive = {
        "importance": "high",
        "push_decision": {"should_push": True},
        "_decision_result": {"action": "archive", "importance": "high"},
    }
    push = {
        "importance": "low",
        "push_decision": {"should_push": False},
        "_decision_result": {"action": "push", "importance": "high"},
    }
    assert market_event_adapter.should_push_analysis(archive) is False
    assert market_event_adapter.analysis_record_fields(archive)[4] == 0
    assert market_event_adapter.should_push_analysis(push) is True
    assert market_event_adapter.analysis_record_fields(push)[4] == 1


def test_legacy_analysis_without_decision_result_cannot_push() -> None:
    assert market_event_adapter.should_push_analysis(
        {"importance": "medium", "push_decision": {"should_push": True}}
    ) is False


def test_analyze_event_returns_interpretation_without_writing_legacy_tables() -> None:
    original = market_flow.interpret_market_item

    def fake_interpret(*args, **kwargs):
        decision = args[1]
        assert decision.action == "push"
        assert decision.rule_hits[0]["rule_id"] == "macro_policy_line"
        return InterpretationResult(
            core_content="美国 CPI 大幅低于预期，美债收益率下行。",
            model="fake-model",
            prompt_version="market_interpreter_v2",
        )

    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        item = NormalizedMarketItem(
            source="sina_flash",
            source_category="news_media",
            content_type="flash_news",
            title="美国 CPI 大幅低于市场预期，2年期美债收益率大跌",
            summary="市场重新定价美联储降息路径。",
            published_at="2026-07-12T12:00:00+00:00",
            raw={"source_event_id": "macro-1"},
        )
        try:
            market_flow.interpret_market_item = fake_interpret
            flow_result = market_flow.evaluate_event_item(
                item,
                DecisionResult(
                    action="push",
                    importance="high",
                    reason="大模型程度决策命中。",
                    rule_hits=[{"rule_id": "macro_policy_line"}],
                ),
                task="portfolio_event",
                db_path=db_path,
                storage_ref={},
            )
            analysis = market_event_adapter.project_event_analysis(flow_result)
        finally:
            market_flow.interpret_market_item = original

        with sqlite3.connect(db_path) as conn:
            legacy_tables = {
                str(row[0])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
    assert analysis["_decision_result"]["action"] == "push"
    assert analysis["core_content"].startswith("美国 CPI")
    assert "brief_reason" not in analysis
    assert "related_holdings" not in analysis
    assert analysis["_interpretation_result"]["model"] == "fake-model"
    assert analysis["_interpretation_result"]["brief_reason"] == ""
    assert analysis["_interpretation_result"]["related_targets"] == []
    assert not legacy_tables.intersection({"events", "event_analyses"})


def test_event_entry_without_decision_fails_closed() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        item = NormalizedMarketItem(
            source="sina_flash",
            content_type="flash_news",
            title="测试事件",
            summary="测试摘要。",
            published_at="2026-07-12T12:00:00+00:00",
            raw={"source_event_id": "missing-decision"},
        )
        try:
            market_flow.evaluate_event_item(
                item,
                None,
                task="portfolio_event",
                db_path=db_path,
                storage_ref={},
            )
        except RuntimeError as exc:
            assert "决策结果缺失" in str(exc)
        else:
            raise AssertionError("event processing must fail closed without DecisionResult")


def main() -> int:
    test_decision_result_action_precedes_legacy_push_fields()
    test_legacy_analysis_without_decision_result_cannot_push()
    test_analyze_event_returns_interpretation_without_writing_legacy_tables()
    test_event_entry_without_decision_fails_closed()
    print("event pipeline convergence checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
