#!/usr/bin/env python3
"""Regression checks for the company-feed collector."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

import official_collector
from source_profiles import save_source_profile_config


ROOT = Path(__file__).resolve().parents[1]
TEST_RULE_CONFIG = ROOT / "config" / "rule_core_v1.test.json"


def test_official_sources_include_expected_company_feeds() -> None:
    with TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "source_profiles.local.json"
        feeds = official_collector.official_rss_feeds(config_path=config_path)
        assert {"openai_news", "nvidia_blog", "samsung_semiconductor_news", "skhynix_newsroom"} <= set(feeds)
        assert "semianalysis" not in feeds
        assert "trendforce_semiconductors" not in feeds


def test_disabled_source_is_filtered() -> None:
    with TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "source_profiles.local.json"
        save_source_profile_config({"profiles": [{"id": "nvidia_blog", "enabled": False}]}, path=config_path)
        feeds = official_collector.selected_sources([], config_path=config_path)
        assert "nvidia_blog" not in feeds
        assert "openai_news" in feeds


def test_collect_delegates_to_unified_rss_pipeline() -> None:
    calls: list[tuple[dict[str, str], bool]] = []
    original = official_collector.run_rss_once

    def fake_run(feeds: dict[str, str], notify_baseline: bool = False) -> int:
        calls.append((feeds, notify_baseline))
        return 2

    try:
        official_collector.run_rss_once = fake_run
        payload = official_collector.collect_production(
            feeds={"nvidia_blog": "https://example.com/feed.xml"},
            notify_baseline=True,
        )
    finally:
        official_collector.run_rss_once = original

    assert payload["ok"] is True
    assert payload["counts"] == {"rss_sources": 1, "new_items": 2}
    assert calls == [({"nvidia_blog": "https://example.com/feed.xml"}, True)]


def main() -> int:
    previous = os.environ.get("RULE_CORE_CONFIG")
    os.environ["RULE_CORE_CONFIG"] = str(TEST_RULE_CONFIG)
    try:
        test_official_sources_include_expected_company_feeds()
        test_disabled_source_is_filtered()
        test_collect_delegates_to_unified_rss_pipeline()
    finally:
        if previous is None:
            os.environ.pop("RULE_CORE_CONFIG", None)
        else:
            os.environ["RULE_CORE_CONFIG"] = previous
    print("official collector checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
