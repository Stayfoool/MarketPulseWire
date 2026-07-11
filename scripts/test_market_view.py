#!/usr/bin/env python3
"""Regression checks for unified market view adapters."""

from __future__ import annotations

import json
import sqlite3

from market_view import article_view_from_row, event_view_from_row, official_view_from_row
from signals_extract import article_signal_from_row


def test_article_view_prefers_unified_interpretation_and_decision_metadata() -> None:
    row = {
        "source": "nvidia_blog",
        "item_id": "rubin-1",
        "url": "https://example.com/rubin",
        "title": "NVIDIA Rubin 平台更新",
        "source_module": "NVIDIA Blog",
        "published_at": "2026-07-11T00:00:00+00:00",
        "created_at": "2026-07-11T00:01:00+00:00",
        "pushed_at": "",
        "importance": "low",
        "incremental_classification": "",
        "push_now": 0,
        "daily_summary": "旧摘要",
        "reason": "旧门控长理由",
        "affected_targets_json": "[]",
        "gate_json": json.dumps(
            {
                "raw": {
                    "core_content": "统一解读：Rubin 强调液冷与高速互联。",
                    "related_targets": [{"name": "液冷"}],
                    "decision_result": {
                        "action": "push",
                        "brief_reason": "公司官网硬变量规则命中。",
                        "rule_hits": [{"rule_id": "official_company_hard_variable", "affected_targets": ["AI/半导体产业链"]}],
                    },
                }
            },
            ensure_ascii=False,
        ),
    }
    view = article_view_from_row(row)
    web = view.to_web_row()
    assert web["summary"] == "统一解读：Rubin 强调液冷与高速互联。"
    assert web["decision_action"] == "push"
    assert "液冷" in web["related_targets"]
    assert "AI/半导体产业链" in web["related_targets"]


def test_official_and_event_views_read_prefixed_decision_metadata() -> None:
    official = official_view_from_row(
        {
            "source": "nvidia_blog",
            "item_id": "rubin-2",
            "url": "",
            "title": "NVIDIA Rubin",
            "published_at": "2026-07-11T00:00:00+00:00",
            "created_at": "2026-07-11T00:01:00+00:00",
            "pushed_at": "",
            "importance": "high",
            "should_push_now": 1,
            "daily_summary": "官网摘要",
            "reason": "",
            "analysis_json": json.dumps(
                {
                    "core_content": "统一官网解读。",
                    "related_targets": [{"name": "高速互联"}],
                    "_decision_result": {"action": "push", "brief_reason": "官网规则命中。"},
                },
                ensure_ascii=False,
            ),
        }
    )
    assert official.core_content == "统一官网解读。"
    assert official.decision_reason == "官网规则命中。"
    assert official.push is True

    event = event_view_from_row(
        {
            "id": 7,
            "source": "sina_flash",
            "source_event_id": "flash-7",
            "event_type": "flash_news",
            "title": "美国 CPI 大幅低于预期",
            "summary": "美债收益率回落。",
            "url": "",
            "published_at": "2026-07-11T12:30:00+00:00",
            "first_seen_at": "2026-07-11T12:31:00+00:00",
            "pushed_at": "",
            "importance": "high",
            "classification": "",
            "should_push": 1,
            "delivery_status": "",
            "baseline_only": 0,
            "symbols_json": "[]",
            "themes_json": json.dumps(["宏观流动性/美联储政策"], ensure_ascii=False),
            "analysis_json": json.dumps(
                {
                    "core_content": "统一事件解读。",
                    "related_holdings": [{"name": "A股风险偏好"}],
                    "_decision_result": {
                        "action": "push",
                        "brief_reason": "宏观政策线规则命中。",
                        "rule_hits": [{"rule_id": "macro_policy_line", "affected_targets": ["成长股估值"]}],
                    },
                },
                ensure_ascii=False,
            ),
        }
    )
    assert event.core_content == "统一事件解读。"
    assert event.decision_reason == "宏观政策线规则命中。"
    assert event.related_targets == ["A股风险偏好", "成长股估值", "宏观流动性/美联储政策"]


def test_article_signal_extraction_uses_market_view_thesis_and_targets() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE article_reviews (
            source TEXT, item_id TEXT, url TEXT, title TEXT, source_module TEXT,
            published_at TEXT, importance TEXT, push_now INTEGER, market_impact TEXT,
            incremental_classification TEXT, affected_targets_json TEXT, reason TEXT,
            daily_summary TEXT, confidence TEXT, gate_json TEXT, pushed_at TEXT, created_at TEXT
        )
        """
    )
    gate = {
        "raw": {
            "core_content": "统一解读：液冷供应链关注度提升。",
            "related_targets": [{"name": "液冷"}],
            "decision_result": {
                "action": "push",
                "brief_reason": "产业硬变量规则命中。",
                "rule_hits": [{"rule_id": "industry_quantified_hardline", "affected_targets": ["AI服务器"]}],
            },
        }
    }
    conn.execute(
        """
        INSERT INTO article_reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "digitimes_tw_server",
            "item-1",
            "",
            "液冷供应链新闻",
            "DIGITIMES",
            "2026-07-11T00:00:00+00:00",
            "high",
            1,
            "",
            "",
            "[]",
            "旧理由",
            "旧摘要",
            "",
            json.dumps(gate, ensure_ascii=False),
            "",
            "2026-07-11T00:01:00+00:00",
        ),
    )
    row = conn.execute("SELECT * FROM article_reviews").fetchone()
    extracted = article_signal_from_row(conn, row)
    assert extracted is not None
    signal, targets, _evidence = extracted
    assert signal["thesis"] == "统一解读：液冷供应链关注度提升。"
    target_names = {target.get("name") for target in targets}
    assert "液冷" in target_names
    assert "AI服务器" in target_names


def main() -> int:
    test_article_view_prefers_unified_interpretation_and_decision_metadata()
    test_official_and_event_views_read_prefixed_decision_metadata()
    test_article_signal_extraction_uses_market_view_thesis_and_targets()
    print("market view checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
