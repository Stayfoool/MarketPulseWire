#!/usr/bin/env python3
"""Regression checks for the single unified event runtime."""

from __future__ import annotations

import inspect
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import ifind_batch
import market_runtime
import sina_flash
import sina_stock_news
from market_db import init_db
from settings_store import FIELDS_BY_KEY


def test_runtime_selects_only_unified_adapters() -> None:
    assert market_runtime._selected_module("article").__name__ == "market_content_adapter"
    assert market_runtime._selected_module("official").__name__ == "market_content_adapter"
    assert market_runtime._selected_module("event").__name__ == "market_event_adapter"
    assert "SURVEIL_MARKET_FLOW_DIRECT_PATH" not in FIELDS_BY_KEY
    assert "SURVEIL_CONTENT_DIRECT_PATH" not in FIELDS_BY_KEY
    assert "SURVEIL_EVENT_DIRECT_PATH" not in FIELDS_BY_KEY


def test_all_event_collectors_import_runtime_entrypoints() -> None:
    for module in (sina_flash, sina_stock_news, ifind_batch):
        assert module.process_market_item.__module__ == "market_runtime"
        source = inspect.getsource(module)
        for forbidden in (
            "content_runtime",
            "market_content_flow",
            "market_event_flow",
            "event_runtime",
            "event_pipeline",
        ):
            assert f"from {forbidden} import" not in source
            assert f"import {forbidden}" not in source


def test_ifind_batch_only_builds_notice_events() -> None:
    source = inspect.getsource(ifind_batch)
    assert "ifind_report" not in source
    assert "IFIND_RESEARCH" not in source
    event = ifind_batch.event_from_notice_row(
        {
            "thscode": "300308.SZ",
            "secName": "中际旭创",
            "reportTitle": "关于重大合同的公告",
            "reportDate": "2026-07-12",
            "seq": "notice-1",
        },
        {"300308.SZ": {"symbol": "300308.SZ", "name": "中际旭创"}},
        parse_pdf=False,
    )
    assert event["source"] == "ifind_notice"
    assert event["event_type"] == "announcement"
    assert event["symbols"] == ["300308.SZ"]


def test_unified_upsert_preserves_store_contract() -> None:
    event = {
        "source": "sina_flash",
        "source_event_id": "runtime-contract-1",
        "event_type": "flash_news",
        "title": "美国 CPI 大幅低于预期",
        "summary": "美债收益率下跌。",
        "published_at": "2026-07-12T00:00:00+00:00",
        "raw": {},
    }
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "unified.sqlite3"
        init_db(db_path).close()
        normalized = market_runtime.normalize_market_item("sina_flash", event, store_kind="event")
        first = market_runtime.process_market_item(
            normalized,
            event,
            store_kind="event",
            db_path=db_path,
            baseline_only=True,
        )
        assert first.event_id == 1
        assert first.inserted is True
        assert first.delivery_status == "baseline"
        assert first.flow_result.decision.action == "baseline"
        with sqlite3.connect(db_path) as conn:
            raw = json.loads(conn.execute("SELECT raw_json FROM events WHERE id = 1").fetchone()[0])
        assert raw["_normalized_market_item"]["source_category"] == "news_media"
        assert raw["_normalized_market_item"]["publisher_role"] == "news_media"
        assert raw["_normalized_market_item"]["content_type"] == "flash"
        second = market_runtime.process_market_item(
            normalized,
            event,
            store_kind="event",
            db_path=db_path,
            baseline_only=True,
        )
        assert second.event_id == first.event_id
        assert second.inserted is False
        assert second.delivery_status == "existing"


def test_sina_flash_uses_news_media_flash_shape() -> None:
    item = market_runtime.normalize_market_item(
        "sina_flash",
        {"source": "sina_flash", "source_event_id": "flash-1", "event_type": "flash_news", "title": "测试快讯"},
        store_kind="event",
    )
    assert item.source_category == "news_media"
    assert item.publisher_role == "news_media"
    assert item.content_type == "flash"


def main() -> int:
    test_runtime_selects_only_unified_adapters()
    test_all_event_collectors_import_runtime_entrypoints()
    test_ifind_batch_only_builds_notice_events()
    test_unified_upsert_preserves_store_contract()
    test_sina_flash_uses_news_media_flash_shape()
    print("event runtime checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
