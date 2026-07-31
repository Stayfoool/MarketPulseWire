#!/usr/bin/env python3
"""Regression checks for normalized market item data structures."""

from __future__ import annotations

from market_item import (
    DecisionResult,
    InterpretationResult,
    decision_result_from_dict,
    item_from_mapping,
    normalize_action,
    normalize_llm_judgement,
    stable_dedupe_key,
)


def test_mapping_preserves_source_context_and_dedupe_key() -> None:
    item = {
        "id": "862591",
        "title": "野村-中际旭创：上调目标价",
        "summary": "国际投行个股研报索引。",
        "full_text": "Nomura raises target price.",
        "url": "https://example.com/report/862591",
        "published_at": "2026-07-11T00:00:00+00:00",
        "source_module": "价值目录 / 国际投行-个股",
        "access_note": "用户账号可见列表页。",
    }
    normalized = item_from_mapping(
        "value_directory_ib_stocks",
        item,
        source_category="research_industry_media",
        publisher_role="research_origin",
        collector="value_directory_monitor",
        content_type="research_index",
    )
    assert normalized.source == "value_directory_ib_stocks"
    assert normalized.source_category == "research_industry_media"
    assert normalized.publisher_role == "research_origin"
    assert normalized.collector == "value_directory_monitor"
    assert normalized.content_type == "research_index"
    assert normalized.dedupe_key == "value_directory_ib_stocks:862591"
    assert "中际旭创" in normalized.text_for_rules
    payload = normalized.to_dict()
    assert payload["access_note"] == "用户账号可见列表页。"


def test_mapping_uses_generic_source_item_id_and_symbols() -> None:
    item = {
        "source": "sina_flash",
        "id": "flash-1",
        "content_type": "flash_news",
        "title": "美国 CPI 大幅超预期",
        "summary": "美债收益率快速上行。",
        "published_at": "2026-07-11T12:30:00+00:00",
        "symbols": ["688017.SH", "688017.SH", ""],
        "themes": ["宏观流动性/美联储政策"],
        "raw": {"provider": "legacy"},
    }
    normalized = item_from_mapping(
        "sina_flash",
        item,
        source_category="news_media",
        publisher_role="news_media",
        collector="sina_flash",
    )
    assert normalized.source == "sina_flash"
    assert normalized.content_type == "flash_news"
    assert normalized.publisher_role == "news_media"
    assert normalized.dedupe_key == "sina_flash:flash-1"
    assert normalized.symbols == ["688017.SH"]
    assert normalized.raw["id"] == "flash-1"


def test_decision_result_normalizes_current_fields() -> None:
    decision = DecisionResult(
        action="push",
        reason="命中持仓关联关键词。",
        brief_reason="关联关键词命中。",
        rule_hits=[{"rule_id": "holding_keyword_immediate_alert"}],
    )
    payload = decision.to_dict()
    assert payload["action"] == "push"
    assert payload["rule_hits"][0]["rule_id"] == "holding_keyword_immediate_alert"
    assert "importance" not in payload
    assert "need_llm_interpretation" not in payload
    assert "need_limited_llm_judgement" not in payload


def test_decision_result_ignores_historical_extra_fields() -> None:
    decision = decision_result_from_dict(
        {
            "action": "daily",
            "importance": "medium",
            "need_llm_interpretation": True,
            "need_limited_llm_judgement": True,
            "reason": "历史决策。",
        }
    )
    assert decision is not None
    assert decision.action == "daily"
    assert decision.reason == "历史决策。"
    assert set(decision.to_dict()).isdisjoint(
        {"importance", "need_llm_interpretation", "need_limited_llm_judgement"}
    )


def test_interpretation_result_restricts_llm_judgement_values() -> None:
    result = InterpretationResult(
        core_content="报道称 NVIDIA AI 机架可能因 PCB 中板瓶颈推迟。",
        brief_reason="命中行业媒体硬变量候选。",
        related_targets=[{"name": "PCB", "relation": "产业链环节"}],
        notes=["待确认", "待确认", ""],
        llm_judgement="confirm",
        model="deepseek-v4",
        prompt_version="limited-judge-v1",
    )
    payload = result.to_dict()
    assert payload["llm_judgement"] == "confirm"
    assert payload["notes"] == ["待确认"]
    assert payload["related_targets"][0]["name"] == "PCB"


def test_invalid_enums_fall_back_to_safe_defaults() -> None:
    assert normalize_action("send-now") == "archive"
    assert normalize_llm_judgement("freeform bullish") == "not_needed"


def test_stable_dedupe_key_prefers_source_item_id_then_url() -> None:
    assert stable_dedupe_key(source="demo", source_item_id="abc") == "demo:abc"
    assert stable_dedupe_key(source="demo", url="https://example.com/x") == "demo:https://example.com/x"
    generated = stable_dedupe_key(source="demo", content_type="article", title="same", published_at="2026")
    assert generated.startswith("demo:")
    assert len(generated.split(":", 1)[1]) == 24


def main() -> int:
    test_mapping_preserves_source_context_and_dedupe_key()
    test_mapping_uses_generic_source_item_id_and_symbols()
    test_decision_result_normalizes_current_fields()
    test_decision_result_ignores_historical_extra_fields()
    test_interpretation_result_restricts_llm_judgement_values()
    test_invalid_enums_fall_back_to_safe_defaults()
    test_stable_dedupe_key_prefers_source_item_id_then_url()
    print("market item checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
