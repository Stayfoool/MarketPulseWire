#!/usr/bin/env python3
"""Regression checks for AlphaAbstract public-summary monitoring."""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import alphabstract_monitor
from source_profiles import runtime_source_profile


SAMPLE_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://alphabstract.com/</loc>
    <lastmod>2026-07-12T17:27:17.318Z</lastmod>
  </url>
  <url>
    <loc>https://alphabstract.com/summaries/dylan-patel/dylan_patel_ai_infrastructure_stack_investment_thesis</loc>
    <lastmod>2026-07-08T00:00:00.000Z</lastmod>
  </url>
  <url>
    <loc>https://alphabstract.com/summaries</loc>
    <lastmod>2026-07-12T17:27:30.984Z</lastmod>
  </url>
</urlset>
"""


SAMPLE_ARTICLE = """
<!doctype html>
<html>
<head>
  <title>Dylan Patel — AI Infrastructure Stack Investment Thesis · AlphaAbstract</title>
  <meta property="og:title" content="Dylan Patel — AI Infrastructure Stack Investment Thesis" />
  <meta name="description" content="Source: Dylan Patel on the infrastructure powering the AI revolution." />
  <meta property="article:published_time" content="2026-07-08" />
  <meta property="article:modified_time" content="2026-07-12T17:27:17.310Z" />
  <link rel="canonical" href="https://alphabstract.com/summaries/dylan-patel/dylan_patel_ai_infrastructure_stack_investment_thesis" />
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "Article",
    "headline": "Dylan Patel — AI Infrastructure Stack Investment Thesis",
    "description": "Source: Dylan Patel on the infrastructure powering the AI revolution.",
    "url": "https://alphabstract.com/summaries/dylan-patel/dylan_patel_ai_infrastructure_stack_investment_thesis",
    "datePublished": "2026-07-08",
    "dateModified": "2026-07-12T17:27:17.310Z",
    "author": {"@type": "Person", "name": "Dylan Patel"},
    "publisher": {"@type": "Organization", "name": "Alpha Abstract"},
    "isBasedOn": {
      "@type": "VideoObject",
      "name": "Dylan Patel on the infrastructure powering the AI revolution",
      "url": "https://www.youtube.com/watch?v=lHnxU9f-rwc"
    }
  }
  </script>
</head>
<body>
  <article>
    <div class="summary-prose prose">
      <h1>Dylan Patel — AI Infrastructure Stack Investment Thesis</h1>
      <p>Memory pricing is up ~<strong>4x</strong> and Patel sees another <strong>2-3x</strong>.</p>
      <p>CPO is not 2027; tail-end <strong>2028</strong>, real scale-up ramp <strong>2029</strong>.</p>
    </div>
    <div class="mt-12">Disclaimer</div>
  </article>
</body>
</html>
"""
SAMPLE_URL = "https://alphabstract.com/summaries/dylan-patel/dylan_patel_ai_infrastructure_stack_investment_thesis"


def sample_article_for(url: str) -> str:
    return SAMPLE_ARTICLE.replace(SAMPLE_URL, url)


@contextmanager
def temporary_database() -> Iterator[Path]:
    original_db = alphabstract_monitor.DB_PATH
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.sqlite3"
        alphabstract_monitor.DB_PATH = db_path
        try:
            yield db_path
        finally:
            alphabstract_monitor.DB_PATH = original_db


def fake_outcome(action: str = "push") -> SimpleNamespace:
    decision = SimpleNamespace(importance="high", action=action)
    return SimpleNamespace(flow_result=SimpleNamespace(decision=decision), delivery_status="sent")


def test_sitemap_filters_summary_articles() -> None:
    entries = alphabstract_monitor.parse_sitemap_entries(SAMPLE_SITEMAP)
    assert entries == [
        {
            "url": "https://alphabstract.com/summaries/dylan-patel/dylan_patel_ai_infrastructure_stack_investment_thesis",
            "lastmod": "2026-07-08T00:00:00+00:00",
        }
    ]
    assert not alphabstract_monitor.is_summary_url("https://notalphabstract.com/summaries/example/item")


def test_sitemap_discovery_does_not_fetch_summary_pages() -> None:
    calls: list[str] = []
    original_fetch_entries = alphabstract_monitor.fetch_sitemap_entries
    original_fetch_text = alphabstract_monitor.fetch_text

    def unexpected_fetch(url: str) -> str:
        calls.append(url)
        raise AssertionError("discovery must not fetch an AlphaAbstract summary page")

    try:
        alphabstract_monitor.fetch_sitemap_entries = lambda source=alphabstract_monitor.DEFAULT_SOURCE: [
            {
                "url": "https://alphabstract.com/summaries/dylan-patel/dylan_patel_ai_infrastructure_stack_investment_thesis",
                "lastmod": "2026-07-08T00:00:00+00:00",
            }
        ]
        alphabstract_monitor.fetch_text = unexpected_fetch
        items = alphabstract_monitor.discover_items()
    finally:
        alphabstract_monitor.fetch_sitemap_entries = original_fetch_entries
        alphabstract_monitor.fetch_text = original_fetch_text

    assert calls == []
    assert items[0]["id"].startswith("summaries/dylan-patel/")
    assert items[0]["title"] == ""
    assert items[0]["raw"]["sitemap_lastmod"] == "2026-07-08T00:00:00+00:00"


def test_article_parser_preserves_public_provenance() -> None:
    item = alphabstract_monitor.normalize_entry_from_article(
        "https://alphabstract.com/summaries/dylan-patel/dylan_patel_ai_infrastructure_stack_investment_thesis",
        SAMPLE_ARTICLE,
        sitemap_lastmod="2026-07-08T00:00:00+00:00",
    )
    assert item is not None
    assert item["id"] == "summaries/dylan-patel/dylan_patel_ai_infrastructure_stack_investment_thesis"
    assert item["title"] == "Dylan Patel — AI Infrastructure Stack Investment Thesis"
    assert item["published_at"] == "2026-07-08T00:00:00+00:00"
    assert "Memory pricing is up" in item["full_text"]
    assert "scale-up ramp 2029" in item["full_text"]
    assert item["raw"]["author"] == "Dylan Patel"
    assert item["raw"]["original_source_url"] == "https://www.youtube.com/watch?v=lHnxU9f-rwc"
    assert item["raw"]["modified_at"] == "2026-07-12T17:27:17.310000+00:00"


def test_normalized_item_uses_unified_research_shape() -> None:
    item = alphabstract_monitor.normalize_entry_from_article(
        "https://alphabstract.com/summaries/dylan-patel/dylan_patel_ai_infrastructure_stack_investment_thesis",
        SAMPLE_ARTICLE,
    )
    assert item is not None
    normalized = alphabstract_monitor.normalized_alphabstract_item(item)
    assert normalized.source == "alphabstract_summaries"
    assert normalized.source_category == "research_industry_media"
    assert normalized.publisher_role == "third_party_research_summary"
    assert normalized.collector == "alphabstract_monitor"
    assert normalized.content_type == "research_summary"
    assert normalized.raw["original_source_url"].startswith("https://www.youtube.com/")


def test_source_profile_registers_alphabstract() -> None:
    profile = runtime_source_profile("alphabstract_summaries")
    assert profile is not None
    assert profile["category"] == "research_industry_media"
    assert "alphabstract_monitor.py" in profile["fetcher"]
    assert profile["health_keys"] == [{"monitor": "alphabstract", "source": "alphabstract_summaries"}]


def test_notify_item_uses_process_market_item() -> None:
    url = "https://alphabstract.com/summaries/dylan-patel/dylan_patel_ai_infrastructure_stack_investment_thesis"
    item = {
        "id": alphabstract_monitor.canonical_article_id(url),
        "url": url,
        "title": "",
        "summary": "",
        "published_at": "2026-07-08T00:00:00+00:00",
        "raw": {"sitemap_lastmod": "2026-07-08T00:00:00+00:00"},
    }
    calls: list[tuple] = []
    original_process = alphabstract_monitor.process_market_item
    original_fetch = alphabstract_monitor.fetch_text

    def fake_process(normalized, raw_item, **kwargs):
        calls.append((normalized, raw_item, kwargs))
        return fake_outcome()

    with temporary_database():
        try:
            with alphabstract_monitor.connect_db() as conn:
                conn.execute(
                    "INSERT INTO seen_sources (source, first_seen_at) VALUES (?, ?)",
                    (alphabstract_monitor.SOURCE_ID, "2026-07-08T00:00:00+00:00"),
                )
                selected = alphabstract_monitor.save_new_items(
                    conn,
                    alphabstract_monitor.SOURCE_ID,
                    [item],
                )
            assert len(selected) == 1
            alphabstract_monitor.fetch_text = lambda requested_url: SAMPLE_ARTICLE
            alphabstract_monitor.process_market_item = fake_process
            alphabstract_monitor.notify_item(item)
            with alphabstract_monitor.connect_db() as conn:
                row = conn.execute(
                    """
                    SELECT title, summary, processability_status, admission_status,
                           processing_status, processing_error
                    FROM seen_items WHERE source = ? AND item_id = ?
                    """,
                    (alphabstract_monitor.SOURCE_ID, item["id"]),
                ).fetchone()
        finally:
            alphabstract_monitor.process_market_item = original_process
            alphabstract_monitor.fetch_text = original_fetch

    assert len(calls) == 1
    normalized, raw_item, kwargs = calls[0]
    assert normalized.source == "alphabstract_summaries"
    assert normalized.content_type == "research_summary"
    assert raw_item["title"].startswith("Dylan Patel")
    assert "Memory pricing is up" in raw_item["full_text"]
    assert kwargs["store_kind"] == "article"
    assert kwargs["source_profile_id"] == "alphabstract_summaries"
    assert kwargs["use_rule_dedup"] is True
    assert kwargs["production_admission"].status == "admitted"
    assert row is not None
    assert row[0].startswith("Dylan Patel")
    assert row[1].startswith("Source: Dylan Patel")
    assert row[2:] == ("succeeded", "admitted", "succeeded", "")


def test_expanded_scope_baseline_then_live_item_is_reserved_before_enrichment() -> None:
    old_url = "https://alphabstract.com/summaries/example/old-summary"
    new_url = "https://alphabstract.com/summaries/example/new-summary"
    entries = [{"url": old_url, "lastmod": "2026-07-20T00:00:00+00:00"}]
    detail_calls: list[str] = []
    detail_states: list[tuple[str, str, str]] = []
    process_states: list[tuple[str, str, str]] = []
    original_fetch_entries = alphabstract_monitor.fetch_sitemap_entries
    original_fetch = alphabstract_monitor.fetch_text
    original_process = alphabstract_monitor.process_market_item
    original_enabled = alphabstract_monitor.source_profile_enabled

    def fake_process(normalized, raw_item, **kwargs):
        with alphabstract_monitor.connect_db() as conn:
            state = conn.execute(
                """
                SELECT collection_class, processability_status, admission_status
                FROM seen_items WHERE source = ? AND item_id = ?
                """,
                (alphabstract_monitor.SOURCE_ID, alphabstract_monitor.canonical_article_id(new_url)),
            ).fetchone()
        assert state is not None
        process_states.append(tuple(state))
        return fake_outcome("daily")

    def fetch_detail(url: str) -> str:
        detail_calls.append(url)
        with alphabstract_monitor.connect_db() as conn:
            state = conn.execute(
                """
                SELECT collection_class, processability_status, admission_status
                FROM seen_items WHERE source = ? AND item_id = ?
                """,
                (alphabstract_monitor.SOURCE_ID, alphabstract_monitor.canonical_article_id(url)),
            ).fetchone()
        assert state is not None
        detail_states.append(tuple(state))
        return sample_article_for(url)

    with temporary_database():
        try:
            alphabstract_monitor.source_profile_enabled = lambda source: True
            alphabstract_monitor.fetch_sitemap_entries = lambda source=alphabstract_monitor.DEFAULT_SOURCE: list(entries)
            alphabstract_monitor.fetch_text = fetch_detail
            alphabstract_monitor.process_market_item = fake_process

            assert alphabstract_monitor.run_once() == 0
            assert detail_calls == []
            with alphabstract_monitor.connect_db() as conn:
                baseline = conn.execute(
                    """
                    SELECT collection_class, processability_status, admission_status, processing_status
                    FROM seen_items WHERE source = ? AND item_id = ?
                    """,
                    (alphabstract_monitor.SOURCE_ID, alphabstract_monitor.canonical_article_id(old_url)),
                ).fetchone()
                scope = alphabstract_monitor.load_scope_state(conn, alphabstract_monitor.SOURCE_ID)
            assert baseline == ("baseline", "not_required", "not_applicable", "not_applicable")
            assert scope[alphabstract_monitor.EXPANDED_SCOPE_BASELINE_STATE_KEY]

            entries.insert(0, {"url": new_url, "lastmod": "2026-07-22T00:00:00+00:00"})
            assert alphabstract_monitor.run_once() == 1
            assert detail_calls == [new_url]
            assert detail_states == [("live", "pending", "pending")]
            assert process_states == [("live", "succeeded", "admitted")]
            with alphabstract_monitor.connect_db() as conn:
                completed = conn.execute(
                    """
                    SELECT title, processability_status, admission_status, processing_status
                    FROM seen_items WHERE source = ? AND item_id = ?
                    """,
                    (alphabstract_monitor.SOURCE_ID, alphabstract_monitor.canonical_article_id(new_url)),
                ).fetchone()
            assert completed is not None
            assert completed[0].startswith("Dylan Patel")
            assert completed[1:] == ("succeeded", "admitted", "succeeded")
        finally:
            alphabstract_monitor.fetch_sitemap_entries = original_fetch_entries
            alphabstract_monitor.fetch_text = original_fetch
            alphabstract_monitor.process_market_item = original_process
            alphabstract_monitor.source_profile_enabled = original_enabled


def test_processing_failure_retries_after_successful_enrichment() -> None:
    url = "https://alphabstract.com/summaries/dylan-patel/dylan_patel_ai_infrastructure_stack_investment_thesis"
    item = {
        "id": alphabstract_monitor.canonical_article_id(url),
        "url": url,
        "title": "",
        "summary": "",
        "published_at": "2026-07-22T02:00:00+00:00",
        "raw": {"sitemap_lastmod": "2026-07-22T02:00:00+00:00"},
    }
    original_fetch = alphabstract_monitor.fetch_text
    original_process = alphabstract_monitor.process_market_item
    attempts = 0

    def process_with_one_failure(normalized, raw_item, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary review store failure")
        return fake_outcome("daily")

    with temporary_database():
        try:
            with alphabstract_monitor.connect_db() as conn:
                conn.execute(
                    "INSERT INTO seen_sources (source, first_seen_at) VALUES (?, ?)",
                    (alphabstract_monitor.SOURCE_ID, "2026-07-22T00:00:00+00:00"),
                )
                conn.commit()
                assert alphabstract_monitor.save_new_items(
                    conn,
                    alphabstract_monitor.SOURCE_ID,
                    [item],
                ) == [item]
            alphabstract_monitor.fetch_text = lambda requested_url: SAMPLE_ARTICLE
            alphabstract_monitor.process_market_item = process_with_one_failure
            try:
                alphabstract_monitor.notify_item(item)
            except RuntimeError as exc:
                assert "review store" in str(exc)
            else:
                raise AssertionError("processing failure should propagate after lifecycle persistence")

            with alphabstract_monitor.connect_db() as conn:
                failed = conn.execute(
                    """
                    SELECT processability_status, admission_status, processing_status
                    FROM seen_items WHERE source = ? AND item_id = ?
                    """,
                    (alphabstract_monitor.SOURCE_ID, item["id"]),
                ).fetchone()
                retry = alphabstract_monitor.save_new_items(
                    conn,
                    alphabstract_monitor.SOURCE_ID,
                    [item],
                )
            assert failed == ("succeeded", "admitted", "failed_retryable")
            assert len(retry) == 1
            assert retry[0]["_seen_item_retry"] is True

            alphabstract_monitor.notify_item(retry[0])
            with alphabstract_monitor.connect_db() as conn:
                completed = conn.execute(
                    """
                    SELECT processability_status, admission_status, processing_status
                    FROM seen_items WHERE source = ? AND item_id = ?
                    """,
                    (alphabstract_monitor.SOURCE_ID, item["id"]),
                ).fetchone()
            assert completed == ("succeeded", "admitted", "succeeded")
            assert attempts == 2
        finally:
            alphabstract_monitor.fetch_text = original_fetch
            alphabstract_monitor.process_market_item = original_process


def test_detail_failure_is_retryable_on_next_sitemap_discovery() -> None:
    url = "https://alphabstract.com/summaries/example/retry-summary"
    entries = [{"url": url, "lastmod": "2026-07-22T01:00:00+00:00"}]
    attempts = 0
    process_calls = 0
    original_fetch_entries = alphabstract_monitor.fetch_sitemap_entries
    original_fetch = alphabstract_monitor.fetch_text
    original_process = alphabstract_monitor.process_market_item
    original_enabled = alphabstract_monitor.source_profile_enabled

    def fetch_detail(requested_url: str) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("temporary detail timeout")
        return sample_article_for(requested_url)

    def fake_process(normalized, raw_item, **kwargs):
        nonlocal process_calls
        process_calls += 1
        return fake_outcome()

    with temporary_database():
        try:
            alphabstract_monitor.source_profile_enabled = lambda source: True
            alphabstract_monitor.fetch_sitemap_entries = lambda source=alphabstract_monitor.DEFAULT_SOURCE: list(entries)
            alphabstract_monitor.fetch_text = fetch_detail
            alphabstract_monitor.process_market_item = fake_process
            with alphabstract_monitor.connect_db() as conn:
                alphabstract_monitor.save_scope_state(
                    conn,
                    alphabstract_monitor.SOURCE_ID,
                    {alphabstract_monitor.EXPANDED_SCOPE_BASELINE_STATE_KEY: "2026-07-22T00:00:00+00:00"},
                )
                conn.execute(
                    "INSERT INTO seen_sources (source, first_seen_at) VALUES (?, ?)",
                    (alphabstract_monitor.SOURCE_ID, "2026-07-22T00:00:00+00:00"),
                )
                conn.commit()

            assert alphabstract_monitor.run_once() == 1
            with alphabstract_monitor.connect_db() as conn:
                failed = conn.execute(
                    """
                    SELECT collection_class, processability_status, admission_status, processing_status
                    FROM seen_items WHERE source = ? AND item_id = ?
                    """,
                    (alphabstract_monitor.SOURCE_ID, alphabstract_monitor.canonical_article_id(url)),
                ).fetchone()
            assert failed == ("live", "failed_retryable", "pending", "not_applicable")
            assert process_calls == 0

            assert alphabstract_monitor.run_once() == 1
            with alphabstract_monitor.connect_db() as conn:
                completed = conn.execute(
                    """
                    SELECT processability_status, admission_status, processing_status
                    FROM seen_items WHERE source = ? AND item_id = ?
                    """,
                    (alphabstract_monitor.SOURCE_ID, alphabstract_monitor.canonical_article_id(url)),
                ).fetchone()
            assert completed == ("succeeded", "admitted", "succeeded")
            assert attempts == 2
            assert process_calls == 1
        finally:
            alphabstract_monitor.fetch_sitemap_entries = original_fetch_entries
            alphabstract_monitor.fetch_text = original_fetch
            alphabstract_monitor.process_market_item = original_process
            alphabstract_monitor.source_profile_enabled = original_enabled


def test_empty_sitemap_does_not_establish_expanded_scope_baseline() -> None:
    original_fetch_entries = alphabstract_monitor.fetch_sitemap_entries
    original_enabled = alphabstract_monitor.source_profile_enabled
    with temporary_database():
        try:
            alphabstract_monitor.source_profile_enabled = lambda source: True
            alphabstract_monitor.fetch_sitemap_entries = lambda source=alphabstract_monitor.DEFAULT_SOURCE: []
            assert alphabstract_monitor.run_once() == 0
            with alphabstract_monitor.connect_db() as conn:
                assert alphabstract_monitor.load_scope_state(conn, alphabstract_monitor.SOURCE_ID) == {}
                assert conn.execute(
                    "SELECT COUNT(*) FROM seen_sources WHERE source = ?",
                    (alphabstract_monitor.SOURCE_ID,),
                ).fetchone()[0] == 0
        finally:
            alphabstract_monitor.fetch_sitemap_entries = original_fetch_entries
            alphabstract_monitor.source_profile_enabled = original_enabled


def main() -> int:
    test_sitemap_filters_summary_articles()
    test_sitemap_discovery_does_not_fetch_summary_pages()
    test_article_parser_preserves_public_provenance()
    test_normalized_item_uses_unified_research_shape()
    test_source_profile_registers_alphabstract()
    test_notify_item_uses_process_market_item()
    test_expanded_scope_baseline_then_live_item_is_reserved_before_enrichment()
    test_processing_failure_retries_after_successful_enrichment()
    test_detail_failure_is_retryable_on_next_sitemap_discovery()
    test_empty_sitemap_does_not_establish_expanded_scope_baseline()
    print("alphabstract monitor checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
