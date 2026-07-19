#!/usr/bin/env python3
"""Regression checks for the report-only rule-core-v1 comparison contract."""

from __future__ import annotations

import json
from pathlib import Path

from market_item import DecisionResult, NormalizedMarketItem
from rule_core_shadow import compare_rule_core, safe_compare_rule_core
from rule_core_v1 import SourceAdmissionPolicy, parse_portfolio_config, parse_rule_config


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "rule_core_v1.test.json"


def contracts():
    config = parse_rule_config(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return config, parse_portfolio_config([])


def item(source: str) -> NormalizedMarketItem:
    return NormalizedMarketItem(
        source=source,
        source_category="news_media" if source == "finance_media" else "research_industry_media",
        publisher_role="news_media" if source == "finance_media" else "research_publisher",
        content_type="article",
        title="DRAM价格持续上涨，供应极度紧缺",
        full_text="DRAM价格持续上涨，供应极度紧缺，预计第三季度环比涨幅13%至18%。",
    )


def test_candidate_action_is_recorded_without_replacing_current_decision() -> None:
    config, portfolio = contracts()
    current = DecisionResult(action="daily", importance="medium", reason="当前规则结果")
    results = [
        compare_rule_core(
            item(source),
            current_decision=current,
            current_admission_status="admitted",
            current_admission_reason="legacy_rule",
            current_matched_families=("semiconductor_ai",),
            rule_config=config,
            portfolio=portfolio,
            source_policy=SourceAdmissionPolicy(),
        )
        for source in ("finance_media", "industry_media")
    ]
    assert all(result["ok"] is True for result in results)
    assert all(result["comparison_only"] is True for result in results)
    assert all(result["affects_current_decision"] is False for result in results)
    assert all(result["current"]["action"] == "daily" for result in results)
    assert all(result["current"]["importance"] == "medium" for result in results)
    assert all(result["current"]["reason"] == "当前规则结果" for result in results)
    assert all(result["candidate"]["action"] == "push" for result in results)
    assert all(result["candidate"]["importance"] == "high" for result in results)
    assert all(result["candidate"]["reason"] for result in results)
    assert all(result["candidate"]["admission_evidence"] for result in results)
    assert all("action" in result["changed_fields"] for result in results)
    assert results[0]["candidate"] == results[1]["candidate"]


def test_candidate_exclusion_does_not_create_an_active_decision() -> None:
    config, portfolio = contracts()
    result = compare_rule_core(
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="普通行业动态",
            full_text="没有命中任何目标规则。",
        ),
        current_decision=DecisionResult(action="archive", importance="low", reason="当前规则结果"),
        current_admission_status="admitted",
        current_admission_reason="legacy_rule",
        rule_config=config,
        portfolio=portfolio,
        source_policy=SourceAdmissionPolicy(),
    )
    assert result["candidate"]["admission_status"] == "excluded"
    assert result["candidate"]["action"] is None
    assert result["current"]["action"] == "archive"
    assert result["affects_current_decision"] is False


def test_candidate_failure_is_closed_without_changing_current_result() -> None:
    config, portfolio = contracts()
    current = DecisionResult(action="push", importance="high", reason="当前硬规则")
    result = safe_compare_rule_core(
        item("finance_media"),
        current_decision=current,
        rule_config=None,  # type: ignore[arg-type]
        portfolio=portfolio,
        source_policy=SourceAdmissionPolicy(),
    )
    assert result["ok"] is False
    assert result["comparison_only"] is True
    assert result["affects_current_decision"] is False
    assert "error" in result
    assert current.action == "push"


def main() -> int:
    test_candidate_action_is_recorded_without_replacing_current_decision()
    test_candidate_exclusion_does_not_create_an_active_decision()
    test_candidate_failure_is_closed_without_changing_current_result()
    print("rule core shadow checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
