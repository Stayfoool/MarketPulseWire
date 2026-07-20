#!/usr/bin/env python3
"""Small regression tests for domestic finance media helpers."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import china_finance_media_monitor as cfm
import investment_universe as iu
from decision_engine import decide_market_item
from market_item import NormalizedMarketItem
from china_finance_media_monitor import cls_sign, next_data_from_html, parse_cls_time
from media_keyword_config import keyword_matches_text


def test_cls_sign_includes_empty_values_and_sorts_keys() -> None:
    params = {
        "sv": "7.7.5",
        "rn": "20",
        "refresh_type": "1",
        "os": "web",
        "lastTime": "",
        "category": "",
        "app": "CailianpressWeb",
    }
    assert cls_sign(params) == "0151cb1ca42557f82288f8ac65797220"


def test_parse_cls_time_accepts_seconds_and_milliseconds() -> None:
    assert parse_cls_time("1719806400") == "2024-07-01T04:00:00+00:00"
    assert parse_cls_time("1719806400000") == "2024-07-01T04:00:00+00:00"


def test_parse_cls_time_keeps_timezone_aware_iso() -> None:
    assert parse_cls_time("2026-06-29T08:00:00+08:00") == "2026-06-29T00:00:00+00:00"


def test_cls_items_preserve_vip_product_and_author_targets() -> None:
    payload = {
        "errno": 0,
        "data": {
            "roll_data": [
                {
                    "id": 2426205,
                    "ctime": 1784028159,
                    "type": 20026,
                    "title": "【机构龙虎榜解读】光通信+AI PCB，机构大额净买入这家公司",
                    "content": "①光通信+AI PCB，机构大额净买入这家公司。",
                    "shareurl": "https://api3.cls.cn/share/article/2426205?os=web",
                    "share_img": "https://img.cls.cn/share/vip.png",
                    "author_extends": "sz002245@@蔚蓝锂芯##sz002384@@东山精密",
                }
            ]
        },
    }
    original_http_get = cfm.http_get
    try:
        class Response:
            content = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        cfm.http_get = lambda *args, **kwargs: Response()
        items = cfm.parse_cls_items(persist_state=False, force=True)
    finally:
        cfm.http_get = original_http_get

    assert len(items) == 1
    metadata = items[0]["cls_metadata"]
    assert items[0]["title"] == "①光通信+AI PCB，机构大额净买入这家公司。"
    assert metadata["type"] == "20026"
    assert metadata["product_label"] == "机构龙虎榜解读"
    assert metadata["share_img_name"] == "vip.png"
    assert metadata["is_vip"] is True
    assert metadata["author_targets"] == [
        {"name": "蔚蓝锂芯", "code": "002245.SZ", "raw_code": "sz002245"},
        {"name": "东山精密", "code": "002384.SZ", "raw_code": "sz002384"},
    ]
    assert items[0]["raw"]["cls_metadata"] == metadata


def test_cls_observation_metadata_does_not_change_decision_action() -> None:
    holding = {
        "symbol": "301217.SZ",
        "name": "铜冠铜箔",
        "aliases": [],
        "news_keywords": ["pcb"],
        "news_exclude_keywords": [],
    }
    item = {
        "source": "cls_telegraph_api",
        "source_category": "news_media",
        "publisher_role": "news_media",
        "content_type": "article",
        "title": "AI PCB项目计划扩产，这家公司获机构净买入。",
        "summary": "AI PCB项目计划扩产，这家公司获机构净买入。",
    }
    metadata = {
        "type": "20026",
        "product_label": "机构龙虎榜解读",
        "share_img_name": "vip.png",
        "is_vip": True,
        "author_targets": [{"name": "东山精密", "code": "002384.SZ", "raw_code": "sz002384"}],
    }
    without_metadata = decide_market_item(item, holdings=[holding])
    with_metadata = decide_market_item({**item, "raw": {"cls_metadata": metadata}}, holdings=[holding])
    assert without_metadata.action == with_metadata.action == "push"
    assert [hit["rule_id"] for hit in without_metadata.rule_hits] == [
        hit["rule_id"] for hit in with_metadata.rule_hits
    ]


def test_cls_poll_interval_skips_recent_fetch() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        original_db = cfm.DB_PATH
        original_min = os.environ.get("CLS_MIN_POLL_SECONDS")
        try:
            cfm.DB_PATH = Path(tmpdir) / "test.sqlite3"
            os.environ["CLS_MIN_POLL_SECONDS"] = "300"
            cfm.save_source_state("cls_telegraph_api", {"last_fetch_at": "2026-06-29T00:00:00+00:00"})
            original_now = cfm.datetime

            class FakeDateTime(cfm.datetime):
                @classmethod
                def now(cls, tz=None):  # noqa: ANN001
                    return cls.fromisoformat("2026-06-29T00:01:00+00:00")

            cfm.datetime = FakeDateTime
            assert cfm.should_skip_cls_poll("cls_telegraph_api") is True
        finally:
            cfm.DB_PATH = original_db
            cfm.datetime = original_now
            if original_min is None:
                os.environ.pop("CLS_MIN_POLL_SECONDS", None)
            else:
                os.environ["CLS_MIN_POLL_SECONDS"] = original_min


def test_run_once_fetches_sources_independently() -> None:
    calls: list[str] = []
    original_source_items = cfm.source_items
    original_record_success = cfm.record_source_success
    original_record_failure = cfm.record_source_failure
    original_save = cfm.save_new_items_with_retry
    original_retryable = cfm.retryable_seen_items
    original_notify = cfm.notify_item
    try:
        def fake_source_items(source: str, **_kwargs):
            calls.append(source)
            if source == "bad":
                raise RuntimeError("boom")
            return [{"id": source, "title": source, "url": "", "published_at": ""}]

        cfm.source_items = fake_source_items
        successes: list[str] = []
        failures: list[str] = []
        cfm.record_source_success = lambda conn, monitor, source: successes.append(source)
        cfm.record_source_failure = lambda conn, monitor, source, exc: failures.append(source)
        cfm.save_new_items_with_retry = lambda source, items, notify_baseline=False: list(items)
        cfm.retryable_seen_items = lambda source: []
        cfm.notify_item = lambda source, item: None
        count = cfm.run_once(["good", "bad"], notify_baseline=False)
        assert count == 1
        assert sorted(calls) == ["bad", "good"]
        assert successes == ["good"]
        assert failures == ["bad"]
    finally:
        cfm.source_items = original_source_items
        cfm.record_source_success = original_record_success
        cfm.record_source_failure = original_record_failure
        cfm.save_new_items_with_retry = original_save
        cfm.retryable_seen_items = original_retryable
        cfm.notify_item = original_notify


def test_seen_item_lifecycle_migration_baseline_and_retry() -> None:
    original_db = cfm.DB_PATH
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfm.DB_PATH = Path(tmpdir) / "test.sqlite3"
            with cfm.connect_db() as conn:
                conn.execute(
                    """
                    CREATE TABLE seen_items (
                        source TEXT NOT NULL, item_id TEXT NOT NULL, url TEXT NOT NULL,
                        title TEXT NOT NULL, summary TEXT, published_at TEXT,
                        first_seen_at TEXT NOT NULL, PRIMARY KEY (source, item_id)
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO seen_items VALUES ('legacy', 'old', '', 'old', '', '', '')"
                )
                conn.commit()
                cfm.ensure_seen_table(conn)
                legacy = conn.execute(
                    "SELECT collection_class, processability_status, admission_status, processing_status "
                    "FROM seen_items WHERE source = 'legacy' AND item_id = 'old'"
                ).fetchone()
                assert legacy == (
                    "legacy_unclassified",
                    "legacy_unclassified",
                    "legacy_unclassified",
                    "legacy_unclassified",
                )

            baseline_item = {"id": "base", "title": "baseline", "url": "", "summary": ""}
            assert cfm.save_new_items_with_retry("baseline-source", [baseline_item]) == []
            with cfm.connect_db() as conn:
                baseline = conn.execute(
                    "SELECT collection_class, processability_status, admission_status, processing_status "
                    "FROM seen_items WHERE source = 'baseline-source' AND item_id = 'base'"
                ).fetchone()
                assert baseline == ("baseline", "not_required", "not_applicable", "not_applicable")

            with cfm.connect_db() as conn:
                conn.execute(
                    "INSERT INTO seen_sources (source, first_seen_at) VALUES ('live-source', '')"
                )
                conn.commit()
            live_item = {"id": "live", "title": "live", "url": "", "summary": ""}
            assert cfm.save_new_items_with_retry("live-source", [live_item]) == [live_item]
            cfm.set_seen_item_lifecycle(
                "live-source",
                "live",
                processability_status="failed_retryable",
                processability_reason="temporary",
            )
            assert cfm.save_new_items_with_retry("live-source", [live_item]) == [live_item]
            with cfm.connect_db() as conn:
                retry = conn.execute(
                    "SELECT processability_status, admission_status, processing_status "
                    "FROM seen_items WHERE source = 'live-source' AND item_id = 'live'"
                ).fetchone()
                assert retry == ("pending", "pending", "not_applicable")
            cfm.set_seen_item_lifecycle(
                "live-source",
                "live",
                processability_status="succeeded",
                admission_status="pending",
                admission_reason="evaluation_failed:RuntimeError",
                processing_status="not_applicable",
            )
            assert [row["id"] for row in cfm.retryable_seen_items("live-source")] == ["live"]
            assert cfm.save_new_items_with_retry("live-source", [live_item]) == [live_item]
            cfm.set_seen_item_lifecycle(
                "live-source",
                "live",
                processability_status="succeeded",
                admission_status="excluded",
                processing_status="not_applicable",
            )
            assert cfm.save_new_items_with_retry("live-source", [live_item]) == []
    finally:
        cfm.DB_PATH = original_db


def test_excluded_item_uses_one_normalized_item_for_report_only_comparison() -> None:
    original_db = cfm.DB_PATH
    original_admission = cfm.current_admission_result
    original_record = cfm.record_rule_comparison
    original_process = cfm.process_market_item
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfm.DB_PATH = Path(tmpdir) / "test.sqlite3"
            with cfm.connect_db() as conn:
                cfm.ensure_seen_table(conn)
                conn.execute("INSERT INTO seen_sources (source, first_seen_at) VALUES ('test-source', '')")
                conn.commit()
            item = {"id": "excluded", "title": "ordinary market article", "url": "", "summary": "text"}
            assert cfm.save_new_items_with_retry("test-source", [item]) == [item]
            captured: list[tuple[object, object, dict, dict]] = []
            cfm.current_admission_result = lambda item, source='', **_kwargs: {
                "admitted": False,
                "reason": "investment_universe_no_match",
                "matched_families": (),
            }
            cfm.record_rule_comparison = lambda normalized, decision, storage, **kwargs: captured.append(
                (normalized, decision, storage, kwargs)
            )
            cfm.process_market_item = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("excluded item must not enter current decision/runtime")
            )
            cfm.notify_item("test-source", item)
            assert len(captured) == 1
            normalized, decision, storage, kwargs = captured[0]
            assert normalized.source == "test-source"
            assert decision is None
            assert storage["store_kind"] == "seen_items"
            assert kwargs["current_admission_status"] == "excluded"
            with cfm.connect_db() as conn:
                state = conn.execute(
                    "SELECT processability_status, admission_status, processing_status "
                    "FROM seen_items WHERE source = 'test-source' AND item_id = 'excluded'"
                ).fetchone()
                assert state == ("succeeded", "excluded", "not_applicable")
    finally:
        cfm.DB_PATH = original_db
        cfm.current_admission_result = original_admission
        cfm.record_rule_comparison = original_record
        cfm.process_market_item = original_process


def test_admitted_item_reuses_normalized_item_and_processing_failure_retries() -> None:
    original_db = cfm.DB_PATH
    original_admission = cfm.current_admission_result
    original_match = cfm.investment_universe_match
    original_normalize = cfm.normalize_market_item
    original_process = cfm.process_market_item
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfm.DB_PATH = Path(tmpdir) / "test.sqlite3"
            with cfm.connect_db() as conn:
                cfm.ensure_seen_table(conn)
                conn.execute("INSERT INTO seen_sources (source, first_seen_at) VALUES ('test-source', '')")
                conn.commit()
            item = {"id": "admitted", "title": "DRAM supply", "url": "", "summary": "DRAM shortage"}
            assert cfm.save_new_items_with_retry("test-source", [item]) == [item]
            captured_normalized: list[NormalizedMarketItem] = []

            def capture_normalize(*args, **kwargs):
                normalized = original_normalize(*args, **kwargs)
                captured_normalized.append(normalized)
                return normalized

            cfm.current_admission_result = lambda item, source='', **_kwargs: {
                "admitted": True,
                "reason": "media_focus",
                "matched_families": ("semiconductor_ai",),
            }
            cfm.investment_universe_match = lambda source, item: {
                "matched": True,
                "tags": ["user_include_keyword"],
                "reason": "current admission",
            }
            cfm.normalize_market_item = capture_normalize

            def fail_processing(normalized, raw_item, **kwargs):
                assert normalized is captured_normalized[0]
                assert kwargs["current_admission_status"] == "admitted"
                raise RuntimeError("temporary processing failure")

            cfm.process_market_item = fail_processing
            try:
                cfm.notify_item("test-source", item)
            except RuntimeError as exc:
                assert "temporary processing failure" in str(exc)
            else:
                raise AssertionError("processing failure must remain visible")
            with cfm.connect_db() as conn:
                state = conn.execute(
                    "SELECT processability_status, admission_status, processing_status "
                    "FROM seen_items WHERE source = 'test-source' AND item_id = 'admitted'"
                ).fetchone()
                assert state == ("succeeded", "admitted", "failed_retryable")
            assert [row["id"] for row in cfm.retryable_seen_items("test-source")] == ["admitted"]
            assert cfm.save_new_items_with_retry("test-source", [item]) == [item]
    finally:
        cfm.DB_PATH = original_db
        cfm.current_admission_result = original_admission
        cfm.investment_universe_match = original_match
        cfm.normalize_market_item = original_normalize
        cfm.process_market_item = original_process


def test_wallstreetcn_processability_retry_waits_for_source_rediscovery() -> None:
    original_db = cfm.DB_PATH
    original_enrich = cfm.enrich_item
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfm.DB_PATH = Path(tmpdir) / "test.sqlite3"
            with cfm.connect_db() as conn:
                cfm.ensure_seen_table(conn)
                conn.execute(
                    "INSERT INTO seen_sources (source, first_seen_at) VALUES (?, '')",
                    (cfm.WALLSTREETCN_SOURCE,),
                )
                conn.commit()
            item = {
                "id": "livenews:empty-new",
                "url": "https://wallstreetcn.com/livenews/1",
                "title": "",
                "summary": "",
            }
            assert cfm.save_new_items_with_retry(cfm.WALLSTREETCN_SOURCE, [item]) == [item]
            cfm.enrich_item = lambda source, item: (_ for _ in ()).throw(
                ValueError(cfm.WALLSTREETCN_EMPTY_DETAIL_ERROR)
            )
            try:
                cfm.notify_item(cfm.WALLSTREETCN_SOURCE, item)
            except ValueError as exc:
                assert str(exc) == cfm.WALLSTREETCN_EMPTY_DETAIL_ERROR
            else:
                raise AssertionError("empty detail failure must remain visible")
            with cfm.connect_db() as conn:
                state = conn.execute(
                    "SELECT processability_status, processability_reason, admission_status, processing_status "
                    "FROM seen_items WHERE source=? AND item_id=?",
                    (cfm.WALLSTREETCN_SOURCE, item["id"]),
                ).fetchone()
                assert state == (
                    "failed_retryable",
                    f"ValueError: {cfm.WALLSTREETCN_EMPTY_DETAIL_ERROR}",
                    "pending",
                    "not_applicable",
                )
            # WallstreetCN retries when its list/sitemap discovers the identity again,
            # rather than appending every failed detail to every two-minute run.
            assert cfm.retryable_seen_items(cfm.WALLSTREETCN_SOURCE) == []
            assert cfm.save_new_items_with_retry(cfm.WALLSTREETCN_SOURCE, [item]) == [item]

            cfm.set_seen_item_lifecycle(
                cfm.WALLSTREETCN_SOURCE,
                item["id"],
                processability_status="failed_terminal",
                processability_reason="wallstreetcn_detail_empty",
                admission_status="not_applicable",
                processing_status="not_applicable",
            )
            assert cfm.restore_existing_wallstreetcn_empty_details(cfm.WALLSTREETCN_SOURCE) == 1
            with cfm.connect_db() as conn:
                restored = conn.execute(
                    "SELECT processability_status, processability_reason, admission_status "
                    "FROM seen_items WHERE source=? AND item_id=?",
                    (cfm.WALLSTREETCN_SOURCE, item["id"]),
                ).fetchone()
                assert restored == (
                    "failed_retryable",
                    f"ValueError: {cfm.WALLSTREETCN_EMPTY_DETAIL_ERROR}",
                    "pending",
                )
            assert cfm.retryable_seen_items(cfm.WALLSTREETCN_SOURCE) == []
    finally:
        cfm.DB_PATH = original_db
        cfm.enrich_item = original_enrich


def test_yicai_morning_brief_is_mandatory_push() -> None:
    item = {
        "title": "<b>券商晨会观点速递  |</b> ①中信建投：半导体设备全球景气周期持续确认",
        "summary": "",
        "full_text": "",
    }
    assert cfm.is_mandatory_yicai_morning_brief("yicai_brief", item) is True


def test_short_english_keyword_requires_token_boundary() -> None:
    assert keyword_matches_text("ai", "https://m.yicai.com CailianpressWeb aijd") is False
    assert keyword_matches_text("ai", "AI PCB需求放量、高阶升级趋势明确") is True


def test_china_media_focus_filters_generic_power_and_accepts_ai_context() -> None:
    original_match = cfm.investment_universe_match
    original_focus = cfm.is_media_focus_item
    original_media = iu.media_keyword_match
    with tempfile.TemporaryDirectory() as tmpdir:
        empty_db = Path(tmpdir) / "empty.sqlite3"

        def no_holding_match(source: str, item: dict):
            return original_match(source, item, db_path=empty_db)

        try:
            cfm.investment_universe_match = no_holding_match
            cfm.is_media_focus_item = lambda *parts: True
            iu.media_keyword_match = lambda *parts: {"matched": False, "blocked": False, "keyword": "", "bucket": ""}
            transformer = {
                "title": "常州：前五个月变压器出口额同比增长33.2%，订单已排至2027年下半年",
                "summary": "据常州发布，常州变压器出口额同比增长，龙头企业订单已排至2027年下半年。",
                "full_text": "",
            }
            assert cfm.should_focus_item(transformer, "yicai_brief") is False
            assert "generic_power_filtered" in transformer["_investment_universe_match"]["tags"]

            ai_power = {
                "title": "AI数据中心电力瓶颈推动高压变压器订单增长",
                "summary": "海外AI数据中心扩建拉动电力设备需求。",
                "full_text": "",
            }
            assert cfm.should_focus_item(ai_power, "yicai_brief") is True
        finally:
            cfm.investment_universe_match = original_match
            cfm.is_media_focus_item = original_focus
            iu.media_keyword_match = original_media


def test_current_admission_is_equivalent_after_normalization() -> None:
    original_fed = cfm.fed_path_candidate
    original_match = cfm.investment_universe_match
    original_macro = cfm.is_macro_event
    original_focus = cfm.is_media_focus_item
    try:
        cfm.fed_path_candidate = lambda item: False
        cfm.investment_universe_match = lambda source, item: {
            "matched": True,
            "tags": ["semiconductor_ai"],
            "reason": "industry scope",
        }
        cfm.is_macro_event = lambda item: False
        cfm.is_media_focus_item = lambda *parts: "HBM" in " ".join(parts)
        mapping = {
            "id": "same-item",
            "title": "HBM supply update",
            "summary": "DRAM supply remains tight",
            "full_text": "HBM supply update with DRAM evidence",
            "url": "https://example.test/same-item",
            "published_at": "2026-07-20T00:00:00+00:00",
            "source_module": "Test Media",
        }
        normalized = cfm.normalize_market_item("test-source", mapping, store_kind="article")
        before = cfm.current_admission_result(dict(mapping), "test-source")
        after = cfm.current_admission_result(
            normalized,
            "test-source",
            source_module="Test Media",
        )
        for field in ("admitted", "reason", "matched_families"):
            assert before[field] == after[field]
    finally:
        cfm.fed_path_candidate = original_fed
        cfm.investment_universe_match = original_match
        cfm.is_macro_event = original_macro
        cfm.is_media_focus_item = original_focus


def test_jin10_mixed_digest_keeps_only_relevant_lines() -> None:
    original_holding = iu.matched_holding
    original_media = iu.media_keyword_match
    try:
        iu.matched_holding = lambda text, db_path=iu.DEFAULT_DB_PATH: ""
        iu.media_keyword_match = lambda *parts: {"matched": False, "blocked": False, "keyword": "", "bucket": ""}
        item = {
            "title": "金十重要事件",
            "summary": "\n".join(
                [
                    "国外",
                    "1. 汇丰银行：金价将保持震荡走势，预计年底目标价为4750美元/盎司。",
                    "2. 高盛：若美伊谈判继续，波斯湾石油供应量有望7月底恢复。",
                    "3. 巴克莱：美联储或将按兵不动到2027年底。",
                    "国内",
                    "1. 中信建投：AI PCB需求放量、高阶升级趋势明确，打开设备耗材新空间。",
                    "2. 华泰证券：原奶或迎景气周期，全产业链受益。",
                ]
            ),
            "full_text": "",
        }
        digest = iu.relevant_digest_for_mixed_item("jin10_rsshub_important", item)
        assert "AI PCB需求放量" in digest
        assert "金价" not in digest
        assert "美联储或将按兵不动" not in digest
        assert "原奶" not in digest
    finally:
        iu.matched_holding = original_holding
        iu.media_keyword_match = original_media


def test_star_market_daily_next_data_parser() -> None:
    payload = {
        "props": {
            "pageProps": {
                "data": {
                    "articles": [
                        {
                            "article_id": 2414199,
                            "article_title": "【炬光科技：现阶段并不认为康宁Glass Bridge方案会对公司的CPO业务产生实质性的负面影响】",
                            "article_brief": "《科创板日报》1日讯，炬光科技发布投资者关系活动记录表公告。",
                            "article_author": "科创板日报记者",
                            "article_time": 1782900000,
                            "share_url": "https://api3.cls.cn/share/article/2414199?os=web&sv=7.7.5&app=CailianpressWeb",
                            "stock_list": [{"name": "炬光科技", "StockID": "sh688167"}],
                            "subjects": [{"subject_name": "科创板最新动态"}],
                            "article_tags": [{"name": "原创"}],
                        }
                    ]
                }
            }
        }
    }
    html = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(payload, ensure_ascii=False)}</script></html>'
    parsed = next_data_from_html(html)
    assert parsed["props"]["pageProps"]["data"]["articles"][0]["article_id"] == 2414199

    original_http_get = cfm.http_get
    try:
        class Response:
            content = html.encode("utf-8")

        cfm.http_get = lambda *args, **kwargs: Response()
        items = cfm.parse_star_market_daily_subject_items()
        assert len(items) == 1
        assert items[0]["source_module"] == "科创板日报 / 科创板最新动态"
        assert "炬光科技" in items[0]["summary"]
        assert items[0]["published_at"] == "2026-07-01T10:00:00+00:00"
    finally:
        cfm.http_get = original_http_get


def test_star_market_daily_cross_source_dedup() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        original_db = cfm.DB_PATH
        try:
            cfm.DB_PATH = Path(tmpdir) / "test.sqlite3"
            first = {
                "id": "cls-1",
                "url": "https://api3.cls.cn/share/article/1?os=web",
                "title": "《科创板日报》讯 AI芯片公司订单大增",
                "summary": "《科创板日报》讯 AI芯片公司订单大增",
                "published_at": "2026-07-01T00:00:00+00:00",
                "source_module": "科创板日报 / 财联社电报",
            }
            second = {
                "id": "subject-1",
                "url": "https://api3.cls.cn/share/article/1?os=web",
                "title": "《科创板日报》讯 AI芯片公司订单大增",
                "summary": "科创板日报专题页",
                "published_at": "2026-07-01T00:01:00+00:00",
                "source_module": "科创板日报 / 科创板最新动态",
            }
            assert len(cfm.save_new_items_with_retry("cls_telegraph_api", [first], notify_baseline=True)) == 1
            assert len(cfm.save_new_items_with_retry("star_market_daily_subject", [second], notify_baseline=True)) == 0
        finally:
            cfm.DB_PATH = original_db


def sina_detail_fixture() -> str:
    return """
    <html>
      <head>
        <title>英伟达：CPO已进入量产（信息量很大）_新浪财经_新浪网</title>
        <meta property="og:title" content="英伟达：CPO已进入量产（信息量很大）" />
        <meta property="og:url" content="https://finance.sina.com.cn/wm/2026-07-09/doc-inihefve8236522.shtml" />
        <meta property="bytedance:published_time" content="2026-07-09T07:49:00+08:00" />
        <meta property="article:author" content="北向牧风" />
        <meta name="description" content="花旗前几天和英伟达IR进行了交流。" />
      </head>
      <body>
        <div class="article" id="artibody">
          <p>（来源：北向牧风）</p>
          <p>花旗前几天和英伟达IR进行了交流，把投资者关心的几个核心话题的答复做了梳理。</p>
          <p>Kyber / Rubin Ultra 延迟</p>
          <p>英伟达仍坚持其产品路线图完全没有变动。</p>
          <p>CPO（共封装光学）</p>
          <p>管理层重申，面向 scale-out 的 CPO 目前已随 Spectrum-X 进入量产，客户导入的更多细节会在今年晚些时候披露；scale-out 场景下 CPO 的客户采用度很高。从 2028 自然年的 Feynman 开始，客户在 NVLink 上将可以在 CPO 与铜连接（Copper）之间二选一。</p>
          <div class="appendQr_wrap">海量资讯、精准解读，尽在新浪财经APP</div>
        </div>
        <!-- 原始正文end -->
      </body>
    </html>
    """


def test_sina_detail_parser_extracts_wm_article_evidence() -> None:
    parsed = cfm.parse_sina_detail_html(sina_detail_fixture())
    assert parsed["title"] == "英伟达：CPO已进入量产（信息量很大）"
    assert parsed["published_at"] == "2026-07-08T23:49:00+00:00"
    assert parsed["author"] == "北向牧风"
    assert parsed["docid"] == "nihefve8236522"
    assert "Rubin Ultra 延迟" in parsed["full_text"]
    assert "Spectrum-X 进入量产" in parsed["full_text"]
    assert "Feynman 开始" in parsed["full_text"]
    assert "新浪财经APP" not in parsed["full_text"]


def test_sina_roll_row_normalizes_docid_timestamp_and_url() -> None:
    row = {
        "docid": "comos:nihefve8236522",
        "url": "http://finance.sina.com.cn/wm/2026-07-09/doc-inihefve8236522.shtml?utm_source=x&from=wap",
        "title": "英伟达：CPO已进入量产（信息量很大）",
        "intro": "花旗与英伟达IR交流。",
        "ctime": "1783554540",
        "media_name": "市场资讯",
    }
    item = cfm.sina_roll_row_to_item(row, lid="2517", page=2)
    assert item is not None
    assert item["id"] == "nihefve8236522"
    assert item["url"] == "https://finance.sina.com.cn/wm/2026-07-09/doc-inihefve8236522.shtml"
    assert item["published_at"] == "2026-07-08T23:49:00+00:00"
    assert item["raw"]["roll_lid"] == "2517"
    assert item["raw"]["roll_media_name"] == "市场资讯"


def test_sina_roll_union_deduplicates_channels_before_detail_fetch() -> None:
    original_fetch_page = cfm.fetch_sina_roll_page
    original_enrich = cfm.enrich_sina_finance_item
    original_lids = os.environ.get("SINA_FINANCE_ROLL_LIDS")
    original_pages = os.environ.get("SINA_FINANCE_ROLL_MAX_PAGES")
    calls: list[str] = []
    try:
        os.environ["SINA_FINANCE_ROLL_LIDS"] = "2516,2517"
        os.environ["SINA_FINANCE_ROLL_MAX_PAGES"] = "1"

        def fake_fetch(lid: str, page: int, num: int) -> list[dict]:
            if lid == "2516":
                return [
                    {
                        "docid": "comos:nihefve8236522",
                        "url": "https://finance.sina.com.cn/wm/2026-07-09/doc-inihefve8236522.shtml",
                        "title": "英伟达：CPO已进入量产（信息量很大）",
                        "ctime": "1783554540",
                    }
                ]
            return [
                {
                    "docid": "comos:nihefve8236522",
                    "url": "https://finance.sina.cn/2026-07-09/detail-inihefve8236522.d.html",
                    "title": "英伟达：CPO已进入量产（信息量很大）",
                    "ctime": "1783554540",
                }
            ]

        def fake_enrich(item: dict) -> dict:
            calls.append(str(item["id"]))
            return {**item, "full_text": "Spectrum-X CPO已量产", "body_source": "fake detail"}

        cfm.fetch_sina_roll_page = fake_fetch
        cfm.enrich_sina_finance_item = fake_enrich
        items = cfm.parse_sina_finance_article_items(persist_state=False)
    finally:
        cfm.fetch_sina_roll_page = original_fetch_page
        cfm.enrich_sina_finance_item = original_enrich
        if original_lids is None:
            os.environ.pop("SINA_FINANCE_ROLL_LIDS", None)
        else:
            os.environ["SINA_FINANCE_ROLL_LIDS"] = original_lids
        if original_pages is None:
            os.environ.pop("SINA_FINANCE_ROLL_MAX_PAGES", None)
        else:
            os.environ["SINA_FINANCE_ROLL_MAX_PAGES"] = original_pages

    assert calls == ["nihefve8236522"]
    assert len(items) == 1
    assert len(items[0]["raw"]["roll_channels"]) == 2


def test_sina_roll_partial_channel_failure_keeps_successful_rows() -> None:
    original_fetch_page = cfm.fetch_sina_roll_page
    original_enrich = cfm.enrich_sina_finance_item
    original_lids = os.environ.get("SINA_FINANCE_ROLL_LIDS")
    original_pages = os.environ.get("SINA_FINANCE_ROLL_MAX_PAGES")
    try:
        os.environ["SINA_FINANCE_ROLL_LIDS"] = "bad,2517"
        os.environ["SINA_FINANCE_ROLL_MAX_PAGES"] = "1"

        def fake_fetch(lid: str, page: int, num: int) -> list[dict]:
            if lid == "bad":
                raise RuntimeError("channel down")
            return [
                {
                    "docid": "comos:nihtvuk5345523",
                    "url": "https://finance.sina.com.cn/stock/usstock/c/2026-07-14/doc-inihtvuk5345523.shtml",
                    "title": "软银孙正义认为核聚变将是满足未来AI发展能源需求的关键",
                    "ctime": "1784001411",
                }
            ]

        cfm.fetch_sina_roll_page = fake_fetch
        cfm.enrich_sina_finance_item = lambda item: {**item, "full_text": "detail"}
        items = cfm.parse_sina_finance_article_items(persist_state=False)
    finally:
        cfm.fetch_sina_roll_page = original_fetch_page
        cfm.enrich_sina_finance_item = original_enrich
        if original_lids is None:
            os.environ.pop("SINA_FINANCE_ROLL_LIDS", None)
        else:
            os.environ["SINA_FINANCE_ROLL_LIDS"] = original_lids
        if original_pages is None:
            os.environ.pop("SINA_FINANCE_ROLL_MAX_PAGES", None)
        else:
            os.environ["SINA_FINANCE_ROLL_MAX_PAGES"] = original_pages

    assert len(items) == 1
    assert items[0]["id"] == "nihtvuk5345523"


def test_sina_roll_api_rejects_malformed_response() -> None:
    original_http_get = cfm.http_get
    try:
        class Response:
            content = b'{"result":{"status":{"code":0},"data":{}}}'

        cfm.http_get = lambda *args, **kwargs: Response()
        try:
            cfm.fetch_sina_roll_page("2517", 1, 10)
        except RuntimeError as exc:
            assert "data" in str(exc)
        else:
            raise AssertionError("malformed roll response should fail")
    finally:
        cfm.http_get = original_http_get


def test_sina_roll_stops_at_watermark_and_skips_seen_items() -> None:
    original_db = cfm.DB_PATH
    original_fetch_page = cfm.fetch_sina_roll_page
    original_enrich = cfm.enrich_sina_finance_item
    original_lids = os.environ.get("SINA_FINANCE_ROLL_LIDS")
    original_pages = os.environ.get("SINA_FINANCE_ROLL_MAX_PAGES")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfm.DB_PATH = Path(tmpdir) / "test.sqlite3"
            cfm.save_source_state(cfm.SINA_FINANCE_SOURCE, {"roll_watermarks": {"2517": 200}})
            with cfm.connect_db() as conn:
                cfm.ensure_seen_table(conn)
                conn.execute(
                    """
                    INSERT INTO seen_items (source, item_id, url, title, summary, published_at, first_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (cfm.SINA_FINANCE_SOURCE, "seen-doc", "https://example.com/seen", "seen", "", "", ""),
                )
                conn.commit()
            os.environ["SINA_FINANCE_ROLL_LIDS"] = "2517"
            os.environ["SINA_FINANCE_ROLL_MAX_PAGES"] = "3"
            pages: list[int] = []

            def fake_fetch(lid: str, page: int, num: int) -> list[dict]:
                pages.append(page)
                if page == 1:
                    return [
                        {
                            "docid": "comos:new-doc",
                            "url": "https://finance.sina.com.cn/stock/usstock/c/2026-07-14/doc-inew-doc.shtml",
                            "title": "AI芯片新消息",
                            "ctime": "300",
                        },
                        {
                            "docid": "comos:seen-doc",
                            "url": "https://example.com/seen",
                            "title": "seen",
                            "ctime": "299",
                        },
                    ]
                return [
                    {
                        "docid": "comos:old-doc",
                        "url": "https://example.com/old",
                        "title": "old",
                        "ctime": "199",
                    }
                ]

            cfm.fetch_sina_roll_page = fake_fetch
            cfm.enrich_sina_finance_item = lambda item: {**item, "full_text": "detail"}
            items = cfm.parse_sina_finance_article_items(persist_state=True)
            assert cfm.load_source_state(cfm.SINA_FINANCE_SOURCE)["roll_watermarks"]["2517"] == 200
            cfm.commit_sina_finance_roll_state()
            state = cfm.load_source_state(cfm.SINA_FINANCE_SOURCE)
    finally:
        cfm.DB_PATH = original_db
        cfm.fetch_sina_roll_page = original_fetch_page
        cfm.enrich_sina_finance_item = original_enrich
        if original_lids is None:
            os.environ.pop("SINA_FINANCE_ROLL_LIDS", None)
        else:
            os.environ["SINA_FINANCE_ROLL_LIDS"] = original_lids
        if original_pages is None:
            os.environ.pop("SINA_FINANCE_ROLL_MAX_PAGES", None)
        else:
            os.environ["SINA_FINANCE_ROLL_MAX_PAGES"] = original_pages

    assert pages == [1, 2]
    assert [item["id"] for item in items] == ["new-doc"]
    assert state["roll_watermarks"]["2517"] == 300


def test_sina_first_run_baseline_and_idempotency() -> None:
    original_db = cfm.DB_PATH
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfm.DB_PATH = Path(tmpdir) / "test.sqlite3"
            item = {
                "id": "nihefve8236522",
                "url": "https://finance.sina.com.cn/wm/2026-07-09/doc-inihefve8236522.shtml",
                "title": "英伟达：CPO已进入量产（信息量很大）",
                "summary": "花旗与英伟达IR交流。",
                "published_at": "2026-07-08T23:49:00+00:00",
            }
            assert cfm.save_new_items_with_retry(cfm.SINA_FINANCE_SOURCE, [item], notify_baseline=False) == []
            assert cfm.save_new_items_with_retry(cfm.SINA_FINANCE_SOURCE, [item], notify_baseline=True) == []
            with cfm.connect_db() as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM seen_items WHERE source = ?",
                    (cfm.SINA_FINANCE_SOURCE,),
                ).fetchone()[0]
    finally:
        cfm.DB_PATH = original_db

    assert count == 1


def test_sina_generic_title_uses_detail_body_for_focus() -> None:
    original_match = cfm.investment_universe_match
    try:
        def fake_match(source: str, item: dict):
            assert source == cfm.SINA_FINANCE_SOURCE
            assert "Spectrum-X 进入量产" in item["full_text"]
            return {"matched": True, "tags": ["user_include_keyword"], "reason": "CPO detail body"}

        cfm.investment_universe_match = fake_match
        item = {
            "title": "信息量很大",
            "summary": "",
            "full_text": "英伟达表示 CPO 已随 Spectrum-X 进入量产，NVLink 未来可选择 CPO。",
            "source_module": "新浪财经 / 滚动文章",
        }
        assert cfm.should_focus_item(item, cfm.SINA_FINANCE_SOURCE) is True
    finally:
        cfm.investment_universe_match = original_match


def test_sina_nvidia_cpo_decision_is_source_neutral() -> None:
    text = (
        "花旗与英伟达IR交流，英伟达表示Rubin Ultra没有延迟，CPO已随Spectrum-X交换机进入量产，"
        "客户采用率很高。从2028年Feynman架构开始，NVLink客户可以选择CPO或铜连接。"
    )
    variants = (
        NormalizedMarketItem(
            source=cfm.SINA_FINANCE_SOURCE,
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="英伟达：CPO已进入量产",
            summary=text,
            full_text=text,
        ),
        NormalizedMarketItem(
            source="cls_telegraph_api",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="英伟达：CPO已进入量产",
            summary=text,
            full_text=text,
        ),
    )
    decisions = [decide_market_item(item, holdings=[]) for item in variants]
    assert {decision.action for decision in decisions} == {"push"}
    assert {decision.importance for decision in decisions} == {"high"}
    assert {decision.rule_hits[0]["rule_id"] for decision in decisions} == {"industry_quantified_hardline"}


def test_default_sources_include_star_market_daily() -> None:
    assert "star_market_daily_subject" in cfm.parse_sources_arg([])
    assert "sina_finance_articles" in cfm.parse_sources_arg([])


def main() -> int:
    test_cls_sign_includes_empty_values_and_sorts_keys()
    test_parse_cls_time_accepts_seconds_and_milliseconds()
    test_parse_cls_time_keeps_timezone_aware_iso()
    test_cls_items_preserve_vip_product_and_author_targets()
    test_cls_observation_metadata_does_not_change_decision_action()
    test_cls_poll_interval_skips_recent_fetch()
    test_run_once_fetches_sources_independently()
    test_seen_item_lifecycle_migration_baseline_and_retry()
    test_excluded_item_uses_one_normalized_item_for_report_only_comparison()
    test_admitted_item_reuses_normalized_item_and_processing_failure_retries()
    test_wallstreetcn_processability_retry_waits_for_source_rediscovery()
    test_yicai_morning_brief_is_mandatory_push()
    test_short_english_keyword_requires_token_boundary()
    test_china_media_focus_filters_generic_power_and_accepts_ai_context()
    test_current_admission_is_equivalent_after_normalization()
    test_jin10_mixed_digest_keeps_only_relevant_lines()
    test_star_market_daily_next_data_parser()
    test_star_market_daily_cross_source_dedup()
    test_sina_detail_parser_extracts_wm_article_evidence()
    test_sina_roll_row_normalizes_docid_timestamp_and_url()
    test_sina_roll_union_deduplicates_channels_before_detail_fetch()
    test_sina_roll_partial_channel_failure_keeps_successful_rows()
    test_sina_roll_api_rejects_malformed_response()
    test_sina_roll_stops_at_watermark_and_skips_seen_items()
    test_sina_first_run_baseline_and_idempotency()
    test_sina_generic_title_uses_detail_body_for_focus()
    test_sina_nvidia_cpo_decision_is_source_neutral()
    test_default_sources_include_star_market_daily()
    print("china_finance_media_monitor helper tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
