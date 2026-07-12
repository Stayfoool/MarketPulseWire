#!/usr/bin/env python3
"""Regression checks for unified event decision and interpretation flow."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import event_pipeline
import market_event_flow
from market_db import init_db
from market_item import InterpretationResult


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
    assert event_pipeline.should_push_analysis(archive) is False
    assert event_pipeline.analysis_record_fields(archive)[4] == 0
    assert event_pipeline.should_push_analysis(push) is True
    assert event_pipeline.analysis_record_fields(push)[4] == 1


def test_legacy_analysis_without_decision_result_still_uses_compatibility_helper() -> None:
    assert event_pipeline.should_push_analysis(
        {"importance": "medium", "push_decision": {"should_push": True}}
    ) is True


def test_analyze_event_writes_interpretation_result_and_legacy_fields() -> None:
    original = market_event_flow.interpret_market_item

    def fake_interpret(*args, **kwargs):
        decision = args[1]
        assert decision.action == "push"
        assert decision.rule_hits[0]["rule_id"] == "macro_policy_line"
        return InterpretationResult(
            core_content="美国 CPI 大幅低于预期，美债收益率下行。",
            brief_reason="宏观政策线硬规则命中。",
            related_targets=[{"name": "A股风险偏好", "relation": "宏观线"}],
            model="fake-model",
            prompt_version="market_interpreter_v1",
        )

    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        event_id, _ = event_pipeline.upsert_event(
            {
                "source": "sina_flash",
                "source_event_id": "macro-1",
                "event_type": "flash_news",
                "title": "美国 CPI 大幅低于市场预期，2年期美债收益率大跌",
                "summary": "市场重新定价美联储降息路径。",
                "published_at": "2026-07-12T12:00:00+00:00",
                "symbols": [],
                "raw": {"source_event_id": "macro-1"},
            },
            db_path,
        )
        try:
            market_event_flow.interpret_market_item = fake_interpret
            analysis = event_pipeline.analyze_event(event_id, db_path=db_path)
        finally:
            market_event_flow.interpret_market_item = original

        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT model, importance, should_push, analysis_json FROM event_analyses WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        finally:
            conn.close()
    assert analysis["_decision_result"]["action"] == "push"
    assert analysis["core_content"].startswith("美国 CPI")
    assert analysis["related_holdings"][0]["name"] == "A股风险偏好"
    assert analysis["_interpretation_result"]["model"] == "fake-model"
    assert row[:3] == ("fake-model", "high", 1)
    stored = json.loads(row[3])
    assert stored["_interpretation_result"]["prompt_version"] == "market_interpreter_v1"


def test_event_entry_applies_international_bank_theme_decision() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        analysis = event_pipeline.apply_event_rules_to_analysis(
            {
                "source": "sina_flash",
                "event_type": "flash_news",
                "title": "高盛发布投资策略：做多中国 AI 价值链",
                "summary": (
                    "高盛认为中国 AI 公司市值与市场空间严重错配，资金从韩国 AI 交易出现结构性资本轮动，"
                    "建议做多中国 AI 价值链，覆盖半导体、算力和数据中心电力。"
                ),
                "full_text": "",
                "url": "",
                "published_at": "2026-07-12T12:00:00+00:00",
                "symbols_json": "[]",
                "raw_json": "{}",
            },
            {},
            db_path=db_path,
        )
    assert analysis["_decision_result"]["action"] == "push"
    assert analysis["_decision_result"]["rule_hits"][0]["rule_id"] == "international_bank_theme_strategy"


def test_event_entry_applies_direct_holding_rating_target_decision() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO portfolio_holdings
                    (symbol, name, full_name, aliases_json, enabled, raw_json, updated_at)
                VALUES (?, ?, ?, ?, 1, '{}', ?)
                """,
                (
                    "688017.SH",
                    "绿的谐波",
                    "苏州绿的谐波传动科技股份有限公司",
                    "[]",
                    "2026-07-12T00:00:00+00:00",
                ),
            )
        analysis = event_pipeline.apply_event_rules_to_analysis(
            {
                "source": "sina_stock_news",
                "event_type": "portfolio_news",
                "title": "高盛看衰绿的谐波：488元股价 vs 138元目标价",
                "summary": "高盛给予绿的谐波显著低于现价的目标价。",
                "full_text": "",
                "url": "",
                "published_at": "2026-07-12T12:00:00+00:00",
                "symbols_json": '["688017.SH"]',
                "raw_json": "{}",
            },
            {},
            db_path=db_path,
        )
    assert analysis["_decision_result"]["action"] == "push"
    assert analysis["_decision_result"]["rule_hits"][0]["rule_id"] == "investment_bank_rating_target_direct_holding"


def main() -> int:
    test_decision_result_action_precedes_legacy_push_fields()
    test_legacy_analysis_without_decision_result_still_uses_compatibility_helper()
    test_analyze_event_writes_interpretation_result_and_legacy_fields()
    test_event_entry_applies_international_bank_theme_decision()
    test_event_entry_applies_direct_holding_rating_target_decision()
    print("event pipeline convergence checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
