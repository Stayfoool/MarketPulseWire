#!/usr/bin/env python3
"""Regression checks for the bounded production LLM Web projection."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from llm_decision_audit_cleanup import redact_expired_production_audits
from llm_decision_web import (
    WEB_PROJECTION_VERSION,
    build_web_projection,
    llm_decision_rows,
    llm_decision_summary,
    load_web_projections,
    write_web_projection,
)


def audit_payload(
    status: str = "uncertain",
    review_id: int = 12,
    *,
    market_item_id: int = 1,
    source: str = "source-a",
    source_item_id: str = "item-a",
    generated_at: str = "2026-07-26T01:02:03+00:00",
    legacy_segments: bool = False,
) -> dict:
    user_payload = {
        "article_segments" if legacy_segments else "source_segments": [
            {"id": "T1", "field": "title", "text": "标题证据"},
            {"id": "B1", "field": "full_text", "text": "正文反证"},
        ]
    }
    response = {
        "rule_results": [
            {
                "rule_id": "holding_material_event",
                "judgement": "uncertain",
                "counterevidence_ids": ["B1"],
                "reason": "缺少决定性事实",
            },
            {"rule_id": "semiconductor_material_change", "judgement": "not_matched"},
        ]
    }
    return {
        "generated_at": generated_at,
        "market_item_id": market_item_id,
        "market_review_id": review_id,
        "source": source,
        "source_item_id": source_item_id,
        "evaluation_status": status,
        "failure_reason": "没有具体规则命中且存在 uncertain",
        "llm_decision_rule_version": "test-v1",
        "prompt_version": "prompt-test",
        "model": "test-model",
        "provider": "test-provider",
        "model_audit": {
            "calls": [
                {
                    "request": {
                        "messages": [
                            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}
                        ]
                    },
                    "response": {"content": json.dumps(response, ensure_ascii=False)},
                    "validation": {
                        "validation_errors": ["test validation error"],
                        "evidence_reference_count": 1,
                        "evidence_character_count": 4,
                    },
                }
            ]
        },
        "decision": None,
    }


def current_audit_payload() -> dict:
    payload = audit_payload(status="completed")
    response = {
        "rule_results": [
            {
                "rule_id": "company_industry_execution_change",
                "action": "push",
                "evidence_ids": ["T1"],
                "reason": "标题完整证明 push。",
            },
            {"rule_id": "company_performance_change", "action": "archive"},
        ]
    }
    payload["failure_reason"] = ""
    payload["model_audit"]["calls"][0]["response"]["content"] = json.dumps(
        response, ensure_ascii=False
    )
    return payload


def walk_values(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_values(child)


def test_historical_uncertain_projection_is_bounded() -> None:
    projection = build_web_projection(audit_payload())
    assert projection["version"] == WEB_PROJECTION_VERSION
    assert projection["evaluation_status"] == "uncertain"
    assessment = projection["calls"][0]["rule_assessments"][0]
    assert assessment["judgement"] == "uncertain"
    assert assessment["reason"] == "缺少决定性事实"
    assert assessment["counterevidence"][0]["quote"] == "正文反证"
    forbidden = {
        "request",
        "response",
        "content",
        "source_segments",
        "article_segments",
        "system_prompt",
        "user_payload",
    }
    assert not any(key in forbidden for key, _ in walk_values(projection))


def test_current_action_projection_is_bounded() -> None:
    projection = build_web_projection(current_audit_payload())
    assessments = projection["calls"][0]["rule_assessments"]
    assert assessments[0]["action"] == "push"
    assert "judgement" not in assessments[0]
    assert assessments[0]["evidence"][0]["quote"] == "标题证据"
    assert assessments[1] == {
        "rule_id": "company_performance_change",
        "action": "archive",
    }


def test_existing_private_audits_remain_readable() -> None:
    projection = build_web_projection(audit_payload(legacy_segments=True))
    assessment = projection["calls"][0]["rule_assessments"][0]
    assert assessment["counterevidence"][0]["quote"] == "正文反证"


def test_projection_write_is_idempotent_and_mode_bounded() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        directory.chmod(0o700)
        path = directory / "llm-decision-audit-1-12.json"
        path.write_text(json.dumps(audit_payload(), ensure_ascii=False), encoding="utf-8")
        path.chmod(0o600)
        assert write_web_projection(path, apply=False) is True
        assert write_web_projection(path, apply=True) is True
        assert write_web_projection(path, apply=True) is False
        loaded = load_web_projections(directory)
        assert (12, 1, "source-a", "item-a") in loaded
        assert loaded[(12, 1, "source-a", "item-a")][0]["evaluation_status"] == "uncertain"


def test_rows_show_terminal_insufficient_evidence_without_action() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        directory.chmod(0o700)
        path = directory / "llm-decision-audit-1-12.json"
        path.write_text(json.dumps(audit_payload(), ensure_ascii=False), encoding="utf-8")
        path.chmod(0o600)
        write_web_projection(path, apply=True)
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE market_items (
                id INTEGER PRIMARY KEY, source TEXT, source_item_id TEXT, title TEXT,
                url TEXT, published_at TEXT, first_seen_at TEXT, content_type TEXT
            );
            CREATE TABLE market_reviews (
                id INTEGER PRIMARY KEY, market_item_id INTEGER, is_current INTEGER,
                admission_status TEXT, review_status TEXT, decision_action TEXT,
                decision_json TEXT, created_at TEXT, completed_at TEXT
            );
            INSERT INTO market_items VALUES (1, 'source-a', 'item-a', '测试标题', 'https://example.com/a', '', '2026-07-26T01:00:00+00:00', 'article');
            INSERT INTO market_reviews VALUES (12, 1, 1, 'admitted', 'insufficient_evidence', NULL, NULL, '2026-07-26T01:01:00+00:00', NULL);
            """
        )
        rows = llm_decision_rows(
            conn,
            start_utc="2026-07-26T00:00:00+00:00",
            end_utc="2026-07-27T00:00:00+00:00",
            audit_dir=directory,
        )
        assert len(rows) == 1
        assert rows[0]["decision_action"] == ""
        assert rows[0]["model_status"] == "insufficient_evidence"
        assert rows[0]["attempts"][0]["calls"][0]["rule_assessments"]
        summary = llm_decision_summary(rows)
        assert summary["uncertain_attempts"] == 1
        assert summary["current_insufficient_evidence"] == 1
        assert summary["current_failed_retryable"] == 0
        conn.executescript(
            """
            INSERT INTO market_items VALUES (2, 'source-b', 'item-b', '第二条', 'https://example.com/b', '', '2026-07-26T02:00:00+00:00', 'article');
            INSERT INTO market_reviews VALUES (13, 2, 1, 'admitted', 'succeeded', 'push', '{}', '2026-07-26T02:01:00+00:00', '2026-07-26T02:01:01+00:00');
            """
        )
        filtered = llm_decision_rows(
            conn,
            start_utc="2026-07-26T00:00:00+00:00",
            end_utc="2026-07-27T00:00:00+00:00",
            action=["push", "daily"],
            status=["completed", "model_unavailable"],
            source=["source-b", "source-c"],
            audit_dir=directory,
        )
        assert [row["market_review_id"] for row in filtered] == [13]
        conn.close()


def test_rows_ignore_retired_database_audits_with_reused_ids() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        directory.chmod(0o700)
        payloads = (
            audit_payload(
                generated_at="2026-07-26T00:59:00+00:00",
                source="retired-source",
                source_item_id="retired-item",
            ),
            audit_payload(generated_at="2026-07-26T01:00:00+00:00"),
            audit_payload(generated_at="2026-07-26T01:02:00+00:00"),
            audit_payload(status="completed", generated_at="2026-07-26T01:03:00+00:00"),
        )
        for index, payload in enumerate(payloads, start=1):
            path = directory / f"llm-decision-audit-{index}.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            path.chmod(0o600)
            write_web_projection(path, apply=True)

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE market_items (
                id INTEGER PRIMARY KEY, source TEXT, source_item_id TEXT, title TEXT,
                url TEXT, published_at TEXT, first_seen_at TEXT, content_type TEXT
            );
            CREATE TABLE market_reviews (
                id INTEGER PRIMARY KEY, market_item_id INTEGER, is_current INTEGER,
                admission_status TEXT, review_status TEXT, decision_action TEXT,
                decision_json TEXT, created_at TEXT, completed_at TEXT
            );
            INSERT INTO market_items VALUES (1, 'source-a', 'item-a', '当前标题', '', '', '2026-07-26T01:00:00+00:00', 'article');
            INSERT INTO market_reviews VALUES (12, 1, 1, 'admitted', 'succeeded', 'push', '{}', '2026-07-26T01:01:00+00:00', '2026-07-26T01:04:00+00:00');
            """
        )
        rows = llm_decision_rows(
            conn,
            start_utc="2026-07-26T00:00:00+00:00",
            end_utc="2026-07-27T00:00:00+00:00",
            audit_dir=directory,
        )
        assert len(rows) == 1
        assert [attempt["generated_at"] for attempt in rows[0]["attempts"]] == [
            "2026-07-26T01:02:00+00:00",
            "2026-07-26T01:03:00+00:00",
        ]
        assert rows[0]["uncertain_attempts"] == 1
        conn.close()


def test_retention_removes_raw_calls_but_keeps_web_projection() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        directory.chmod(0o700)
        path = directory / "llm-decision-audit-1-12.json"
        path.write_text(json.dumps(audit_payload(), ensure_ascii=False), encoding="utf-8")
        path.chmod(0o600)
        assert write_web_projection(path, apply=True) is True
        removed = redact_expired_production_audits(
            directory,
            now=datetime(2026, 8, 30, tzinfo=timezone.utc),
        )
        assert removed == 1
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["web_projection"]["version"] == WEB_PROJECTION_VERSION
        assert payload["model_audit"]["status"] == "expired"


def test_jev_shadow_rows_and_summary_are_bounded() -> None:
    from llm_decision_web import jev_shadow_summary, load_jev_shadow_rows

    agree_row = {
        "contract_version": "jev-shadow-comparison-v1",
        "comparison_only": True,
        "affects_current_decision": False,
        "generated_at": "2026-09-30T01:00:00+00:00",
        "market_item_id": 1,
        "market_review_id": 1,
        "source": "digitimes",
        "source_item_id": "a1",
        "production": {"action": "push", "model": "qwen3.7-flash-2026-07-15", "usage": {"prompt_tokens": 1000, "completion_tokens": 100}, "cost_cny": 0.00028},
        "jev": {"status": "completed", "action": "push", "usage": {"prompt_tokens": 900}, "cost_cny": 0.00027,
                "rule_choices": [{"rule_id": "r1", "action": "push", "probability": 0.9, "confidence": 0.8}]},
        "comparison": {"item_agree": True, "action_pair": "push->push", "rule_agree": 1, "rule_total": 1, "missed_push": False, "extra_push": False},
    }
    error_row = {"contract_version": "jev-shadow-comparison-v1", "generated_at": "2026-09-30T02:00:00+00:00", "error": "boom"}
    out_of_window = dict(agree_row, generated_at="2026-09-01T00:00:00+00:00", market_item_id=2, market_review_id=2)
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        (directory / "jev-shadow-20260930.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in (agree_row, error_row, out_of_window)) + "\n",
            encoding="utf-8",
        )
        rows = load_jev_shadow_rows(directory, start_day="2026-09-30", end_day="2026-09-30")
        assert len(rows) == 2
        summary = jev_shadow_summary(rows)
        assert summary["rows"] == 2 and summary["completed"] == 1 and summary["errors"] == 1
        assert summary["item_agree"] == {"agree": 1, "total": 1, "rate": 1.0}
        assert summary["action_pairs"] == {"push->push": 1}
        assert summary["missed_push"] == 0
        assert summary["rule_agree"]["rate"] == 1.0
        assert summary["avg_jev_confidence"] == 0.8
        assert summary["cost"]["production_cny"] == 0.00028
        assert summary["cost"]["jev_cny"] == 0.00027



def test_jev_shadow_details_filters_and_metadata() -> None:
    from llm_decision_web import jev_shadow_details

    missed_row = {
        "generated_at": "2026-09-30T01:00:00+00:00",
        "market_item_id": 101,
        "market_review_id": 11,
        "source": "digitimes",
        "source_item_id": "a-101",
        "production_rule_actions": {"rule_a": "push", "rule_b": "archive"},
        "production": {"action": "push", "model": "qwen3.7-flash-2026-07-15", "cost_cny": 0.002},
        "jev": {"status": "completed", "action": "archive", "cost_cny": 0.001,
                "rule_choices": [
                    {"rule_id": "rule_a", "action": "archive", "probability": 0.7, "confidence": 0.6},
                    {"rule_id": "rule_b", "action": "archive", "probability": 0.9, "confidence": 0.8},
                ]},
        "comparison": {"item_agree": False, "action_pair": "push->archive", "rule_agree": 1,
                       "rule_total": 2, "missed_push": True, "extra_push": False},
    }
    agree_row = {
        "generated_at": "2026-09-30T02:00:00+00:00",
        "market_item_id": 102,
        "market_review_id": 12,
        "source": "rss",
        "source_item_id": "b-102",
        "production_rule_actions": {},
        "production": {"action": "archive", "model": "m", "cost_cny": 0.0},
        "jev": {"status": "completed", "action": "archive", "cost_cny": 0.0, "rule_choices": []},
        "comparison": {"item_agree": True, "action_pair": "archive->archive", "rule_agree": 0,
                       "rule_total": 0, "missed_push": False, "extra_push": False},
    }
    error_row = {"generated_at": "2026-09-30T03:00:00+00:00", "market_item_id": 103,
                 "market_review_id": 13, "error": "boom"}
    meta = {
        101: {"id": 101, "title": "扩产新闻标题", "url": "https://example.test/101", "published_at": "2026-09-30T08:00:00"},
        102: {"id": 102, "title": "普通新闻", "url": "https://example.test/102", "published_at": ""},
    }
    all_rows = jev_shadow_details([missed_row, agree_row, error_row], item_meta=meta)
    assert [row["market_item_id"] for row in all_rows] == [101, 102, 103]
    assert all_rows[0]["title"] == "扩产新闻标题"
    assert all_rows[0]["url"] == "https://example.test/101"
    assert all_rows[0]["production_action"] == "push"
    assert all_rows[0]["production_rule_actions"] == {"rule_a": "push", "rule_b": "archive"}
    assert len(all_rows[0]["jev_rule_choices"]) == 2
    assert all_rows[0]["jev_rule_choices"][0]["probability"] == 0.7
    assert all_rows[2]["error"] == "boom" and all_rows[2]["production_action"] == ""

    assert [r["market_item_id"] for r in jev_shadow_details([missed_row, agree_row, error_row], item_meta=meta, kind="missed_push")] == [101]
    assert [r["market_item_id"] for r in jev_shadow_details([missed_row, agree_row, error_row], item_meta=meta, kind="extra_push")] == []
    assert [r["market_item_id"] for r in jev_shadow_details([missed_row, agree_row, error_row], item_meta=meta, kind="mismatch")] == [101]
    limited = jev_shadow_details([missed_row, agree_row, error_row], item_meta=meta, limit=1)
    assert len(limited) == 1
    unknown = jev_shadow_details([missed_row], item_meta=meta, kind="bogus")
    assert len(unknown) == 1


def main() -> None:
    test_historical_uncertain_projection_is_bounded()
    test_current_action_projection_is_bounded()
    test_existing_private_audits_remain_readable()
    test_projection_write_is_idempotent_and_mode_bounded()
    test_rows_show_terminal_insufficient_evidence_without_action()
    test_rows_ignore_retired_database_audits_with_reused_ids()
    test_retention_removes_raw_calls_but_keeps_web_projection()
    test_jev_shadow_rows_and_summary_are_bounded()
    test_jev_shadow_details_filters_and_metadata()
    print("llm decision web checks passed")


if __name__ == "__main__":
    main()
