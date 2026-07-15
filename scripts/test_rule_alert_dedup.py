#!/usr/bin/env python3
"""Regression checks for cross-source deterministic alert deduplication."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from rule_alert_dedup import confirm_rule_alert, release_rule_alert, reserve_rule_alert, reserve_rule_alert_set


def review_with_rule(key: str, rule_id: str = "international_bank_theme_strategy") -> dict:
    return {
        "raw": {
            "rule_hits": [
                {
                    "rule_id": rule_id,
                    "dedup_key": key,
                    "dedup_lookback_days": 14,
                }
            ]
        }
    }


def test_reserve_confirm_and_duplicate() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        first = reserve_rule_alert(
            review_with_rule("ib_theme:first"),
            source="cls_telegraph_api",
            item_id="1",
            title="高盛做多中国 AI",
            published_at="2026-07-09T03:57:30+00:00",
            db_path=db_path,
        )
        assert first["reserved"] is True
        confirm_rule_alert(first, db_path=db_path)
        duplicate = reserve_rule_alert(
            review_with_rule("ib_theme:first"),
            source="sina_stock_news",
            item_id="2",
            title="高盛中国 AI 策略转载",
            published_at="2026-07-09T04:10:00+00:00",
            db_path=db_path,
        )
        assert duplicate["duplicate"] is True
        assert duplicate["first"]["source"] == "cls_telegraph_api"


def test_release_makes_failed_send_retryable() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        first = reserve_rule_alert(
            review_with_rule("ib_theme:retry"),
            source="cls_telegraph_api",
            item_id="1",
            title="策略",
            published_at="2026-07-09T03:57:30+00:00",
            db_path=db_path,
        )
        release_rule_alert(first, db_path=db_path)
        retry = reserve_rule_alert(
            review_with_rule("ib_theme:retry"),
            source="cls_telegraph_api",
            item_id="1",
            title="策略",
            published_at="2026-07-09T03:57:30+00:00",
            db_path=db_path,
        )
        assert retry["reserved"] is True


def test_fed_path_rule_uses_cross_source_reservation() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        first = reserve_rule_alert(
            review_with_rule("ib_fed_path:test", rule_id="international_bank_fed_rate_path_revision"),
            source="wallstreetcn_news",
            item_id="article:3775241",
            title="美银转为预计美联储加息三次",
            published_at="2026-06-22T16:39:42+00:00",
            db_path=db_path,
        )
        assert first["reserved"] is True
        confirm_rule_alert(first, db_path=db_path)
        duplicate = reserve_rule_alert(
            review_with_rule("ib_fed_path:test", rule_id="international_bank_fed_rate_path_revision"),
            source="sina_finance_articles",
            item_id="repost-1",
            title="美银加息预测转载",
            published_at="2026-06-23T01:00:00+00:00",
            db_path=db_path,
        )
        assert duplicate["duplicate"] is True
        assert duplicate["first"]["source"] == "wallstreetcn_news"


def test_attributed_research_rule_uses_the_same_cross_source_reservation() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        first = reserve_rule_alert(
            review_with_rule(
                "attributed_research:semianalysis:test",
                rule_id="attributed_research_hard_variable",
            ),
            source="cls_telegraph_api",
            item_id="1",
            title="SemiAnalysis存储观点",
            published_at="2026-07-10T09:24:30+00:00",
            db_path=db_path,
        )
        confirm_rule_alert(first, db_path=db_path)
        duplicate = reserve_rule_alert(
            review_with_rule(
                "attributed_research:semianalysis:test",
                rule_id="attributed_research_hard_variable",
            ),
            source="sina_flash",
            item_id="2",
            title="新浪转述SemiAnalysis存储观点",
            published_at="2026-07-10T09:30:00+00:00",
            db_path=db_path,
        )
        assert duplicate["duplicate"] is True
        assert duplicate["rule_id"] == "attributed_research_hard_variable"


def test_delivery_alias_migrates_legacy_reservation_to_canonical_key() -> None:
    legacy_key = "macro:market_reaction:US:CPI:2026-07-15"
    canonical_key = "macro:market_reaction:US:CPI:2026-06"
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        first = reserve_rule_alert(
            {},
            source="cls_telegraph_api",
            item_id="legacy-1",
            title="美国CPI发布后美股期货上涨",
            published_at="2026-07-15T00:10:00+00:00",
            delivery_hit={
                "rule_id": "macro_market_reaction",
                "dedup_key": legacy_key,
                "dedup_lookback_days": 90,
            },
            db_path=db_path,
        )
        confirm_rule_alert(first, db_path=db_path)
        duplicate = reserve_rule_alert(
            {},
            source="wallstreetcn_news",
            item_id="canonical-2",
            title="美国6月CPI发布后黄金上涨",
            published_at="2026-07-15T01:10:00+00:00",
            delivery_hit={
                "rule_id": "macro_market_reaction",
                "dedup_key": canonical_key,
                "dedup_alias_keys": [legacy_key],
                "dedup_lookback_days": 90,
            },
            db_path=db_path,
        )
        assert duplicate["duplicate"] is True
        assert duplicate["matched_dedup_key"] == legacy_key
        assert duplicate["first"]["source"] == "cls_telegraph_api"
        with sqlite3.connect(db_path) as conn:
            migrated = conn.execute(
                "SELECT status, first_source FROM rule_alert_dedup WHERE dedup_key = ?",
                (canonical_key,),
            ).fetchone()
        assert migrated == ("sent", "cls_telegraph_api")


def test_fact_set_reserves_all_new_keys_and_duplicates_regardless_of_order() -> None:
    hits = [
        {"rule_id": "company_event_dedup", "dedup_key": "company_event:a", "dedup_lookback_days": 90},
        {"rule_id": "company_event_dedup", "dedup_key": "company_event:b", "dedup_lookback_days": 90},
    ]
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        first = reserve_rule_alert_set(
            hits,
            source="sina_stock_news",
            item_id="1",
            title="多公司事件综述",
            published_at="2026-07-15T12:19:35+00:00",
            db_path=db_path,
        )
        assert first["reserved"] is True
        assert len(first["reservations"]) == 2
        confirm_rule_alert(first, db_path=db_path)
        duplicate = reserve_rule_alert_set(
            list(reversed(hits)),
            source="yicai_brief",
            item_id="2",
            title="相同事件换序转载",
            published_at="2026-07-15T12:19:51+00:00",
            db_path=db_path,
        )
        assert duplicate["duplicate"] is True
        assert len(duplicate["covered"]) == 2


def test_fact_set_with_one_new_identity_sends_and_failure_releases_only_new_keys() -> None:
    first_hits = [
        {"rule_id": "company_event_dedup", "dedup_key": "company_event:a", "dedup_lookback_days": 90},
    ]
    mixed_hits = [
        *first_hits,
        {"rule_id": "company_event_dedup", "dedup_key": "company_event:b", "dedup_lookback_days": 90},
        {"rule_id": "company_event_dedup", "dedup_key": "company_event:c", "dedup_lookback_days": 90},
    ]
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        first = reserve_rule_alert_set(
            first_hits,
            source="source-a",
            item_id="1",
            title="事件A",
            published_at="2026-07-15T12:00:00+00:00",
            db_path=db_path,
        )
        confirm_rule_alert(first, db_path=db_path)
        mixed = reserve_rule_alert_set(
            mixed_hits,
            source="source-b",
            item_id="2",
            title="事件A、B、C",
            published_at="2026-07-15T12:01:00+00:00",
            db_path=db_path,
        )
        assert mixed["duplicate"] is False
        assert {item["dedup_key"] for item in mixed["reservations"]} == {"company_event:b", "company_event:c"}
        release_rule_alert(mixed, db_path=db_path)
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute("SELECT dedup_key, status FROM rule_alert_dedup ORDER BY dedup_key").fetchall()
        assert rows == [("company_event:a", "sent")]


def main() -> int:
    test_reserve_confirm_and_duplicate()
    test_release_makes_failed_send_retryable()
    test_fed_path_rule_uses_cross_source_reservation()
    test_attributed_research_rule_uses_the_same_cross_source_reservation()
    test_delivery_alias_migrates_legacy_reservation_to_canonical_key()
    test_fact_set_reserves_all_new_keys_and_duplicates_regardless_of_order()
    test_fact_set_with_one_new_identity_sends_and_failure_releases_only_new_keys()
    print("rule alert dedup checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
