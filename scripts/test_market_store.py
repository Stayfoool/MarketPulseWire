#!/usr/bin/env python3
"""CI-safe checks for canonical market item/review/delivery storage."""

from __future__ import annotations

import json
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


def test_new_current_result_takes_over_compatibility_reference() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        init_db(path).close()
        normalized = item("source-transfer")
        market_item_id, first_review_id = record_production_admission(
            normalized, admission("admitted"), db_path=path
        )
        first_flow = MarketFlowResult(
            item=normalized,
            decision=DecisionResult(action="archive", importance="low", reason="first result"),
            interpretation=InterpretationResult(core_content="first interpretation"),
        )
        first_payload = first_flow.decision.legacy_push_fields(push_key="push_now")
        first_payload["daily_summary"] = "first compatibility copy"

        def write_first_compatibility(conn: sqlite3.Connection):
            save_article_review(
                conn,
                normalized.source,
                {"id": "a-1", "title": normalized.title, "url": normalized.url},
                first_payload,
                decision_item=normalized,
                commit=False,
            )
            return "article_reviews", f"{normalized.source}:a-1"

        complete_market_review(
            first_review_id,
            first_flow,
            db_path=path,
            legacy_payload=first_payload,
            compatibility_writer=write_first_compatibility,
            alias=("article", normalized.source, "a-1", "article_reviews"),
        )
        repeated_item_id, second_review_id = record_production_admission(
            normalized,
            admission("admitted"),
            db_path=path,
            force_new=True,
        )
        assert repeated_item_id == market_item_id
        second_flow = MarketFlowResult(
            item=normalized,
            decision=DecisionResult(action="daily", importance="medium", reason="current result"),
            interpretation=InterpretationResult(core_content="current interpretation"),
        )
        second_payload = second_flow.decision.legacy_push_fields(push_key="push_now")
        second_payload["daily_summary"] = "current compatibility copy"

        def write_second_compatibility(conn: sqlite3.Connection):
            save_article_review(
                conn,
                normalized.source,
                {"id": "a-1", "title": normalized.title, "url": normalized.url},
                second_payload,
                decision_item=normalized,
                commit=False,
            )
            return "article_reviews", f"{normalized.source}:a-1"

        complete_market_review(
            second_review_id,
            second_flow,
            db_path=path,
            legacy_payload=second_payload,
            compatibility_writer=write_second_compatibility,
            alias=("article", normalized.source, "a-1", "article_reviews"),
        )
        with sqlite3.connect(path) as conn:
            rows = conn.execute(
                """
                SELECT id,is_current,review_status,decision_action,decision_json,
                       interpretation_json,legacy_payload_json,legacy_store_kind,legacy_store_id
                FROM market_reviews ORDER BY id
                """
            ).fetchall()
            compatibility_rows = conn.execute(
                "SELECT gate_json FROM article_reviews WHERE source=? AND item_id='a-1'",
                (normalized.source,),
            ).fetchall()
            ensure_official_news_table(conn)
            report = audit_storage(
                conn,
                since="2000-01-01T00:00:00+00:00",
                until="9999-12-31T23:59:59+00:00",
            )
        assert rows[0][0:4] == (first_review_id, 0, "succeeded", "archive")
        assert json.loads(rows[0][4])["action"] == "archive"
        assert json.loads(rows[0][5])["core_content"] == "first interpretation"
        assert json.loads(rows[0][6]) == first_payload
        assert rows[0][7:9] == (None, None)
        assert rows[1][0:4] == (second_review_id, 1, "succeeded", "daily")
        assert rows[1][7:9] == ("article_reviews", f"{normalized.source}:a-1")
        assert len(compatibility_rows) == 1
        assert json.loads(compatibility_rows[0][0])["raw"]["decision_result"]["action"] == "daily"
        assert report["ok"] is True


def test_cross_item_compatibility_reference_owner_rolls_back() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        init_db(path).close()
        first_item = item("owner-item")
        first_item_id, first_review_id = record_production_admission(
            first_item, admission("admitted"), db_path=path
        )
        first_flow = MarketFlowResult(
            item=first_item,
            decision=DecisionResult(action="archive", importance="low", reason="owner result"),
            interpretation=InterpretationResult(core_content="owner interpretation"),
        )
        first_payload = first_flow.decision.legacy_push_fields(push_key="push_now")

        def write_owner(conn: sqlite3.Connection):
            save_article_review(
                conn,
                "shared-source",
                {"id": "shared-id", "title": first_item.title, "url": first_item.url},
                first_payload,
                decision_item=first_item,
                commit=False,
            )
            return "article_reviews", "shared-source:shared-id"

        complete_market_review(
            first_review_id,
            first_flow,
            db_path=path,
            legacy_payload=first_payload,
            compatibility_writer=write_owner,
        )
        second_item = item("other-item")
        second_item.raw["id"] = "other-id"
        second_item.url = "https://example.com/other"
        second_item_id, second_review_id = record_production_admission(
            second_item, admission("admitted"), db_path=path
        )
        second_flow = MarketFlowResult(
            item=second_item,
            decision=DecisionResult(action="push", importance="high", reason="other result"),
            interpretation=InterpretationResult(core_content="other interpretation"),
        )
        second_payload = second_flow.decision.legacy_push_fields(push_key="push_now")

        def overwrite_owner(conn: sqlite3.Connection):
            save_article_review(
                conn,
                "shared-source",
                {"id": "shared-id", "title": second_item.title, "url": second_item.url},
                second_payload,
                decision_item=second_item,
                commit=False,
            )
            return "article_reviews", "shared-source:shared-id"

        try:
            complete_market_review(
                second_review_id,
                second_flow,
                db_path=path,
                legacy_payload=second_payload,
                compatibility_writer=overwrite_owner,
            )
            raise AssertionError("cross-item compatibility reference must fail closed")
        except RuntimeError as exc:
            assert str(exc) == "compatibility reference is owned by another current result, item, or task"
        with sqlite3.connect(path) as conn:
            first_row = conn.execute(
                "SELECT market_item_id,review_status,decision_action,legacy_store_id FROM market_reviews WHERE id=?",
                (first_review_id,),
            ).fetchone()
            second_row = conn.execute(
                "SELECT market_item_id,review_status,decision_action,legacy_store_id FROM market_reviews WHERE id=?",
                (second_review_id,),
            ).fetchone()
            compatibility_payload = conn.execute(
                "SELECT gate_json FROM article_reviews WHERE source='shared-source' AND item_id='shared-id'"
            ).fetchone()[0]
        assert first_row == (first_item_id, "succeeded", "archive", "shared-source:shared-id")
        assert second_row == (second_item_id, "admitted_pending", None, None)
        assert json.loads(compatibility_payload)["raw"]["decision_result"]["action"] == "archive"


def test_audit_fails_on_current_compatibility_reference_conflict() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        init_db(path).close()
        normalized = item("source-conflict-audit")
        _, first_review_id = record_production_admission(
            normalized, admission("admitted"), db_path=path
        )
        flow = MarketFlowResult(
            item=normalized,
            decision=DecisionResult(action="archive", importance="low", reason="existing result"),
            interpretation=InterpretationResult(core_content="existing interpretation"),
        )
        payload = flow.decision.legacy_push_fields(push_key="push_now")

        def write_compatibility(conn: sqlite3.Connection):
            save_article_review(
                conn,
                normalized.source,
                {"id": "a-1", "title": normalized.title, "url": normalized.url},
                payload,
                decision_item=normalized,
                commit=False,
            )
            return "article_reviews", f"{normalized.source}:a-1"

        complete_market_review(
            first_review_id,
            flow,
            db_path=path,
            legacy_payload=payload,
            compatibility_writer=write_compatibility,
            alias=("article", normalized.source, "a-1", "article_reviews"),
        )
        _, failed_review_id = record_production_admission(
            normalized, admission("admitted"), db_path=path, force_new=True
        )
        fail_market_review(
            failed_review_id,
            sqlite3.IntegrityError(
                "UNIQUE constraint failed: "
                "market_reviews.legacy_store_kind, market_reviews.legacy_store_id"
            ),
            db_path=path,
        )
        unrelated = item("source-unrelated-failure")
        unrelated.raw["id"] = "unrelated-id"
        _, unrelated_review_id = record_production_admission(
            unrelated, admission("admitted"), db_path=path
        )
        fail_market_review(unrelated_review_id, RuntimeError("temporary network failure"), db_path=path)
        with sqlite3.connect(path) as conn:
            ensure_official_news_table(conn)
            report = audit_storage(
                conn,
                since="2000-01-01T00:00:00+00:00",
                until="9999-12-31T23:59:59+00:00",
            )
        assert report["checks"]["current_compatibility_reference_conflict"] == 1
        assert report["counts"]["current_retryable_failures"] == 2
        assert report["counts"]["current_terminal_failures"] == 0
        assert report["ok"] is False


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
    test_new_current_result_takes_over_compatibility_reference()
    test_cross_item_compatibility_reference_owner_rolls_back()
    test_audit_fails_on_current_compatibility_reference_conflict()
    test_retry_with_changed_admission_creates_a_new_current_result()
    test_delivery_remains_authoritative_when_compatibility_update_fails()
    print("market store checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
