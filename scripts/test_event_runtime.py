#!/usr/bin/env python3
"""Regression checks for atomic event runtime route selection."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import event_runtime
import ifind_batch
import sina_flash
import sina_stock_news
from settings_store import FIELDS_BY_KEY


def test_global_switch_selects_exactly_one_runtime_module() -> None:
    original = os.environ.get(event_runtime.DIRECT_PATH_ENV)
    try:
        os.environ[event_runtime.DIRECT_PATH_ENV] = "0"
        assert event_runtime.runtime_path_name() == "compat"
        assert event_runtime.selected_event_module().__name__ == "event_pipeline"
        os.environ[event_runtime.DIRECT_PATH_ENV] = "1"
        assert event_runtime.runtime_path_name() == "direct"
        assert event_runtime.selected_event_module().__name__ == "market_event_flow"
    finally:
        if original is None:
            os.environ.pop(event_runtime.DIRECT_PATH_ENV, None)
        else:
            os.environ[event_runtime.DIRECT_PATH_ENV] = original


def test_runtime_dispatch_calls_selected_function_once() -> None:
    calls: list[tuple] = []
    original = event_runtime.selected_event_module
    fake = SimpleNamespace(
        upsert_event=lambda event, db_path: calls.append((event, db_path)) or (7, True),
    )
    try:
        event_runtime.selected_event_module = lambda: fake
        result = event_runtime.upsert_event({"source": "sina_flash"}, Path("/tmp/test.sqlite3"))
    finally:
        event_runtime.selected_event_module = original
    assert result == (7, True)
    assert len(calls) == 1


def test_all_event_collectors_import_runtime_entrypoints() -> None:
    for module in (sina_flash, sina_stock_news, ifind_batch):
        assert module.upsert_event.__module__ == "event_runtime"
        assert module.analyze_event.__module__ == "event_runtime"
        assert module.maybe_deliver_event.__module__ == "event_runtime"


def test_global_switch_is_exposed_in_web_settings_registry() -> None:
    field = FIELDS_BY_KEY[event_runtime.DIRECT_PATH_ENV]
    assert field.group == "pipeline"
    assert field.sensitive is False


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
    original = os.environ.get(event_runtime.DIRECT_PATH_ENV)
    try:
        with TemporaryDirectory() as tmpdir:
            for enabled, name in (("0", "compat.sqlite3"), ("1", "direct.sqlite3")):
                os.environ[event_runtime.DIRECT_PATH_ENV] = enabled
                event_id, inserted = event_runtime.upsert_event(event, Path(tmpdir) / name)
                assert event_id == 1
                assert inserted is True
                same_event_id, inserted_again = event_runtime.upsert_event(event, Path(tmpdir) / name)
                assert same_event_id == event_id
                assert inserted_again is False
    finally:
        if original is None:
            os.environ.pop(event_runtime.DIRECT_PATH_ENV, None)
        else:
            os.environ[event_runtime.DIRECT_PATH_ENV] = original


def main() -> int:
    test_global_switch_selects_exactly_one_runtime_module()
    test_runtime_dispatch_calls_selected_function_once()
    test_all_event_collectors_import_runtime_entrypoints()
    test_global_switch_is_exposed_in_web_settings_registry()
    test_direct_and_compat_upsert_preserve_same_store_contract()
    print("event runtime checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
