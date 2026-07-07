#!/usr/bin/env python3
"""Regression checks for shared collector runtime helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from collector_runtime import (
    filter_enabled_mapping_for_run,
    filter_enabled_named_for_run,
    load_source_state,
    save_source_state,
    source_state_key,
    split_sources_by_backoff,
)
from source_profiles import save_source_profile_config


class NamedSource:
    def __init__(self, name: str) -> None:
        self.name = name


def test_source_profile_filtering_for_mapping_and_named_sources() -> None:
    with TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "source_profiles.local.json"
        save_source_profile_config(
            {"profiles": [{"id": "semianalysis", "enabled": False}]},
            path=config_path,
        )
        mapping = {"semianalysis": "feed-a", "nvidia_blog": "feed-b"}
        assert filter_enabled_mapping_for_run(mapping, label="test", config_path=config_path) == {
            "nvidia_blog": "feed-b"
        }
        named = [NamedSource("semianalysis"), NamedSource("nvidia_blog")]
        enabled = filter_enabled_named_for_run(named, label="test", config_path=config_path)
        assert [item.name for item in enabled] == ["nvidia_blog"]


def test_source_state_roundtrip_with_prefix() -> None:
    conn = sqlite3.connect(":memory:")
    assert source_state_key("demo", prefix="rss_feed") == "rss_feed:demo"
    save_source_state(conn, "demo", {"etag": '"abc"'}, prefix="rss_feed")
    assert load_source_state(conn, "demo", prefix="rss_feed") == {"etag": '"abc"'}


def test_split_sources_by_backoff() -> None:
    sources = ["ready", "waiting"]
    states = {"waiting": {"skip_until": "2099-01-01T00:00:00+00:00"}}
    runnable, skipped = split_sources_by_backoff(sources, states)
    assert runnable == ["ready"]
    assert skipped == {"waiting"}


def main() -> int:
    test_source_profile_filtering_for_mapping_and_named_sources()
    test_source_state_roundtrip_with_prefix()
    test_split_sources_by_backoff()
    print("collector runtime checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
