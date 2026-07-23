#!/usr/bin/env python3
"""CI-safe checks for unified market-storage readers."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from market_canonical_reader import (
    canonical_delivered_items,
    canonical_digest_rows,
    canonical_event_rows,
    canonical_feedback_snapshot,
    canonical_signal_rows,
)
from article_daily import fetch_digest_rows as fetch_article_digest_rows
from holdings_web import fetch_events_rows
from market_db import init_db
from market_feedback import feedback_quality_payload
from market_review_store import (
    ensure_article_reviews_table,
    ensure_official_news_table,
    insert_event_analysis,
    upsert_event_record,
)
from market_storage_migration import migrate_legacy_results
from official_news_daily import fetch_digest_rows as fetch_official_digest_rows
from signals_extract import extract_signals


def decision_payload(action: str, *, importance: str = "medium", core: str = "统一解读") -> str:
    return json.dumps(
        {
            "decision_result": {
                "action": action,
                "importance": importance,
                "reason": f"{action} reason",
                "brief_reason": f"{action} brief",
                "rule_hits": [{"rule_id": f"rule_{action}", "related_targets": ["NVIDIA"]}],
                "audit_json": {"decision_version": "decision-test-v1"},
            },
            "_interpretation_result": {
                "core_content": core,
                "brief_reason": "解读理由",
                "related_targets": ["NVIDIA"],
            },
        },
        ensure_ascii=False,
    )


def seed(path: Path) -> dict[str, int]:
    init_db(path).close()
    with sqlite3.connect(path) as conn:
        ensure_article_reviews_table(conn)
        ensure_official_news_table(conn)
        for item_id, title, status in (
            ("article-daily", "文章日报条目", "admitted"),
            ("article-push", "文章推送条目", "admitted"),
            ("official-daily", "官网日报条目", "admitted"),
            ("excluded", "已排除条目", "excluded"),
        ):
            conn.execute(
                """
                INSERT INTO seen_items (
                    source,item_id,url,title,summary,published_at,first_seen_at,
                    collection_class,processability_status,admission_status,
                    admission_reason,processing_status
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "test_source",
                    item_id,
                    f"https://example.com/{item_id}",
                    title,
                    f"{title}摘要",
                    "2026-07-23T01:00:00+00:00",
                    "2026-07-23T01:01:00+00:00",
                    "live",
                    "succeeded",
                    status,
                    "test",
                    "succeeded" if status == "admitted" else "not_applicable",
                ),
            )
        conn.execute(
            """
            INSERT INTO article_reviews (
                source,item_id,url,title,source_module,published_at,importance,push_now,
                market_impact,incremental_classification,affected_targets_json,reason,
                daily_summary,confidence,gate_json,pushed_at,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "test_source", "article-daily", "https://example.com/article-daily",
                "文章日报条目", "测试媒体", "2026-07-23T01:00:00+00:00", "medium", 0,
                "影响", "新增", '["NVIDIA"]', "日报理由", "日报摘要", "high",
                decision_payload("daily"), "", "2026-07-23T01:02:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO article_reviews (
                source,item_id,url,title,source_module,published_at,importance,push_now,
                market_impact,incremental_classification,affected_targets_json,reason,
                daily_summary,confidence,gate_json,pushed_at,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "test_source", "article-push", "https://example.com/article-push",
                "文章推送条目", "测试媒体", "2026-07-23T01:03:00+00:00", "high", 1,
                "重大影响", "新增", '["NVIDIA"]', "推送理由", "推送摘要", "high",
                decision_payload("push", importance="high"), "2026-07-23T01:04:00+00:00",
                "2026-07-23T01:03:30+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO official_news_reviews (
                source,item_id,url,title,published_at,importance,should_push_now,
                reason,daily_summary,analysis_json,pushed_at,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "test_source", "official-daily", "https://example.com/official-daily",
                "官网日报条目", "2026-07-23T01:05:00+00:00", "low", 0,
                "官网理由", "官网摘要", decision_payload("archive", importance="low"), "",
                "2026-07-23T01:06:00+00:00",
            ),
        )
        conn.commit()

    event_id, _ = upsert_event_record(
        {
            "source": "event_source",
            "source_event_id": "event-reviewed",
            "event_type": "flash",
            "title": "多任务事件",
            "summary": "事件摘要",
            "full_text": "完整事件正文",
            "url": "https://example.com/event-reviewed",
            "published_at": "2026-07-23T01:07:00+00:00",
            "symbols": ["NVDA"],
            "themes": ["AI"],
        },
        path,
    )
    insert_event_analysis(
        event_id, "task-a", "model", importance="high", classification="first",
        direction="up", impact_duration="long", should_push=1,
        analysis=json.loads(decision_payload("push", importance="high", core="旧结果")), db_path=path,
    )
    insert_event_analysis(
        event_id, "task-b", "model", importance="low", classification="latest",
        direction="neutral", impact_duration="short", should_push=0,
        analysis=json.loads(decision_payload("archive", importance="low", core="最新结果")), db_path=path,
    )
    baseline_id, _ = upsert_event_record(
        {
            "source": "event_source",
            "source_event_id": "event-baseline",
            "event_type": "announcement",
            "title": "事件基线",
            "summary": "基线摘要",
            "baseline_only": True,
            "first_seen_at": "2026-07-23T01:08:00+00:00",
        },
        path,
    )
    pending_id, _ = upsert_event_record(
        {
            "source": "event_source",
            "source_event_id": "event-no-analysis",
            "event_type": "announcement",
            "title": "尚无分析的旧事件",
            "summary": "仍应保留展示",
            "first_seen_at": "2026-07-23T01:09:00+00:00",
        },
        path,
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO deliveries(event_id,channel,status,sent_at,payload_json) VALUES (?, 'feishu', 'sent', ?, '{}')",
            (event_id, "2026-07-23T01:10:00+00:00"),
        )
        conn.commit()
        migrate_legacy_results(conn, apply=True)
    return {"reviewed": event_id, "baseline": baseline_id, "pending": pending_id}


def test_canonical_readers_preserve_behavior_and_identity() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        ids = seed(path)
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            rows = canonical_event_rows(
                conn,
                start_utc="2026-07-23T00:00:00+00:00",
                end_utc="2026-07-24T00:00:00+00:00",
                time_basis="seen",
                include_baseline=False,
            )
            by_title = {row["title"]: row for row in rows}
            assert "已排除条目" not in by_title
            assert "事件基线" not in by_title
            assert by_title["多任务事件"]["id"] == str(ids["reviewed"])
            assert by_title["多任务事件"]["decision_action"] == "archive"
            assert by_title["多任务事件"]["summary"] == "最新结果"
            assert by_title["多任务事件"]["delivery_status"] == "sent"
            assert by_title["多任务事件"]["push"] is False
            assert by_title["文章推送条目"]["id"] == "article-push"
            assert by_title["文章推送条目"]["push"] is True
            assert "尚无分析的旧事件" in by_title

            with_baseline = canonical_event_rows(
                conn,
                start_utc="2026-07-23T00:00:00+00:00",
                end_utc="2026-07-24T00:00:00+00:00",
                time_basis="seen",
                include_baseline=True,
            )
            baseline = next(row for row in with_baseline if row["title"] == "事件基线")
            assert baseline["kind"] == "announcement"
            assert baseline["id"] == str(ids["baseline"])
            assert baseline["baseline_only"] is True

            article_daily = canonical_digest_rows(
                conn,
                item_kind="article",
                start_utc="2026-07-23T00:00:00+00:00",
                end_utc="2026-07-24T00:00:00+00:00",
            )
            assert [row["item_id"] for row in article_daily] == ["article-daily"]
            assert article_daily[0]["source_module"] == "测试媒体"
            official_daily = canonical_digest_rows(
                conn,
                item_kind="official",
                start_utc="2026-07-23T00:00:00+00:00",
                end_utc="2026-07-24T00:00:00+00:00",
            )
            assert [row["item_id"] for row in official_daily] == ["official-daily"]

            article_feedback = canonical_feedback_snapshot(
                conn, "article", "test_source", "article-push"
            )
            assert article_feedback is not None
            assert article_feedback["decision"]["action"] == "push"
            assert article_feedback["delivery_status"] == "sent"
            event_feedback = canonical_feedback_snapshot(
                conn, "event", "event_source", str(ids["reviewed"])
            )
            assert event_feedback is not None
            assert event_feedback["decision"]["action"] == "archive"
            assert event_feedback["delivery_status"] == "sent"

            delivered = canonical_delivered_items(conn, "2026-07-23T00:00:00+00:00")
            delivered_keys = {(item["item_kind"], item["item_id"]) for item in delivered}
            assert ("article", "article-push") in delivered_keys
            assert ("event", str(ids["reviewed"])) in delivered_keys

            event_signals = canonical_signal_rows(
                conn, item_kind="event", since="2026-07-23T00:00:00+00:00"
            )
            assert len(event_signals) == 1
            assert event_signals[0]["id"] == ids["reviewed"]
            assert event_signals[0]["classification"] == "latest"
            assert event_signals[0]["full_text"] == "完整事件正文"

        web_rows = fetch_events_rows(day="2026-07-23", db_path=path)
        web_by_title = {row["title"]: row for row in web_rows}
        assert web_by_title["多任务事件"]["id"] == str(ids["reviewed"])
        assert "事件基线" not in web_by_title
        web_baseline = fetch_events_rows(
            day="2026-07-23", source="event_source", include_baseline=True, db_path=path
        )
        assert any(row["title"] == "事件基线" for row in web_baseline)

        with sqlite3.connect(path) as conn:
            article_rows = fetch_article_digest_rows(conn, "2026-07-23")
            official_rows = fetch_official_digest_rows(conn, "2026-07-23")
        assert [row["item_id"] for row in article_rows] == ["article-daily"]
        assert [row["item_id"] for row in official_rows] == ["official-daily"]

        quality = feedback_quality_payload(db_path=path, days=365)
        assert quality["summary"]["delivered"] == 2

        counts = extract_signals(db_path=path, days=365, dry_run=False)
        assert counts["signals"] == 2
        with sqlite3.connect(path) as conn:
            signal_keys = set(conn.execute("SELECT source_table,source_id FROM signals"))
        assert ("article_reviews", "test_source:article-push") in signal_keys
        assert ("events", str(ids["reviewed"])) in signal_keys


def main() -> int:
    test_canonical_readers_preserve_behavior_and_identity()
    print("canonical market reader checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
