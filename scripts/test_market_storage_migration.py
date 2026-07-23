#!/usr/bin/env python3
"""CI-safe checks for the canonical historical-result migration."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import market_storage_migration
from market_db import init_db
from market_item import item_from_event_mapping
from market_review_store import ensure_article_reviews_table, insert_event_analysis, upsert_event_record
from market_storage_migration import migrate_legacy_results
from market_store import ensure_market_item_alias, upsert_market_item


def decision_payload(action: str) -> str:
    return json.dumps(
        {
            "decision_result": {
                "action": action,
                "importance": "high" if action == "push" else "low",
                "reason": "test",
            },
            "_interpretation_result": {"core_content": "test interpretation"},
        }
    )


def seed(path: Path) -> None:
    init_db(path).close()
    with sqlite3.connect(path) as conn:
        ensure_article_reviews_table(conn)
        common = (
            "rss", "item-1", "https://example.com/1", "valid decision", "rss", "2026-07-20T00:00:00+00:00",
            "high", 1, "", "", "[]", "reason", "summary", "high",
        )
        conn.execute(
            """
            INSERT INTO article_reviews (
                source,item_id,url,title,source_module,published_at,importance,push_now,
                market_impact,incremental_classification,affected_targets_json,reason,
                daily_summary,confidence,gate_json,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (*common, decision_payload("push"), "2026-07-20T00:01:00+00:00"),
        )
        invalid = list(common)
        invalid[1] = "item-2"
        invalid[3] = "legacy push only"
        conn.execute(
            """
            INSERT INTO article_reviews (
                source,item_id,url,title,source_module,published_at,importance,push_now,
                market_impact,incremental_classification,affected_targets_json,reason,
                daily_summary,confidence,gate_json,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (*invalid, "{}", "2026-07-20T00:02:00+00:00"),
        )
        conn.commit()
    event_id, _ = upsert_event_record(
        {
            "source": "sina_flash",
            "source_event_id": "event-1",
            "event_type": "flash",
            "title": "event",
            "summary": "summary",
            "full_text": "full body",
        },
        path,
    )
    insert_event_analysis(
        event_id, "task", "model", importance="low", classification="", direction="",
        impact_duration="", should_push=0, analysis=json.loads(decision_payload("archive")), db_path=path,
    )
    insert_event_analysis(
        event_id, "task", "model", importance="high", classification="", direction="",
        impact_duration="", should_push=1, analysis=json.loads(decision_payload("push")), db_path=path,
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO deliveries(event_id,channel,status,sent_at,payload_json) VALUES (?, 'feishu', 'sent', ?, '{}')",
            (event_id, "2026-07-20T00:03:00+00:00"),
        )
        conn.commit()


def test_preview_apply_and_repeat() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        seed(path)
        with sqlite3.connect(path) as conn:
            preview = migrate_legacy_results(conn, apply=False)
            assert preview.reviews == 4
            assert conn.execute("SELECT count(*) FROM market_reviews").fetchone()[0] == 0
            applied = migrate_legacy_results(conn, apply=True)
            assert applied.reviews == 4
            repeated = migrate_legacy_results(conn, apply=True)
            assert repeated.reviews == 0
            assert repeated.skipped_existing == 4
            rows = conn.execute(
                "SELECT legacy_store_kind,legacy_store_id,decision_action,review_status FROM market_reviews ORDER BY id"
            ).fetchall()
            assert rows[0][2:] == ("push", "succeeded")
            assert rows[1][2:] == (None, "legacy_unclassified")
            current = conn.execute(
                "SELECT decision_action FROM market_reviews WHERE task='task' AND is_current=1"
            ).fetchone()
            assert current[0] == "push"
            assert conn.execute("SELECT count(*) FROM market_item_aliases").fetchone()[0] == 3
            delivery = conn.execute("SELECT market_item_id,market_review_id FROM deliveries").fetchone()
            assert delivery[0] is not None and delivery[1] is None


def test_first_stage_event_review_is_reconciled_without_duplication() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        seed(path)
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            event = conn.execute("SELECT * FROM events").fetchone()
            analyses = list(conn.execute("SELECT * FROM event_analyses ORDER BY id"))
            item = item_from_event_mapping(
                {
                    "source": event["source"],
                    "source_event_id": event["source_event_id"],
                    "event_type": event["event_type"],
                    "title": event["title"],
                    "summary": event["summary"],
                    "full_text": event["full_text"],
                    "url": event["url"],
                    "published_at": event["published_at"],
                    "first_seen_at": event["first_seen_at"],
                    "raw": {},
                }
            )
            market_item_id = upsert_market_item(conn, item, processing_status="succeeded")
            ensure_market_item_alias(
                conn,
                market_item_id,
                item_kind="event",
                source=str(event["source"]),
                legacy_item_id=str(event["id"]),
                legacy_store_kind="events",
            )
            latest_payload = json.loads(analyses[-1]["analysis_json"])
            conn.execute(
                """
                INSERT INTO market_reviews (
                    market_item_id,task,run_key,is_current,review_status,
                    admission_status,admission_json,decision_action,importance,
                    decision_json,interpretation_json,legacy_payload_json,
                    legacy_store_kind,legacy_store_id,created_at,completed_at
                ) VALUES (?,?,?,1,'succeeded','legacy_unclassified','{}',?,?,?,?,?,?,?, ?,?)
                """,
                (
                    market_item_id,
                    "task",
                    "existing-run",
                    "push",
                    "high",
                    json.dumps(latest_payload["decision_result"]),
                    json.dumps(latest_payload["_interpretation_result"]),
                    json.dumps(latest_payload),
                    "event_analyses",
                    f"{event['id']}:task",
                    analyses[-1]["created_at"],
                    analyses[-1]["created_at"],
                ),
            )
            conn.commit()

            preview = migrate_legacy_results(conn, apply=False)
            assert preview.reconciled_event_reviews == 1
            assert preview.reviews == 3
            migrate_legacy_results(conn, apply=True)
            event_reviews = conn.execute(
                """
                SELECT id,legacy_store_id,is_current FROM market_reviews
                WHERE legacy_store_kind='event_analyses'
                ORDER BY CAST(legacy_store_id AS INTEGER)
                """
            ).fetchall()
            assert len(event_reviews) == 2
            assert event_reviews[-1]["id"] == 1
            assert event_reviews[-1]["legacy_store_id"] == str(analyses[-1]["id"])
            assert event_reviews[-1]["is_current"] == 1
            assert event_reviews[0]["is_current"] == 0


def test_apply_failure_rolls_back_every_migration_write() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        seed(path)
        with sqlite3.connect(path, isolation_level=None) as conn:
            conn.row_factory = sqlite3.Row
            before = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("market_items", "market_item_aliases", "market_reviews")
            }
            original = market_storage_migration._insert_review
            calls = 0

            def fail_after_first_review(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("forced migration failure")
                return original(*args, **kwargs)

            market_storage_migration._insert_review = fail_after_first_review
            try:
                try:
                    migrate_legacy_results(conn, apply=True)
                except RuntimeError as exc:
                    assert str(exc) == "forced migration failure"
                else:
                    raise AssertionError("forced migration failure must propagate")
            finally:
                market_storage_migration._insert_review = original

            after = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("market_items", "market_item_aliases", "market_reviews")
            }
            assert after == before
            assert conn.execute(
                "SELECT 1 FROM source_state WHERE source=?",
                (market_storage_migration.MIGRATION_VERSION,),
            ).fetchone() is None


def main() -> int:
    test_preview_apply_and_repeat()
    test_first_stage_event_review_is_reconciled_without_duplication()
    test_apply_failure_rolls_back_every_migration_write()
    print("market storage migration checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
