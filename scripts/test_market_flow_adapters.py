#!/usr/bin/env python3
"""Regression checks for explicit market-flow persistence adapters."""

from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import market_delivery
import market_flow_adapters
from market_db import init_db
from market_flow_adapters import (
    ingest_event_item,
    store_article_flow_review,
    store_event_flow_analysis,
    store_official_flow_review,
)
from market_item import DecisionResult, NormalizedMarketItem
from market_review_store import article_review_exists, official_review_exists


def article_review(push_key: str = "push_now") -> dict:
    decision = DecisionResult(
        action="push",
        importance="high",
        reason="测试规则命中。",
        rule_hits=[{"rule_id": "test_rule"}],
    )
    return {
        "importance": "high",
        push_key: True,
        "reason": "测试规则命中。",
        "daily_summary": "测试摘要。",
        "affected_targets": ["测试标的"],
        "raw": {"decision_result": decision.to_dict()},
        "analysis": {"_decision_result": decision.to_dict()},
    }


def test_ingestion_adapter_preserves_normalized_event_audit() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        event = {
            "source": "sina_flash",
            "source_event_id": "adapter-event-1",
            "event_type": "flash",
            "title": "新浪财经快讯",
            "summary": "测试快讯。",
            "raw": {"provider": "sina"},
        }
        normalized = NormalizedMarketItem(
            source="sina_flash",
            source_category="news_media",
            collector="sina_flash",
            content_type="flash",
            title=event["title"],
            summary=event["summary"],
            raw={"source_event_id": event["source_event_id"], "provider": "sina"},
        )
        event_id, inserted = ingest_event_item(event, normalized, db_path)
        same_id, inserted_again = ingest_event_item(event, normalized, db_path)
        with sqlite3.connect(db_path) as conn:
            raw_json = conn.execute("SELECT raw_json FROM events WHERE id = ?", (event_id,)).fetchone()[0]
    assert inserted is True
    assert inserted_again is False
    assert same_id == event_id
    assert '"source_category": "news_media"' in raw_json
    assert '"content_type": "flash"' in raw_json


def test_article_and_official_store_adapters_keep_legacy_tables() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        article_item = {"id": "article-1", "title": "测试文章"}
        article_normalized = NormalizedMarketItem(
            source="cls_telegraph_api",
            source_category="news_media",
            collector="news_collector",
            content_type="article",
            title="测试文章",
            raw={"id": "article-1"},
        )
        official_item = {"id": "official-1", "title": "测试官网新闻"}
        official_normalized = NormalizedMarketItem(
            source="nvidia_blog",
            source_category="official_company",
            collector="official_collector",
            content_type="official_news",
            title="测试官网新闻",
            raw={"id": "official-1"},
        )
        with sqlite3.connect(db_path) as conn:
            store_article_flow_review(
                conn,
                "cls_telegraph_api",
                article_item,
                article_review(),
                article_normalized,
            )
            store_official_flow_review(
                conn,
                "nvidia_blog",
                official_item,
                article_review("should_push_now"),
                official_normalized,
            )
            stored_article = article_review_exists(conn, "cls_telegraph_api", "article-1")
            stored_official = official_review_exists(conn, "nvidia_blog", "official-1")
    assert stored_article is not None and stored_article["push_now"] is True
    assert stored_official is not None and stored_official["should_push_now"] is True


def test_event_analysis_adapter_inserts_and_updates_legacy_row() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        event_id, _ = ingest_event_item(
            {
                "source": "ifind_notice",
                "source_event_id": "notice-1",
                "event_type": "notice",
                "title": "测试公告",
            },
            NormalizedMarketItem(
                source="ifind_notice",
                source_category="company_disclosures",
                collector="ifind_batch",
                content_type="notice",
                title="测试公告",
                raw={"source_event_id": "notice-1"},
            ),
            db_path,
        )
        store_event_flow_analysis(
            event_id,
            "portfolio_event",
            "test-model",
            {"core_content": "初始分析"},
            importance="high",
            classification="规则命中",
            direction="positive",
            impact_duration="short",
            should_push=1,
            db_path=db_path,
        )
        with sqlite3.connect(db_path) as conn:
            analysis_id = conn.execute("SELECT id FROM event_analyses WHERE event_id = ?", (event_id,)).fetchone()[0]
        store_event_flow_analysis(
            event_id,
            "portfolio_event",
            "test-model",
            {"core_content": "更新分析"},
            importance="low",
            classification="",
            direction="neutral",
            impact_duration="",
            should_push=0,
            existing_analysis_id=analysis_id,
            db_path=db_path,
        )
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT importance, should_push, analysis_json FROM event_analyses WHERE id = ?",
                (analysis_id,),
            ).fetchone()
    assert row[0] == "low"
    assert row[1] == 0
    assert "更新分析" in row[2]


def test_adapters_do_not_own_rules_or_interpretation() -> None:
    adapter_source = inspect.getsource(market_flow_adapters)
    delivery_source = inspect.getsource(market_delivery)
    for forbidden in ("decision_engine", "market_interpreter", "push_rules", "call_chat_completion"):
        assert forbidden not in adapter_source
        assert forbidden not in delivery_source


def main() -> int:
    test_ingestion_adapter_preserves_normalized_event_audit()
    test_article_and_official_store_adapters_keep_legacy_tables()
    test_event_analysis_adapter_inserts_and_updates_legacy_row()
    test_adapters_do_not_own_rules_or_interpretation()
    print("market flow adapter checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
