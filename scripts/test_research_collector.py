#!/usr/bin/env python3
"""Regression checks for the research-source collector."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import research_collector
from source_profiles import save_source_profile_config


def test_research_sources_include_expected_groups() -> None:
    feeds = research_collector.research_rss_feeds()
    pages = {source.name for source in research_collector.research_page_sources()}
    alphabstract = {source.name for source in research_collector.research_alphabstract_sources()}
    assert {"semianalysis", "trendforce_semiconductors", "digitimes_en_daily"} <= set(feeds)
    assert "openai_news" not in feeds
    assert "trendforce_research_latest" in pages
    assert "alphabstract_summaries" in alphabstract


def test_disabled_source_is_filtered() -> None:
    with TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "source_profiles.local.json"
        save_source_profile_config(
            {
                "profiles": [
                    {"id": "semianalysis", "enabled": False},
                    {"id": "trendforce_research_latest", "enabled": False},
                    {"id": "alphabstract_summaries", "enabled": False},
                ]
            },
            path=config_path,
        )
        feeds, pages, alphabstract = research_collector.selected_sources([], config_path=config_path)
        assert "semianalysis" not in feeds
        assert "trendforce_research_latest" not in {source.name for source in pages}
        assert "alphabstract_summaries" not in {source.name for source in alphabstract}


def test_collect_delegates_to_unified_source_pipelines() -> None:
    calls: list[tuple[str, object, bool]] = []
    originals = (
        research_collector.run_rss_once,
        research_collector.run_page_once,
        research_collector.run_alphabstract_once,
        research_collector.due_page_sources,
        research_collector.mark_page_sources_checked,
    )

    class Source:
        def __init__(self, name: str):
            self.name = name

    page = Source("trendforce_research_latest")
    alpha = Source("alphabstract_summaries")
    research_collector.run_rss_once = lambda feeds, notify_baseline=False: calls.append(("rss", feeds, notify_baseline)) or 2
    research_collector.run_page_once = lambda sources, notify_baseline=False: calls.append(("pages", [s.name for s in sources], notify_baseline)) or 1
    research_collector.run_alphabstract_once = lambda sources, notify_baseline=False: calls.append(("alpha", [s.name for s in sources], notify_baseline)) or 3
    research_collector.due_page_sources = lambda sources, **_kwargs: (sources, [])
    research_collector.mark_page_sources_checked = lambda sources: calls.append(("mark", [s.name for s in sources], False))
    try:
        payload = research_collector.collect_production(
            feeds={"semianalysis": "https://example.com/feed.xml"},
            page_sources=[page],  # type: ignore[list-item]
            alphabstract_sources=[alpha],  # type: ignore[list-item]
            notify_baseline=True,
        )
    finally:
        (
            research_collector.run_rss_once,
            research_collector.run_page_once,
            research_collector.run_alphabstract_once,
            research_collector.due_page_sources,
            research_collector.mark_page_sources_checked,
        ) = originals

    assert payload["ok"] is True
    assert payload["counts"]["new_items"] == 6
    assert calls == [
        ("rss", {"semianalysis": "https://example.com/feed.xml"}, True),
        ("pages", ["trendforce_research_latest"], True),
        ("mark", ["trendforce_research_latest"], False),
        ("alpha", ["alphabstract_summaries"], True),
        ("mark", ["alphabstract_summaries"], False),
    ]


def main() -> int:
    test_research_sources_include_expected_groups()
    test_disabled_source_is_filtered()
    test_collect_delegates_to_unified_source_pipelines()
    print("research collector checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
