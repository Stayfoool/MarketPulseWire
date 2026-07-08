#!/usr/bin/env python3
"""Regression checks for the official-company shadow collector."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import official_collector
from source_profiles import save_source_profile_config


def test_official_sources_include_expected_company_feeds() -> None:
    feeds = official_collector.official_rss_feeds()
    assert "openai_news" in feeds
    assert "nvidia_blog" in feeds
    assert "nvidia_developer_blog" in feeds
    assert "samsung_semiconductor_news" in feeds
    assert "samsung_global_semiconductor" in feeds
    assert "skhynix_newsroom" in feeds
    assert "micron_news_releases" in feeds
    assert "semianalysis" not in feeds
    assert "trendforce_semiconductors" not in feeds


def test_disabled_source_is_filtered() -> None:
    with TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "source_profiles.local.json"
        save_source_profile_config(
            {"profiles": [{"id": "nvidia_blog", "enabled": False}]},
            path=config_path,
        )
        feeds = official_collector.selected_sources([], config_path=config_path)
        assert "nvidia_blog" not in feeds
        assert "openai_news" in feeds


def test_shadow_collect_rss_does_not_write_prod_seen_or_reviews() -> None:
    calls: list[dict] = []
    original_fetch_feed = official_collector.fetch_feed
    original_db_path = official_collector.DB_PATH

    def fake_fetch_feed(source: str, url: str, state: dict | None = None):
        calls.append({"source": source, "url": url, "state": state or {}})
        return (
            [
                {
                    "id": "official-1",
                    "url": "https://example.com/official-1",
                    "title": "NVIDIA announces new AI infrastructure platform",
                    "summary": "<p>New platform for AI factories.</p>",
                    "published_at": "2026-07-08T00:00:00+00:00",
                    "categories": ["AI"],
                }
            ],
            {"etag": '"official"'},
            False,
        )

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
            CREATE TABLE official_news_reviews (
                source TEXT NOT NULL,
                item_id TEXT NOT NULL,
                url TEXT,
                title TEXT NOT NULL,
                published_at TEXT,
                importance TEXT NOT NULL,
                should_push_now INTEGER NOT NULL DEFAULT 0,
                reason TEXT,
                daily_summary TEXT,
                analysis_json TEXT NOT NULL,
                pushed_at TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (source, item_id)
            )
            """
        )
        conn.commit()
        conn.close()

        try:
            official_collector.fetch_feed = fake_fetch_feed
            official_collector.DB_PATH = db_path
            payload = official_collector.collect_shadow(
                feeds={"nvidia_blog": "https://example.com/feed.xml"},
                limit=5,
                compare_seen=True,
                compare_reviews=True,
                save_shadow_state=False,
            )
        finally:
            official_collector.fetch_feed = original_fetch_feed
            official_collector.DB_PATH = original_db_path

        conn = sqlite3.connect(db_path)
        seen_count = conn.execute("SELECT COUNT(*) FROM seen_items").fetchone()[0]
        review_count = conn.execute("SELECT COUNT(*) FROM official_news_reviews").fetchone()[0]
        conn.close()

    assert calls == [{"source": "nvidia_blog", "url": "https://example.com/feed.xml", "state": {}}]
    assert payload["ok"] is True
    assert payload["sent_feishu"] is False
    assert payload["ran_llm_review"] is False
    assert payload["wrote_production_seen_items"] is False
    assert payload["wrote_production_reviews"] is False
    assert payload["counts"]["candidates"] == 1
    assert payload["rss"][0]["candidates"][0]["pipeline"] == "official_news_gate shadow"
    assert payload["rss"][0]["candidates"][0]["already_reviewed"] is False
    assert seen_count == 0
    assert review_count == 0


def test_json_report_shape() -> None:
    payload = official_collector.collect_shadow(feeds={}, compare_seen=False, compare_reviews=False)
    assert payload["ok"] is True
    assert payload["mode"] == "shadow_dry_run"
    assert payload["counts"] == {
        "rss_sources": 0,
        "sources": 0,
        "failed_sources": 0,
        "raw_items": 0,
        "candidates": 0,
        "already_seen_candidates": 0,
        "already_reviewed_candidates": 0,
    }
    assert payload["rss"] == []
    assert payload["errors"] == []


def main() -> int:
    test_official_sources_include_expected_company_feeds()
    test_disabled_source_is_filtered()
    test_shadow_collect_rss_does_not_write_prod_seen_or_reviews()
    test_json_report_shape()
    print("official collector checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
