#!/usr/bin/env python3
"""Regression checks for the domestic news-media shadow collector."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import news_collector
from source_profiles import save_source_profile_config


def test_news_sources_include_expected_batch_and_exclude_sina_flash() -> None:
    sources = news_collector.news_sources()
    assert "yicai_brief" in sources
    assert "cls_telegraph_api" in sources
    assert "star_market_daily_subject" in sources
    assert "jin10_rsshub_important" in sources
    assert "sina_flash" not in sources
    assert "yicai_brief_rsshub" not in sources
    assert "cls_telegraph_page" not in sources


def test_disabled_source_is_filtered() -> None:
    with TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "source_profiles.local.json"
        save_source_profile_config(
            {"profiles": [{"id": "jin10_rsshub_important", "enabled": False}]},
            path=config_path,
        )
        sources = news_collector.selected_sources([], config_path=config_path)
        assert "jin10_rsshub_important" not in sources
        assert "cls_telegraph_api" in sources


def test_shadow_collect_does_not_write_prod_seen_reviews_or_source_state() -> None:
    calls: list[dict] = []
    original_source_items = news_collector.china_media.source_items
    original_db_path = news_collector.DB_PATH

    def fake_source_items(source: str, *, persist_state: bool = True, force: bool = False):
        calls.append({"source": source, "persist_state": persist_state, "force": force})
        return [
            {
                "id": f"{source}-1",
                "url": f"https://example.com/{source}/1",
                "title": "全球功率半导体厂商新一轮涨价",
                "summary": "AI服务器需求拉动功率半导体供需偏紧。",
                "content": "",
                "published_at": "2026-07-08T00:00:00+00:00",
                "source_module": source,
                "body_source": "fake",
            }
        ]

    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE seen_items (
                source TEXT NOT NULL,
                item_id TEXT NOT NULL,
                url TEXT,
                title TEXT,
                summary TEXT,
                published_at TEXT,
                first_seen_at TEXT,
                PRIMARY KEY (source, item_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE article_reviews (
                source TEXT NOT NULL,
                item_id TEXT NOT NULL,
                url TEXT,
                title TEXT NOT NULL,
                source_module TEXT,
                published_at TEXT,
                importance TEXT NOT NULL,
                push_now INTEGER NOT NULL DEFAULT 0,
                market_impact TEXT,
                incremental_classification TEXT,
                affected_targets_json TEXT NOT NULL,
                reason TEXT,
                daily_summary TEXT,
                confidence TEXT,
                gate_json TEXT NOT NULL,
                pushed_at TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (source, item_id)
            )
            """
        )
        conn.execute("CREATE TABLE source_state (source TEXT PRIMARY KEY, state_json TEXT, updated_at TEXT NOT NULL)")
        conn.commit()
        conn.close()

        try:
            news_collector.china_media.source_items = fake_source_items
            news_collector.DB_PATH = db_path
            payload = news_collector.collect_shadow(
                sources={"cls_telegraph_api": "https://example.com/cls"},
                limit=5,
                compare_seen=True,
                compare_reviews=True,
                respect_prod_cls_state=False,
            )
        finally:
            news_collector.china_media.source_items = original_source_items
            news_collector.DB_PATH = original_db_path

        conn = sqlite3.connect(db_path)
        seen_count = conn.execute("SELECT COUNT(*) FROM seen_items").fetchone()[0]
        review_count = conn.execute("SELECT COUNT(*) FROM article_reviews").fetchone()[0]
        state_count = conn.execute("SELECT COUNT(*) FROM source_state").fetchone()[0]
        conn.close()

    assert calls == [{"source": "cls_telegraph_api", "persist_state": False, "force": True}]
    assert payload["ok"] is True
    assert payload["sent_feishu"] is False
    assert payload["ran_llm_review"] is False
    assert payload["wrote_production_seen_items"] is False
    assert payload["wrote_production_reviews"] is False
    assert payload["touched_production_source_state"] is False
    assert payload["counts"]["candidates"] == 1
    assert payload["sources"][0]["candidates"][0]["pipeline"] == "news_media shadow -> article_gate/event-first planned"
    assert seen_count == 0
    assert review_count == 0
    assert state_count == 0


def test_respect_prod_cls_state_passes_force_false() -> None:
    calls: list[dict] = []
    original_source_items = news_collector.china_media.source_items

    def fake_source_items(source: str, *, persist_state: bool = True, force: bool = False):
        calls.append({"source": source, "persist_state": persist_state, "force": force})
        return []

    try:
        news_collector.china_media.source_items = fake_source_items
        news_collector.collect_shadow(
            sources={"cls_telegraph_api": "https://example.com/cls"},
            compare_seen=False,
            compare_reviews=False,
            respect_prod_cls_state=True,
        )
    finally:
        news_collector.china_media.source_items = original_source_items

    assert calls == [{"source": "cls_telegraph_api", "persist_state": False, "force": False}]


def test_json_report_shape() -> None:
    payload = news_collector.collect_shadow(sources={}, compare_seen=False, compare_reviews=False)
    assert payload["ok"] is True
    assert payload["mode"] == "shadow_dry_run"
    assert payload["counts"] == {
        "sources": 0,
        "failed_sources": 0,
        "raw_items": 0,
        "candidates": 0,
        "focus_candidates": 0,
        "mandatory_candidates": 0,
        "already_seen_candidates": 0,
        "already_reviewed_candidates": 0,
    }
    assert payload["sources"] == []
    assert payload["errors"] == []


def main() -> int:
    test_news_sources_include_expected_batch_and_exclude_sina_flash()
    test_disabled_source_is_filtered()
    test_shadow_collect_does_not_write_prod_seen_reviews_or_source_state()
    test_respect_prod_cls_state_passes_force_false()
    test_json_report_shape()
    print("news collector checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
