#!/usr/bin/env python3
"""CI-safe checks for explicit retirement of the four old result tables."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import market_db
from market_db import init_db, preview_legacy_results_retirement, retire_legacy_results
from market_item import (
    AdmissionEvidence,
    AdmissionResult,
    DecisionResult,
    InterpretationResult,
    MarketFlowResult,
    NormalizedMarketItem,
)
from market_store import complete_market_review, record_production_admission


def admission() -> AdmissionResult:
    return AdmissionResult(
        status="admitted",
        reason_code="semiconductor_ai_match",
        matched_families=("semiconductor_ai",),
        evidence=(AdmissionEvidence("semiconductor_ai", "term", "HBM"),),
        config_version="test-v1",
    )


def seed_unified_result(
    path: Path,
    *,
    source: str,
    source_item_id: str,
    item_kind: str,
    legacy_item_id: str,
    legacy_store_kind: str,
    legacy_store_id: str,
) -> tuple[int, int]:
    content_type = {"article": "article", "official": "official_news", "event": "flash_news"}[item_kind]
    item = NormalizedMarketItem(
        source=source,
        source_category="news_media",
        content_type=content_type,
        title=f"{item_kind} title",
        full_text=f"{item_kind} body",
        published_at="2026-07-28T00:00:00+00:00",
        raw={"source_event_id": source_item_id} if item_kind == "event" else {"id": source_item_id},
    )
    task = "event-task" if item_kind == "event" else "production"
    market_item_id, review_id = record_production_admission(item, admission(), db_path=path, task=task)
    complete_market_review(
        review_id,
        MarketFlowResult(
            item=item,
            decision=DecisionResult(action="push", importance="high", reason="test"),
            interpretation=InterpretationResult(core_content="test"),
        ),
        db_path=path,
        alias=(item_kind, source, legacy_item_id, legacy_store_kind),
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE market_reviews SET legacy_store_kind=?,legacy_store_id=? WHERE id=?",
            (legacy_store_kind, legacy_store_id, review_id),
        )
        conn.commit()
    return market_item_id, review_id


def seed(path: Path) -> dict[str, int]:
    init_db(path).close()
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE article_reviews (
                source TEXT NOT NULL,item_id TEXT NOT NULL,title TEXT NOT NULL,
                created_at TEXT NOT NULL,PRIMARY KEY(source,item_id)
            );
            CREATE TABLE official_news_reviews (
                source TEXT NOT NULL,item_id TEXT NOT NULL,title TEXT NOT NULL,
                created_at TEXT NOT NULL,PRIMARY KEY(source,item_id)
            );
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,source TEXT NOT NULL,
                source_event_id TEXT NOT NULL,event_type TEXT NOT NULL,title TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,content_hash TEXT NOT NULL,
                UNIQUE(source,source_event_id)
            );
            CREATE TABLE event_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,event_id INTEGER NOT NULL,task TEXT NOT NULL,
                analysis_json TEXT NOT NULL,created_at TEXT NOT NULL,
                FOREIGN KEY(event_id) REFERENCES events(id)
            );
            ALTER TABLE deliveries ADD COLUMN event_id INTEGER REFERENCES events(id);
            INSERT INTO article_reviews VALUES ('article-source','article-1','article','2026-07-28T00:00:00Z');
            INSERT INTO official_news_reviews VALUES ('official-source','official-1','official','2026-07-28T00:00:00Z');
            INSERT INTO events(source,source_event_id,event_type,title,first_seen_at,content_hash)
            VALUES ('event-source','event-1','flash','event','2026-07-28T00:00:00Z','hash');
            INSERT INTO event_analyses(event_id,task,analysis_json,created_at)
            VALUES (1,'event-task','{}','2026-07-28T00:00:00Z');
            """
        )
        conn.commit()
    article_item, article_review = seed_unified_result(
        path,
        source="article-source",
        source_item_id="article-1",
        item_kind="article",
        legacy_item_id="article-1",
        legacy_store_kind="article_reviews",
        legacy_store_id="article-source:article-1",
    )
    official_item, official_review = seed_unified_result(
        path,
        source="official-source",
        source_item_id="official-1",
        item_kind="official",
        legacy_item_id="official-1",
        legacy_store_kind="official_news_reviews",
        legacy_store_id="official-source:official-1",
    )
    event_item, event_review = seed_unified_result(
        path,
        source="event-source",
        source_item_id="event-1",
        item_kind="event",
        legacy_item_id="1",
        legacy_store_kind="event_analyses",
        legacy_store_id="1",
    )
    with sqlite3.connect(path) as conn:
        first_delivery = int(
            conn.execute(
                """
                INSERT INTO deliveries(
                    market_item_id,market_review_id,channel,status,decision_action,
                    attempted_at,sent_at,error,payload_json,event_id
                ) VALUES (?,?,'feishu','sent','push','2026-07-28T00:01:00Z',
                          '2026-07-28T00:01:01Z','','{\"key\":\"value\"}',1)
                """,
                (event_item, event_review),
            ).lastrowid
        )
        second_delivery = int(
            conn.execute(
                """
                INSERT INTO deliveries(
                    market_item_id,market_review_id,channel,status,decision_action,
                    attempted_at,sent_at,error,payload_json,event_id
                ) VALUES (?,NULL,'feishu','skipped','archive','2026-07-28T00:02:00Z',
                          '','','{}',1)
                """,
                (event_item,),
            ).lastrowid
        )
        conn.execute(
            """
            INSERT INTO market_feedback(
                feedback_event_id,item_kind,source,item_id,delivery_id,label,
                reason_tags_json,operator_id,rule_ids_json,clicked_at_us,received_at,raw_json
            ) VALUES ('feedback','event','event-source','1',?,'high_value','[]','operator',
                      '[]',1,'2026-07-28T00:03:00Z','{}')
            """,
            (first_delivery,),
        )
        conn.commit()
    return {
        "article_item": article_item,
        "article_review": article_review,
        "official_item": official_item,
        "official_review": official_review,
        "event_item": event_item,
        "event_review": event_review,
        "first_delivery": first_delivery,
        "second_delivery": second_delivery,
    }


def test_preview_apply_preserves_deliveries_feedback_and_provenance() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        ids = seed(path)
        with sqlite3.connect(path, isolation_level=None) as conn:
            preview = preview_legacy_results_retirement(conn)
            assert preview["status"] == "ready"
            assert preview["table_counts"] == {
                "article_reviews": 1,
                "official_news_reviews": 1,
                "event_analyses": 1,
                "events": 1,
            }
            assert preview["delivery_event_links"] == 2
            result = retire_legacy_results(conn)
            assert result["status"] == "completed"
            assert result["marker_present"] is True
            assert retire_legacy_results(conn)["status"] == "already_retired"

            tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert not tables.intersection(market_db.LEGACY_RESULT_TABLES)
            assert "event_id" not in market_db.table_columns(conn, "deliveries")
            deliveries = conn.execute(
                "SELECT id,market_item_id,market_review_id,status,decision_action,payload_json "
                "FROM deliveries ORDER BY id"
            ).fetchall()
            assert deliveries == [
                (ids["first_delivery"], ids["event_item"], ids["event_review"], "sent", "push", '{"key":"value"}'),
                (ids["second_delivery"], ids["event_item"], None, "skipped", "archive", "{}"),
            ]
            assert conn.execute("SELECT delivery_id FROM market_feedback").fetchone()[0] == ids["first_delivery"]
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert conn.execute(
                "SELECT COUNT(*) FROM market_reviews WHERE legacy_store_id IS NOT NULL"
            ).fetchone()[0] == 3
            marker = json.loads(
                conn.execute(
                    "SELECT state_json FROM source_state WHERE source=?",
                    (market_db.LEGACY_RESULTS_RETIREMENT_MARKER,),
                ).fetchone()[0]
            )
            assert marker["delivery_missing_review_links"] == 1


def test_preview_fails_for_each_missing_mapping_and_delivery_item() -> None:
    mutations = (
        "DELETE FROM market_item_aliases WHERE item_kind='article'",
        "DELETE FROM market_reviews WHERE legacy_store_kind='article_reviews'",
        "DELETE FROM market_item_aliases WHERE item_kind='official'",
        "DELETE FROM market_reviews WHERE legacy_store_kind='official_news_reviews'",
        "DELETE FROM market_item_aliases WHERE item_kind='event'",
        "DELETE FROM market_reviews WHERE legacy_store_kind='event_analyses'",
        "UPDATE deliveries SET market_item_id=NULL WHERE event_id IS NOT NULL",
    )
    for mutation in mutations:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "db.sqlite3"
            seed(path)
            with sqlite3.connect(path) as conn:
                conn.execute(mutation)
                conn.commit()
                try:
                    preview_legacy_results_retirement(conn)
                except RuntimeError as exc:
                    assert "preview failed" in str(exc)
                else:
                    raise AssertionError(f"preview must fail after: {mutation}")


def test_apply_failure_rolls_back_all_schema_changes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        seed(path)
        original = market_db._rebuild_deliveries_without_event_id

        def fail_after_rebuild(conn: sqlite3.Connection) -> None:
            original(conn)
            raise RuntimeError("forced failure")

        market_db._rebuild_deliveries_without_event_id = fail_after_rebuild
        try:
            with sqlite3.connect(path, isolation_level=None) as conn:
                try:
                    retire_legacy_results(conn)
                except RuntimeError as exc:
                    assert str(exc) == "forced failure"
                else:
                    raise AssertionError("forced retirement failure must propagate")
                assert set(market_db.LEGACY_RESULT_TABLES).issubset(
                    {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                )
                assert "event_id" in market_db.table_columns(conn, "deliveries")
                assert conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 2
        finally:
            market_db._rebuild_deliveries_without_event_id = original


def test_fresh_schema_never_creates_old_result_tables() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fresh.sqlite3"
        with init_db(path) as conn:
            tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert not tables.intersection(market_db.LEGACY_RESULT_TABLES)
            assert "event_id" not in market_db.table_columns(conn, "deliveries")


def main() -> int:
    test_preview_apply_preserves_deliveries_feedback_and_provenance()
    test_preview_fails_for_each_missing_mapping_and_delivery_item()
    test_apply_failure_rolls_back_all_schema_changes()
    test_fresh_schema_never_creates_old_result_tables()
    print("market database retirement checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
