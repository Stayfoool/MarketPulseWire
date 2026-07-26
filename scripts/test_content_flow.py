#!/usr/bin/env python3
"""Regression checks for unified article/news/official content flow."""

from __future__ import annotations

import inspect

import alphabstract_monitor
import china_finance_media_monitor
import market_flow
import market_content_adapter
import market_runtime
import rss_monitor
import trendforce_page_monitor
import value_directory_monitor
from market_item import DecisionResult, InterpretationResult
from settings_store import FIELDS_BY_KEY


def fake_interpretation(*args, **kwargs) -> InterpretationResult:
    decision = args[1]
    return InterpretationResult(
        core_content="统一薄解读核心内容。",
        brief_reason=decision.brief_reason or decision.reason or "规则上下文解读。",
        related_targets=[{"name": "A股风险偏好", "relation": "规则给定关系"}],
        model="fake-model",
        prompt_version="market_interpreter_v1",
    )


def test_article_interpretation_cannot_override_decision_action() -> None:
    original = market_flow.interpret_market_item
    try:
        market_flow.interpret_market_item = fake_interpretation
        review = market_content_adapter.evaluate_article_review(
            None,
            "cls_telegraph_api",
            {
                "id": "macro-1",
                "title": "美国 CPI 大幅低于市场预期，2年期美债收益率下跌",
                "summary": "市场重新定价美联储降息路径。",
                "published_at": "2026-07-12T00:00:00+00:00",
            },
            decision=DecisionResult(
                action="push",
                importance="high",
                reason="大模型程度决策命中。",
                need_llm_interpretation=True,
            ),
        )
    finally:
        market_flow.interpret_market_item = original
    assert review["push_now"] is True
    assert review["raw"]["decision_result"]["action"] == "push"
    assert review["raw"]["_interpretation_result"]["model"] == "fake-model"
    assert "should_push" not in review["raw"]["_interpretation_result"]


def test_official_flow_uses_same_decision_and_interpretation_contract() -> None:
    original = market_flow.interpret_market_item
    try:
        market_flow.interpret_market_item = fake_interpretation
        review = market_content_adapter.evaluate_official_review(
            None,
            "nvidia_blog",
            {
                "id": "rubin-1",
                "title": "NVIDIA announces Rubin rack-scale AI platform with liquid cooling",
                "summary": "NVIDIA details GPU systems, liquid cooling, and AI factory deployment.",
                "published_at": "2026-07-12T00:00:00+00:00",
            },
            decision=DecisionResult(
                action="push",
                importance="high",
                reason="大模型程度决策命中。",
                need_llm_interpretation=True,
            ),
        )
    finally:
        market_flow.interpret_market_item = original
    assert review["should_push_now"] is True
    assert review["analysis"]["_decision_result"]["action"] == "push"
    assert review["analysis"]["_interpretation_result"]["core_content"] == "统一薄解读核心内容。"


def test_runtime_and_monitor_imports_use_one_unified_path() -> None:
    assert market_runtime._selected_module("article").__name__ == "market_content_adapter"
    assert market_runtime._selected_module("official").__name__ == "market_content_adapter"
    assert market_runtime._selected_module("event").__name__ == "market_event_adapter"
    for module in (rss_monitor, china_finance_media_monitor, trendforce_page_monitor, alphabstract_monitor, value_directory_monitor):
        assert module.process_market_item.__module__ == "market_runtime"
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
    assert value_directory_monitor.process_market_item.__module__ == "market_runtime"
    assert "process_market_item(" in source


def main() -> int:
    test_article_interpretation_cannot_override_decision_action()
    test_official_flow_uses_same_decision_and_interpretation_contract()
    test_runtime_and_monitor_imports_use_one_unified_path()
    test_value_directory_uses_unified_runtime_after_private_enrichment()
    print("content flow checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
