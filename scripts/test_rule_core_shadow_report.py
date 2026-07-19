#!/usr/bin/env python3
"""Regression checks for comparison of live shadow reports."""

from __future__ import annotations

import json
from pathlib import Path

from collector_direct_shadow import compact_normalized_item
from market_item import DecisionResult, NormalizedMarketItem
from rule_core_shadow_report import compare_shadow_report
from rule_core_v1 import parse_portfolio_config, parse_rule_config


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "rule_core_v1.test.json"


def contracts():
    return parse_rule_config(json.loads(CONFIG_PATH.read_text(encoding="utf-8"))), parse_portfolio_config([])


def test_shadow_report_compares_only_new_items_and_keeps_body_out_of_output() -> None:
    config, portfolio = contracts()
    normalized = NormalizedMarketItem(
        source="finance_media",
        source_category="news_media",
        publisher_role="news_media",
        collector="news_collector",
        content_type="article",
        title="DRAM价格持续上涨，供应极度紧缺",
        summary="行业供应紧张",
        full_text="DRAM价格持续上涨，供应极度紧缺，预计第三季度环比涨幅13%至18%。",
    )
    current = DecisionResult(action="daily", importance="medium", reason="旧规则")
    payload = {
        "mode": "shadow_dry_run",
        "sources": [
            {
                "source": "finance_media",
                "candidates": [
                    {
                        "source": "finance_media",
                        "id": "new-1",
                        "title": normalized.title,
                        "direct_shadow": {
                            "ok": True,
                            "normalized_item": compact_normalized_item(normalized),
                            "decision": current.to_dict(),
                        },
                    },
                    {
                        "source": "finance_media",
                        "id": "seen-1",
                        "already_seen": True,
                        "direct_shadow": {
                            "ok": True,
                            "normalized_item": compact_normalized_item(normalized),
                            "decision": current.to_dict(),
                        },
                    },
                ],
            }
        ],
    }
    result = compare_shadow_report(payload, rule_config=config, portfolio=portfolio)
    assert result["comparison_only"] is True
    assert result["affects_current_decision"] is False
    assert result["counts"]["compared"] == 1
    assert result["counts"]["skipped"]["already_seen_or_reviewed"] == 1
    assert result["items"][0]["comparison"]["candidate"]["action"] == "push"
    assert "full_text" not in result["items"][0]


def test_shadow_report_marks_missing_body_without_guessing() -> None:
    config, portfolio = contracts()
    result = compare_shadow_report(
        {
            "mode": "shadow_dry_run",
            "sources": [
                {
                    "source": "finance_media",
                    "candidates": [
                        {
                            "id": "missing-body",
                            "direct_shadow": {
                                "ok": True,
                                "normalized_item": {"source": "finance_media", "title": "只有标题"},
                                "decision": {"action": "archive"},
                            },
                        }
                    ],
                }
            ],
        },
        rule_config=config,
        portfolio=portfolio,
    )
    assert result["counts"]["compared"] == 0
    assert result["counts"]["skipped"]["missing_full_text_or_shadow"] == 1


def main() -> int:
    test_shadow_report_compares_only_new_items_and_keeps_body_out_of_output()
    test_shadow_report_marks_missing_body_without_guessing()
    print("rule core shadow report checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
