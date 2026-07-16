#!/usr/bin/env python3
"""Regression checks for read-only event direct dry-run reports."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from event_direct_dry_run import EVENT_SOURCES, build_report, load_recent_events_read_only, write_report
from market_db import init_db


def insert_event_fixture(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events (
                source, source_event_id, event_type, title, summary, full_text, url,
                published_at, first_seen_at, symbols_json, themes_json, raw_json,
                content_hash, baseline_only
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                "sina_flash",
                "recent-1",
                "flash_news",
                "美国 CPI 大幅低于市场预期",
                "2年期美债收益率下跌。",
                "",
                "",
                "2026-07-12T00:00:00+00:00",
                "2026-07-12T00:00:01+00:00",
                "[]",
                '["宏观流动性/美联储政策"]',
                '{"source_event_id":"recent-1"}',
                "hash-recent-1",
            ),
        )


def test_canonical_report_covers_all_event_sources_and_declares_no_side_effects() -> None:
    with TemporaryDirectory() as tmpdir:
        missing_db = Path(tmpdir) / "missing.sqlite3"
        payload = build_report(
            db_path=missing_db,
            include_self_check=True,
            include_recent=False,
        )
    assert payload["ok"] is True
    assert set(payload["coverage"]) == set(EVENT_SOURCES)
    assert all(item["ok"] and item["rows"] == 1 for item in payload["coverage"].values())
    assert not missing_db.exists()
    assert all(value is False for value in payload["side_effects"].values())
    contexts = {
        row["source"]: row["result"]["normalized_item"]
        for row in payload["rows"]
    }
    assert contexts["sina_flash"]["source_category"] == "news_media"
    assert contexts["sina_stock_news"]["source_category"] == "portfolio_stock_news"
    assert contexts["company_disclosures"]["collector"] == "company_disclosures"
    assert contexts["ifind_notice"]["collector"] == "ifind_batch"


def test_recent_database_scan_is_read_only_and_report_write_only_creates_report() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        db_path = root / "surveil.sqlite3"
        report_dir = root / "reports"
        init_db(db_path).close()
        insert_event_fixture(db_path)
        before = db_path.read_bytes()

        events, error = load_recent_events_read_only(db_path, sources=["sina_flash"], limit=5)
        payload = build_report(
            db_path=db_path,
            sources=["sina_flash"],
            include_self_check=False,
            include_recent=True,
            recent_limit=5,
        )
        report_path = write_report(payload, report_dir)
        after = db_path.read_bytes()
        saved = json.loads(report_path.read_text(encoding="utf-8"))

    assert error == ""
    assert events[0]["source_event_id"] == "recent-1"
    assert payload["ok"] is True
    assert payload["rows"][0]["input_kind"] == "recent_database_event"
    assert payload["rows"][0]["result"]["decision"]["action"] == "push"
    assert before == after
    assert saved["side_effects"]["production_state_written"] is False


def main() -> int:
    test_canonical_report_covers_all_event_sources_and_declares_no_side_effects()
    test_recent_database_scan_is_read_only_and_report_write_only_creates_report()
    print("event direct dry-run checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
