#!/usr/bin/env python3
"""Regression checks for event delivery execution and dedup transactions."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import market_delivery
from feishu import FeishuResponse
from market_db import init_db
from market_item import DecisionResult, decision_result_from_payload
from market_review_store import (
    article_review_exists,
    official_review_exists,
    save_article_review,
    save_official_review,
    upsert_event_record,
)


def insert_event(
    db_path: Path,
    source_event_id: str,
    title: str = "测试事件",
    *,
    source: str = "sina_flash",
    summary: str = "测试摘要。",
    published_at: str = "2026-07-12T12:00:00+00:00",
) -> int:
    event_id, _ = upsert_event_record(
        {
            "source": source,
            "source_event_id": source_event_id,
            "event_type": "flash_news",
            "title": title,
            "summary": summary,
            "published_at": published_at,
            "raw": {"source_event_id": source_event_id},
        },
        db_path,
    )
    return event_id


def decision_analysis(action: str = "push", *, rule_hits: list[dict] | None = None) -> dict:
    return {
        "core_content": "测试事件核心内容。",
        "brief_reason": "确定性规则命中。",
        "_decision_result": {
            "action": action,
            "importance": "high" if action == "push" else "low",
            "rule_hits": rule_hits or [],
        },
    }


def delivery_rows(db_path: Path) -> list[tuple]:
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT status, error, payload_json FROM deliveries ORDER BY id").fetchall()


def content_review(action: str = "push", *, rule_hits: list[dict] | None = None, official: bool = False) -> dict:
    decision = {
        "action": action,
        "importance": "high" if action == "push" else "low",
        "reason": "确定性规则命中。",
        "rule_hits": rule_hits or [],
    }
    review = {
        "importance": decision["importance"],
        "push_now": True,
        "should_push_now": True,
        "reason": decision["reason"],
        "daily_summary": "测试摘要。",
        "affected_targets": [],
        "raw": {"decision_result": decision},
        "analysis": {"_decision_result": decision},
    }
    if official:
        review.pop("push_now")
    return review


def holding_market_move_rule(target_name: str, target_code: str) -> dict:
    return {
        "rule_id": "holding_keyword_immediate_alert",
        "related_targets": [{"name": target_name, "code": target_code, "relation": "直接持仓"}],
    }


def macro_rule() -> dict:
    return {"rule_id": "macro_policy_line"}


def industry_rule() -> dict:
    return {"rule_id": "industry_quantified_hardline"}


def required_decision(payload: dict) -> DecisionResult:
    decision = decision_result_from_payload(payload)
    assert decision is not None
    return decision


def test_simple_event_card_formats_published_time_for_beijing() -> None:
    card = market_delivery.simple_event_card(
        "sina_stock_news",
        "测试事件",
        "测试摘要。",
        "",
        "2026-07-14T11:31:00+00:00",
        ["核心内容：测试。"],
    )
    expected_time = "**发布时间**：2026-07-14 19:31:00 北京时间（UTC 2026-07-14 11:31:00）"
    assert card["elements"][1]["text"]["content"] == expected_time

    invalid_card = market_delivery.simple_event_card("source", "title", "text", "", "not-a-time", [])
    assert invalid_card["elements"][1]["text"]["content"] == "**发布时间**：not-a-time"

    unknown_card = market_delivery.simple_event_card("source", "title", "text", "", "", [])
    assert unknown_card["elements"][1]["text"]["content"] == "**发布时间**：unknown"


def test_archive_and_missing_webhook_are_recorded_without_sending() -> None:
    original_webhook = os.environ.pop("FEISHU_WEBHOOK", None)
    try:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "surveil.sqlite3"
            init_db(db_path).close()
            archive_id = insert_event(db_path, "archive-1")
            push_id = insert_event(db_path, "push-1")
            archive = decision_analysis("archive")
            push = decision_analysis("push")
            assert market_delivery.deliver_event(
                archive_id, archive, decision=required_decision(archive), db_path=db_path
            ) == "skipped"
            assert market_delivery.deliver_event(
                push_id, push, decision=required_decision(push), db_path=db_path
            ) == "skipped"
            rows = delivery_rows(db_path)
        assert json.loads(rows[0][2])["decision_action"] == "archive"
        assert json.loads(rows[1][2])["reason"] == "FEISHU_WEBHOOK 未配置"
    finally:
        if original_webhook is not None:
            os.environ["FEISHU_WEBHOOK"] = original_webhook


def test_send_failure_releases_reservation_and_records_failure() -> None:
    original_webhook = os.environ.get("FEISHU_WEBHOOK")
    original_send = market_delivery.send_card_with_response
    try:
        os.environ["FEISHU_WEBHOOK"] = "https://example.invalid/webhook"
        market_delivery.send_card_with_response = lambda card: (_ for _ in ()).throw(RuntimeError("send failed"))
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "surveil.sqlite3"
            init_db(db_path).close()
            event_id = insert_event(db_path, "failed-1")
            analysis = decision_analysis()
            assert market_delivery.deliver_event(
                event_id, analysis, decision=required_decision(analysis), db_path=db_path
            ) == "failed"
            row = delivery_rows(db_path)[0]
        assert row[0] == "failed"
        assert row[1] == "send failed"
    finally:
        market_delivery.send_card_with_response = original_send
        if original_webhook is None:
            os.environ.pop("FEISHU_WEBHOOK", None)
        else:
            os.environ["FEISHU_WEBHOOK"] = original_webhook


def test_success_confirms_rule_dedup_and_duplicate_skips_second_send() -> None:
    original_webhook = os.environ.get("FEISHU_WEBHOOK")
    original_send = market_delivery.send_card_with_response
    calls: list[dict] = []
    rule_hit = {
        "rule_id": "international_bank_theme_strategy",
        "dedup_key": "ib_theme:test-convergence",
        "dedup_lookback_days": 14,
    }
    try:
        os.environ["FEISHU_WEBHOOK"] = "https://example.invalid/webhook"

        def fake_send(card: dict) -> FeishuResponse:
            calls.append(card)
            return FeishuResponse(True, 0, "ok", '{"code":0}')

        market_delivery.send_card_with_response = fake_send
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "surveil.sqlite3"
            init_db(db_path).close()
            first_id = insert_event(db_path, "dedup-1", "高盛做多中国 AI 价值链")
            second_id = insert_event(db_path, "dedup-2", "同一报告二次传播")
            analysis = decision_analysis(rule_hits=[rule_hit])
            assert market_delivery.deliver_event(
                first_id, analysis, decision=required_decision(analysis), db_path=db_path
            ) == "sent"
            assert market_delivery.deliver_event(
                second_id, analysis, decision=required_decision(analysis), db_path=db_path
            ) == "skipped"
            with sqlite3.connect(db_path) as conn:
                dedup_status = conn.execute(
                    "SELECT status FROM rule_alert_dedup WHERE dedup_key = ?", (rule_hit["dedup_key"],)
                ).fetchone()[0]
            rows = delivery_rows(db_path)
        assert len(calls) == 1
        assert dedup_status == "sent"
        assert [row[0] for row in rows] == ["sent", "skipped"]
        assert json.loads(rows[1][2])["reason"] == "同一规则观点跨来源去重"
    finally:
        market_delivery.send_card_with_response = original_send
        if original_webhook is None:
            os.environ.pop("FEISHU_WEBHOOK", None)
        else:
            os.environ["FEISHU_WEBHOOK"] = original_webhook


def test_content_delivery_uses_decision_action_and_marks_legacy_rows() -> None:
    original_send = market_delivery.send_card
    try:
        market_delivery.send_card = lambda card: True
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "surveil.sqlite3"
            init_db(db_path).close()
            article_item = {"id": "article-1", "title": "测试文章"}
            archive_review = content_review("archive")
            push_review = content_review("push")
            official_item = {"id": "official-1", "title": "测试官网新闻"}
            official_push = content_review("push", official=True)
            with sqlite3.connect(db_path) as conn:
                save_article_review(conn, "cls_telegraph_api", article_item, push_review)
                save_official_review(conn, "nvidia_blog", official_item, official_push)
            assert (
                market_delivery.deliver_article_review(
                    "cls_telegraph_api",
                    article_item,
                    archive_review,
                    decision=required_decision(archive_review),
                    db_path=db_path,
                    use_rule_dedup=False,
                )
                == "skipped"
            )
            assert (
                market_delivery.deliver_article_review(
                    "cls_telegraph_api",
                    article_item,
                    push_review,
                    decision=required_decision(push_review),
                    db_path=db_path,
                    use_rule_dedup=False,
                )
                == "sent"
            )
            assert (
                market_delivery.deliver_official_review(
                    "nvidia_blog",
                    official_item,
                    official_push,
                    decision=required_decision(official_push),
                    analysis_lines=["核心内容：测试"],
                    db_path=db_path,
                )
                == "sent"
            )
            with sqlite3.connect(db_path) as conn:
                stored_article = article_review_exists(conn, "cls_telegraph_api", "article-1")
                stored_official = official_review_exists(conn, "nvidia_blog", "official-1")
        assert stored_article is not None and stored_article["pushed_at"]
        assert stored_official is not None and stored_official["pushed_at"]
    finally:
        market_delivery.send_card = original_send


def test_reloaded_article_review_still_uses_nested_decision_action() -> None:
    original_send = market_delivery.send_card
    calls: list[dict] = []
    try:
        market_delivery.send_card = lambda card: calls.append(card) or True
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "surveil.sqlite3"
            init_db(db_path).close()
            item = {"id": "nested-archive", "title": "兼容字段冲突测试"}
            review = content_review("archive")
            assert review["push_now"] is True
            with sqlite3.connect(db_path) as conn:
                save_article_review(conn, "value_directory_ib_stocks", item, review)
                loaded = article_review_exists(conn, "value_directory_ib_stocks", "nested-archive")
            assert loaded is not None and loaded["push_now"] is True
            status = market_delivery.deliver_article_review(
                "value_directory_ib_stocks",
                item,
                loaded,
                decision=required_decision(loaded),
                db_path=db_path,
                use_rule_dedup=False,
            )
        assert status == "skipped"
        assert calls == []
    finally:
        market_delivery.send_card = original_send


def test_article_delivery_dedup_skips_without_changing_decision_action() -> None:
    original_send = market_delivery.send_card
    calls: list[dict] = []
    rule_hit = {
        "rule_id": "international_bank_theme_strategy",
        "dedup_key": "ib_theme:article-adapter",
        "dedup_lookback_days": 14,
    }
    try:
        market_delivery.send_card = lambda card: calls.append(card) or True
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "surveil.sqlite3"
            init_db(db_path).close()
            first_item = {"id": "article-dedup-1", "title": "高盛做多 AI 价值链"}
            second_item = {"id": "article-dedup-2", "title": "同一报告再次传播"}
            review = content_review("push", rule_hits=[rule_hit])
            with sqlite3.connect(db_path) as conn:
                save_article_review(conn, "cls_telegraph_api", first_item, review)
                save_article_review(conn, "jin10_rsshub_important", second_item, review)
            assert (
                market_delivery.deliver_article_review(
                    "cls_telegraph_api",
                    first_item,
                    review,
                    decision=required_decision(review),
                    db_path=db_path,
                )
                == "sent"
            )
            assert (
                market_delivery.deliver_article_review(
                    "jin10_rsshub_important",
                    second_item,
                    review,
                    decision=required_decision(review),
                    db_path=db_path,
                )
                == "duplicate"
            )
            with sqlite3.connect(db_path) as conn:
                stored = article_review_exists(conn, "jin10_rsshub_important", "article-dedup-2")
        assert len(calls) == 1
        assert stored is not None and stored["push_now"] is False
        assert stored["raw"]["raw"]["decision_result"]["action"] == "push"
    finally:
        market_delivery.send_card = original_send


def test_intraday_market_move_cross_source_dedup_preserves_push_decision() -> None:
    original_send = market_delivery.send_card
    calls: list[dict] = []
    first_item = {
        "id": "yicai-cpo-1",
        "title": "CPO概念股午后直线拉升，则成电子涨超27%，源杰科技涨超10%。",
        "published_at": "2026-07-14T05:17:35+00:00",
    }
    second_item = {
        "id": "jin10-cpo-1",
        "title": "A股CPO概念股午后直线拉升，源杰科技、则成电子等涨超10%。",
        "published_at": "2026-07-14T05:16:48+00:00",
    }
    rule_hit = holding_market_move_rule("源杰科技", "688498.SH")
    try:
        market_delivery.send_card = lambda card: calls.append(card) or True
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "surveil.sqlite3"
            init_db(db_path).close()
            review = content_review("push", rule_hits=[rule_hit])
            with sqlite3.connect(db_path) as conn:
                save_article_review(conn, "yicai_brief", first_item, review)
                save_article_review(conn, "jin10_rsshub_important", second_item, review)
            assert required_decision(review).action == "push"
            assert (
                market_delivery.deliver_article_review(
                    "yicai_brief", first_item, review, decision=required_decision(review), db_path=db_path
                )
                == "sent"
            )
            assert (
                market_delivery.deliver_article_review(
                    "jin10_rsshub_important", second_item, review, decision=required_decision(review), db_path=db_path
                )
                == "duplicate"
            )
            with sqlite3.connect(db_path) as conn:
                stored = article_review_exists(conn, "jin10_rsshub_important", "jin10-cpo-1")
        assert len(calls) == 1
        assert stored is not None
        assert stored["raw"]["raw"]["rule_alert_dedup"]["rule_id"] == "intraday_market_move"
        assert stored["raw"]["raw"]["decision_result"]["action"] == "push"
    finally:
        market_delivery.send_card = original_send


def test_distinct_concepts_are_not_intraday_market_move_duplicates() -> None:
    original_send = market_delivery.send_card
    calls: list[dict] = []
    cpo = {
        "id": "cpo-1",
        "title": "CPO概念股午后直线拉升，源杰科技涨超10%。",
        "published_at": "2026-07-14T05:17:35+00:00",
    }
    pcb = {
        "id": "pcb-1",
        "title": "PCB概念涨势扩大，铜冠铜箔涨超10%。",
        "published_at": "2026-07-14T05:34:16+00:00",
    }
    try:
        market_delivery.send_card = lambda card: calls.append(card) or True
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "surveil.sqlite3"
            init_db(db_path).close()
            cpo_review = content_review("push", rule_hits=[holding_market_move_rule("源杰科技", "688498.SH")])
            pcb_review = content_review("push", rule_hits=[holding_market_move_rule("铜冠铜箔", "301217.SZ")])
            assert (
                market_delivery.deliver_article_review(
                    "yicai_brief", cpo, cpo_review, decision=required_decision(cpo_review), db_path=db_path
                )
                == "sent"
            )
            assert (
                market_delivery.deliver_article_review(
                    "cls_telegraph_api", pcb, pcb_review, decision=required_decision(pcb_review), db_path=db_path
                )
                == "sent"
            )
        assert len(calls) == 2
    finally:
        market_delivery.send_card = original_send


def test_intraday_market_move_send_failure_releases_reservation() -> None:
    original_send = market_delivery.send_card
    first_item = {
        "id": "cpo-failed-1",
        "title": "CPO概念股午后直线拉升，源杰科技涨超10%。",
        "published_at": "2026-07-14T05:17:35+00:00",
    }
    retry_item = {**first_item, "id": "cpo-retry-1"}
    review = content_review("push", rule_hits=[holding_market_move_rule("源杰科技", "688498.SH")])
    try:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "surveil.sqlite3"
            init_db(db_path).close()
            market_delivery.send_card = lambda card: False
            assert (
                market_delivery.deliver_article_review(
                    "yicai_brief", first_item, review, decision=required_decision(review), db_path=db_path
                )
                == "skipped"
            )
            market_delivery.send_card = lambda card: True
            assert (
                market_delivery.deliver_article_review(
                    "jin10_rsshub_important", retry_item, review, decision=required_decision(review), db_path=db_path
                )
                == "sent"
            )
    finally:
        market_delivery.send_card = original_send


def test_event_delivery_records_intraday_market_move_duplicate() -> None:
    original_webhook = os.environ.get("FEISHU_WEBHOOK")
    original_send = market_delivery.send_card_with_response
    rule_hit = holding_market_move_rule("源杰科技", "688498.SH")
    try:
        os.environ["FEISHU_WEBHOOK"] = "https://example.invalid/webhook"
        market_delivery.send_card_with_response = lambda card: FeishuResponse(True, 0, "ok", '{"code":0}')
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "surveil.sqlite3"
            init_db(db_path).close()
            first_id = insert_event(
                db_path,
                "event-cpo-1",
                "CPO概念股午后直线拉升，源杰科技涨超10%。",
                source="yicai_brief",
                published_at="2026-07-14T05:17:35+00:00",
            )
            second_id = insert_event(
                db_path,
                "event-cpo-2",
                "A股CPO概念股午后直线拉升，源杰科技涨超10%。",
                source="jin10_rsshub_important",
                published_at="2026-07-14T05:16:48+00:00",
            )
            analysis = decision_analysis(rule_hits=[rule_hit])
            assert market_delivery.deliver_event(first_id, analysis, decision=required_decision(analysis), db_path=db_path) == "sent"
            assert market_delivery.deliver_event(second_id, analysis, decision=required_decision(analysis), db_path=db_path) == "duplicate"
            rows = delivery_rows(db_path)
        assert [row[0] for row in rows] == ["sent", "duplicate"]
        assert json.loads(rows[1][2])["first_source"] == "yicai_brief"
    finally:
        market_delivery.send_card_with_response = original_send
        if original_webhook is None:
            os.environ.pop("FEISHU_WEBHOOK", None)
        else:
            os.environ["FEISHU_WEBHOOK"] = original_webhook


def test_macro_release_and_reaction_each_send_once_while_warsh_speech_is_retained() -> None:
    original_send = market_delivery.send_card
    calls: list[dict] = []
    release_one = {
        "id": "cpi-release-1",
        "title": "美国6月CPI环比下降0.4%，同比增长3.5%，均低于预期。",
        "published_at": "2026-07-14T12:32:47+00:00",
    }
    release_two = {
        "id": "cpi-release-2",
        "title": "美国6月消费者价格指数同比增长3.5%，环比下降0.4%。",
        "published_at": "2026-07-14T12:34:27+00:00",
    }
    reaction_one = {
        "id": "cpi-reaction-1",
        "title": "美国6月CPI环比下降0.4%超预期，美股期货跳涨，纳指期货涨1.3%。",
        "published_at": "2026-07-14T12:34:32+00:00",
    }
    reaction_two = {
        "id": "cpi-reaction-2",
        "title": "美国6月CPI同比增长3.5%，美元走低，美债收益率下跌。",
        "published_at": "2026-07-14T12:42:00+00:00",
    }
    warsh = {
        "id": "cpi-warsh-1",
        "title": "美国6月CPI环比下降0.4%。美联储主席沃什表示，不会容忍通胀过高。",
        "published_at": "2026-07-14T12:46:37+00:00",
    }
    review = content_review("push", rule_hits=[macro_rule()])
    try:
        market_delivery.send_card = lambda card: calls.append(card) or True
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "surveil.sqlite3"
            init_db(db_path).close()
            items = (release_one, release_two, reaction_one, reaction_two, warsh)
            sources = (
                "cls_telegraph_api",
                "yicai_brief",
                "cls_telegraph_api",
                "wallstreetcn_news",
                "sina_finance_articles",
            )
            with sqlite3.connect(db_path) as conn:
                for source, item in zip(sources, items, strict=True):
                    save_article_review(conn, source, item, review)
            statuses = [
                market_delivery.deliver_article_review(
                    source, item, review, decision=required_decision(review), db_path=db_path
                )
                for source, item in zip(sources, items, strict=True)
            ]
            with sqlite3.connect(db_path) as conn:
                stored = article_review_exists(conn, "yicai_brief", "cpi-release-2")
                dedup_rows = conn.execute(
                    "SELECT rule_id, status FROM rule_alert_dedup ORDER BY created_at"
                ).fetchall()
        assert statuses == ["sent", "duplicate", "sent", "duplicate", "sent"]
        assert len(calls) == 3
        assert stored is not None and stored["push_now"] is False
        assert stored["raw"]["raw"]["decision_result"]["action"] == "push"
        assert dedup_rows == [("macro_data_release", "sent"), ("macro_market_reaction", "sent")]
    finally:
        market_delivery.send_card = original_send


def test_event_delivery_records_macro_release_duplicate() -> None:
    original_webhook = os.environ.get("FEISHU_WEBHOOK")
    original_send = market_delivery.send_card_with_response
    try:
        os.environ["FEISHU_WEBHOOK"] = "https://example.invalid/webhook"
        market_delivery.send_card_with_response = lambda card: FeishuResponse(True, 0, "ok", '{"code":0}')
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "surveil.sqlite3"
            init_db(db_path).close()
            first_id = insert_event(
                db_path,
                "event-cpi-1",
                "美国6月CPI环比下降0.4%，同比增长3.5%。",
                source="cls_telegraph_api",
                published_at="2026-07-14T12:32:47+00:00",
            )
            second_id = insert_event(
                db_path,
                "event-cpi-2",
                "美国6月消费者价格指数同比增长3.5%，环比下降0.4%。",
                source="sina_flash",
                published_at="2026-07-14T12:34:00+00:00",
            )
            analysis = decision_analysis(rule_hits=[macro_rule()])
            assert market_delivery.deliver_event(
                first_id, analysis, decision=required_decision(analysis), db_path=db_path
            ) == "sent"
            assert market_delivery.deliver_event(
                second_id, analysis, decision=required_decision(analysis), db_path=db_path
            ) == "duplicate"
            rows = delivery_rows(db_path)
        duplicate = json.loads(rows[1][2])
        assert [row[0] for row in rows] == ["sent", "duplicate"]
        assert duplicate["dedup_kind"] == "macro_data_release"
        assert duplicate["first_source"] == "cls_telegraph_api"
    finally:
        market_delivery.send_card_with_response = original_send
        if original_webhook is None:
            os.environ.pop("FEISHU_WEBHOOK", None)
        else:
            os.environ["FEISHU_WEBHOOK"] = original_webhook


def test_ibm_industry_fact_cross_source_article_dedup_preserves_push_decision() -> None:
    original_send = market_delivery.send_card
    calls: list[dict] = []
    first = {
        "id": "ibm-thesis-1",
        "title": "IBM称客户将资本支出转向服务器、存储和内存采购",
        "summary": "企业为在涨价前锁定供应紧张的基础设施而调整预算。",
        "published_at": "2026-07-14T11:04:49+00:00",
    }
    second = {
        "id": "ibm-thesis-2",
        "title": "IBM暴跌验证存储短缺，企业IT支出转向硬件",
        "summary": "IBM客户将预算转向服务器、存储和内存采购以保障供应。",
        "published_at": "2026-07-14T17:13:45+00:00",
    }
    review = content_review("push", rule_hits=[industry_rule()])
    try:
        market_delivery.send_card = lambda card: calls.append(card) or True
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "surveil.sqlite3"
            init_db(db_path).close()
            with sqlite3.connect(db_path) as conn:
                save_article_review(conn, "cls_telegraph_api", first, review)
                save_article_review(conn, "wallstreetcn_news", second, review)
            first_status = market_delivery.deliver_article_review(
                "cls_telegraph_api", first, review, decision=required_decision(review), db_path=db_path
            )
            second_status = market_delivery.deliver_article_review(
                "wallstreetcn_news", second, review, decision=required_decision(review), db_path=db_path
            )
            with sqlite3.connect(db_path) as conn:
                stored = article_review_exists(conn, "wallstreetcn_news", "ibm-thesis-2")
        assert [first_status, second_status] == ["sent", "duplicate"]
        assert len(calls) == 1
        assert stored is not None and stored["push_now"] is False
        assert stored["raw"]["raw"]["decision_result"]["action"] == "push"
        assert stored["raw"]["raw"]["rule_alert_dedup"]["rule_id"] == "industry_fact_dedup"
    finally:
        market_delivery.send_card = original_send


def test_event_delivery_records_ibm_industry_fact_duplicate() -> None:
    original_webhook = os.environ.get("FEISHU_WEBHOOK")
    original_send = market_delivery.send_card_with_response
    try:
        os.environ["FEISHU_WEBHOOK"] = "https://example.invalid/webhook"
        market_delivery.send_card_with_response = lambda card: FeishuResponse(True, 0, "ok", '{"code":0}')
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "surveil.sqlite3"
            init_db(db_path).close()
            first_id = insert_event(
                db_path,
                "event-ibm-1",
                "IBM称客户将资本支出转向服务器、存储和内存采购",
                source="cls_telegraph_api",
                summary="企业为锁定供应紧张的基础设施而调整预算。",
            )
            second_id = insert_event(
                db_path,
                "event-ibm-2",
                "IBM暴跌验证存储短缺，企业IT支出转向硬件",
                source="wallstreetcn_news",
                summary="IBM客户将支出转向服务器和内存采购以保障供应。",
            )
            analysis = decision_analysis(rule_hits=[industry_rule()])
            assert market_delivery.deliver_event(
                first_id, analysis, decision=required_decision(analysis), db_path=db_path
            ) == "sent"
            assert market_delivery.deliver_event(
                second_id, analysis, decision=required_decision(analysis), db_path=db_path
            ) == "duplicate"
            rows = delivery_rows(db_path)
        duplicate = json.loads(rows[1][2])
        assert [row[0] for row in rows] == ["sent", "duplicate"]
        assert duplicate["dedup_kind"] == "industry_fact"
        assert duplicate["first_source"] == "cls_telegraph_api"
    finally:
        market_delivery.send_card_with_response = original_send
        if original_webhook is None:
            os.environ.pop("FEISHU_WEBHOOK", None)
        else:
            os.environ["FEISHU_WEBHOOK"] = original_webhook


def test_coreweave_hedge_cross_source_article_dedup_preserves_push_decision() -> None:
    original_send = market_delivery.send_card
    calls: list[dict] = []
    first = {
        "id": "coreweave-hedge-1",
        "title": "人工智能云计算公司CoreWeave借鉴华尔街策略 对冲内存芯片价格风险",
        "summary": "CoreWeave正在探索使用看跌期权，对冲未来存储芯片价格下跌风险。",
        "published_at": "2026-07-14T23:47:24+00:00",
    }
    second = {
        "id": "coreweave-hedge-2",
        "title": "消息人士称，Coreweave(CRWV.O)正在探索使用金融衍生品",
        "summary": "该公司拟以衍生品对冲未来存储芯片价格下跌的风险。",
        "published_at": "2026-07-14T23:48:36+00:00",
    }
    review = content_review("push", rule_hits=[industry_rule()])
    try:
        market_delivery.send_card = lambda card: calls.append(card) or True
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "surveil.sqlite3"
            init_db(db_path).close()
            with sqlite3.connect(db_path) as conn:
                save_article_review(conn, "sina_finance_articles", first, review)
                save_article_review(conn, "jin10_rsshub_important", second, review)
            first_status = market_delivery.deliver_article_review(
                "sina_finance_articles", first, review, decision=required_decision(review), db_path=db_path
            )
            second_status = market_delivery.deliver_article_review(
                "jin10_rsshub_important", second, review, decision=required_decision(review), db_path=db_path
            )
            with sqlite3.connect(db_path) as conn:
                stored = article_review_exists(conn, "jin10_rsshub_important", "coreweave-hedge-2")
        assert [first_status, second_status] == ["sent", "duplicate"]
        assert len(calls) == 1
        assert stored is not None and stored["push_now"] is False
        assert stored["raw"]["raw"]["decision_result"]["action"] == "push"
        assert stored["raw"]["raw"]["rule_alert_dedup"]["dedup_key"] == (
            "industry_fact:coreweave:price_risk_hedge:exploring:storage_chip:down"
        )
    finally:
        market_delivery.send_card = original_send


def main() -> int:
    test_simple_event_card_formats_published_time_for_beijing()
    test_archive_and_missing_webhook_are_recorded_without_sending()
    test_send_failure_releases_reservation_and_records_failure()
    test_success_confirms_rule_dedup_and_duplicate_skips_second_send()
    test_content_delivery_uses_decision_action_and_marks_legacy_rows()
    test_reloaded_article_review_still_uses_nested_decision_action()
    test_article_delivery_dedup_skips_without_changing_decision_action()
    test_intraday_market_move_cross_source_dedup_preserves_push_decision()
    test_distinct_concepts_are_not_intraday_market_move_duplicates()
    test_intraday_market_move_send_failure_releases_reservation()
    test_event_delivery_records_intraday_market_move_duplicate()
    test_macro_release_and_reaction_each_send_once_while_warsh_speech_is_retained()
    test_event_delivery_records_macro_release_duplicate()
    test_ibm_industry_fact_cross_source_article_dedup_preserves_push_decision()
    test_event_delivery_records_ibm_industry_fact_duplicate()
    test_coreweave_hedge_cross_source_article_dedup_preserves_push_decision()
    print("market delivery checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
