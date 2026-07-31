#!/usr/bin/env python3
"""CI-safe checks for current unified read projections."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from holdings_web import fetch_market_rows
from market_daily import fetch_digest_rows
from market_canonical_reader import (
    canonical_digest_rows,
    canonical_market_rows,
    canonical_feedback_snapshot,
)
from market_db import init_db
from market_item import (
    AdmissionEvidence,
    AdmissionResult,
    DecisionResult,
    InterpretationResult,
    MarketFlowResult,
    NormalizedMarketItem,
)
from market_store import complete_market_review, record_delivery, record_production_admission


def admitted() -> AdmissionResult:
    return AdmissionResult(
        status="admitted",
        reason_code="semiconductor_ai_match",
        matched_families=("semiconductor_ai",),
        evidence=(AdmissionEvidence("semiconductor_ai", "term", "HBM"),),
        config_version="test-v1",
    )


def add_result(
    path: Path,
    *,
    source: str,
    source_item_id: str,
    title: str,
    action: str,
    published_at: str,
    sent: bool,
    source_category: str = "news_media",
    content_type: str = "unknown",
) -> tuple[int, int, int | None]:
    item = NormalizedMarketItem(
        source=source,
        source_category=source_category,
        content_type=content_type,
        title=title,
        summary=f"{title}摘要",
        full_text=f"{title}正文",
        url=f"https://example.com/{source_item_id}",
        published_at=published_at,
        first_seen_at=published_at,
        raw={"id": source_item_id},
    )
    market_item_id, review_id = record_production_admission(item, admitted(), db_path=path)
    decision = DecisionResult(
        action=action,
        importance={"push": "high", "daily": "medium", "archive": "low"}[action],
        reason=f"{action} reason",
        rule_hits=[{"rule_id": "test_rule", "decision_action": action}],
    )
    flow = MarketFlowResult(
        item=item,
        decision=decision,
        interpretation=InterpretationResult(core_content=f"{title}核心内容"),
    )
    complete_market_review(
        review_id,
        flow,
        db_path=path,
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE market_reviews SET created_at=?,completed_at=? WHERE id=?",
            (published_at, published_at, review_id),
        )
        conn.commit()
    delivery_id = None
    if sent:
        delivery_id = record_delivery(
            market_item_id,
            review_id,
            status="sent",
            decision_action=action,
            db_path=path,
        )
    return market_item_id, review_id, delivery_id


def test_canonical_readers_use_direct_unified_identity() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        init_db(path).close()
        add_result(
            path,
            source="test_source",
            source_item_id="item-push",
            title="即时推送",
            action="push",
            published_at="2026-07-23T01:00:00+00:00",
            sent=True,
        )
        add_result(
            path,
            source="test_source",
            source_item_id="item-daily",
            title="日报信息",
            action="daily",
            published_at="2026-07-23T02:00:00+00:00",
            sent=False,
        )
        add_result(
            path,
            source="company_source",
            source_item_id="company-daily",
            title="公司来源日报",
            action="daily",
            published_at="2026-07-23T03:00:00+00:00",
            sent=False,
        )
        flash_item_id, flash_review_id, flash_delivery_id = add_result(
            path,
            source="flash_source",
            source_item_id="77",
            title="快讯推送",
            action="push",
            published_at="2026-07-23T04:00:00+00:00",
            sent=True,
        )
        assert flash_delivery_id is not None
        with sqlite3.connect(path) as conn:
            conn.execute(
                """
                INSERT INTO market_feedback(
                    feedback_event_id,market_item_id,delivery_id,label,
                    reason_tags_json,operator_id,rule_ids_json,clicked_at_us,
                    received_at,raw_json
                ) VALUES ('feedback-1',?,?,'high_value',
                          '[]','operator','[]',1,'2026-07-23T04:01:00+00:00','{}')
                """,
                (flash_item_id, flash_delivery_id),
            )
            conn.commit()

        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            digest = canonical_digest_rows(
                conn,
                start_utc="2026-07-23T00:00:00+00:00",
                end_utc="2026-07-24T00:00:00+00:00",
            )
            items = canonical_market_rows(
                conn,
                start_utc="2026-07-23T00:00:00+00:00",
                end_utc="2026-07-24T00:00:00+00:00",
                time_basis="seen",
                include_baseline=False,
            )
            feedback = canonical_feedback_snapshot(conn, flash_item_id)
            tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        assert [row["item_id"] for row in digest] == ["company-daily", "item-daily"]
        item = next(row for row in items if row["title"] == "快讯推送")
        assert item["id"] == "77"
        assert item["feedback_identity"] == {
            "market_item_id": flash_item_id,
            "delivered": True,
        }
        assert feedback is not None
        assert feedback["decision"]["action"] == "push"
        assert feedback["delivery_id"] == flash_delivery_id
        assert flash_review_id > 0
        assert not tables.intersection({"article_reviews", "official_news_reviews", "events", "event_analyses"})

        web_rows = fetch_market_rows(day="2026-07-23", db_path=path)
        assert any(row["title"] == "快讯推送" and row["id"] == "77" for row in web_rows)
        with sqlite3.connect(path) as conn:
            assert [row["item_id"] for row in fetch_digest_rows(conn, "2026-07-23")] == [
                "company-daily", "item-daily"
            ]


def main() -> int:
    test_canonical_readers_use_direct_unified_identity()
    print("canonical market reader checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
