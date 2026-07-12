#!/usr/bin/env python3
"""Regression checks for atomic event runtime route selection."""

from __future__ import annotations

import inspect
import json
import os
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import ifind_batch
import market_runtime
import sina_flash
import sina_stock_news
from market_db import init_db
from settings_store import FIELDS_BY_KEY


def test_global_switch_selects_exactly_one_runtime_module() -> None:
    original = {key: os.environ.get(key) for key in (market_runtime.DIRECT_PATH_ENV, *market_runtime.LEGACY_DIRECT_PATH_ENVS)}
    try:
        os.environ.pop(market_runtime.DIRECT_PATH_ENV, None)
        os.environ["SURVEIL_CONTENT_DIRECT_PATH"] = "1"
        os.environ["SURVEIL_EVENT_DIRECT_PATH"] = "0"
        assert market_runtime.runtime_path_name() == "compat"
        os.environ[market_runtime.DIRECT_PATH_ENV] = "1"
        assert market_runtime.runtime_path_name() == "direct"
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_all_event_collectors_import_runtime_entrypoints() -> None:
    for module in (sina_flash, sina_stock_news, ifind_batch):
        assert module.process_market_item.__module__ == "market_runtime"
        source = inspect.getsource(module)
        for forbidden in ("content_runtime", "market_content_flow", "market_event_flow", "event_runtime"):
            assert f"from {forbidden} import" not in source
            assert f"import {forbidden}" not in source


def test_global_switch_is_exposed_in_web_settings_registry() -> None:
    field = FIELDS_BY_KEY[market_runtime.DIRECT_PATH_ENV]
    assert field.group == "pipeline"
    assert field.sensitive is False
    assert "SURVEIL_CONTENT_DIRECT_PATH" not in FIELDS_BY_KEY
    assert "SURVEIL_EVENT_DIRECT_PATH" not in FIELDS_BY_KEY


def test_direct_and_compat_upsert_preserve_same_store_contract() -> None:
    event = {
        "source": "sina_flash",
        "source_event_id": "runtime-contract-1",
        "event_type": "flash_news",
        "title": "美国 CPI 大幅低于预期",
        "summary": "美债收益率下跌。",
        "published_at": "2026-07-12T00:00:00+00:00",
        "raw": {},
    }
    original = os.environ.get(market_runtime.DIRECT_PATH_ENV)
    try:
        with TemporaryDirectory() as tmpdir:
            for enabled, name in (("0", "compat.sqlite3"), ("1", "direct.sqlite3")):
                os.environ[market_runtime.DIRECT_PATH_ENV] = enabled
                db_path = Path(tmpdir) / name
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
    finally:
        if original is None:
            os.environ.pop(market_runtime.DIRECT_PATH_ENV, None)
        else:
            os.environ[market_runtime.DIRECT_PATH_ENV] = original


def test_sina_flash_uses_news_media_flash_shape() -> None:
    item = market_runtime.normalize_market_item(
        "sina_flash",
        {"source": "sina_flash", "source_event_id": "flash-1", "event_type": "flash_news", "title": "测试快讯"},
        store_kind="event",
    )
    assert item.source_category == "news_media"
    assert item.content_type == "flash"


def main() -> int:
    test_global_switch_selects_exactly_one_runtime_module()
    test_all_event_collectors_import_runtime_entrypoints()
    test_global_switch_is_exposed_in_web_settings_registry()
    test_direct_and_compat_upsert_preserve_same_store_contract()
    test_sina_flash_uses_news_media_flash_shape()
    print("event runtime checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
