#!/usr/bin/env python3
"""CI-safe checks for canonical market item/review/delivery storage."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from market_db import MARKET_RESULTS_MIGRATION_VERSION, init_db
from market_item import AdmissionEvidence, AdmissionResult, DecisionResult, InterpretationResult, MarketFlowResult, NormalizedMarketItem
from market_review_store import ensure_official_news_table, save_article_review
from market_storage_audit import audit_storage
from market_store import (
    complete_market_review,
    fail_market_review,
    market_review_snapshot,
    record_article_delivery,
    record_production_admission,
)


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


def test_unified_result_and_compatibility_copy_commit_together() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        init_db(path).close()
        normalized = item("source-c")
        market_item_id, review_id = record_production_admission(normalized, admission("admitted"), db_path=path)
        flow = MarketFlowResult(
            item=normalized,
            decision=DecisionResult(action="daily", importance="medium", reason="扩产跟踪"),
            interpretation=InterpretationResult(core_content="HBM扩产跟踪"),
        )
        legacy = flow.decision.legacy_push_fields(push_key="push_now")
        legacy["daily_summary"] = flow.interpretation.core_content

        def fail_after_unified(conn: sqlite3.Connection):
            row = conn.execute(
                "SELECT review_status,decision_action FROM market_reviews WHERE id=?", (review_id,)
            ).fetchone()
            assert row == ("succeeded", "daily")
            raise RuntimeError("compatibility write failed")

        try:
            complete_market_review(
                review_id,
                flow,
                db_path=path,
                legacy_payload=legacy,
                compatibility_writer=fail_after_unified,
            )
            raise AssertionError("forced compatibility failure must escape")
        except RuntimeError as exc:
            assert str(exc) == "compatibility write failed"
        with sqlite3.connect(path) as conn:
            rolled_back = conn.execute(
                "SELECT review_status,decision_action FROM market_reviews WHERE id=?", (review_id,)
            ).fetchone()
        assert rolled_back == ("admitted_pending", None)

        def write_compatibility(conn: sqlite3.Connection):
            save_article_review(
                conn,
                normalized.source,
                {"id": "a-1", "title": normalized.title, "url": normalized.url},
                legacy,
                decision_item=normalized,
                commit=False,
            )
            return "article_reviews", f"{normalized.source}:a-1"

        complete_market_review(
            review_id,
            flow,
            db_path=path,
            legacy_payload=legacy,
            compatibility_writer=write_compatibility,
            alias=("article", normalized.source, "a-1", "article_reviews"),
        )
        snapshot = market_review_snapshot(review_id, db_path=path)
        assert snapshot is not None
        assert snapshot["review_status"] == "succeeded"
        assert snapshot["payload"]["decision_result"]["action"] == "daily"
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM article_reviews").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM market_item_aliases").fetchone()[0] == 1


def test_admission_reuses_current_unified_result_and_audit_is_clean() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        init_db(path).close()
        normalized = item("source-d")
        market_item_id, review_id = record_production_admission(normalized, admission("admitted"), db_path=path)
        flow = MarketFlowResult(
            item=normalized,
            decision=DecisionResult(action="archive", importance="low", reason="普通跟踪"),
            interpretation=InterpretationResult(core_content="普通跟踪"),
        )
        legacy = flow.decision.legacy_push_fields(push_key="push_now")

        def write_compatibility(conn: sqlite3.Connection):
            save_article_review(
                conn,
                normalized.source,
                {"id": "a-1", "title": normalized.title, "url": normalized.url},
                legacy,
                decision_item=normalized,
                commit=False,
            )
            return "article_reviews", f"{normalized.source}:a-1"

        complete_market_review(
            review_id,
            flow,
            db_path=path,
            legacy_payload=legacy,
            compatibility_writer=write_compatibility,
            alias=("article", normalized.source, "a-1", "article_reviews"),
        )
        repeated_item_id, repeated_review_id = record_production_admission(
            normalized, admission("admitted"), db_path=path
        )
        assert (repeated_item_id, repeated_review_id) == (market_item_id, review_id)
        with sqlite3.connect(path) as conn:
            ensure_official_news_table(conn)
            conn.execute(
                "INSERT INTO source_state(source,state_json,updated_at) VALUES (?,?,?)",
                (MARKET_RESULTS_MIGRATION_VERSION, "{}", "2026-07-23T00:00:00+00:00"),
            )
            conn.commit()
            report = audit_storage(
                conn,
                since="2026-07-23T00:00:00+00:00",
                until="2026-07-24T00:00:00+00:00",
            )
        assert report["ok"] is True


def test_retry_with_changed_admission_creates_a_new_current_result() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        init_db(path).close()
        normalized = item("source-admission-change")
        market_item_id, review_id = record_production_admission(
            normalized, admission("admitted"), db_path=path
        )
        fail_market_review(review_id, RuntimeError("temporary failure"), db_path=path)
        changed_admission = AdmissionResult(
            status="admitted",
            reason_code="holding_match",
            matched_families=("holding",),
            evidence=(AdmissionEvidence("holding", "entity", "测试公司"),),
            config_version="test-v2",
        )
        repeated_item_id, repeated_review_id = record_production_admission(
            normalized, changed_admission, db_path=path
        )
        assert repeated_item_id == market_item_id
        assert repeated_review_id != review_id
        with sqlite3.connect(path) as conn:
            rows = conn.execute(
                "SELECT id,is_current,review_status,admission_reason FROM market_reviews ORDER BY id"
            ).fetchall()
        assert rows == [
            (review_id, 0, "failed_retryable", "semiconductor_ai_match"),
            (repeated_review_id, 1, "admitted_pending", "holding_match"),
        ]


def test_delivery_remains_authoritative_when_compatibility_update_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        init_db(path).close()
        normalized = item("source-e")
        market_item_id, review_id = record_production_admission(normalized, admission("admitted"), db_path=path)
        flow = MarketFlowResult(
            item=normalized,
            decision=DecisionResult(action="push", importance="high", reason="扩产"),
            interpretation=InterpretationResult(core_content="扩产"),
        )
        complete_market_review(review_id, flow, db_path=path)

        def fail_compatibility(_conn: sqlite3.Connection):
            raise RuntimeError("legacy delivery update failed")

        delivery_id = record_article_delivery(
            market_item_id,
            review_id,
            status="sent",
            decision_action="push",
            compatibility_kind="article",
            compatibility_source=normalized.source,
            compatibility_item_id="a-1",
            compatibility_writer=fail_compatibility,
            db_path=path,
        )
        with sqlite3.connect(path) as conn:
            row = conn.execute("SELECT status,decision_action,error FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
        assert row[0:2] == ("sent", "push")
        assert str(row[2]).startswith("compatibility projection failed: RuntimeError")


def main() -> int:
    test_excluded_has_no_decision()
    test_admitted_result_and_delivery_share_identity()
    test_unified_result_and_compatibility_copy_commit_together()
    test_admission_reuses_current_unified_result_and_audit_is_clean()
    test_retry_with_changed_admission_creates_a_new_current_result()
    test_delivery_remains_authoritative_when_compatibility_update_fails()
    print("market store checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
