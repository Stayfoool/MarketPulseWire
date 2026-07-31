#!/usr/bin/env python3
"""CI-safe checks for unified market item, review, and delivery storage."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from market_db import init_db
from market_canonical_reader import (
    canonical_digest_rows,
    canonical_market_rows,
    canonical_feedback_snapshot,
)
from market_item import (
    AdmissionEvidence,
    AdmissionResult,
    DecisionResult,
    InterpretationResult,
    MarketFlowResult,
    NormalizedMarketItem,
)
from market_store import (
    complete_market_review,
    fail_market_review,
    market_review_snapshot,
    record_delivery,
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
        evidence=(
            AdmissionEvidence(
                "semiconductor_ai",
                "term",
                "HBM",
                matched_term_ids=("hbm",),
            ),
        )
        if admitted
        else (),
        config_version="test-v1",
    )


def flow(item_value: NormalizedMarketItem, action: str = "push") -> MarketFlowResult:
    return MarketFlowResult(
        item=item_value,
        decision=DecisionResult(action=action, reason="扩产"),
        interpretation=InterpretationResult(core_content="HBM扩产"),
    )


def test_excluded_has_no_decision() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        init_db(path).close()
        market_item_id, review_id = record_production_admission(
            item(), admission("excluded"), db_path=path
        )
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                "SELECT admission_status,decision_action,review_status "
                "FROM market_reviews WHERE id=?",
                (review_id,),
            ).fetchone()
            stored = conn.execute(
                "SELECT full_text,processing_status FROM market_items WHERE id=?",
                (market_item_id,),
            ).fetchone()
        assert row == ("excluded", None, "excluded")
        assert stored == ("公司确认新增HBM产线并扩大产能。", "not_applicable")


def test_fresh_schema_omits_retired_result_fields_and_statuses() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        init_db(path).close()
        with sqlite3.connect(path) as conn:
            item_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(market_items)")}
            review_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(market_reviews)")}
            schema_sql = "\n".join(
                str(row[0] or "")
                for row in conn.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")
            )
        assert {"legacy_store_kind", "legacy_store_id"}.isdisjoint(item_columns)
        assert {"importance", "legacy_payload_json", "legacy_store_kind", "legacy_store_id"}.isdisjoint(
            review_columns
        )
        assert "legacy_unclassified" not in schema_sql


def test_result_and_delivery_use_only_unified_storage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        init_db(path).close()
        normalized = item("source-b")
        market_item_id, review_id = record_production_admission(
            normalized, admission("admitted"), db_path=path
        )
        result = flow(normalized)
        complete_market_review(
            review_id,
            result,
            db_path=path,
        )
        delivery_id = record_delivery(
            market_item_id,
            review_id,
            status="sent",
            decision_action="push",
            db_path=path,
        )

        snapshot = market_review_snapshot(review_id, db_path=path)
        assert snapshot is not None
        assert snapshot["review_status"] == "succeeded"
        assert snapshot["payload"]["decision_result"]["action"] == "push"
        with sqlite3.connect(path) as conn:
            review = conn.execute(
                "SELECT admission_status,decision_action,review_status "
                "FROM market_reviews WHERE id=?",
                (review_id,),
            ).fetchone()
            alias_table = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='market_item_aliases'"
            ).fetchone()[0]
            delivery = conn.execute(
                "SELECT id,market_item_id,market_review_id,status,decision_action "
                "FROM deliveries WHERE id=?",
                (delivery_id,),
            ).fetchone()

        assert review == ("admitted", "push", "succeeded")
        assert "importance" not in snapshot["payload"]["decision_result"]
        assert alias_table == 0
        assert delivery == (delivery_id, market_item_id, review_id, "sent", "push")


def test_admission_reuses_current_unified_result() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        init_db(path).close()
        normalized = item("source-c")
        market_item_id, review_id = record_production_admission(
            normalized, admission("admitted"), db_path=path
        )
        complete_market_review(review_id, flow(normalized, "archive"), db_path=path)
        repeated = record_production_admission(normalized, admission("admitted"), db_path=path)
        assert repeated == (market_item_id, review_id)


def test_force_new_result_replaces_current_version() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        init_db(path).close()
        normalized = item("source-d")
        market_item_id, first_review_id = record_production_admission(
            normalized, admission("admitted"), db_path=path
        )
        complete_market_review(first_review_id, flow(normalized, "archive"), db_path=path)
        repeated_item_id, second_review_id = record_production_admission(
            normalized,
            admission("admitted"),
            db_path=path,
            force_new=True,
        )
        complete_market_review(second_review_id, flow(normalized, "daily"), db_path=path)

        assert repeated_item_id == market_item_id
        assert second_review_id != first_review_id
        with sqlite3.connect(path) as conn:
            rows = conn.execute(
                "SELECT id,is_current,review_status,decision_action,decision_json,"
                "interpretation_json "
                "FROM market_reviews ORDER BY id"
            ).fetchall()
        assert rows[0][0:4] == (first_review_id, 0, "succeeded", "archive")
        assert json.loads(rows[0][4])["action"] == "archive"
        assert json.loads(rows[0][5])["core_content"] == "HBM扩产"
        assert rows[1][0:4] == (second_review_id, 1, "succeeded", "daily")


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
                "SELECT id,is_current,review_status,admission_reason "
                "FROM market_reviews ORDER BY id"
            ).fetchall()
        assert rows == [
            (review_id, 0, "failed_retryable", "semiconductor_ai_match"),
            (repeated_review_id, 1, "admitted_pending", "holding_match"),
        ]


def test_all_source_metadata_uses_the_same_views() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        init_db(path).close()
        items = {
            "research-source": item("research-source"),
            "company-source": item("company-source"),
            "flash-source": item("flash-source"),
        }
        items["company-source"].content_type = "company_update"
        items["flash-source"].content_type = "flash_news"
        identities = {
            kind: record_production_admission(value, admission("admitted"), db_path=path)
            for kind, value in items.items()
        }
        for kind, normalized in items.items():
            market_item_id, review_id = identities[kind]
            action = "push" if kind == "flash-source" else "daily"
            complete_market_review(
                review_id,
                flow(normalized, action),
                db_path=path,
            )
            record_delivery(
                market_item_id,
                review_id,
                status="sent" if kind == "flash-source" else "skipped",
                decision_action=action,
                db_path=path,
            )

        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            digest = canonical_digest_rows(
                conn,
                start_utc="2000-01-01T00:00:00+00:00",
                end_utc="2100-01-01T00:00:00+00:00",
            )
            rows = canonical_market_rows(
                conn,
                start_utc="2000-01-01T00:00:00+00:00",
                end_utc="2100-01-01T00:00:00+00:00",
                time_basis="seen",
                include_baseline=False,
            )
            feedback = canonical_feedback_snapshot(conn, identities["flash-source"][0])
            legacy_tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }

        assert {row["source"] for row in digest} == {"research-source", "company-source"}
        assert {row["source"] for row in rows} == set(items)
        assert feedback is not None and feedback["decision"]["action"] == "push"
        assert not legacy_tables.intersection(
            {"article_reviews", "official_news_reviews", "events", "event_analyses"}
        )


def main() -> int:
    test_excluded_has_no_decision()
    test_fresh_schema_omits_retired_result_fields_and_statuses()
    test_result_and_delivery_use_only_unified_storage()
    test_admission_reuses_current_unified_result()
    test_force_new_result_replaces_current_version()
    test_retry_with_changed_admission_creates_a_new_current_result()
    test_all_source_metadata_uses_the_same_views()
    print("market store checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
