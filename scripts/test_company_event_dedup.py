#!/usr/bin/env python3
"""Regression checks for feedback-confirmed company-event identities."""

from __future__ import annotations

from company_event_dedup import COMPANY_EVENT_RULE_ID, company_event_dedup_hit
from market_item import DecisionResult


HOLDING_DECISION = DecisionResult(action="push", rule_hits=[{"rule_id": "holding_keyword_immediate_alert"}])
INDUSTRY_DECISION = DecisionResult(action="push", rule_hits=[{"rule_id": "industry_quantified_hardline"}])
COMBINED_DECISION = DecisionResult(
    action="push",
    rule_hits=[{"rule_id": "holding_keyword_immediate_alert"}, {"rule_id": "industry_quantified_hardline"}],
)


def hit(title: str, decision: DecisionResult = INDUSTRY_DECISION, published_at: str = "2026-07-15T10:00:00+00:00"):
    return company_event_dedup_hit({"title": title, "published_at": published_at}, decision)


def test_earnings_forecasts_converge_across_sources_and_rules() -> None:
    samples = (
        ("佰维存储：预计上半年净利润70亿元至75亿元，上年同期亏损2.26亿元", HOLDING_DECISION),
        ("佰维存储公告，预计2026年半年度净利润70亿元-75亿元，同比扭亏", COMBINED_DECISION),
        ("大普微：上半年预盈12亿元-13.5亿元，同比扭亏", INDUSTRY_DECISION),
        ("江丰电子：预计2026年半年度净利润4.8亿元-5.6亿元", INDUSTRY_DECISION),
    )
    matches = [hit(text, decision) for text, decision in samples]
    assert all(match is not None for match in matches)
    assert matches[0]["dedup_key"] == matches[1]["dedup_key"]
    assert matches[0]["rule_id"] == COMPANY_EVENT_RULE_ID
    assert matches[2]["dedup_key"] == "company_event:dapustor:earnings_forecast:2026-H1:net_profit"
    assert matches[3]["dedup_key"] == (
        "company_event:jiangfeng_electronics:earnings_forecast:2026-H1:net_profit"
    )


def test_financing_and_price_facts_converge() -> None:
    placement = hit("仕佳光子：拟定增募资不超过28亿元，用于高速AWG芯片产能建设", COMBINED_DECISION)
    placement_rewrite = hit("仕佳光子公告，拟向特定对象发行A股，募集资金不超28亿元", HOLDING_DECISION)
    price = hit("力积电7月起将存储代工报价上调45%")
    price_rewrite = hit("本月起，力积电存储代工涨价45%")
    assert placement is not None and placement_rewrite is not None
    assert placement["dedup_key"] == placement_rewrite["dedup_key"]
    assert price is not None and price_rewrite is not None
    assert price["dedup_key"] == price_rewrite["dedup_key"]


def test_material_updates_different_periods_and_ineligible_decisions_fail_open() -> None:
    revised = hit("佰维存储上修2026年半年度净利润预告至80亿元-85亿元", HOLDING_DECISION)
    corrected = hit("更正：大普微预计2026年半年度净利润12亿元-13.5亿元")
    approved = hit("仕佳光子定增方案获批，拟募集资金不超过28亿元", HOLDING_DECISION)
    different_period = hit("佰维存储预计2026年全年净利润100亿元", HOLDING_DECISION)
    archive = DecisionResult(action="archive", rule_hits=[{"rule_id": "industry_quantified_hardline"}])
    macro = DecisionResult(action="push", rule_hits=[{"rule_id": "macro_policy_line"}])
    item = {"title": "佰维存储预计2026年半年度净利润70亿元", "published_at": "2026-07-15"}
    assert revised is None
    assert corrected is None
    assert approved is None
    assert different_period is not None
    assert different_period["dedup_key"].endswith(":2026-FY:net_profit")
    assert company_event_dedup_hit(item, archive) is None
    assert company_event_dedup_hit(item, macro) is None


def test_unrelated_or_incomplete_company_mentions_do_not_match() -> None:
    assert hit("佰维存储股价上涨5%") is None
    assert hit("某存储公司预计上半年业绩增长") is None
    assert hit("仕佳光子计划扩产，但未披露融资方案") is None
    assert hit("力积电存储代工业务保持稳定") is None
    assert hit(
        "存储芯片概念回落，佰维存储跌超8%。消息面上，德明利公告称，"
        "预计2026年半年度净利润57亿元-65亿元。"
    ) is None


def main() -> int:
    test_earnings_forecasts_converge_across_sources_and_rules()
    test_financing_and_price_facts_converge()
    test_material_updates_different_periods_and_ineligible_decisions_fail_open()
    test_unrelated_or_incomplete_company_mentions_do_not_match()
    print("company event dedup checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
