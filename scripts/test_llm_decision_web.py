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


def audit_payload(status: str = "uncertain") -> dict:
    user_payload = {
        "article_segments": [
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
        "generated_at": "2026-07-26T01:02:03+00:00",
        "market_review_id": 12,
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


def walk_values(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_values(child)


def test_uncertain_projection_is_bounded() -> None:
    projection = build_web_projection(audit_payload())
    assert projection["version"] == WEB_PROJECTION_VERSION
    assert projection["evaluation_status"] == "uncertain"
    assessment = projection["calls"][0]["rule_assessments"][0]
    assert assessment["judgement"] == "uncertain"
    assert assessment["reason"] == "缺少决定性事实"
    assert assessment["counterevidence"][0]["quote"] == "正文反证"
    forbidden = {"request", "response", "content", "article_segments", "system_prompt", "user_payload"}
    assert not any(key in forbidden for key, _ in walk_values(projection))


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
        assert 12 in loaded
        assert loaded[12][0]["evaluation_status"] == "uncertain"


def test_rows_preserve_authoritative_action_and_uncertain_status() -> None:
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
                importance TEXT, decision_json TEXT, created_at TEXT, completed_at TEXT
            );
            CREATE TABLE market_item_aliases (
                market_item_id INTEGER, source TEXT, legacy_item_id TEXT, created_at TEXT
            );
            INSERT INTO market_items VALUES (1, 'source-a', 'item-a', '测试标题', 'https://example.com/a', '', '2026-07-26T01:00:00+00:00', 'article');
            INSERT INTO market_reviews VALUES (12, 1, 1, 'admitted', 'failed_retryable', NULL, NULL, NULL, '2026-07-26T01:01:00+00:00', NULL);
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
        assert rows[0]["model_status"] == "uncertain"
        assert rows[0]["attempts"][0]["calls"][0]["rule_assessments"]
        summary = llm_decision_summary(rows)
        assert summary["uncertain_attempts"] == 1
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


def main() -> None:
    test_uncertain_projection_is_bounded()
    test_projection_write_is_idempotent_and_mode_bounded()
    test_rows_preserve_authoritative_action_and_uncertain_status()
    test_retention_removes_raw_calls_but_keeps_web_projection()
    print("llm decision web checks passed")


if __name__ == "__main__":
    main()
