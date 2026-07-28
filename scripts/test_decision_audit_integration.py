#!/usr/bin/env python3
"""Regression checks for DecisionResult audit attachment before unified storage."""

from __future__ import annotations

from decision_engine import (
    attach_decision_result_to_article_review,
    attach_decision_result_to_event_analysis,
    attach_decision_result_to_official_review,
    ensure_article_decision_audit,
    ensure_official_decision_audit,
)
from market_item import DecisionResult


def fixed_decision(rule_id: str) -> DecisionResult:
    return DecisionResult(
        action="push",
        importance="high",
        reason="大模型程度决策命中。",
        rule_hits=[{"rule_id": rule_id}],
    )


def test_article_review_audit_does_not_flip_compatibility_push_flag() -> None:
    item = {"id": "goldman-ai-theme", "title": "高盛发布投资策略"}
    review = attach_decision_result_to_article_review(
        fixed_decision("test_article_rule"),
        {"importance": "low", "push_now": False, "raw": {}},
    )
    refreshed = ensure_article_decision_audit("cls_telegraph_api", item, review)
    raw = refreshed["raw"]
    assert raw["decision_passthrough"] is True
    assert raw["decision_result"]["action"] == "push"
    assert raw["decision_result"]["rule_hits"][0]["rule_id"] == "test_article_rule"
    assert raw["decision_final_fields"]["push_now"] is False


def test_official_review_audit_does_not_flip_compatibility_push_flag() -> None:
    item = {"id": "nvidia-rubin", "title": "NVIDIA announces Rubin"}
    review = attach_decision_result_to_official_review(
        fixed_decision("test_official_rule"),
        {"importance": "low", "should_push_now": False, "analysis": {}},
    )
    refreshed = ensure_official_decision_audit("nvidia_blog", item, review)
    analysis = refreshed["analysis"]
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
    test_article_review_audit_does_not_flip_compatibility_push_flag()
    test_official_review_audit_does_not_flip_compatibility_push_flag()
    test_event_analysis_accepts_only_an_explicit_decision()
    print("decision audit integration checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
