#!/usr/bin/env python3
"""CI-safe checks for unified historical and operational read projections."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from article_daily import fetch_digest_rows as fetch_article_digest_rows
from holdings_web import fetch_events_rows
from market_canonical_reader import (
    canonical_digest_rows,
    canonical_event_rows,
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
from market_store import complete_market_review, record_article_delivery, record_production_admission


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
    item_kind: str,
    source: str,
    source_item_id: str,
    title: str,
    action: str,
    published_at: str,
    sent: bool,
    external_item_id: str | None = None,
) -> tuple[int, int, int | None]:
    content_type = {
        "article": "article",
        "official": "official_news",
        "event": "flash_news",
    }[item_kind]
    item = NormalizedMarketItem(
        source=source,
        source_category="official_company" if item_kind == "official" else "news_media",
        content_type=content_type,
        title=title,
        summary=f"{title}摘要",
        full_text=f"{title}正文",
        url=f"https://example.com/{source_item_id}",
        published_at=published_at,
        first_seen_at=published_at,
        raw={"source_event_id": source_item_id} if item_kind == "event" else {"id": source_item_id},
    )
    task = "portfolio_event" if item_kind == "event" else "production"
    market_item_id, review_id = record_production_admission(item, admitted(), db_path=path, task=task)
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
        alias=(item_kind, source, external_item_id or source_item_id, "market_items"),
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE market_reviews SET created_at=?,completed_at=? WHERE id=?",
            (published_at, published_at, review_id),
        )
        conn.commit()
    delivery_id = None
    if sent:
        delivery_id = record_article_delivery(
            market_item_id,
            review_id,
            status="sent",
            decision_action=action,
            db_path=path,
        )
    return market_item_id, review_id, delivery_id


def test_canonical_readers_use_unified_tables_and_preserve_external_identity() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        init_db(path).close()
        add_result(
            path,
            item_kind="article",
            source="test_source",
            source_item_id="article-push",
            title="文章推送",
            action="push",
            published_at="2026-07-23T01:00:00+00:00",
            sent=True,
        )
        add_result(
            path,
            item_kind="article",
            source="test_source",
            source_item_id="article-daily",
            title="文章日报",
            action="daily",
            published_at="2026-07-23T02:00:00+00:00",
            sent=False,
        )
        add_result(
            path,
            item_kind="official",
            source="official_source",
            source_item_id="official-daily",
            title="官网日报",
            action="daily",
            published_at="2026-07-23T03:00:00+00:00",
            sent=False,
        )
        _, event_review_id, event_delivery_id = add_result(
            path,
            item_kind="event",
            source="event_source",
            source_item_id="event-reviewed",
            external_item_id="77",
            title="事件推送",
            action="push",
            published_at="2026-07-23T04:00:00+00:00",
            sent=True,
        )
        assert event_delivery_id is not None
        with sqlite3.connect(path) as conn:
            conn.execute(
                """
                INSERT INTO market_feedback(
                    feedback_event_id,item_kind,source,item_id,delivery_id,label,
                    reason_tags_json,operator_id,rule_ids_json,clicked_at_us,
                    received_at,raw_json
                ) VALUES ('feedback-1','event','event_source','77',?,'high_value',
                          '[]','operator','[]',1,'2026-07-23T04:01:00+00:00','{}')
                """,
                (event_delivery_id,),
            )
            conn.commit()

        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            articles = canonical_digest_rows(
                conn,
                start_utc="2026-07-23T00:00:00+00:00",
                end_utc="2026-07-24T00:00:00+00:00",
            )
            events = canonical_event_rows(
                conn,
                start_utc="2026-07-23T00:00:00+00:00",
                end_utc="2026-07-24T00:00:00+00:00",
                time_basis="seen",
                include_baseline=False,
            )
            feedback = canonical_feedback_snapshot(conn, "event", "event_source", "77")
            tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        assert [row["item_id"] for row in articles] == ["article-daily"]
        event = next(row for row in events if row["title"] == "事件推送")
        assert event["id"] == "77"
        assert event["feedback_identity"] == {
            "item_kind": "event",
            "source": "event_source",
            "item_id": "77",
            "delivered": True,
        }
        assert feedback is not None
        assert feedback["decision"]["action"] == "push"
        assert feedback["delivery_id"] == event_delivery_id
        assert feedback["historical_payload"] == {}
        assert event_review_id > 0
        assert not tables.intersection({"article_reviews", "official_news_reviews", "events", "event_analyses"})

        web_rows = fetch_events_rows(day="2026-07-23", db_path=path)
        assert any(row["title"] == "事件推送" and row["id"] == "77" for row in web_rows)
        with sqlite3.connect(path) as conn:
            assert [row["item_id"] for row in fetch_article_digest_rows(conn, "2026-07-23")] == [
                "article-daily"
            ]


def main() -> int:
    test_canonical_readers_use_unified_tables_and_preserve_external_identity()
    print("canonical market reader checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
