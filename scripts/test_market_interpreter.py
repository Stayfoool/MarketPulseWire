#!/usr/bin/env python3
"""Regression checks for shared market interpretation prompts."""

from __future__ import annotations

from market_interpreter import (
    LLM_JUDGEMENT_ENUM,
    forbidden_field_line,
    interpretation_schema,
    normalize_interpretation_payload,
    restricted_judgement_instruction,
    thin_system_prompt,
    thin_user_prompt_template,
)
from market_item import DecisionResult


def test_thin_prompt_schema_keeps_push_fields_out_of_output() -> None:
    schema = interpretation_schema("targets")
    assert set(schema) == {"core_content", "brief_reason", "related_targets"}
    prompt = thin_user_prompt_template(
        intro="请分析以下资讯/报告",
        mode="targets",
        forbidden_mode="article",
        include_source_module=True,
    )
    assert '"related_targets"' in prompt
    assert "来源模块：{source_module}" in prompt
    assert "不要输出：" in prompt
    assert "push_now" in prompt
    assert '"importance"' not in prompt


def test_event_prompt_uses_related_holdings_schema() -> None:
    prompt = thin_user_prompt_template(intro="请分析以下持仓事件", mode="holdings", forbidden_mode="event")
    assert '"related_holdings"' in prompt
    assert "impact_direction" in prompt
    assert "incremental_view" in forbidden_field_line("event")


def test_restricted_judgement_instruction_includes_rule_context_and_enum() -> None:
    decision = DecisionResult(
        action="daily",
        importance="medium",
        reason="候选宏观规则命中但需确认市场反应。",
        candidate_rules=[{"rule_id": "macro_policy_line", "tier": "secondary_major"}],
        need_limited_llm_judgement=True,
    )
    instruction = restricted_judgement_instruction(decision)
    assert "不能覆盖硬规则强推" in instruction
    assert "macro_policy_line" in instruction
    for value in LLM_JUDGEMENT_ENUM:
        assert value in instruction


def test_normalize_interpretation_payload_accepts_related_holdings_and_restricts_judgement() -> None:
    result = normalize_interpretation_payload(
        {
            "core_content": "美国 ADP 大幅不及预期，美债收益率回落。",
            "brief_reason": "命中宏观候选规则，需确认市场反应。",
            "related_holdings": [{"name": "A股风险偏好", "relation": "宏观线"}],
            "llm_judgement": "freeform bullish",
        },
        model="test-model",
    )
    payload = result.to_dict()
    assert payload["related_targets"][0]["name"] == "A股风险偏好"
    assert payload["llm_judgement"] == "not_needed"
    assert payload["model"] == "test-model"


def test_system_prompt_states_llm_is_not_final_push_judge() -> None:
    prompt = thin_system_prompt(task="为一条测试信息生成极简实时摘要。")
    assert "不要把自己当成最终裁判" in prompt
    assert "不能自由扩散主题" in prompt


def main() -> int:
    test_thin_prompt_schema_keeps_push_fields_out_of_output()
    test_event_prompt_uses_related_holdings_schema()
    test_restricted_judgement_instruction_includes_rule_context_and_enum()
    test_normalize_interpretation_payload_accepts_related_holdings_and_restricts_judgement()
    test_system_prompt_states_llm_is_not_final_push_judge()
    print("market interpreter checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
