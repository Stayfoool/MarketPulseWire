#!/usr/bin/env python3
"""Regression checks for the single unified event runtime."""

from __future__ import annotations

import inspect
import json
import os
import sqlite3
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory

import ifind_batch
import company_disclosures
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
    for module in (sina_flash, sina_stock_news, ifind_batch, company_disclosures):
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


def test_sina_flash_current_admission_reports_macro_and_fed_families() -> None:
    macro_item = SimpleNamespace(
        symbols=(),
        raw={"macro_policy_line": {"matched": True, "tags": ["primary_data"]}},
    )
    fed_item = SimpleNamespace(
        symbols=(),
        raw={"macro_policy_line": {"matched": True, "tags": ["fed_event", "market_reaction"]}},
    )
    mixed_item = SimpleNamespace(
        symbols=("300308.SZ",),
        raw={"macro_policy_line": {"matched": True, "tags": ["primary_data", "fed_event"]}},
    )
    assert sina_flash.current_admission(macro_item)[2] == ("macro_data",)
    assert sina_flash.current_admission(fed_item)[2] == ("fed_policy",)
    assert sina_flash.current_admission(mixed_item)[2] == ("holding", "macro_data", "fed_policy")


def test_sina_flash_reserves_all_discoveries_before_current_admission() -> None:
    rows = [
        {"id": "old-related", "content": "中际旭创发布经营进展", "create_time": 1784736000},
        {"id": "old-unrelated", "content": "普通消费活动资讯", "create_time": 1784736001},
    ]
    holding = {"symbol": "300308.SZ", "name": "中际旭创", "full_name": "", "aliases": []}
    original_db = sina_flash.DEFAULT_DB_PATH
    original_import = sina_flash.import_holdings
    original_holdings = sina_flash.load_enabled_holdings
    original_enabled = sina_flash.source_profile_enabled
    original_fetch = sina_flash.fetch_sina_feed
    original_process = sina_flash.process_market_item
    original_compare = sina_flash.record_rule_comparison
    previous_notify = os.environ.pop("SURVEIL_NOTIFY_BASELINE", None)
    process_calls: list[dict[str, object]] = []
    comparison_calls: list[dict[str, object]] = []
    try:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sina.sqlite3"
            init_db(db_path).close()
            sina_flash.DEFAULT_DB_PATH = db_path
            sina_flash.import_holdings = lambda *_args, **_kwargs: None
            sina_flash.load_enabled_holdings = lambda *_args, **_kwargs: [holding]
            sina_flash.source_profile_enabled = lambda _source: True
            sina_flash.fetch_sina_feed = lambda **_kwargs: list(rows)

            # The first widened response is discovery baseline only.
            assert sina_flash.run_once() == 0
            with sqlite3.connect(db_path) as conn:
                baseline = conn.execute(
                    "SELECT item_id, collection_class FROM seen_items WHERE source = ? ORDER BY item_id",
                    (sina_flash.SOURCE,),
                ).fetchall()
                assert baseline == [("old-related", "baseline"), ("old-unrelated", "baseline")]
                assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0

            rows.extend(
                [
                    {"id": "new-related", "content": "中际旭创签订新订单", "create_time": 1784736002},
                    {"id": "new-unrelated", "content": "普通消费活动更新", "create_time": 1784736003},
                ]
            )

            def fake_process(normalized, raw_item, **kwargs):
                process_calls.append({"normalized": normalized, "raw_item": raw_item, **kwargs})
                return SimpleNamespace(
                    event_id=1,
                    inserted=True,
                    payload={"core_content": "test"},
                    delivery_status="not_requested",
                )

            def fake_compare(normalized, current_decision, storage_ref, **kwargs):
                comparison_calls.append(
                    {
                        "normalized": normalized,
                        "current_decision": current_decision,
                        "storage_ref": storage_ref,
                        **kwargs,
                    }
                )

            sina_flash.process_market_item = fake_process
            sina_flash.record_rule_comparison = fake_compare
            assert sina_flash.run_once() == 1
            assert len(process_calls) == 1
            assert process_calls[0]["raw_item"]["source_event_id"] == "new-related"
            assert process_calls[0]["current_admission_status"] == "admitted"
            assert len(comparison_calls) == 1
            assert comparison_calls[0]["storage_ref"]["item_id"] == "new-unrelated"
            assert comparison_calls[0]["current_admission_status"] == "excluded"
            with sqlite3.connect(db_path) as conn:
                statuses = dict(
                    conn.execute(
                        "SELECT item_id, admission_status FROM seen_items WHERE source = ?",
                        (sina_flash.SOURCE,),
                    ).fetchall()
                )
                assert statuses["new-related"] == "admitted"
                assert statuses["new-unrelated"] == "excluded"
    finally:
        sina_flash.DEFAULT_DB_PATH = original_db
        sina_flash.import_holdings = original_import
        sina_flash.load_enabled_holdings = original_holdings
        sina_flash.source_profile_enabled = original_enabled
        sina_flash.fetch_sina_feed = original_fetch
        sina_flash.process_market_item = original_process
        sina_flash.record_rule_comparison = original_compare
        if previous_notify is not None:
            os.environ["SURVEIL_NOTIFY_BASELINE"] = previous_notify


def test_sina_flash_empty_response_does_not_finish_expanded_scope_baseline() -> None:
    rows: list[dict[str, object]] = []
    original_db = sina_flash.DEFAULT_DB_PATH
    original_import = sina_flash.import_holdings
    original_holdings = sina_flash.load_enabled_holdings
    original_enabled = sina_flash.source_profile_enabled
    original_fetch = sina_flash.fetch_sina_feed
    previous_notify = os.environ.pop("SURVEIL_NOTIFY_BASELINE", None)
    try:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "sina.sqlite3"
            init_db(db_path).close()
            sina_flash.DEFAULT_DB_PATH = db_path
            sina_flash.import_holdings = lambda *_args, **_kwargs: None
            sina_flash.load_enabled_holdings = lambda *_args, **_kwargs: []
            sina_flash.source_profile_enabled = lambda _source: True
            sina_flash.fetch_sina_feed = lambda **_kwargs: list(rows)

            assert sina_flash.run_once() == 0
            assert sina_flash.load_state().get(sina_flash.SEEN_FLOW_STATE_KEY) is None

            rows.append({"id": "first-real", "content": "普通消费活动资讯", "create_time": 1784736001})
            assert sina_flash.run_once() == 0
            assert sina_flash.load_state()[sina_flash.SEEN_FLOW_STATE_KEY] is True
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT collection_class FROM seen_items WHERE source = ? AND item_id = ?",
                    (sina_flash.SOURCE, "first-real"),
                ).fetchone()
                assert row == ("baseline",)
    finally:
        sina_flash.DEFAULT_DB_PATH = original_db
        sina_flash.import_holdings = original_import
        sina_flash.load_enabled_holdings = original_holdings
        sina_flash.source_profile_enabled = original_enabled
        sina_flash.fetch_sina_feed = original_fetch
        if previous_notify is not None:
            os.environ["SURVEIL_NOTIFY_BASELINE"] = previous_notify


def main() -> int:
    test_runtime_selects_only_unified_adapters()
    test_all_event_collectors_import_runtime_entrypoints()
    test_ifind_batch_only_builds_notice_events()
    test_unified_upsert_preserves_store_contract()
    test_sina_flash_uses_news_media_flash_shape()
    test_sina_flash_current_admission_reports_macro_and_fed_families()
    test_sina_flash_reserves_all_discoveries_before_current_admission()
    test_sina_flash_empty_response_does_not_finish_expanded_scope_baseline()
    print("event runtime checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
