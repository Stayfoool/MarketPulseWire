#!/usr/bin/env python3
"""Regression checks for report-only direct decision shadow helpers."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from collector_direct_shadow import direct_decision_payload, direct_event_decision_payload, safe_load_shadow_holdings


def test_safe_load_shadow_holdings_is_read_only_when_db_missing() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "missing.sqlite3"
        holdings, error = safe_load_shadow_holdings(db_path)
        assert holdings == []
        assert error == ""
        assert not db_path.exists()


def test_direct_decision_payload_is_report_only_summary() -> None:
    payload = direct_decision_payload(
        "cls_telegraph_api",
        {
            "id": "cls-ai-theme",
            "title": "高盛发布《投资策略：做多中国 AI 价值链》",
            "summary": (
                "高盛认为中国 AI 公司市值与市场空间严重错配，资金正从韩国 AI 交易出现结构性资本轮动，"
                "建议做多中国 AI 价值链，覆盖算力、半导体和数据中心电力。"
            ),
            "published_at": "2026-07-12T00:00:00+00:00",
        },
        source_category="news_media",
        collector="news_collector",
        content_type="article",
        holdings=[],
    )
    assert payload["ok"] is True
    assert payload["normalized_item"]["dedupe_key"] == "cls_telegraph_api:cls-ai-theme"
    assert payload["decision"]["action"] == "push"
    assert payload["decision"]["rule_hit_ids"] == ["international_bank_theme_strategy"]
    assert payload["decision"]["audit"]["source_category"] == "news_media"


def test_event_decision_payload_exposes_delivery_and_dedup_intent_without_reservation() -> None:
    payload = direct_event_decision_payload(
        {
            "source": "sina_flash",
            "source_event_id": "bank-theme-1",
            "event_type": "flash_news",
            "title": "高盛发布《投资策略：做多中国 AI 价值链》",
            "summary": (
                "高盛认为中国 AI 公司市值与市场空间严重错配，资金正从韩国 AI 交易出现结构性资本轮动，"
                "建议做多中国 AI 价值链，覆盖算力、半导体和数据中心电力。"
            ),
            "published_at": "2026-07-12T00:00:00+00:00",
            "raw": {},
        }
    )
    assert payload["normalized_item"]["source_category"] == "news_media"
    assert payload["normalized_item"]["collector"] == "sina_flash"
    assert payload["decision"]["action"] == "push"
    assert payload["decision"]["rule_hit_ids"] == ["international_bank_theme_strategy"]
    assert payload["delivery_intent"]["would_send"] is True
    assert payload["dedup_intent"]["rule_alert_reservation_required"] is True
    assert payload["dedup_intent"]["reservation_attempted"] is False


def main() -> int:
    test_safe_load_shadow_holdings_is_read_only_when_db_missing()
    test_direct_decision_payload_is_report_only_summary()
    test_event_decision_payload_exposes_delivery_and_dedup_intent_without_reservation()
    print("collector direct shadow checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
