#!/usr/bin/env python3
"""Regression checks for unified article/news/official content flow."""

from __future__ import annotations

import inspect
import os
import sqlite3

import article_gate
import china_finance_media_monitor
import market_flow
import market_content_adapter
import market_content_flow
import market_runtime
import official_news_gate
import rss_monitor
import trendforce_page_monitor
import value_directory_monitor
from market_item import InterpretationResult
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


def test_gate_modules_are_thin_compatibility_exports() -> None:
    assert article_gate.review_article.__module__ == "market_content_adapter"
    assert article_gate.process_article_review.__module__ == "market_content_adapter"
    assert article_gate.rule_first_review.__module__ == "market_content_adapter"
    assert official_news_gate.review_official_news.__module__ == "market_content_adapter"
    assert official_news_gate.process_official_review.__module__ == "market_content_adapter"
    assert "from market_content_adapter import" in inspect.getsource(market_content_flow)
    assert "call_chat_completion_with_prompts" not in article_gate.__dict__
    assert "call_chat_completion_with_prompts" not in official_news_gate.__dict__


def test_article_interpretation_cannot_override_decision_action() -> None:
    original = market_flow.interpret_market_item
    try:
        market_flow.interpret_market_item = fake_interpretation
        review = market_content_adapter.review_article(
            "cls_telegraph_api",
            {
                "id": "macro-1",
                "title": "美国 CPI 大幅低于市场预期，2年期美债收益率下跌",
                "summary": "市场重新定价美联储降息路径。",
                "published_at": "2026-07-12T00:00:00+00:00",
            },
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
        review = market_content_adapter.review_official_news(
            "nvidia_blog",
            {
                "id": "rubin-1",
                "title": "NVIDIA announces Rubin rack-scale AI platform with liquid cooling",
                "summary": "NVIDIA details GPU systems, liquid cooling, and AI factory deployment.",
                "published_at": "2026-07-12T00:00:00+00:00",
            },
        )
    finally:
        market_flow.interpret_market_item = original
    assert review["should_push_now"] is True
    assert review["analysis"]["_decision_result"]["action"] == "push"
    assert review["analysis"]["_interpretation_result"]["core_content"] == "统一薄解读核心内容。"


def test_yicai_morning_brief_is_auditable_decision_rule() -> None:
    review = market_content_adapter.rule_first_review(
        "yicai_brief",
        {
            "id": "morning-1",
            "title": "券商晨会观点速递",
            "summary": "多家券商发布今日行业观点。",
        },
    )
    assert review is not None
    assert review["push_now"] is True
    assert review["raw"]["decision_result"]["rule_hits"][0]["rule_id"] == "yicai_morning_brief"


def test_interpretation_failure_does_not_cancel_hard_rule_push() -> None:
    original_interpreter = market_flow.interpret_market_item
    original_skeptic = market_content_adapter.apply_skeptic_review
    conn = sqlite3.connect(":memory:")
    market_content_adapter.ensure_article_reviews_table(conn)
    try:
        market_flow.interpret_market_item = lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("test interpreter failure")
        )
        market_content_adapter.apply_skeptic_review = lambda conn, **kwargs: kwargs["review"]
        review = market_content_adapter.process_article_review(
            conn,
            "yicai_brief",
            {
                "id": "morning-failure-1",
                "title": "券商晨会观点速递",
                "summary": "多家券商发布今日行业观点。",
            },
        )
    finally:
        market_flow.interpret_market_item = original_interpreter
        market_content_adapter.apply_skeptic_review = original_skeptic
        conn.close()
    assert review["push_now"] is True
    assert review["model"] == "interpretation_failed"
    assert "强制推送规则" in review["reason"]
    assert review["raw"]["decision_result"]["action"] == "push"


def test_skeptic_final_action_is_persisted_in_decision_result() -> None:
    original_interpreter = market_flow.interpret_market_item
    original_skeptic = market_content_adapter.apply_skeptic_review
    conn = sqlite3.connect(":memory:")
    market_content_adapter.ensure_article_reviews_table(conn)

    def block_review(conn, **kwargs):
        review = dict(kwargs["review"])
        review["push_now"] = False
        review["importance"] = "low"
        review["skeptic_blocked"] = True
        review["skeptic"] = {"skeptic_verdict": "block", "reason": "测试阻断"}
        return review

    try:
        market_flow.interpret_market_item = fake_interpretation
        market_content_adapter.apply_skeptic_review = block_review
        review = market_content_adapter.process_article_review(
            conn,
            "yicai_brief",
            {
                "id": "morning-blocked-1",
                "title": "券商晨会观点速递",
                "summary": "多家券商发布今日行业观点。",
            },
        )
        stored = market_content_adapter.article_review_exists(conn, "yicai_brief", "morning-blocked-1")
    finally:
        market_flow.interpret_market_item = original_interpreter
        market_content_adapter.apply_skeptic_review = original_skeptic
        conn.close()
    assert review["push_now"] is False
    assert review["raw"]["decision_result"]["action"] == "ignore"
    assert review["raw"]["decision_result"]["skeptic"]["skeptic_verdict"] == "block"
    assert stored is not None
    assert stored["raw"]["raw"]["decision_result"]["action"] == "ignore"


def test_runtime_switch_and_monitor_imports() -> None:
    original = os.environ.get(market_runtime.DIRECT_PATH_ENV)
    try:
        os.environ[market_runtime.DIRECT_PATH_ENV] = "0"
        assert market_runtime.runtime_path_name() == "compat"
        os.environ[market_runtime.DIRECT_PATH_ENV] = "1"
        assert market_runtime.runtime_path_name() == "direct"
    finally:
        if original is None:
            os.environ.pop(market_runtime.DIRECT_PATH_ENV, None)
        else:
            os.environ[market_runtime.DIRECT_PATH_ENV] = original
    for module in (rss_monitor, china_finance_media_monitor, trendforce_page_monitor):
        assert module.process_market_item.__module__ == "market_runtime"
        source = inspect.getsource(module)
        for forbidden in ("content_runtime", "market_content_flow", "market_event_flow", "event_runtime"):
            assert f"from {forbidden} import" not in source
            assert f"import {forbidden}" not in source
    assert FIELDS_BY_KEY[market_runtime.DIRECT_PATH_ENV].group == "pipeline"
    assert "SURVEIL_CONTENT_DIRECT_PATH" not in FIELDS_BY_KEY
    assert "SURVEIL_EVENT_DIRECT_PATH" not in FIELDS_BY_KEY


def test_value_directory_remains_outside_content_runtime_route() -> None:
    source = inspect.getsource(value_directory_monitor)
    assert "from article_gate import" in source
    assert "process_market_item" not in source


def main() -> int:
    test_gate_modules_are_thin_compatibility_exports()
    test_article_interpretation_cannot_override_decision_action()
    test_official_flow_uses_same_decision_and_interpretation_contract()
    test_yicai_morning_brief_is_auditable_decision_rule()
    test_interpretation_failure_does_not_cancel_hard_rule_push()
    test_skeptic_final_action_is_persisted_in_decision_result()
    test_runtime_switch_and_monitor_imports()
    test_value_directory_remains_outside_content_runtime_route()
    print("content flow checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
