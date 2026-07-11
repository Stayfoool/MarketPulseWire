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
    assert payload["rss"][0]["candidates"][0]["pipeline"] == "official_company shadow -> decision layer / thin interpretation planned"
    assert payload["rss"][0]["candidates"][0]["already_reviewed"] is False
    assert seen_count == 0
    assert review_count == 0


def test_shadow_collect_rss_can_attach_direct_decision() -> None:
    original_fetch_feed = official_collector.fetch_feed

    def fake_fetch_feed(source: str, url: str, state: dict | None = None):
        return (
            [
                {
                    "id": "official-ai-platform",
                    "url": "https://example.com/official-ai-platform",
                    "title": "NVIDIA announces rack-scale AI platform with liquid cooling",
                    "summary": "NVIDIA details GPU, rack-scale systems, and liquid cooling for AI factories.",
                    "published_at": "2026-07-08T00:00:00+00:00",
                }
            ],
            {},
            False,
        )

    try:
        official_collector.fetch_feed = fake_fetch_feed
        payload = official_collector.collect_shadow(
            feeds={"nvidia_blog": "https://example.com/feed.xml"},
            limit=5,
            compare_seen=False,
            compare_reviews=False,
            save_shadow_state=False,
            direct_shadow=True,
            direct_shadow_holdings=[],
        )
    finally:
        official_collector.fetch_feed = original_fetch_feed

    candidate = payload["rss"][0]["candidates"][0]
    decision = candidate["direct_shadow"]["decision"]
    assert payload["ran_direct_decision_shadow"] is True
    assert payload["counts"]["direct_shadow_candidates"] == 1
    assert payload["counts"]["direct_shadow_push_candidates"] == 1
    assert decision["action"] == "push"
    assert decision["rule_hit_ids"] == ["official_company_hard_variable"]
    assert candidate["direct_shadow"]["normalized_item"]["content_type"] == "official_news"


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


def test_production_collect_delegates_to_existing_rss_pipeline() -> None:
    calls: list[tuple[dict[str, str], bool]] = []
    original_run_rss_once = official_collector.run_rss_once

    def fake_run_rss_once(feeds: dict[str, str], notify_baseline: bool = False) -> int:
        calls.append((feeds, notify_baseline))
        return 2

    try:
        official_collector.run_rss_once = fake_run_rss_once
        payload = official_collector.collect_production(
            feeds={"nvidia_blog": "https://example.com/feed.xml"},
            notify_baseline=True,
        )
    finally:
        official_collector.run_rss_once = original_run_rss_once

    assert payload["mode"] == "production"
    assert payload["wrote_production_seen_items"] is True
    assert payload["wrote_production_reviews"] is True
    assert payload["counts"]["rss_sources"] == 1
    assert payload["counts"]["new_items"] == 2
    assert calls == [({"nvidia_blog": "https://example.com/feed.xml"}, True)]


def main() -> int:
    test_official_sources_include_expected_company_feeds()
    test_disabled_source_is_filtered()
    test_shadow_collect_rss_does_not_write_prod_seen_or_reviews()
    test_shadow_collect_rss_can_attach_direct_decision()
    test_json_report_shape()
    test_production_collect_delegates_to_existing_rss_pipeline()
    print("official collector checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
