#!/usr/bin/env python3
"""CI-safe checks for canonical market item/review/delivery storage."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from market_db import init_db
from market_item import AdmissionEvidence, AdmissionResult, DecisionResult, InterpretationResult, MarketFlowResult, NormalizedMarketItem
from market_store import complete_market_review, record_article_delivery, record_production_admission


def item(source: str = "source-a") -> NormalizedMarketItem:
    return NormalizedMarketItem(
        source=source,
        source_category="news_media",
        content_type="article",
        title="HBM产能扩张",
        summary="新增产线",
        full_text="公司确认新增HBM产线并扩大产能。",
        url="https://example.com/a",
        published_at="2026-07-23T00:00:00+00:00",
        raw={"id": "a-1"},
    )


def admission(status: str) -> AdmissionResult:
    admitted = status == "admitted"
    return AdmissionResult(
        status=status,  # type: ignore[arg-type]
        reason_code="semiconductor_ai_match" if admitted else "out_of_scope",
        matched_families=("semiconductor_ai",) if admitted else (),
        evidence=(AdmissionEvidence("semiconductor_ai", "term", "HBM", matched_term_ids=("hbm",)),) if admitted else (),
        config_version="test-v1",
    )


def test_excluded_has_no_decision() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        init_db(path).close()
        market_item_id, review_id = record_production_admission(item(), admission("excluded"), db_path=path)
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                "SELECT admission_status, decision_action, review_status FROM market_reviews WHERE id = ?",
                (review_id,),
            ).fetchone()
            stored = conn.execute("SELECT full_text, processing_status FROM market_items WHERE id = ?", (market_item_id,)).fetchone()
        assert row == ("excluded", None, "excluded")
        assert stored == ("公司确认新增HBM产线并扩大产能。", "not_applicable")


def test_admitted_result_and_delivery_share_identity() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        init_db(path).close()
        normalized = item("source-b")
        market_item_id, review_id = record_production_admission(normalized, admission("admitted"), db_path=path)
        flow = MarketFlowResult(
            item=normalized,
            decision=DecisionResult(action="push", importance="high", reason="扩产"),
            interpretation=InterpretationResult(core_content="HBM扩产"),
        )
        complete_market_review(review_id, flow, db_path=path)
        record_article_delivery(
            market_item_id,
            review_id,
            status="sent",
            decision_action="push",
            db_path=path,
        )
        with sqlite3.connect(path) as conn:
            review = conn.execute(
                "SELECT admission_status, decision_action, importance, review_status FROM market_reviews WHERE id = ?",
                (review_id,),
            ).fetchone()
            delivery = conn.execute(
                "SELECT market_item_id, market_review_id, status, decision_action FROM deliveries"
            ).fetchone()
        assert review == ("admitted", "push", "high", "succeeded")
        assert delivery == (market_item_id, review_id, "sent", "push")


def main() -> int:
    test_excluded_has_no_decision()
    test_admitted_result_and_delivery_share_identity()
    print("market store checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
