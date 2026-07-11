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
from market_review_store import upsert_event_record


def insert_event(db_path: Path, source_event_id: str, title: str = "测试事件") -> int:
    event_id, _ = upsert_event_record(
        {
            "source": "sina_flash",
            "source_event_id": source_event_id,
            "event_type": "flash_news",
            "title": title,
            "summary": "测试摘要。",
            "published_at": "2026-07-12T12:00:00+00:00",
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


def test_archive_and_missing_webhook_are_recorded_without_sending() -> None:
    original_webhook = os.environ.pop("FEISHU_WEBHOOK", None)
    try:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "surveil.sqlite3"
            init_db(db_path).close()
            archive_id = insert_event(db_path, "archive-1")
            push_id = insert_event(db_path, "push-1")
            assert market_delivery.deliver_event(archive_id, decision_analysis("archive"), db_path) == "skipped"
            assert market_delivery.deliver_event(push_id, decision_analysis("push"), db_path) == "skipped"
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
            assert market_delivery.deliver_event(event_id, decision_analysis(), db_path) == "failed"
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
            assert market_delivery.deliver_event(first_id, analysis, db_path) == "sent"
            assert market_delivery.deliver_event(second_id, analysis, db_path) == "skipped"
            with sqlite3.connect(db_path) as conn:
                dedup_status = conn.execute(
                    "SELECT status FROM rule_alert_dedup WHERE dedup_key = ?", (rule_hit["dedup_key"],)
                ).fetchone()[0]
            rows = delivery_rows(db_path)
        assert len(calls) == 1
        assert dedup_status == "sent"
        assert [row[0] for row in rows] == ["sent", "skipped"]
        assert json.loads(rows[1][2])["reason"] == "同一国际投行主题报告跨来源去重"
    finally:
        market_delivery.send_card_with_response = original_send
        if original_webhook is None:
            os.environ.pop("FEISHU_WEBHOOK", None)
        else:
            os.environ["FEISHU_WEBHOOK"] = original_webhook


def main() -> int:
    test_archive_and_missing_webhook_are_recorded_without_sending()
    test_send_failure_releases_reservation_and_records_failure()
    test_success_confirms_rule_dedup_and_duplicate_skips_second_send()
    print("market delivery checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
