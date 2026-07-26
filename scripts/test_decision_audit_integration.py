#!/usr/bin/env python3
"""Regression checks for DecisionResult audit writeback into legacy stores."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from decision_engine import (
    attach_decision_result_to_article_review,
    attach_decision_result_to_event_analysis,
    attach_decision_result_to_official_review,
)
from market_item import DecisionResult
from market_review_store import save_article_review, save_official_review


def fixed_decision(rule_id: str) -> DecisionResult:
    return DecisionResult(
        action="push",
        importance="high",
        reason="大模型程度决策命中。",
        rule_hits=[{"rule_id": rule_id}],
    )


def test_article_review_save_adds_decision_audit_without_flipping_push_flag() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        conn = sqlite3.connect(db_path)
        item = {
            "id": "goldman-ai-theme",
            "title": "高盛发布《投资策略：做多中国 AI 价值链》",
            "summary": (
                "高盛认为中国 AI 公司市值与市场空间严重错配，资金正从韩国 AI 交易出现结构性资本轮动，"
                "建议做多中国 AI 价值链，覆盖算力、半导体和数据中心电力。"
            ),
            "published_at": "2026-07-09T03:57:30+00:00",
        }
        review = {
            "importance": "low",
            "push_now": False,
            "affected_targets": [],
            "reason": "旧链路暂不推送。",
            "daily_summary": item["title"],
            "confidence": "低",
            "raw": {},
        }
        try:
            review = attach_decision_result_to_article_review(
                fixed_decision("test_article_rule"), review
            )
            save_article_review(conn, "cls_telegraph_api", item, review)
            row = conn.execute(
                "SELECT push_now, gate_json FROM article_reviews WHERE source = ? AND item_id = ?",
                ("cls_telegraph_api", "goldman-ai-theme"),
            ).fetchone()
            review_with_existing_audit = json.loads(row[1])
            review_with_existing_audit["push_now"] = False
            review_with_existing_audit["raw"]["decision_final_fields"]["push_now"] = True
            save_article_review(conn, "cls_telegraph_api", item, review_with_existing_audit)
            refreshed_row = conn.execute(
                "SELECT push_now, gate_json FROM article_reviews WHERE source = ? AND item_id = ?",
                ("cls_telegraph_api", "goldman-ai-theme"),
            ).fetchone()
        finally:
            conn.close()
    assert row[0] == 0
    gate = json.loads(row[1])
    raw = gate["raw"]
    assert raw["decision_passthrough"] is True
    assert raw["decision_result"]["action"] == "push"
    assert raw["decision_result"]["rule_hits"][0]["rule_id"] == "test_article_rule"
    assert raw["decision_final_fields"]["push_now"] is False
    refreshed = json.loads(refreshed_row[1])
    assert refreshed["raw"]["decision_final_fields"]["push_now"] is False


def test_official_review_save_adds_decision_audit_to_analysis_json() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        conn = sqlite3.connect(db_path)
        item = {
            "id": "nvidia-rubin",
            "title": "NVIDIA announces Rubin rack-scale AI platform with liquid cooling",
            "summary": "NVIDIA details GPU, rack-scale systems, and liquid cooling for AI factories.",
            "published_at": "2026-07-11T00:00:00+00:00",
        }
        review = {
            "importance": "low",
            "should_push_now": False,
            "reason": "旧链路暂不推送。",
            "daily_summary": item["title"],
            "analysis": {"core_content": item["summary"]},
        }
        try:
            review = attach_decision_result_to_official_review(
                fixed_decision("test_official_rule"), review
            )
            save_official_review(conn, "nvidia_blog", item, review)
            row = conn.execute(
                "SELECT should_push_now, analysis_json FROM official_news_reviews WHERE source = ? AND item_id = ?",
                ("nvidia_blog", "nvidia-rubin"),
            ).fetchone()
        finally:
            conn.close()
    assert row[0] == 0
    analysis = json.loads(row[1])
    assert analysis["_decision_passthrough"] is True
    assert analysis["_decision_result"]["action"] == "push"
    assert analysis["_decision_result"]["rule_hits"][0]["rule_id"] == "test_official_rule"
    assert analysis["_decision_final_fields"]["should_push_now"] is False


def test_event_analysis_accepts_only_an_explicit_decision() -> None:
    updated = attach_decision_result_to_event_analysis(
        fixed_decision("test_event_rule"),
        {"importance": "medium", "push_decision": {"should_push": False}},
    )
    assert updated["_decision_passthrough"] is True
    assert updated["_decision_result"]["action"] == "push"
    assert updated["_decision_result"]["rule_hits"][0]["rule_id"] == "test_event_rule"
    assert updated["_decision_final_fields"]["should_push"] is True


def main() -> int:
    test_article_review_save_adds_decision_audit_without_flipping_push_flag()
    test_official_review_save_adds_decision_audit_to_analysis_json()
    test_event_analysis_accepts_only_an_explicit_decision()
    print("decision audit integration checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
