#!/usr/bin/env python3
"""Regression checks for cross-source deterministic alert deduplication."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from rule_alert_dedup import confirm_rule_alert, release_rule_alert, reserve_rule_alert


def review_with_rule(key: str) -> dict:
    return {
        "raw": {
            "rule_hits": [
                {
                    "rule_id": "international_bank_theme_strategy",
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


def main() -> int:
    test_reserve_confirm_and_duplicate()
    test_release_makes_failed_send_retryable()
    print("rule alert dedup checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
