#!/usr/bin/env python3
"""Regression checks for public WallstreetCN collection."""

from __future__ import annotations

from datetime import datetime, timezone

import wallstreetcn_monitor as monitor
from source_profiles import default_profile_map


ARTICLE_HTML = """
<html><body>
  <a href="/articles/3777001"><h2>美银大幅修正美联储利率路径</h2><time datetime="2026-07-14T10:00:00Z"></time></a>
  <a href="/articles/3777001"><span>重复入口</span></a>
  <a href="/member/articles/3777002"><h2>会员文章</h2></a>
</body></html>
"""

LIVE_HTML = """
<html><body>
  <a href="/livenews/3134001"><span>高盛下调美联储降息次数预测</span><time datetime="2026-07-14T10:01:00Z"></time></a>
</body></html>
"""

SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url><loc>https://wallstreetcn.com/articles/3775241</loc><lastmod>2026-06-22</lastmod>
    <news:news><news:publication_date>2026-06-22T16:39:42+00:00</news:publication_date>
      <news:title>美银大幅转鹰：预期美联储年内加息3次</news:title></news:news>
  </url>
</urlset>"""

DETAIL_HTML = """
<html><head>
  <meta property="og:title" content="美银大幅修正美联储利率路径" />
  <meta property="article:published_time" content="2026-07-14T10:00:00Z" />
  <meta name="author" content="华尔街见闻" />
</head><body><article><p>美银证券将此前不调整利率的预测改为年内加息三次，累计七十五个基点。</p></article></body></html>
"""


def test_list_parsing_and_canonical_ids() -> None:
    articles = monitor.parse_list_page(ARTICLE_HTML, surface="article", discovery_url="https://wallstreetcn.com/news/global")
    assert {item["id"] for item in articles} == {"article:3777001", "article:3777002"}
    public = next(item for item in articles if item["id"] == "article:3777001")
    assert public["url"] == "https://wallstreetcn.com/articles/3777001"
    assert public["published_at"] == "2026-07-14T10:00:00Z"
    member = next(item for item in articles if item["id"] == "article:3777002")
    assert member["raw"]["wallstreetcn_access_tier"] == "member"
    assert monitor.enrich_item(member)["_skip_decision"] is True

    live = monitor.parse_list_page(LIVE_HTML, surface="livenews", discovery_url="https://wallstreetcn.com/live")
    assert live[0]["id"] == "livenews:3134001"


def test_detail_enrichment_uses_public_body() -> None:
    original = monitor.http_get

    class Response:
        content = DETAIL_HTML.encode()
        url = "https://wallstreetcn.com/articles/3777001"

    try:
        monitor.http_get = lambda *_args, **_kwargs: Response()
        item = monitor.parse_list_page(ARTICLE_HTML, surface="article", discovery_url="https://wallstreetcn.com/news/global")[0]
        enriched = monitor.enrich_item(item)
    finally:
        monitor.http_get = original
    assert enriched["title"] == "美银大幅修正美联储利率路径"
    assert "累计七十五个基点" in enriched["full_text"]
    assert enriched["raw"]["wallstreetcn_detail_status"] == "ok"


def test_invalid_list_and_sitemap_fail_visibly() -> None:
    for function, payload, kwargs in (
        (monitor.parse_list_page, "<html></html>", {"surface": "article", "discovery_url": "x"}),
        (monitor.parse_sitemap, "<not-xml", {"surface": "article", "discovery_url": "x"}),
    ):
        try:
            function(payload, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid WallstreetCN payload must fail visibly")


def test_sitemap_preserves_news_title_and_publication_time() -> None:
    item = monitor.parse_sitemap(SITEMAP_XML, surface="article", discovery_url="https://wallstreetcn.com/sitemap.xml")[0]
    assert item["id"] == "article:3775241"
    assert item["title"].startswith("美银大幅转鹰")
    assert item["published_at"] == "2026-06-22T16:39:42+00:00"


def test_source_profile_is_peer_news_media() -> None:
    profile = default_profile_map()["wallstreetcn_news"]
    assert profile["category"] == "news_media"
    assert profile["publisher_role"] == "news_media"
    assert profile["service_units"] == ["surveil-news-collector.timer", "surveil-news-collector.service"]
    assert {row["source"] for row in profile["health_keys"]} == {"articles", "livenews", "detail"}


def test_discovery_health_excludes_sitemap_results() -> None:
    original = dict(monitor.LAST_SURFACE_RESULTS)
    try:
        monitor.LAST_SURFACE_RESULTS.clear()
        monitor.LAST_SURFACE_RESULTS.update(
            {
                "articles:global": {"surface": "article", "ok": True},
                "sitemap:article:202607": {"surface": "article", "ok": False},
                "livenews": {"surface": "livenews", "ok": True},
            }
        )
        assert monitor.discovery_surface_results("article") == [{"surface": "article", "ok": True}]
        assert monitor.discovery_surface_results("livenews") == [{"surface": "livenews", "ok": True}]
    finally:
        monitor.LAST_SURFACE_RESULTS.clear()
        monitor.LAST_SURFACE_RESULTS.update(original)


def test_month_rollover_reconciles_previous_state_month() -> None:
    original_list = monitor._fetch_list
    original_sitemap = monitor._sitemap_items
    calls: list[tuple[str, str]] = []
    current_month = datetime.now(timezone.utc).strftime("%Y%m")
    previous_state_month = "200001" if current_month != "200001" else "200002"
    try:
        monitor._fetch_list = lambda _url, surface: monitor.parse_list_page(
            ARTICLE_HTML if surface == "article" else LIVE_HTML,
            surface=surface,
            discovery_url="fixture",
        )
        monitor._sitemap_items = lambda surface, month: calls.append((surface, month)) or []
        monitor.collect_items(
            state={"last_sitemap_month": previous_state_month, "last_sitemap_reconcile_epoch": 0},
            force_reconcile=True,
        )
    finally:
        monitor._fetch_list = original_list
        monitor._sitemap_items = original_sitemap
    assert set(calls) == {
        ("article", current_month),
        ("livenews", current_month),
        ("article", previous_state_month),
        ("livenews", previous_state_month),
    }


if __name__ == "__main__":
    test_list_parsing_and_canonical_ids()
    test_detail_enrichment_uses_public_body()
    test_invalid_list_and_sitemap_fail_visibly()
    test_sitemap_preserves_news_title_and_publication_time()
    test_source_profile_is_peer_news_media()
    test_discovery_health_excludes_sitemap_results()
    test_month_rollover_reconciles_previous_state_month()
    print("WallstreetCN monitor tests passed")
