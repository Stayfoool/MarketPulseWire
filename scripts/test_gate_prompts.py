#!/usr/bin/env python3
"""Regression checks for investment gate prompt guardrails."""

from __future__ import annotations

import market_content_adapter


def assert_contains(text: str, expected: str) -> None:
    if expected not in text:
        raise AssertionError(f"prompt missing expected text: {expected}")


def main() -> int:
    article_prompt = market_content_adapter.GATE_SYSTEM_PROMPT + "\n" + market_content_adapter.GATE_USER_PROMPT
    official_prompt = market_content_adapter.OFFICIAL_SYSTEM_PROMPT + "\n" + market_content_adapter.OFFICIAL_USER_PROMPT

    for prompt in (article_prompt, official_prompt):
        assert_contains(prompt, "星际之门/Stargate-like")
        assert_contains(prompt, "超大资本开支")
        assert_contains(prompt, "待确认/预告性质")
        assert_contains(prompt, "设备、材料、存储、光通信、PCB、先进封装、电力、液冷")
        assert_contains(prompt, "不要输出")
        assert_contains(prompt, "规则层决定")
        assert_contains(prompt, "不要把自己当成最终裁判")
        assert_contains(prompt, "只输出 JSON")

    assert_contains(article_prompt, "是否即时推送由规则层决定")
    assert_contains(article_prompt, "push_now")
    assert_contains(official_prompt, "是否即时推送由规则层决定")
    assert_contains(official_prompt, "should_push_now")
    print("gate prompt guardrail checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
