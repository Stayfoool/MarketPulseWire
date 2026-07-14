#!/usr/bin/env python3
"""Regression checks for cross-source deterministic alert deduplication."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from rule_alert_dedup import confirm_rule_alert, release_rule_alert, reserve_rule_alert


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


def main() -> int:
    test_reserve_confirm_and_duplicate()
    test_release_makes_failed_send_retryable()
    test_fed_path_rule_uses_cross_source_reservation()
    test_attributed_research_rule_uses_the_same_cross_source_reservation()
    print("rule alert dedup checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
