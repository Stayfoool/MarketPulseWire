#!/usr/bin/env python3
"""CI-safe checks for unified market item, review, alias, and delivery storage."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from market_db import init_db
from market_canonical_reader import (
    canonical_digest_rows,
    canonical_event_rows,
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
from market_review_store import ensure_article_reviews_table, ensure_official_news_table
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
    importance = {"push": "high", "daily": "medium", "archive": "low"}[action]
    return MarketFlowResult(
        item=item_value,
        decision=DecisionResult(action=action, importance=importance, reason="扩产"),
        interpretation=InterpretationResult(core_content="HBM扩产"),
    )


def legacy_result_counts(conn: sqlite3.Connection) -> dict[str, int]:
    ensure_article_reviews_table(conn, commit=False)
    ensure_official_news_table(conn, commit=False)
    return {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in ("article_reviews", "official_news_reviews", "events", "event_analyses")
    }


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


def test_result_alias_and_delivery_use_only_unified_storage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        init_db(path).close()
        normalized = item("source-b")
        with sqlite3.connect(path) as conn:
            before = legacy_result_counts(conn)
            conn.commit()

        market_item_id, review_id = record_production_admission(
            normalized, admission("admitted"), db_path=path
        )
        result = flow(normalized)
        legacy_payload = result.decision.legacy_push_fields(push_key="push_now")
        complete_market_review(
            review_id,
            result,
            db_path=path,
            legacy_payload=legacy_payload,
            alias=("article", normalized.source, "a-1", "market_items"),
        )
        delivery_id = record_article_delivery(
            market_item_id,
            review_id,
            status="sent",
            decision_action="push",
            legacy_payload=legacy_payload,
            db_path=path,
        )

        snapshot = market_review_snapshot(review_id, db_path=path)
        assert snapshot is not None
        assert snapshot["review_status"] == "succeeded"
        assert snapshot["payload"]["decision_result"]["action"] == "push"
        with sqlite3.connect(path) as conn:
            review = conn.execute(
                "SELECT admission_status,decision_action,importance,review_status,"
                "legacy_store_kind,legacy_store_id FROM market_reviews WHERE id=?",
                (review_id,),
            ).fetchone()
            alias = conn.execute(
                "SELECT market_item_id,item_kind,source,legacy_item_id,legacy_store_kind "
                "FROM market_item_aliases"
            ).fetchone()
            delivery = conn.execute(
                "SELECT id,event_id,market_item_id,market_review_id,status,decision_action "
                "FROM deliveries WHERE id=?",
                (delivery_id,),
            ).fetchone()
            after = legacy_result_counts(conn)

        assert review == ("admitted", "push", "high", "succeeded", None, None)
        assert alias == (market_item_id, "article", normalized.source, "a-1", "market_items")
        assert delivery == (delivery_id, None, market_item_id, review_id, "sent", "push")
        assert after == before


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
                "interpretation_json,legacy_store_kind,legacy_store_id "
                "FROM market_reviews ORDER BY id"
            ).fetchall()
        assert rows[0][0:4] == (first_review_id, 0, "succeeded", "archive")
        assert json.loads(rows[0][4])["action"] == "archive"
        assert json.loads(rows[0][5])["core_content"] == "HBM扩产"
        assert rows[0][6:8] == (None, None)
        assert rows[1][0:4] == (second_review_id, 1, "succeeded", "daily")
        assert rows[1][6:8] == (None, None)


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


def test_unified_views_work_when_legacy_result_tables_are_absent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        init_db(path).close()
        items = {
            "article": item("article-source"),
            "official": item("official-source"),
            "event": item("event-source"),
        }
        items["official"].content_type = "official_news"
        items["event"].content_type = "flash_news"
        identities = {
            kind: record_production_admission(value, admission("admitted"), db_path=path)
            for kind, value in items.items()
        }
        with sqlite3.connect(path) as conn:
            ensure_article_reviews_table(conn, commit=False)
            ensure_official_news_table(conn, commit=False)
            conn.execute("DROP TABLE event_analyses")
            conn.execute("DROP TABLE events")
            conn.execute("DROP TABLE official_news_reviews")
            conn.execute("DROP TABLE article_reviews")
            conn.commit()

        for kind, normalized in items.items():
            market_item_id, review_id = identities[kind]
            action = "push" if kind == "event" else "daily"
            complete_market_review(
                review_id,
                flow(normalized, action),
                alias=(kind, normalized.source, "a-1", "market_items"),
                db_path=path,
            )
            record_article_delivery(
                market_item_id,
                review_id,
                status="sent" if kind == "event" else "skipped",
                decision_action=action,
                db_path=path,
            )

        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            articles = canonical_digest_rows(
                conn,
                item_kind="article",
                start_utc="2000-01-01T00:00:00+00:00",
                end_utc="2100-01-01T00:00:00+00:00",
            )
            officials = canonical_digest_rows(
                conn,
                item_kind="official",
                start_utc="2000-01-01T00:00:00+00:00",
                end_utc="2100-01-01T00:00:00+00:00",
            )
            events = canonical_event_rows(
                conn,
                start_utc="2000-01-01T00:00:00+00:00",
                end_utc="2100-01-01T00:00:00+00:00",
                time_basis="seen",
                include_baseline=False,
            )
            feedback = canonical_feedback_snapshot(conn, "event", "event-source", "a-1")
            legacy_tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }

        assert [row["source"] for row in articles] == ["article-source"]
        assert [row["source"] for row in officials] == ["official-source"]
        assert any(row["source"] == "event-source" for row in events)
        assert feedback is not None and feedback["decision"]["action"] == "push"
        assert not legacy_tables.intersection(
            {"article_reviews", "official_news_reviews", "events", "event_analyses"}
        )


def main() -> int:
    test_excluded_has_no_decision()
    test_result_alias_and_delivery_use_only_unified_storage()
    test_admission_reuses_current_unified_result()
    test_force_new_result_replaces_current_version()
    test_retry_with_changed_admission_creates_a_new_current_result()
    test_unified_views_work_when_legacy_result_tables_are_absent()
    print("market store checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
