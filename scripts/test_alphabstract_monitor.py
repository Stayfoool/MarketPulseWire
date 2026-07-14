#!/usr/bin/env python3
"""Regression checks for AlphaAbstract public-summary monitoring."""

from __future__ import annotations

from types import SimpleNamespace

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


def test_sitemap_filters_summary_articles() -> None:
    entries = alphabstract_monitor.parse_sitemap_entries(SAMPLE_SITEMAP)
    assert entries == [
        {
            "url": "https://alphabstract.com/summaries/dylan-patel/dylan_patel_ai_infrastructure_stack_investment_thesis",
            "lastmod": "2026-07-08T00:00:00+00:00",
        }
    ]


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
    item = alphabstract_monitor.normalize_entry_from_article(
        "https://alphabstract.com/summaries/dylan-patel/dylan_patel_ai_infrastructure_stack_investment_thesis",
        SAMPLE_ARTICLE,
    )
    assert item is not None
    calls = []
    original_process = alphabstract_monitor.process_market_item

    def fake_process(normalized, raw_item, **kwargs):
        calls.append((normalized, raw_item, kwargs))
        decision = SimpleNamespace(importance="high", action="push")
        return SimpleNamespace(flow_result=SimpleNamespace(decision=decision), delivery_status="sent")

    try:
        alphabstract_monitor.process_market_item = fake_process
        alphabstract_monitor.notify_item(item)
    finally:
        alphabstract_monitor.process_market_item = original_process

    assert len(calls) == 1
    normalized, raw_item, kwargs = calls[0]
    assert normalized.source == "alphabstract_summaries"
    assert normalized.content_type == "research_summary"
    assert raw_item is item
    assert kwargs["store_kind"] == "article"
    assert kwargs["source_profile_id"] == "alphabstract_summaries"
    assert kwargs["use_rule_dedup"] is True


def main() -> int:
    test_sitemap_filters_summary_articles()
    test_article_parser_preserves_public_provenance()
    test_normalized_item_uses_unified_research_shape()
    test_source_profile_registers_alphabstract()
    test_notify_item_uses_process_market_item()
    print("alphabstract monitor checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
