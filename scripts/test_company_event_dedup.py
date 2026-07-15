#!/usr/bin/env python3
"""Regression checks for generic company-event identities."""

from __future__ import annotations

from company_event_dedup import COMPANY_EVENT_RULE_ID, company_event_dedup_hit, company_event_dedup_hits
from market_item import DecisionResult


HOLDING_DECISION = DecisionResult(action="push", rule_hits=[{"rule_id": "holding_keyword_immediate_alert"}])
INDUSTRY_DECISION = DecisionResult(action="push", rule_hits=[{"rule_id": "industry_quantified_hardline"}])
COMBINED_DECISION = DecisionResult(
    action="push",
    rule_hits=[{"rule_id": "holding_keyword_immediate_alert"}, {"rule_id": "industry_quantified_hardline"}],
)


def hits(
    title: str,
    decision: DecisionResult = INDUSTRY_DECISION,
    published_at: str = "2026-07-15T10:00:00+00:00",
    **extra: str,
) -> list[dict]:
    return company_event_dedup_hits({"title": title, "published_at": published_at, **extra}, decision)


def hit(title: str, decision: DecisionResult = INDUSTRY_DECISION, **extra: str):
    result = hits(title, decision, **extra)
    return result[0] if result else None


def test_earnings_forecasts_converge_for_any_explicit_company() -> None:
    samples = (
        ("佰维存储：预计上半年净利润70亿元至75亿元，上年同期亏损2.26亿元", HOLDING_DECISION),
        ("佰維存儲公告，預計2026年半年度淨利潤70億元-75億元，同比扭虧", COMBINED_DECISION),
        ("大普微：上半年预盈12亿元-13.5亿元，同比扭亏", INDUSTRY_DECISION),
        ("测试科技：预计2026年半年度净利润4.8亿元-5.6亿元", INDUSTRY_DECISION),
    )
    matches = [hit(text, decision) for text, decision in samples]
    assert all(match is not None for match in matches)
    assert matches[0]["dedup_key"] == matches[1]["dedup_key"]
    assert matches[0]["rule_id"] == COMPANY_EVENT_RULE_ID
    assert matches[2]["event_facts"]["subject"] == "大普微"
    assert matches[3]["event_facts"]["subject"] == "测试科技"
    assert matches[3]["dedup_key"].startswith("company_event:测试科技:earnings:2026-H1:")
    assert matches[0]["dedup_alias_keys"] == [
        "company_event:biwin_storage:earnings_forecast:2026-H1:net_profit"
    ]


def test_xianfeng_joint_venture_rewrites_converge_without_allowlist() -> None:
    first = hit("2连板贤丰控股：拟出资6018万元设立合资公司布局PCB业务", HOLDING_DECISION)
    second = hit(
        "贤丰控股公告称，公司全资子公司贤丰东莞与广东盈硕签署协议，"
        "共同出资设立贤丰盈硕科技有限公司，注册资本1.18亿元。",
        HOLDING_DECISION,
    )
    assert first is not None and second is not None
    assert first["dedup_key"] == second["dedup_key"]
    assert first["event_facts"]["subject"] == "贤丰控股"
    assert first["event_facts"]["event_family"] == "joint_venture"


def test_multi_company_roundups_extract_the_same_fact_set_regardless_of_order() -> None:
    first = hits(
        "深圳存储军团上半年业绩集体爆发",
        COMBINED_DECISION,
        full_text=(
            "佰维存储预计2026年半年度实现归母净利润70亿元至75亿元。"
            "大普微预计2026年上半年净利润12亿元至13.5亿元，实现扭亏。"
        ),
    )
    second = hits(
        "存储企业成绩单亮眼",
        COMBINED_DECISION,
        full_text=(
            "大普微公告，预计上半年净利润12亿元-13.5亿元，同比扭亏。"
            "佰维存储公告称，预计2026年上半年净利润70亿元-75亿元。"
        ),
    )
    assert {item["dedup_key"] for item in first} == {item["dedup_key"] for item in second}
    assert {item["event_facts"]["subject"] for item in first} == {"佰维存储", "大普微"}


def test_versions_periods_and_distinct_events_remain_independent() -> None:
    original = hit("佰维存储预计2026年半年度净利润70亿元-75亿元", HOLDING_DECISION)
    revised = hit("佰维存储上修2026年半年度净利润预告至80亿元-85亿元", HOLDING_DECISION)
    corrected_hits = hits("更正：佰维存储预计2026年半年度净利润为80亿元-85亿元", HOLDING_DECISION)
    different_period = hit("佰维存储预计2026年全年净利润100亿元", HOLDING_DECISION)
    placement = hit("仕佳光子：拟定增募资不超过28亿元，用于高速AWG芯片产能建设", COMBINED_DECISION)
    approved = hit("仕佳光子定增方案获批，拟募集资金不超过28亿元", HOLDING_DECISION)
    assert original is not None and revised is not None and different_period is not None
    assert placement is not None and approved is not None
    assert original["dedup_key"] != revised["dedup_key"]
    assert len(corrected_hits) == 1 and corrected_hits[0]["event_facts"]["subject"] == "佰维存储"
    assert original["dedup_key"] != different_period["dedup_key"]
    assert placement["dedup_key"] != approved["dedup_key"]
    assert revised["event_facts"]["stage"].startswith("revision-")
    assert approved["event_facts"]["stage"] == "approved"


def test_price_rewrites_converge_and_unrelated_events_do_not() -> None:
    price = hit("力积电7月起存储代工报价上调45%")
    price_rewrite = hit("本月起，力积电存储代工涨价10%")
    acquisition = hit("测试科技：拟以5亿元收购目标公司60%股权")
    acquisition_rewrite = hit("测试科技公告称，计划斥资5亿元收购目标公司控股权")
    contract = hit("测试科技：中标5亿元数据中心设备合同")
    assert price is not None and price_rewrite is not None
    assert price["dedup_key"] == price_rewrite["dedup_key"]
    assert acquisition is not None and acquisition_rewrite is not None and contract is not None
    assert acquisition["dedup_key"] == acquisition_rewrite["dedup_key"]
    assert acquisition["dedup_key"] != contract["dedup_key"]


def test_ineligible_ambiguous_or_incomplete_items_fail_open() -> None:
    archive = DecisionResult(action="archive", rule_hits=[{"rule_id": "industry_quantified_hardline"}])
    macro = DecisionResult(action="push", rule_hits=[{"rule_id": "macro_policy_line"}])
    item = {"title": "测试科技预计2026年半年度净利润70亿元", "published_at": "2026-07-15"}
    assert company_event_dedup_hit(item, archive) is None
    assert company_event_dedup_hit(item, macro) is None
    assert hit("测试科技股价上涨5%") is None
    assert hit("某公司预计上半年业绩增长") is None
    assert hit("公司拟开展一项新业务") is None
    assert hit(
        "存储芯片概念回落，佰维存储跌超8%。消息面上，德明利公告称，"
        "预计2026年半年度净利润57亿元-65亿元。"
    )["event_facts"]["subject"] == "德明利"


def test_event_priority_and_subject_noise_do_not_create_wrong_identities() -> None:
    penalty = hit(
        "巨力索具：收到行政处罚决定书，因未准确披露商业航天订单金额被罚450万元"
    )
    subsidiary_price = hits(
        "民德电子：广微集成近期对产品价格进行调整，全系产品涨价15%-20%"
    )
    assert penalty is not None
    assert penalty["event_facts"]["event_family"] == "regulatory"
    assert {item["event_facts"]["subject"] for item in subsidiary_price} == {"民德电子"}
    assert {item["event_facts"]["event_family"] for item in subsidiary_price} == {"price_change"}


def main() -> int:
    test_earnings_forecasts_converge_for_any_explicit_company()
    test_xianfeng_joint_venture_rewrites_converge_without_allowlist()
    test_multi_company_roundups_extract_the_same_fact_set_regardless_of_order()
    test_versions_periods_and_distinct_events_remain_independent()
    test_price_rewrites_converge_and_unrelated_events_do_not()
    test_ineligible_ambiguous_or_incomplete_items_fail_open()
    test_event_priority_and_subject_noise_do_not_create_wrong_identities()
    print("company event dedup checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
