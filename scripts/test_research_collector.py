#!/usr/bin/env python3
"""Regression checks for the research/industry-media shadow collector."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import research_collector
from source_profiles import save_source_profile_config


def test_research_sources_include_expected_groups() -> None:
    feeds = research_collector.research_rss_feeds()
    pages = research_collector.research_page_sources()
    page_names = {source.name for source in pages}

    assert "semianalysis" in feeds
    assert "trendforce_semiconductors" in feeds
    assert "digitimes_en_daily" in feeds
    assert "nikkei_xtech_all" in feeds
    assert "thelec_kr_semiconductor" in feeds
    assert "openai_news" not in feeds
    assert "micron_news_releases" not in feeds
    assert "trendforce_research_latest" in page_names
    assert "semi_prnewswire_semiconductors" in page_names


def test_disabled_source_is_filtered() -> None:
    with TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "source_profiles.local.json"
        save_source_profile_config(
            {
                "profiles": [
                    {"id": "semianalysis", "enabled": False},
                    {"id": "trendforce_research_latest", "enabled": False},
                ]
            },
            path=config_path,
        )
        feeds, pages = research_collector.selected_sources([], config_path=config_path)
        assert "semianalysis" not in feeds
        assert "trendforce_semiconductors" in feeds
        assert "trendforce_research_latest" not in {source.name for source in pages}


def test_shadow_collect_rss_does_not_write_prod_seen_items() -> None:
    calls: list[dict] = []
    original_fetch_feed = research_collector.fetch_feed
    original_db_path = research_collector.DB_PATH

    def fake_fetch_feed(source: str, url: str, state: dict | None = None):
        calls.append({"source": source, "url": url, "state": state or {}})
        return (
            [
                {
                    "id": "demo-1",
                    "url": "https://example.com/demo-1",
                    "title": "HBM capacity expansion test",
                    "summary": "<p>HBM supply chain test summary.</p>",
                    "published_at": "2026-07-08T00:00:00+00:00",
                    "categories": ["HBM"],
                }
            ],
            {"etag": '"demo"'},
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
        conn.commit()
        conn.close()

        try:
            research_collector.fetch_feed = fake_fetch_feed
            research_collector.DB_PATH = db_path
            payload = research_collector.collect_shadow(
                feeds={"semianalysis": "https://example.com/feed.xml"},
                page_sources=[],
                limit=5,
                compare_seen=True,
                save_shadow_state=False,
            )
        finally:
            research_collector.fetch_feed = original_fetch_feed
            research_collector.DB_PATH = original_db_path

        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM seen_items").fetchone()[0]
        conn.close()

    assert calls == [{"source": "semianalysis", "url": "https://example.com/feed.xml", "state": {}}]
    assert payload["ok"] is True
    assert payload["sent_feishu"] is False
    assert payload["ran_llm_review"] is False
    assert payload["wrote_production_seen_items"] is False
    assert payload["counts"]["candidates"] == 1
    assert payload["rss"][0]["candidates"][0]["title"] == "HBM capacity expansion test"
    assert count == 0


def test_json_report_shape() -> None:
    payload = research_collector.collect_shadow(feeds={}, page_sources=[], compare_seen=False)
    assert payload["ok"] is True
    assert payload["mode"] == "shadow_dry_run"
    assert payload["counts"] == {
        "rss_sources": 0,
        "page_sources": 0,
        "sources": 0,
        "failed_sources": 0,
        "raw_items": 0,
        "candidates": 0,
        "already_seen_candidates": 0,
    }
    assert payload["rss"] == []
    assert payload["pages"] == []
    assert payload["errors"] == []


def test_production_collect_delegates_to_existing_pipelines() -> None:
    calls: list[tuple[str, object, bool]] = []
    original_run_rss_once = research_collector.run_rss_once
    original_run_page_once = research_collector.run_page_once
    original_due_page_sources = research_collector.due_page_sources
    original_mark_page_sources_checked = research_collector.mark_page_sources_checked

    class Page:
        name = "trendforce_research_latest"

    page = Page()

    def fake_run_rss_once(feeds: dict[str, str], notify_baseline: bool = False) -> int:
        calls.append(("rss", feeds, notify_baseline))
        return 2

    def fake_run_page_once(pages: list[object], notify_baseline: bool = False) -> int:
        calls.append(("pages", [item.name for item in pages], notify_baseline))
        return 1

    def fake_due_page_sources(pages: list[object], *, min_interval_seconds: int, force: bool):
        calls.append(("due", {"min_interval_seconds": min_interval_seconds, "force": force}, False))
        return pages, []

    def fake_mark_page_sources_checked(pages: list[object]) -> None:
        calls.append(("mark", [item.name for item in pages], False))

    try:
        research_collector.run_rss_once = fake_run_rss_once
        research_collector.run_page_once = fake_run_page_once
        research_collector.due_page_sources = fake_due_page_sources
        research_collector.mark_page_sources_checked = fake_mark_page_sources_checked
        payload = research_collector.collect_production(
            feeds={"semianalysis": "https://example.com/feed.xml"},
            page_sources=[page],  # type: ignore[list-item]
            notify_baseline=True,
            page_min_interval_seconds=900,
            force_pages=False,
        )
    finally:
        research_collector.run_rss_once = original_run_rss_once
        research_collector.run_page_once = original_run_page_once
        research_collector.due_page_sources = original_due_page_sources
        research_collector.mark_page_sources_checked = original_mark_page_sources_checked

    assert payload["mode"] == "production"
    assert payload["wrote_production_seen_items"] is True
    assert payload["counts"]["rss_new_items"] == 2
    assert payload["counts"]["page_new_items"] == 1
    assert payload["counts"]["new_items"] == 3
    assert calls == [
        ("rss", {"semianalysis": "https://example.com/feed.xml"}, True),
        ("due", {"min_interval_seconds": 900, "force": False}, False),
        ("pages", ["trendforce_research_latest"], True),
        ("mark", ["trendforce_research_latest"], False),
    ]


def main() -> int:
    test_research_sources_include_expected_groups()
    test_disabled_source_is_filtered()
    test_shadow_collect_rss_does_not_write_prod_seen_items()
    test_json_report_shape()
    test_production_collect_delegates_to_existing_pipelines()
    print("research collector checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
