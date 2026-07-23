#!/usr/bin/env python3
"""Regression checks for official trade-policy source monitoring."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import trade_policy_monitor as monitor
from decision_engine import decide_market_item
from market_item import decision_result_from_payload
import market_delivery
from market_content_adapter import save_review
from market_db import init_db
from market_review_store import article_review_exists
from source_profiles import runtime_source_profile, save_source_profile_config, source_profile_enabled
from trade_policy_sources import TRADE_POLICY_SOURCE_MAP, TradePolicySource


FEDERAL_SAMPLE = {
    "results": [
        {
            "title": "Advanced Computing Export Controls for the People's Republic of China",
            "type": "Rule",
            "abstract": "Commerce expands export controls on advanced computing items for China.",
            "document_number": "2026-10001",
            "html_url": "https://www.federalregister.gov/documents/2026/07/14/2026-10001/example",
            "pdf_url": "https://www.govinfo.gov/example.pdf",
            "publication_date": "2026-07-14",
            "agencies": [{"name": "Industry and Security Bureau"}],
        },
        {
            "title": "Graphite electrodes from India",
            "abstract": "Commerce postpones the preliminary determination for imports from India.",
            "excerpts": "A historical filing also mentioned China.",
            "document_number": "2026-10002",
            "html_url": "https://www.federalregister.gov/documents/2026/07/14/2026-10002/example",
            "publication_date": "2026-07-14",
            "agencies": [{"name": "International Trade Administration"}],
        },
    ]
}

USTR_SAMPLE = """
<ul>
  <li>2026-07-14<br><a href="/about/policy-offices/press-office/press-releases/2026/july/ustr-china-section-301">
    USTR Seeks Public Comment on Proposed Section 301 Tariffs Covering China Semiconductor Imports
  </a></li>
</ul>
"""

EU_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><item>
  <title>Commission initiates anti-subsidy investigation into electric vehicles from China</title>
  <link>https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1000</link>
  <description>European Commission Press release Brussels.</description>
  <pubDate>Tue, 14 Jul 2026 08:00:00 GMT</pubDate>
  <guid>ip_26_1000</guid>
  <category>POLICY_AREA=TRADE</category>
</item></channel></rss>
"""

MOFCOM_POLICY_SAMPLE = """
<ul class="policy-list"><li><em>【对外贸易】</em>
<a href="/zcfb/zc/art/2026/art_us_control.html" title="商务部公告：将10家美国实体列入出口管制管控名单">公告</a>
<span>2026-07-14</span></li></ul>
"""

MOFCOM_SPOKESPERSON_SAMPLE = """
<ul><li><a href="/xwfb/xwfyrth/art/2026/art_eu_trade.html"
title="商务部新闻发言人就欧盟对华贸易保护主义措施答记者问">答记者问</a></li></ul>
"""


def test_official_source_parsers() -> None:
    federal = monitor.parse_federal_register_payload(
        FEDERAL_SAMPLE,
        TRADE_POLICY_SOURCE_MAP["federal_register_china_trade"],
    )
    assert len(federal) == 1
    assert federal[0]["id"] == "2026-10001"
    assert federal[0]["raw"]["agencies"] == ["Industry and Security Bureau"]
    assert "United States Industry and Security Bureau document concerning China" in federal[0]["summary"]

    ustr = monitor.parse_ustr_html(USTR_SAMPLE, TRADE_POLICY_SOURCE_MAP["ustr_press_releases"])
    assert len(ustr) == 1
    assert ustr[0]["published_at"] == "2026-07-14T00:00:00+00:00"

    eu = monitor.parse_eu_rss(EU_SAMPLE, TRADE_POLICY_SOURCE_MAP["eu_press_corner_trade_policy"])
    assert len(eu) == 1
    assert eu[0]["id"] == "ip_26_1000"

    policy = monitor.parse_mofcom_policy_html(
        MOFCOM_POLICY_SAMPLE,
        TRADE_POLICY_SOURCE_MAP["mofcom_policy_releases"],
    )
    assert len(policy) == 1
    assert policy[0]["title"].startswith("商务部公告")

    spokesperson = monitor.parse_mofcom_spokesperson_html(
        MOFCOM_SPOKESPERSON_SAMPLE,
        TRADE_POLICY_SOURCE_MAP["mofcom_spokesperson_statements"],
    )
    assert len(spokesperson) == 1
    assert "欧盟" in spokesperson[0]["title"]


def test_normalized_item_and_source_profile_use_unified_article_runtime() -> None:
    source = TRADE_POLICY_SOURCE_MAP["ustr_press_releases"]
    item = monitor.parse_ustr_html(USTR_SAMPLE, source)[0]
    normalized = monitor.normalized_trade_policy_item(item, source)
    assert normalized.source == "ustr_press_releases"
    assert normalized.source_category == "official_policy"
    assert normalized.publisher_role == "government_official"
    assert normalized.collector == "trade_policy_monitor"
    assert normalized.content_type == "official_policy"

    profile = runtime_source_profile(source.name)
    assert profile is not None
    assert profile["category"] == "official_policy"
    assert "trade_policy_monitor.py" in profile["fetcher"]
    assert profile["health_keys"] == [{"monitor": "trade_policy", "source": source.name}]

    federal_source = TRADE_POLICY_SOURCE_MAP["federal_register_china_trade"]
    federal_item = monitor.parse_federal_register_payload(FEDERAL_SAMPLE, federal_source)[0]
    federal_decision = decide_market_item(
        monitor.normalized_trade_policy_item(federal_item, federal_source),
        holdings=[],
    )
    assert federal_decision.action == "push"
    assert federal_decision.rule_hits[0]["rule_id"] == "trade_friction_escalation"


def test_source_can_be_disabled_from_private_profile_config() -> None:
    with TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "source_profiles.local.json"
        save_source_profile_config(
            {"profiles": [{"id": "ustr_press_releases", "enabled": False}]},
            path=config_path,
        )
        assert source_profile_enabled("ustr_press_releases", config_path=config_path) is False


def test_empty_or_invalid_payload_is_visible_as_parse_failure() -> None:
    source = TRADE_POLICY_SOURCE_MAP["federal_register_china_trade"]
    try:
        monitor.parse_federal_register_payload({}, source)
    except ValueError as exc:
        assert "lacks results" in str(exc)
    else:
        raise AssertionError("missing Federal Register results must fail")


def test_notify_item_uses_unified_process_market_item() -> None:
    source = TRADE_POLICY_SOURCE_MAP["ustr_press_releases"]
    item = monitor.parse_ustr_html(USTR_SAMPLE, source)[0]
    calls = []
    original_process = monitor.process_market_item
    original_connect = monitor.connect_db
    original_db_path = monitor.DB_PATH
    original_enrich = monitor.enrich_item

    def fake_process(normalized, raw_item, **kwargs):
        calls.append((normalized, raw_item, kwargs))
        decision = SimpleNamespace(importance="high", action="push")
        return SimpleNamespace(flow_result=SimpleNamespace(decision=decision), delivery_status="sent")

    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        try:
            monitor.connect_db = lambda: sqlite3.connect(db_path)
            monitor.DB_PATH = db_path
            monitor.enrich_item = lambda value: dict(value)
            with sqlite3.connect(db_path) as conn:
                monitor.save_new_items(conn, source.name, [item], notify_baseline=True)
            monitor.process_market_item = fake_process
            monitor.notify_item(item, source=source)
        finally:
            monitor.process_market_item = original_process
            monitor.connect_db = original_connect
            monitor.DB_PATH = original_db_path
            monitor.enrich_item = original_enrich

    assert len(calls) == 1
    normalized, raw_item, kwargs = calls[0]
    assert normalized.source == source.name
    assert raw_item["id"] == item["id"]
    assert kwargs["store_kind"] == "article"
    assert kwargs["source_profile_id"] == source.name
    assert kwargs["use_rule_dedup"] is True
    assert kwargs["production_admission"].status == "admitted"
    assert kwargs["production_admission"].matched_families == ("trade_policy",)


def test_baseline_duplicate_health_and_new_item_processing() -> None:
    source = TradePolicySource(
        name="test_trade_policy",
        module="Test official trade policy",
        url="https://example.com/trade",
        parser="ustr_html",
        access_note="test",
    )
    first = {
        "id": "first",
        "url": "https://example.com/first",
        "title": "USTR seeks public comment on proposed Section 301 tariffs covering China semiconductors",
        "summary": "",
        "published_at": "2026-07-14T00:00:00+00:00",
        "source_module": source.module,
    }
    second = {**first, "id": "second", "url": "https://example.com/second"}
    discovered = [first]
    notified: list[str] = []

    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        original_connect = monitor.connect_db
        original_db_path = monitor.DB_PATH
        original_discover = monitor.discover_items
        original_enabled = monitor.source_profile_enabled
        original_notify = monitor.notify_item
        try:
            monitor.connect_db = lambda: sqlite3.connect(db_path)
            monitor.DB_PATH = db_path
            monitor.discover_items = lambda _source: list(discovered)
            monitor.source_profile_enabled = lambda _name: True
            monitor.notify_item = lambda item, source: notified.append(str(item["id"]))

            assert monitor.run_once([source], notify_baseline=False) == 0
            assert notified == []
            assert monitor.run_once([source], notify_baseline=False) == 0
            assert notified == []
            discovered.append(second)
            assert monitor.run_once([source], notify_baseline=False) == 1
            assert notified == ["second"]

            with sqlite3.connect(db_path) as conn:
                seen_count = conn.execute(
                    "SELECT COUNT(*) FROM seen_items WHERE source = ?", (source.name,)
                ).fetchone()[0]
                health = conn.execute(
                    "SELECT consecutive_failures, last_success_at FROM source_health WHERE monitor = ? AND source = ?",
                    (monitor.MONITOR, source.name),
                ).fetchone()
        finally:
            monitor.connect_db = original_connect
            monitor.DB_PATH = original_db_path
            monitor.discover_items = original_discover
            monitor.source_profile_enabled = original_enabled
            monitor.notify_item = original_notify

    assert seen_count == 2
    assert health[0] == 0
    assert health[1]


def test_parse_failure_updates_source_health() -> None:
    source = TradePolicySource("test_failure", "Test failure", "https://example.com", "ustr_html", "test")
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        original_connect = monitor.connect_db
        original_discover = monitor.discover_items
        original_enabled = monitor.source_profile_enabled
        try:
            monitor.connect_db = lambda: sqlite3.connect(db_path)
            monitor.discover_items = lambda _source: (_ for _ in ()).throw(ValueError("parsed zero items"))
            monitor.source_profile_enabled = lambda _name: True
            assert monitor.run_once([source]) == 0
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT consecutive_failures, last_error FROM source_health WHERE monitor = ? AND source = ?",
                    (monitor.MONITOR, source.name),
                ).fetchone()
        finally:
            monitor.connect_db = original_connect
            monitor.discover_items = original_discover
            monitor.source_profile_enabled = original_enabled
    assert row[0] == 1
    assert "parsed zero items" in row[1]


def test_storage_audit_retains_final_trade_decision() -> None:
    source = TRADE_POLICY_SOURCE_MAP["ustr_press_releases"]
    item = monitor.parse_ustr_html(USTR_SAMPLE, source)[0]
    review = {
        "importance": "low",
        "push_now": False,
        "affected_targets": [],
        "reason": "compatibility input",
        "daily_summary": item["title"],
        "confidence": "low",
        "raw": {},
    }
    original_send = market_delivery.send_card
    cards: list[dict] = []
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        conn = sqlite3.connect(db_path)
        try:
            save_review(conn, source.name, item, review)
            row = conn.execute(
                "SELECT push_now, gate_json FROM article_reviews WHERE source = ? AND item_id = ?",
                (source.name, item["id"]),
            ).fetchone()
        finally:
            conn.close()
        with sqlite3.connect(db_path) as conn:
            loaded = article_review_exists(conn, source.name, item["id"])
        assert loaded is not None
        decision = decision_result_from_payload(loaded)
        assert decision is not None
        try:
            market_delivery.send_card = lambda card: cards.append(card) or True
            delivery_status = market_delivery.deliver_article_review(
                source.name,
                item,
                loaded,
                decision=decision,
                db_path=db_path,
                use_rule_dedup=False,
            )
        finally:
            market_delivery.send_card = original_send
        with sqlite3.connect(db_path) as conn:
            pushed_at = conn.execute(
                "SELECT pushed_at FROM article_reviews WHERE source = ? AND item_id = ?",
                (source.name, item["id"]),
            ).fetchone()[0]
    gate = json.loads(row[1])
    assert row[0] == 0
    assert gate["raw"]["decision_result"]["action"] == "push"
    assert gate["raw"]["decision_result"]["rule_hits"][0]["rule_id"] == "trade_friction_escalation"
    assert gate["raw"]["decision_final_fields"]["push_now"] is False
    assert delivery_status == "sent"
    assert len(cards) == 1
    assert pushed_at


def main() -> int:
    test_official_source_parsers()
    test_normalized_item_and_source_profile_use_unified_article_runtime()
    test_source_can_be_disabled_from_private_profile_config()
    test_empty_or_invalid_payload_is_visible_as_parse_failure()
    test_notify_item_uses_unified_process_market_item()
    test_baseline_duplicate_health_and_new_item_processing()
    test_parse_failure_updates_source_health()
    test_storage_audit_retains_final_trade_decision()
    print("trade policy monitor checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
