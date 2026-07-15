#!/usr/bin/env python3
"""Regression checks for delivery-only repeated industry-fact identities."""

from __future__ import annotations

from industry_fact_dedup import INDUSTRY_FACT_RULE_ID, industry_fact_dedup_hit
from market_item import DecisionResult


INDUSTRY_DECISION = DecisionResult(
    action="push",
    importance="high",
    rule_hits=[{"rule_id": "industry_quantified_hardline"}],
)


def hit(title: str, summary: str = "") -> dict | None:
    return industry_fact_dedup_hit({"title": title, "summary": summary}, INDUSTRY_DECISION)


def test_ibm_cross_source_rewordings_share_one_fact_identity() -> None:
    chinese = hit(
        "IBM称客户将资本支出转向服务器、存储和内存采购",
        "企业为在涨价前锁定供应紧张的基础设施而调整预算。",
    )
    english = hit(
        "IBM warns AI-driven memory shortage shifts enterprise spending away from software",
        "Customers moved budgets toward servers and storage hardware to secure supply.",
    )
    assert chinese is not None and english is not None
    assert chinese["rule_id"] == INDUSTRY_FACT_RULE_ID
    assert chinese["dedup_key"] == english["dedup_key"]
    assert chinese["dedup_lookback_minutes"] == 36 * 60


def test_coreweave_cross_source_rewordings_share_one_fact_identity() -> None:
    variants = (
        "消息人士称，Coreweave(CRWV.O)正在探索使用金融衍生品，以对冲未来存储芯片价格下跌的风险。",
        "财联社电，CoreWeave据悉正在探索使用金融衍生品，以对冲未来存储芯片价格下跌的风险。",
        "人工智能云计算公司CoreWeave正在探索使用金融衍生品，作为防范未来内存和存储芯片价格下跌的潜在对冲手段。",
        "AI云公司CoreWeave正探索使用看跌期权对冲存储芯片价格下跌风险。",
        "CoreWeave is exploring derivatives and put options to hedge against a decline in memory chip prices.",
    )
    matches = [hit(text) for text in variants]
    assert all(match is not None for match in matches)
    assert {match["rule_id"] for match in matches if match} == {INDUSTRY_FACT_RULE_ID}
    assert {match["dedup_key"] for match in matches if match} == {
        "industry_fact:coreweave:price_risk_hedge:exploring:storage_chip:down"
    }
    assert {match["event_facts"]["stage"] for match in matches if match} == {"exploring"}


def test_coreweave_material_updates_and_responses_remain_deliverable() -> None:
    execution = hit(
        "CoreWeave此前正在探索使用金融衍生品，现已买入看跌期权，对冲存储芯片价格下跌风险。"
    )
    confirmation = hit("CoreWeave官方确认，公司正在探索使用金融衍生品对冲存储芯片价格下跌风险。")
    denial = hit("CoreWeave否认正在探索使用金融衍生品对冲存储芯片价格下跌风险。")
    correction = hit("更正：CoreWeave正在探索使用金融衍生品对冲存储芯片价格下跌风险。")
    terms = hit(
        "CoreWeave正在探索使用金融衍生品对冲存储芯片价格下跌风险，拟议看跌期权名义金额为10亿美元。"
    )
    assert execution is None
    assert confirmation is None
    assert denial is None
    assert correction is None
    assert terms is None


def test_unrelated_coreweave_facts_do_not_share_the_hedge_identity() -> None:
    debt_hedge = hit("CoreWeave正在探索使用利率衍生品，对冲未来债务融资成本上升风险。")
    chip_purchase = hit("CoreWeave与美光签订长期存储芯片采购协议。")
    price_view = hit("CoreWeave预计存储芯片价格可能下跌，但尚未讨论任何对冲工具。")
    assert debt_hedge is None
    assert chip_purchase is None
    assert price_view is None


def test_independent_hbm_fact_and_missing_push_eligibility_fail_open() -> None:
    independent = hit("IBM称客户支出转向存储；SK海力士12层HBM4已量产出货并进入产能爬坡")
    archive = DecisionResult(action="archive", rule_hits=[{"rule_id": "industry_quantified_hardline"}])
    other_rule = DecisionResult(action="push", rule_hits=[{"rule_id": "macro_policy_line"}])
    item = {"title": "CoreWeave正在探索使用金融衍生品对冲存储芯片价格下跌风险。"}
    assert independent is None
    assert industry_fact_dedup_hit(item, archive) is None
    assert industry_fact_dedup_hit(item, other_rule) is None


def main() -> int:
    test_ibm_cross_source_rewordings_share_one_fact_identity()
    test_coreweave_cross_source_rewordings_share_one_fact_identity()
    test_coreweave_material_updates_and_responses_remain_deliverable()
    test_unrelated_coreweave_facts_do_not_share_the_hedge_identity()
    test_independent_hbm_fact_and_missing_push_eligibility_fail_open()
    print("industry fact dedup checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
