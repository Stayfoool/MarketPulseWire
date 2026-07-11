#!/usr/bin/env python3
"""Regression checks for the ValueList browser-backed monitor."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import value_directory_monitor
from source_profiles import runtime_source_profile
from value_directory_browser import classify_page_state, dedupe_entries, normalize_entry


def test_normalize_entry_extracts_stable_id_and_utc_date() -> None:
    item = normalize_entry(
        {
            "published": "2026-07-10",
            "title": "高盛-宁德时代(300750.SZ)：首次覆盖评为买入(摘要)-20260709【35页】",
            "url": "https://www.valuelist.cn/862550.html",
        }
    )
    assert item is not None
    assert item["id"] == "862550"
    assert item["published_at"] == "2026-07-09T16:00:00+00:00"
    assert item["source_module"] == "价值目录 / 国际投行-个股"
    assert item["full_text"].startswith("高盛-宁德时代")


def test_page_state_detection_separates_waf_login_and_empty() -> None:
    assert classify_page_state("宝塔防火墙正在检查您的访问", article_count=0) == "waf"
    assert classify_page_state("请先 登录 后继续", article_count=0, url="https://www.valuelist.cn/login") == "login"
    assert classify_page_state("正常页面", article_count=0) == "empty"
    assert classify_page_state("正常页面", article_count=3) == "ok"


def test_dedupe_entries_keeps_first_valid_url() -> None:
    rows = dedupe_entries(
        [
            {"title": "A", "url": "https://www.valuelist.cn/1.html", "published": "2026-07-10"},
            {"title": "A duplicate", "url": "https://www.valuelist.cn/1.html", "published": "2026-07-10"},
            {"title": "", "url": "https://www.valuelist.cn/2.html", "published": "2026-07-10"},
        ]
    )
    assert len(rows) == 1
    assert rows[0]["id"] == "1"
    assert rows[0]["title"] == "A"


def test_shadow_payload_marks_seen_and_reviewed_without_delivery() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        with sqlite3.connect(db_path) as conn:
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
                CREATE TABLE article_reviews (
                    source TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    gate_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (source, item_id)
                )
                """
            )
            conn.execute(
                "INSERT INTO seen_items VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("value_directory_ib_stocks", "862550", "", "", "", "", "2026-07-10T00:00:00+00:00"),
            )
            conn.execute(
                "INSERT INTO article_reviews VALUES (?, ?, ?, ?, ?)",
                ("value_directory_ib_stocks", "862550", "demo", "{}", "2026-07-10T00:00:00+00:00"),
            )
        original_db = value_directory_monitor.DB_PATH
        try:
            value_directory_monitor.DB_PATH = db_path
            payload = value_directory_monitor.shadow_payload(
                [
                    {
                        "id": "862550",
                        "url": "https://www.valuelist.cn/862550.html",
                        "title": "高盛-宁德时代：首次覆盖买入",
                        "summary": "高盛-宁德时代：首次覆盖买入",
                        "published_at": "2026-07-09T16:00:00+00:00",
                    }
                ],
                started_at="2026-07-10T00:00:00+00:00",
            )
        finally:
            value_directory_monitor.DB_PATH = original_db
    assert payload["sent_feishu"] is False
    assert payload["ran_llm_review"] is False
    assert payload["candidates"][0]["already_seen"] is True
    assert payload["candidates"][0]["already_reviewed"] is True


def test_source_profile_registers_value_directory() -> None:
    profile = runtime_source_profile("value_directory_ib_stocks")
    assert profile is not None
    assert profile["category"] == "research_industry_media"
    assert "surveil-value-directory.timer" in profile["service_units"]
    assert profile["skeptic_enabled"] is False


class _DummyContext:
    def __enter__(self):
        return object()

    def __exit__(self, *_):
        return False


def test_recheck_keeps_existing_review_without_new_rule() -> None:
    item = {
        "id": "862592",
        "url": "https://www.valuelist.cn/862592.html",
        "title": "高盛-交易思路：做多中国人工智能价值链",
        "summary": "Trade Idea",
        "published_at": "2026-07-09T16:00:00+00:00",
    }
    existing = {"push_now": False, "pushed_at": "", "importance": "medium"}
    original_connect = value_directory_monitor.connect_db
    original_existing = value_directory_monitor.article_review_exists
    original_rule = value_directory_monitor.rule_first_review
    try:
        value_directory_monitor.connect_db = lambda: _DummyContext()
        value_directory_monitor.article_review_exists = lambda *_: existing
        value_directory_monitor.rule_first_review = lambda *_: None
        assert value_directory_monitor.review_and_maybe_push(item, recheck_rules=True) is False
    finally:
        value_directory_monitor.connect_db = original_connect
        value_directory_monitor.article_review_exists = original_existing
        value_directory_monitor.rule_first_review = original_rule


def main() -> int:
    test_normalize_entry_extracts_stable_id_and_utc_date()
    test_page_state_detection_separates_waf_login_and_empty()
    test_dedupe_entries_keeps_first_valid_url()
    test_shadow_payload_marks_seen_and_reviewed_without_delivery()
    test_source_profile_registers_value_directory()
    test_recheck_keeps_existing_review_without_new_rule()
    print("value directory monitor checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
