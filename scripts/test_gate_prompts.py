#!/usr/bin/env python3
"""Regression checks for core-only market interpretation prompts."""

from __future__ import annotations

import market_content_adapter


def assert_contains(text: str, expected: str) -> None:
    if expected not in text:
        raise AssertionError(f"prompt missing expected text: {expected}")


def main() -> int:
    article_prompt = market_content_adapter.GATE_SYSTEM_PROMPT + "\n" + market_content_adapter.GATE_USER_PROMPT
    official_prompt = market_content_adapter.OFFICIAL_SYSTEM_PROMPT + "\n" + market_content_adapter.OFFICIAL_USER_PROMPT

    for prompt in (article_prompt, official_prompt):
        assert_contains(prompt, '"core_content"')
        assert_contains(prompt, "不要输出")
        assert_contains(prompt, "只由输入中的 DecisionResult 决定")
        assert_contains(prompt, "不要输出推送原因、风险提示")
        assert_contains(prompt, "不要总结规则、风险、估值或相关标的")
        assert_contains(prompt, "只输出 JSON")

    assert_contains(article_prompt, "push_now")
    assert_contains(official_prompt, "should_push_now")
    print("gate prompt guardrail checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
