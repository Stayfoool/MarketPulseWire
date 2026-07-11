#!/usr/bin/env python3
"""Regression checks for the ValueList browser-backed monitor."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import value_directory_preview
import value_directory_monitor
from source_profiles import runtime_source_profile
from value_directory_browser import classify_page_state, dedupe_entries, normalize_entry, source_config
from value_directory_preview import apply_preview_to_item, extract_preview_facts, fallback_facts, flatten_paddleocr_result


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


def test_normalize_entry_supports_industry_macro_source() -> None:
    source = source_config("value_directory_ib_industry_macro")
    item = normalize_entry(
        {
            "published": "2026-07-11",
            "title": "瑞银-亚太科技策略：Agentic AI to carry Semis&Hardware further-20260701【198页】",
            "url": "https://www.valuelist.cn/862079.html",
        },
        source,
    )
    assert item is not None
    assert item["id"] == "862079"
    assert item["source_module"] == "价值目录 / 国际投行-行业宏观"
    assert item["categories"] == ["国际投行-行业宏观"]
    assert item["raw"]["source"] == "value_directory_ib_industry_macro"


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
    macro = runtime_source_profile("value_directory_ib_industry_macro")
    assert macro is not None
    assert macro["name"] == "价值目录 / 国际投行-行业宏观"
    assert "第一页预览" in macro["fetch_range"]


def test_preview_failure_is_recorded_without_fake_summary() -> None:
    item = {
        "id": "862592",
        "url": "https://www.valuelist.cn/862592.html",
        "title": "高盛-交易思路：做多中国人工智能价值链（GSXACART）-[GSX] Trade Idea：Long China AI Value Chain(GSXACART)-20260626【1页】",
        "summary": "title only",
        "raw": {},
    }
    preview = {"state": "ok", "previewImages": [{"src": "https://img.valuelist.cn/demo.jpg"}]}
    facts = fallback_facts(item, preview, RuntimeError("vision unavailable"))
    enriched = apply_preview_to_item(item, preview, facts)
    assert enriched["summary"] == "title only"
    assert enriched["raw"]["value_directory_preview"]["facts"]["status"] == "failed"
    assert "失败/不可用" in enriched["preview_lines"][0]


def test_paddleocr_result_flatten_supports_common_shapes() -> None:
    v2_result = [
        [
            [[[0, 0], [1, 0], [1, 1], [0, 1]], ("Rating Buy", 0.98)],
            [[[0, 2], [1, 2], [1, 3], [0, 3]], ("Target price CNY 1,325.00", 0.96)],
        ]
    ]
    v3_result = [{"rec_texts": ["Nomura", "Implied upside +20.6%"], "rec_scores": [0.99, 0.95]}]
    assert flatten_paddleocr_result(v2_result) == [
        ("Rating Buy", 0.98),
        ("Target price CNY 1,325.00", 0.96),
    ]
    assert flatten_paddleocr_result(v3_result) == [
        ("Nomura", 0.99),
        ("Implied upside +20.6%", 0.95),
    ]


def test_preview_ocr_text_path_uses_text_llm_without_vision() -> None:
    item = {
        "id": "862591",
        "url": "https://www.valuelist.cn/862591.html",
        "title": "野村-中际旭创(300308.SZ)：我们预计2027年后将实现长期增长-20260706【10页】",
        "summary": "title only",
        "source_module": "价值目录 / 国际投行-个股",
        "published_at": "2026-07-10T00:00:00+00:00",
    }
    preview = {
        "state": "ok",
        "articleText": "页面可见标题",
        "previewImages": [{"src": "https://img.valuelist.cn/862591.jpg"}],
    }
    original_ocr = value_directory_preview.extract_ocr_text
    original_text_llm = value_directory_preview.call_preview_text_llm
    original_vision_llm = value_directory_preview.call_preview_vision_llm
    original_ocr_env = os.environ.get("VALUE_DIRECTORY_PREVIEW_OCR_ENABLED")
    original_vision_env = os.environ.get("VALUE_DIRECTORY_PREVIEW_VISION_FALLBACK_ENABLED")
    try:
        os.environ["VALUE_DIRECTORY_PREVIEW_OCR_ENABLED"] = "1"
        os.environ["VALUE_DIRECTORY_PREVIEW_VISION_FALLBACK_ENABLED"] = "0"
        value_directory_preview.extract_ocr_text = lambda _url: {
            "engine": "paddleocr",
            "status": "ok",
            "text": "Action: Maintain Buy; raise TP to CNY 1,325, implying 20.6% upside\nRating Buy\nTarget price CNY 1,325.00",
            "line_count": 3,
        }

        def fake_text_llm(_item, _preview, *, ocr_text=""):
            assert "CNY 1,325" in ocr_text
            return (
                {
                    "core_content": "野村维持中际旭创买入评级并上调目标价至 CNY 1,325。",
                    "stance": "bullish",
                    "action": "buy",
                    "institution": "野村",
                    "report_date": "2026-07-06",
                    "rating": "Buy",
                    "target_price": "CNY 1,325.00",
                    "targets": ["中际旭创", "2.4T/3.2T 光模块", "NPO/CPO"],
                    "key_points": ["维持 Buy", "目标价上调至 CNY 1,325", "隐含 20.6% 上行空间"],
                    "preview_basis": "visible_first_page_ocr",
                    "confidence": "high",
                },
                "deepseek-v4-pro",
            )

        value_directory_preview.call_preview_text_llm = fake_text_llm
        value_directory_preview.call_preview_vision_llm = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("vision fallback should stay disabled")
        )
        facts = extract_preview_facts(item, preview)
    finally:
        value_directory_preview.extract_ocr_text = original_ocr
        value_directory_preview.call_preview_text_llm = original_text_llm
        value_directory_preview.call_preview_vision_llm = original_vision_llm
        if original_ocr_env is None:
            os.environ.pop("VALUE_DIRECTORY_PREVIEW_OCR_ENABLED", None)
        else:
            os.environ["VALUE_DIRECTORY_PREVIEW_OCR_ENABLED"] = original_ocr_env
        if original_vision_env is None:
            os.environ.pop("VALUE_DIRECTORY_PREVIEW_VISION_FALLBACK_ENABLED", None)
        else:
            os.environ["VALUE_DIRECTORY_PREVIEW_VISION_FALLBACK_ENABLED"] = original_vision_env

    assert facts["status"] == "ok"
    assert facts["model"] == "deepseek-v4-pro"
    assert facts["target_price"] == "CNY 1,325.00"
    assert facts["ocr"]["engine"] == "paddleocr"
    assert facts["preview_basis"] == "visible_first_page_ocr"


def test_preview_ocr_failure_falls_back_without_blocking() -> None:
    item = {
        "id": "862591",
        "title": "野村-中际旭创(300308.SZ)：我们预计2027年后将实现长期增长-20260706【10页】",
        "summary": "title only",
    }
    preview = {"state": "ok", "previewImages": [{"src": "https://img.valuelist.cn/862591.jpg"}]}
    original_ocr = value_directory_preview.extract_ocr_text
    original_vision_llm = value_directory_preview.call_preview_vision_llm
    original_ocr_env = os.environ.get("VALUE_DIRECTORY_PREVIEW_OCR_ENABLED")
    original_vision_env = os.environ.get("VALUE_DIRECTORY_PREVIEW_VISION_FALLBACK_ENABLED")
    try:
        os.environ["VALUE_DIRECTORY_PREVIEW_OCR_ENABLED"] = "1"
        os.environ["VALUE_DIRECTORY_PREVIEW_VISION_FALLBACK_ENABLED"] = "0"
        value_directory_preview.extract_ocr_text = lambda _url: {
            "engine": "paddleocr",
            "status": "too_short",
            "text": "",
            "error": "OCR 文字过短：0 chars",
        }
        value_directory_preview.call_preview_vision_llm = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("vision fallback should stay disabled")
        )
        facts = extract_preview_facts(item, preview)
    finally:
        value_directory_preview.extract_ocr_text = original_ocr
        value_directory_preview.call_preview_vision_llm = original_vision_llm
        if original_ocr_env is None:
            os.environ.pop("VALUE_DIRECTORY_PREVIEW_OCR_ENABLED", None)
        else:
            os.environ["VALUE_DIRECTORY_PREVIEW_OCR_ENABLED"] = original_ocr_env
        if original_vision_env is None:
            os.environ.pop("VALUE_DIRECTORY_PREVIEW_VISION_FALLBACK_ENABLED", None)
        else:
            os.environ["VALUE_DIRECTORY_PREVIEW_VISION_FALLBACK_ENABLED"] = original_vision_env

    assert facts["status"] == "failed"
    assert facts["model"] == "preview_failed"
    assert facts["ocr"]["status"] == "too_short"
    assert "OCR 文字过短" in facts["error"]


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


def test_collect_production_rechecks_current_unpushed_reviews() -> None:
    source = source_config("value_directory_ib_stocks")
    entries = [
        {
            "id": "862591",
            "url": "https://www.valuelist.cn/862591.html",
            "title": "野村-中际旭创(300308.SZ)：我们预计2027年后将实现长期增长",
            "summary": "国际投行个股研报索引。",
            "published_at": "2026-07-11T00:00:10+00:00",
        },
        {
            "id": "862592",
            "url": "https://www.valuelist.cn/862592.html",
            "title": "高盛-其他公司：例行观点",
            "summary": "国际投行个股研报索引。",
            "published_at": "2026-07-11T00:00:10+00:00",
        },
    ]
    calls: list[tuple[str, bool]] = []
    original_save_new = value_directory_monitor.save_new_items_with_retry
    original_connect = value_directory_monitor.connect_db
    original_exists = value_directory_monitor.article_review_exists
    original_review = value_directory_monitor.review_and_maybe_push
    original_enabled = os.environ.get("VALUE_DIRECTORY_RECHECK_UNPUSHED")
    original_limit = os.environ.get("VALUE_DIRECTORY_RECHECK_UNPUSHED_LIMIT")
    try:
        os.environ["VALUE_DIRECTORY_RECHECK_UNPUSHED"] = "1"
        os.environ["VALUE_DIRECTORY_RECHECK_UNPUSHED_LIMIT"] = "30"
        value_directory_monitor.save_new_items_with_retry = lambda *_args, **_kwargs: []
        value_directory_monitor.connect_db = lambda: _DummyContext()

        def fake_exists(_conn, _source_id, item_id):
            if item_id == "862591":
                return {"push_now": False, "pushed_at": "", "importance": "medium"}
            return None

        def fake_review(item, *, source=None, recheck_rules=False):
            calls.append((item["id"], recheck_rules))
            return True

        value_directory_monitor.article_review_exists = fake_exists
        value_directory_monitor.review_and_maybe_push = fake_review
        payload = value_directory_monitor.collect_production(
            entries,
            source=source,
            notify_baseline=False,
            started_at="2026-07-11T00:00:00+00:00",
        )
    finally:
        value_directory_monitor.save_new_items_with_retry = original_save_new
        value_directory_monitor.connect_db = original_connect
        value_directory_monitor.article_review_exists = original_exists
        value_directory_monitor.review_and_maybe_push = original_review
        if original_enabled is None:
            os.environ.pop("VALUE_DIRECTORY_RECHECK_UNPUSHED", None)
        else:
            os.environ["VALUE_DIRECTORY_RECHECK_UNPUSHED"] = original_enabled
        if original_limit is None:
            os.environ.pop("VALUE_DIRECTORY_RECHECK_UNPUSHED_LIMIT", None)
        else:
            os.environ["VALUE_DIRECTORY_RECHECK_UNPUSHED_LIMIT"] = original_limit

    assert calls == [("862591", True)]
    assert payload["counts"]["new_items"] == 0
    assert payload["counts"]["reviewed_items"] == 1
    assert payload["counts"]["rechecked_items"] == 1
    assert payload["counts"]["pushed_items"] == 1


def main() -> int:
    test_normalize_entry_extracts_stable_id_and_utc_date()
    test_normalize_entry_supports_industry_macro_source()
    test_page_state_detection_separates_waf_login_and_empty()
    test_dedupe_entries_keeps_first_valid_url()
    test_shadow_payload_marks_seen_and_reviewed_without_delivery()
    test_source_profile_registers_value_directory()
    test_preview_failure_is_recorded_without_fake_summary()
    test_paddleocr_result_flatten_supports_common_shapes()
    test_preview_ocr_text_path_uses_text_llm_without_vision()
    test_preview_ocr_failure_falls_back_without_blocking()
    test_recheck_keeps_existing_review_without_new_rule()
    test_collect_production_rechecks_current_unpushed_reviews()
    print("value directory monitor checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
