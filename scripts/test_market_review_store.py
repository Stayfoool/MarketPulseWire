#!/usr/bin/env python3
"""Regression checks for compatibility review-store adapters."""

from __future__ import annotations

import sqlite3

import article_gate
import market_review_store
import official_news_gate


def test_gate_modules_reexport_store_functions() -> None:
    assert article_gate.article_item_id is market_review_store.article_item_id
    assert article_gate.ensure_article_reviews_table is market_review_store.ensure_article_reviews_table
    assert article_gate.save_review is market_review_store.save_article_review
    assert article_gate.review_exists is market_review_store.article_review_exists
    assert article_gate.mark_pushed is market_review_store.mark_article_pushed

    assert official_news_gate.ensure_official_news_table is market_review_store.ensure_official_news_table
    assert official_news_gate.save_review is market_review_store.save_official_review
    assert official_news_gate.review_exists is market_review_store.official_review_exists
    assert official_news_gate.mark_pushed is market_review_store.mark_official_pushed


def test_article_review_store_round_trip_and_mark_pushed() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        item = {
            "id": "article-1",
            "url": "https://example.com/article-1",
            "title": "SemiAnalysis says AI rack power demand is rising",
            "source_module": "SemiAnalysis",
            "published_at": "2026-07-11T00:00:00+00:00",
            "summary": "AI rack power demand is rising.",
        }
        review = {
            "importance": "medium",
            "push_now": False,
            "market_impact": "关注 AI 电力链。",
            "incremental_classification": "产业趋势",
            "affected_targets": ["AI 电力"],
            "reason": "旧链路暂不推送。",
            "daily_summary": "AI 机架功耗上行。",
            "confidence": "中",
            "raw": {"core_content": "AI 机架功耗上行。"},
        }
        market_review_store.save_article_review(conn, "semianalysis", item, review)
        loaded = market_review_store.article_review_exists(conn, "semianalysis", "article-1")
        assert loaded is not None
        assert loaded["importance"] == "medium"
        assert loaded["push_now"] is False
        assert loaded["affected_targets"] == ["AI 电力"]
        assert loaded["raw"]["raw"]["decision_passthrough"] is True
        assert loaded["pushed_at"] == ""

        market_review_store.mark_article_pushed(conn, "semianalysis", "article-1")
        pushed = market_review_store.article_review_exists(conn, "semianalysis", "article-1")
        assert pushed is not None
        assert pushed["pushed_at"]
    finally:
        conn.close()


def test_official_review_store_round_trip_and_mark_pushed() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        item = {
            "id": "official-1",
            "url": "https://example.com/official-1",
            "title": "NVIDIA announces rack-scale AI platform",
            "published_at": "2026-07-11T00:00:00+00:00",
            "summary": "NVIDIA details liquid cooling and rack-scale systems.",
        }
        review = {
            "importance": "high",
            "should_push_now": True,
            "reason": "官网硬变量。",
            "daily_summary": "NVIDIA 发布 AI 平台。",
            "analysis": {"core_content": "NVIDIA 发布 AI 平台。"},
            "skeptic": {"skeptic_verdict": "pass"},
            "pre_skeptic_importance": "high",
        }
        market_review_store.save_official_review(conn, "nvidia_blog", item, review)
        loaded = market_review_store.official_review_exists(conn, "nvidia_blog", "official-1")
        assert loaded is not None
        assert loaded["importance"] == "high"
        assert loaded["should_push_now"] is True
        assert loaded["analysis"]["core_content"] == "NVIDIA 发布 AI 平台。"
        assert loaded["analysis"]["_decision_passthrough"] is True
        assert loaded["skeptic"]["skeptic_verdict"] == "pass"
        assert loaded["pushed_at"] == ""

        market_review_store.mark_official_pushed(conn, "nvidia_blog", "official-1")
        pushed = market_review_store.official_review_exists(conn, "nvidia_blog", "official-1")
        assert pushed is not None
        assert pushed["pushed_at"]
    finally:
        conn.close()


def main() -> int:
    test_gate_modules_reexport_store_functions()
    test_article_review_store_round_trip_and_mark_pushed()
    test_official_review_store_round_trip_and_mark_pushed()
    print("market review store tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
