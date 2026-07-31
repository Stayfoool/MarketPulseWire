#!/usr/bin/env python3
"""Regression checks for shared market interpretation prompts."""

from __future__ import annotations

import market_interpreter
from market_interpreter import (
    forbidden_field_line,
    interpretation_schema,
    normalize_interpretation_payload,
    thin_system_prompt,
    thin_user_prompt_template,
)
from market_item import DecisionResult, NormalizedMarketItem


def test_thin_prompt_schema_keeps_push_fields_out_of_output() -> None:
    schema = interpretation_schema()
    assert set(schema) == {"core_content"}
    prompt = thin_user_prompt_template(
        intro="请分析以下资讯/报告",
        include_source_module=True,
    )
    assert '"core_content"' in prompt
    assert '"brief_reason"' not in prompt
    assert '"related_targets"' not in prompt
    assert "来源模块：{source_module}" in prompt
    assert "不要输出：" in prompt
    assert "push_now" in prompt
    assert '"importance"' not in prompt


def test_market_information_prompt_uses_the_core_only_schema() -> None:
    prompt = thin_user_prompt_template(intro="请分析以下市场信息")
    assert '"core_content"' in prompt
    assert '"related_holdings"' not in prompt
    assert '"brief_reason"' not in prompt
    assert "incremental_view" in forbidden_field_line()


def test_normalize_interpretation_payload_ignores_non_core_fields() -> None:
    result = normalize_interpretation_payload(
        {
            "core_content": "美国 ADP 大幅不及预期，美债收益率回落。",
            "brief_reason": "命中宏观候选规则，需确认市场反应。",
            "related_holdings": [{"name": "A股风险偏好", "relation": "宏观线"}],
            "risks": ["风险提示"],
            "llm_judgement": "freeform bullish",
        },
        model="test-model",
    )
    payload = result.to_dict()
    assert payload["core_content"] == "美国 ADP 大幅不及预期，美债收益率回落。"
    assert payload["brief_reason"] == ""
    assert payload["related_targets"] == []
    assert payload["llm_judgement"] == "not_needed"
    assert payload["model"] == "test-model"
    assert payload["prompt_version"] == "market_interpreter_v2"


def test_system_prompt_states_llm_is_not_final_push_judge() -> None:
    prompt = thin_system_prompt(task="为一条测试信息生成极简实时摘要。")
    assert "只由输入中的 DecisionResult 决定" in prompt
    assert "不要输出推送原因、风险提示" in prompt


def test_interpret_market_item_passes_decision_context_and_ignores_push_fields() -> None:
    original = market_interpreter.call_chat_completion_with_prompts
    captured: dict[str, str] = {}

    def fake_call(system_prompt: str, user_prompt: str, *, user_agent: str):
        captured.update(system=system_prompt, user=user_prompt, user_agent=user_agent)
        return (
            {
                "core_content": "美国 CPI 低于预期。",
                "brief_reason": "宏观硬规则已命中。",
                "related_holdings": [{"name": "A股风险偏好"}],
                "risks": ["通胀反复"],
                "should_push": False,
            },
            "fake-model",
        )

    try:
        market_interpreter.call_chat_completion_with_prompts = fake_call
        result = market_interpreter.interpret_market_item(
            NormalizedMarketItem(source="sina_flash", title="美国 CPI 低于预期"),
            DecisionResult(
                action="push",
                brief_reason="宏观政策线规则命中。",
                rule_hits=[{"rule_id": "macro_policy_line"}],
            ),
        )
    finally:
        market_interpreter.call_chat_completion_with_prompts = original
    assert result.core_content == "美国 CPI 低于预期。"
    assert result.brief_reason == ""
    assert result.related_targets == []
    assert "should_push" not in result.to_dict()
    assert '"action": "push"' in captured["user"]
    assert "macro_policy_line" in captured["user"]
    assert "只用于选择核心事实" in captured["user"]


def test_interpret_market_item_does_not_request_limited_judgement_when_flagged() -> None:
    original = market_interpreter.call_chat_completion_with_prompts
    captured: dict[str, str] = {}

    def fake_call(system_prompt: str, user_prompt: str, *, user_agent: str):
        captured["user"] = user_prompt
        return ({"core_content": "候选宏观信息。", "llm_judgement": "weak_confirm"}, "fake-model")

    try:
        market_interpreter.call_chat_completion_with_prompts = fake_call
        result = market_interpreter.interpret_market_item(
            NormalizedMarketItem(source="cls_telegraph_api", title="美国 ADP 低于预期"),
            DecisionResult(
                action="daily",
                candidate_rules=[{"rule_id": "macro_policy_line"}],
            ),
        )
    finally:
        market_interpreter.call_chat_completion_with_prompts = original
    assert '"llm_judgement"' not in captured["user"]
    assert result.llm_judgement == "not_needed"
    assert result.to_dict().get("should_push") is None


def main() -> int:
    test_thin_prompt_schema_keeps_push_fields_out_of_output()
    test_market_information_prompt_uses_the_core_only_schema()
    test_normalize_interpretation_payload_ignores_non_core_fields()
    test_system_prompt_states_llm_is_not_final_push_judge()
    test_interpret_market_item_passes_decision_context_and_ignores_push_fields()
    test_interpret_market_item_does_not_request_limited_judgement_when_flagged()
    print("market interpreter checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
