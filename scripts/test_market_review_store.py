#!/usr/bin/env python3
"""Regression checks for compatibility review-store adapters."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import market_content_adapter
import market_review_store
from market_db import init_db


def test_article_review_store_round_trip_and_mark_pushed() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        item = {
            "id": "article-1",
            "url": "https://example.com/article-1",
            "title": "SemiAnalysis says AI rack power demand is rising",
            "source_module": "SemiAnalysis",
            "published_at": "2026-07-11T00:00:00+00:00",
            "summary": "AI rack power demand is rising.",
        }
        review = {
            "importance": "medium",
            "push_now": False,
            "market_impact": "关注 AI 电力链。",
            "incremental_classification": "产业趋势",
            "affected_targets": ["AI 电力"],
            "reason": "旧链路暂不推送。",
            "daily_summary": "AI 机架功耗上行。",
            "confidence": "中",
            "raw": {"core_content": "AI 机架功耗上行。"},
        }
        market_review_store.save_article_review(conn, "semianalysis", item, review)
        loaded = market_review_store.article_review_exists(conn, "semianalysis", "article-1")
        assert loaded is not None
        assert loaded["importance"] == "medium"
        assert loaded["push_now"] is False
        assert loaded["affected_targets"] == ["AI 电力"]
        assert loaded["raw"]["raw"]["decision_passthrough"] is True
        assert loaded["pushed_at"] == ""

        market_review_store.mark_article_pushed(conn, "semianalysis", "article-1")
        pushed = market_review_store.article_review_exists(conn, "semianalysis", "article-1")
        assert pushed is not None
        assert pushed["pushed_at"]
    finally:
        conn.close()


def test_official_review_store_round_trip_and_mark_pushed() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        item = {
            "id": "official-1",
            "url": "https://example.com/official-1",
            "title": "NVIDIA announces rack-scale AI platform",
            "published_at": "2026-07-11T00:00:00+00:00",
            "summary": "NVIDIA details liquid cooling and rack-scale systems.",
        }
        review = {
            "importance": "high",
            "should_push_now": True,
            "reason": "官网硬变量。",
            "daily_summary": "NVIDIA 发布 AI 平台。",
            "analysis": {"core_content": "NVIDIA 发布 AI 平台。"},
            "skeptic": {"skeptic_verdict": "pass"},
            "pre_skeptic_importance": "high",
        }
        market_review_store.save_official_review(conn, "nvidia_blog", item, review)
        loaded = market_review_store.official_review_exists(conn, "nvidia_blog", "official-1")
        assert loaded is not None
        assert loaded["importance"] == "high"
        assert loaded["should_push_now"] is True
        assert loaded["analysis"]["core_content"] == "NVIDIA 发布 AI 平台。"
        assert loaded["analysis"]["_decision_passthrough"] is True
        assert loaded["skeptic"]["skeptic_verdict"] == "pass"
        assert loaded["pushed_at"] == ""

        market_review_store.mark_official_pushed(conn, "nvidia_blog", "official-1")
        pushed = market_review_store.official_review_exists(conn, "nvidia_blog", "official-1")
        assert pushed is not None
        assert pushed["pushed_at"]
    finally:
        conn.close()


def test_event_store_round_trip_analysis_delivery_and_holdings() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                INSERT INTO portfolio_holdings (symbol, name, full_name, aliases_json, enabled, raw_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "688017.SH",
                    "绿的谐波",
                    "苏州绿的谐波传动科技股份有限公司",
                    json.dumps(["绿的"], ensure_ascii=False),
                    1,
                    json.dumps({"news_keywords": ["机器人"], "business_summary": "精密减速器"}, ensure_ascii=False),
                    "2026-07-12T00:00:00+00:00",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        holdings = market_review_store.load_enabled_holdings(db_path)
        assert holdings[0]["symbol"] == "688017.SH"
        assert holdings[0]["news_keywords"] == ["机器人"]
        assert holdings[0]["business_summary"] == "精密减速器"

        event = {
            "source": "sina_flash",
            "source_event_id": "flash-1",
            "event_type": "flash_news",
            "title": "绿的谐波机器人订单增长",
            "summary": "机器人订单增长。",
            "full_text": "机器人订单增长。",
            "url": "https://finance.sina.com.cn/7x24/",
            "published_at": "2026-07-12T00:30:00+00:00",
            "symbols": ["688017.SH"],
            "themes": ["新浪财经快讯"],
            "raw": {"source_event_id": "flash-1"},
        }
        event_id, inserted = market_review_store.upsert_event_record(event, db_path)
        assert inserted is True
        row = market_review_store.event_row_by_id(event_id, db_path)
        assert row is not None
        assert row["source"] == "sina_flash"
        assert json.loads(row["symbols_json"]) == ["688017.SH"]
        assert json.loads(row["raw_json"]) == {"source_event_id": "flash-1"}

        updated = dict(event)
        updated["full_text"] = "机器人订单增长。" * 10
        same_event_id, inserted_again = market_review_store.upsert_event_record(updated, db_path)
        assert same_event_id == event_id
        assert inserted_again is False
        refreshed = market_review_store.event_row_by_id(event_id, db_path)
        assert refreshed is not None
        assert refreshed["full_text"] == updated["full_text"]

        analysis = {"importance": "low", "core_content": "旧模型摘要。"}
        market_review_store.insert_event_analysis(
            event_id,
            "sina_flash_portfolio",
            "test-model",
            importance="low",
            classification="",
            direction="",
            impact_duration="",
            should_push=0,
            analysis=analysis,
            db_path=db_path,
        )
        latest = market_review_store.latest_event_analysis(event_id, "sina_flash_portfolio", db_path)
        assert latest is not None
        assert latest["analysis"]["core_content"] == "旧模型摘要。"

        updated_analysis = {"importance": "high", "core_content": "规则刷新摘要。"}
        market_review_store.update_event_analysis(
            latest["id"],
            importance="high",
            classification="规则命中",
            direction="positive",
            impact_duration="short",
            should_push=1,
            analysis=updated_analysis,
            db_path=db_path,
        )
        latest = market_review_store.latest_event_analysis(event_id, "sina_flash_portfolio", db_path)
        assert latest is not None
        assert latest["analysis"]["core_content"] == "规则刷新摘要。"

        market_review_store.record_event_delivery(
            event_id,
            "feishu",
            "sent",
            {"title": event["title"]},
            db_path=db_path,
        )
        conn = sqlite3.connect(db_path)
        try:
            delivery = conn.execute(
                "SELECT status, sent_at, payload_json FROM deliveries WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        finally:
            conn.close()
        assert delivery[0] == "sent"
        assert delivery[1]
        assert json.loads(delivery[2]) == {"title": event["title"]}


def test_article_adapter_save_uses_normalized_market_item_audit() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        item = {
            "id": "cls-ai-theme",
            "url": "https://example.com/cls-ai-theme",
            "title": "高盛发布投资策略：做多中国 AI 价值链",
            "summary": "高盛建议做多中国 AI 价值链，覆盖半导体、算力和数据中心电力。",
            "published_at": "2026-07-11T00:00:00+00:00",
        }
        review = {
            "importance": "low",
            "push_now": False,
            "affected_targets": [],
            "reason": "旧链路暂不推送。",
            "daily_summary": item["title"],
            "confidence": "低",
            "raw": {},
        }
        market_content_adapter.save_review(conn, "cls_telegraph_api", item, review)
        row = conn.execute(
            "SELECT gate_json FROM article_reviews WHERE source = ? AND item_id = ?",
            ("cls_telegraph_api", "cls-ai-theme"),
        ).fetchone()
        payload = json.loads(row[0])
        audit = payload["raw"]["decision_audit"]
        assert audit["source_category"] == "news_media"
        assert audit["content_type"] == "article"
        assert "news_collector.py" in audit["collector"]
        assert audit["dedupe_key"] == "cls_telegraph_api:cls-ai-theme"
    finally:
        conn.close()


def test_official_adapter_save_uses_normalized_market_item_audit() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        item = {
            "id": "nvidia-platform",
            "url": "https://example.com/nvidia-platform",
            "title": "NVIDIA announces rack-scale AI platform",
            "summary": "NVIDIA details liquid cooling and rack-scale AI systems.",
            "published_at": "2026-07-11T00:00:00+00:00",
        }
        review = {
            "importance": "low",
            "should_push_now": False,
            "reason": "旧链路暂不推送。",
            "daily_summary": item["title"],
            "analysis": {"core_content": item["summary"]},
        }
        market_content_adapter.save_official_review(conn, "nvidia_blog", item, review)
        row = conn.execute(
            "SELECT analysis_json FROM official_news_reviews WHERE source = ? AND item_id = ?",
            ("nvidia_blog", "nvidia-platform"),
        ).fetchone()
        payload = json.loads(row[0])
        audit = payload["_decision_audit"]
        assert audit["source_category"] == "official_company"
        assert audit["content_type"] == "official_news"
        assert "official_collector.py" in audit["collector"]
        assert audit["dedupe_key"] == "nvidia_blog:nvidia-platform"
    finally:
        conn.close()


def main() -> int:
    test_article_review_store_round_trip_and_mark_pushed()
    test_official_review_store_round_trip_and_mark_pushed()
    test_event_store_round_trip_analysis_delivery_and_holdings()
    test_article_adapter_save_uses_normalized_market_item_audit()
    test_official_adapter_save_uses_normalized_market_item_audit()
    print("market review store tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
