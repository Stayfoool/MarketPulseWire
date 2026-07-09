#!/usr/bin/env python3
"""Regression checks for compact Feishu push cards."""

from __future__ import annotations

import os

from cards import build_article_card
from event_pipeline import compact_event_analysis_lines


def flatten_card_text(card: dict) -> str:
    parts: list[str] = []
    for element in card.get("elements") or []:
        text = element.get("text") if isinstance(element, dict) else None
        if isinstance(text, dict):
            parts.append(str(text.get("content") or ""))
        for nested in element.get("elements") or [] if isinstance(element, dict) else []:
            if isinstance(nested, dict):
                parts.append(str(nested.get("content") or ""))
    header = card.get("header") or {}
    title = header.get("title") if isinstance(header, dict) else {}
    if isinstance(title, dict):
        parts.append(str(title.get("content") or ""))
    return "\n".join(parts)


def test_thin_article_card_keeps_llm_gate_reason_out_of_default_push_reason() -> None:
    original = os.environ.get("SURVEIL_THIN_ARTICLE_CARD")
    try:
        os.environ["SURVEIL_THIN_ARTICLE_CARD"] = "1"
        card = build_article_card(
            "yicai_brief",
            {
                "title": "AI PCB需求放量",
                "summary": "核心摘要",
                "full_text": "原文摘要",
                "published_at": "2026-07-09T08:00:00+00:00",
                "source_module": "第一财经 / 早晚快讯",
                "article_review": {
                    "daily_summary": "AI PCB需求放量，高阶升级趋势明确。",
                    "reason": "重要性门控：high\n门控理由：这是一段很长的模型解释，飞书默认不应展示。",
                    "affected_targets": ["沪电股份", "胜宏科技"],
                },
            },
        )
    finally:
        if original is None:
            os.environ.pop("SURVEIL_THIN_ARTICLE_CARD", None)
        else:
            os.environ["SURVEIL_THIN_ARTICLE_CARD"] = original
    text = flatten_card_text(card)
    assert "核心内容" in text
    assert "AI PCB需求放量" in text
    assert "相关标的/环节" in text
    assert "沪电股份" in text
    assert "重要性门控" not in text
    assert "门控理由" not in text


def test_thin_article_card_shows_deterministic_push_reason_when_present() -> None:
    card = build_article_card(
        "cls_telegraph_api",
        {
            "title": "美国CPI今晚公布",
            "summary": "市场关注美联储降息路径。",
            "published_at": "2026-07-09T08:00:00+00:00",
            "push_reason": "美国核心宏观/Fed 政策线。",
            "article_review": {"daily_summary": "美国CPI今晚公布。", "affected_targets": ["A股风险偏好"]},
        },
    )
    text = flatten_card_text(card)
    assert "为什么推送" in text
    assert "美国核心宏观/Fed 政策线" in text


def test_compact_event_analysis_lines_only_keep_core_and_targets() -> None:
    lines = compact_event_analysis_lines(
        {
            "core_content": "某公司上调AI服务器PCB订单指引。",
            "incremental_view": {"classification": "增量利好", "priced_in": "部分定价"},
            "price_impact": {"direction": "上涨", "reason": "可能带动板块情绪。"},
            "a_share": {"positive": [{"name": "沪电股份", "code": "002463.SZ"}], "negative": []},
            "push_decision": {"reason": "模型认为应该推送。"},
            "_model": "deepseek-v4-pro",
        }
    )
    joined = "\n".join(lines)
    assert "核心内容：某公司上调AI服务器PCB订单指引。" in joined
    assert "相关标的：沪电股份 002463.SZ" in joined
    assert "增量/定价" not in joined
    assert "初步影响" not in joined
    assert "为什么推送" not in joined
    assert "模型：" not in joined


def main() -> int:
    test_thin_article_card_keeps_llm_gate_reason_out_of_default_push_reason()
    test_thin_article_card_shows_deterministic_push_reason_when_present()
    test_compact_event_analysis_lines_only_keep_core_and_targets()
    print("thin push card checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
