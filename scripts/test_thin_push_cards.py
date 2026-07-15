#!/usr/bin/env python3
"""Regression checks for compact Feishu push cards."""

from __future__ import annotations

import os

from cards import build_article_card
from market_delivery import compact_event_analysis_lines


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


def test_thin_article_card_prefers_unified_decision_and_interpretation_metadata() -> None:
    card = build_article_card(
        "nvidia_blog",
        {
            "title": "NVIDIA Rubin 平台更新",
            "summary": "旧摘要不应优先展示。",
            "published_at": "2026-07-09T08:00:00+00:00",
            "article_review": {
                "daily_summary": "旧 daily summary",
                "analysis": {
                    "core_content": "统一解读：Rubin 机架级平台强调液冷与高速互联。",
                    "related_targets": [{"name": "液冷", "relation": "产业链环节"}],
                    "_decision_result": {
                        "brief_reason": "公司官网硬变量规则命中。",
                        "rule_hits": [
                            {
                                "rule_id": "official_company_hard_variable",
                                "affected_targets": ["AI/半导体产业链"],
                            }
                        ],
                    },
                },
            },
        },
    )
    text = flatten_card_text(card)
    assert "统一解读：Rubin 机架级平台强调液冷与高速互联。" in text
    assert "公司官网硬变量规则命中。" in text
    assert "液冷" in text
    assert "AI/半导体产业链" in text
    assert "旧 daily summary" not in text


def test_thin_article_card_shows_cls_vip_product_metadata_and_author_targets() -> None:
    card = build_article_card(
        "cls_telegraph_api",
        {
            "title": "①光通信+AI PCB，机构大额净买入这家公司。",
            "summary": "公开摘要未披露公司名称。",
            "published_at": "2026-07-14T11:22:39+00:00",
            "source_module": "财联社 / 电报 API",
            "cls_metadata": {
                "type": "20026",
                "product_label": "机构龙虎榜解读",
                "share_img_name": "vip.png",
                "is_vip": True,
                "author_extends": "sz002245@@蔚蓝锂芯##sz002384@@东山精密",
                "author_targets": [
                    {"name": "蔚蓝锂芯", "code": "002245.SZ"},
                    {"name": "东山精密", "code": "002384.SZ"},
                ],
            },
            "article_review": {"daily_summary": "公开摘要未披露公司名称。"},
        },
    )
    text = flatten_card_text(card)
    assert "财联社元数据" in text
    assert "type：20026" in text
    assert "栏目：机构龙虎榜解读" in text
    assert r"share\_img：vip.png" in text
    assert r"author\_extends：蔚蓝锂芯 002245.SZ；东山精密 002384.SZ" in text


def test_full_article_card_shows_cls_metadata() -> None:
    original = os.environ.get("SURVEIL_THIN_ARTICLE_CARD")
    try:
        os.environ["SURVEIL_THIN_ARTICLE_CARD"] = "0"
        card = build_article_card(
            "cls_telegraph_api",
            {
                "title": "光模块产线测试设备受益扩产",
                "summary": "公开摘要。",
                "published_at": "2026-07-14T23:05:10+00:00",
                "source_module": "财联社 / 电报 API",
                "analysis_lines": ["标题", "解读"],
                "raw": {
                    "cls_metadata": {
                        "type": "20023",
                        "product_label": "研选•研报数据",
                        "share_img_name": "vip.png",
                        "author_targets": [{"name": "罗博特科", "code": "300757.SZ"}],
                    }
                },
            },
        )
    finally:
        if original is None:
            os.environ.pop("SURVEIL_THIN_ARTICLE_CARD", None)
        else:
            os.environ["SURVEIL_THIN_ARTICLE_CARD"] = original
    text = flatten_card_text(card)
    assert "财联社元数据" in text
    assert "栏目：研选•研报数据" in text
    assert "罗博特科 300757.SZ" in text


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
    assert "为什么推送：模型认为应该推送。" in joined
    assert "相关标的：沪电股份 002463.SZ" in joined
    assert "增量/定价" not in joined
    assert "初步影响" not in joined
    assert "模型：" not in joined


def test_compact_event_analysis_lines_prefers_unified_metadata_reason_and_targets() -> None:
    lines = compact_event_analysis_lines(
        {
            "core_content": "美国CPI大幅低于预期。",
            "related_holdings": [{"name": "A股风险偏好"}],
            "_decision_result": {
                "brief_reason": "宏观政策线规则命中。",
                "rule_hits": [
                    {
                        "rule_id": "macro_policy_line",
                        "affected_targets": ["成长股估值"],
                    }
                ],
            },
        }
    )
    joined = "\n".join(lines)
    assert "核心内容：美国CPI大幅低于预期。" in joined
    assert "为什么推送：宏观政策线规则命中。" in joined
    assert "相关标的：A股风险偏好；成长股估值" in joined


def main() -> int:
    test_thin_article_card_keeps_llm_gate_reason_out_of_default_push_reason()
    test_thin_article_card_shows_deterministic_push_reason_when_present()
    test_thin_article_card_prefers_unified_decision_and_interpretation_metadata()
    test_thin_article_card_shows_cls_vip_product_metadata_and_author_targets()
    test_full_article_card_shows_cls_metadata()
    test_compact_event_analysis_lines_only_keep_core_and_targets()
    test_compact_event_analysis_lines_prefers_unified_metadata_reason_and_targets()
    print("thin push card checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
