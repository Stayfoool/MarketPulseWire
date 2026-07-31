#!/usr/bin/env python3
"""Regression checks for the single normalized market-information flow."""

from __future__ import annotations

import inspect

import alphabstract_monitor
import china_finance_media_monitor
import market_flow
import rss_monitor
import trendforce_page_monitor
import value_directory_monitor
from market_card_view import market_result_view
from market_item import DecisionResult, InterpretationResult
from settings_store import FIELDS_BY_KEY


def fake_interpretation(*args, **kwargs) -> InterpretationResult:
    return InterpretationResult(
        core_content="统一薄解读核心内容。",
        model="fake-model",
        prompt_version="market_interpreter_v2",
    )


def test_interpretation_cannot_override_decision_action() -> None:
    item = {
        "id": "macro-1",
        "title": "美国 CPI 大幅低于市场预期，2年期美债收益率下跌",
        "summary": "市场重新定价美联储降息路径。",
        "published_at": "2026-07-12T00:00:00+00:00",
    }
    original = market_flow.interpret_market_item
    try:
        market_flow.interpret_market_item = fake_interpretation
        flow_result = market_flow.evaluate_item(
            market_flow.normalize_market_item("cls_telegraph_api", item),
            item,
            DecisionResult(
                action="push",
                reason="大模型程度决策命中。",
            ),
            storage_ref={},
        )
        result_view = market_result_view(flow_result)
    finally:
        market_flow.interpret_market_item = original
    assert result_view["decision_result"]["action"] == "push"
    assert result_view["interpretation_result"]["model"] == "fake-model"
    assert result_view["interpretation_result"]["brief_reason"] == ""
    assert result_view["interpretation_result"]["related_targets"] == []
    assert "should_push" not in result_view["interpretation_result"]


def test_different_source_uses_same_decision_and_interpretation_contract() -> None:
    item = {
        "id": "rubin-1",
        "title": "NVIDIA announces Rubin rack-scale AI platform with liquid cooling",
        "summary": "NVIDIA details GPU systems, liquid cooling, and AI factory deployment.",
        "published_at": "2026-07-12T00:00:00+00:00",
    }
    original = market_flow.interpret_market_item
    try:
        market_flow.interpret_market_item = fake_interpretation
        flow_result = market_flow.evaluate_item(
            market_flow.normalize_market_item("nvidia_blog", item),
            item,
            DecisionResult(
                action="push",
                reason="大模型程度决策命中。",
            ),
            storage_ref={},
        )
        result_view = market_result_view(flow_result)
    finally:
        market_flow.interpret_market_item = original
    assert result_view["decision_result"]["action"] == "push"
    assert result_view["interpretation_result"]["core_content"] == "统一薄解读核心内容。"


def test_runtime_and_monitor_imports_use_one_unified_path() -> None:
    assert market_flow.process_market_item.__module__ == "market_flow"
    for module in (rss_monitor, china_finance_media_monitor, trendforce_page_monitor, alphabstract_monitor, value_directory_monitor):
        assert module.process_market_item.__module__ == "market_flow"
        source = inspect.getsource(module)
        for forbidden in (
            "content_runtime",
            "market_content_flow",
            "market_event_flow",
            "event_runtime",
            "article_gate",
            "official_news_gate",
            "event_pipeline",
        ):
            assert f"from {forbidden} import" not in source
            assert f"import {forbidden}" not in source
    assert "SURVEIL_MARKET_FLOW_DIRECT_PATH" not in FIELDS_BY_KEY
    assert "SURVEIL_CONTENT_DIRECT_PATH" not in FIELDS_BY_KEY
    assert "SURVEIL_EVENT_DIRECT_PATH" not in FIELDS_BY_KEY


def test_value_directory_uses_unified_runtime_after_private_enrichment() -> None:
    source = inspect.getsource(value_directory_monitor)
    assert "from article_gate import" not in source
    assert value_directory_monitor.process_market_item.__module__ == "market_flow"
    assert "process_market_item(" in source


def main() -> int:
    test_interpretation_cannot_override_decision_action()
    test_different_source_uses_same_decision_and_interpretation_contract()
    test_runtime_and_monitor_imports_use_one_unified_path()
    test_value_directory_uses_unified_runtime_after_private_enrichment()
    print("content flow checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
