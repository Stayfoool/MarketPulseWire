#!/usr/bin/env python3
"""Regression checks for source-neutral topic and hard-variable rules."""

from __future__ import annotations

from industry_hardline import (
    apply_hardline_review_override,
    explain_hardline,
    industry_topic_hard_variable_rule,
    topic_hard_variable_match,
)


SEMI_EQUIPMENT_CASE = {
    "title": "SEMI Raises 2026 Front-End Equipment Forecast to $152.2 Billion",
    "summary": "SEMI raised semiconductor equipment growth from 16.5% to 23.5%.",
}


def test_same_topic_and_hard_variable_match_across_sources() -> None:
    sources = (
        "trendforce_page",
        "sina_flash",
        "cls_telegraph_api",
        "company_blog",
        "future_media",
    )
    rules = [industry_topic_hard_variable_rule(source, SEMI_EQUIPMENT_CASE) for source in sources]
    assert all(rule is not None for rule in rules)
    assert {tuple(rule["claim_topics"]) for rule in rules if rule} == {("半导体设备/材料",)}
    assert {tuple(rule["hard_variable_types"]) for rule in rules if rule} == {("预测调整",)}


def test_review_override_is_content_based() -> None:
    review = {
        "importance": "medium",
        "push_now": False,
        "affected_targets": [],
        "reason": "模型认为需要日报观察。",
        "raw": {},
    }
    updated = apply_hardline_review_override("sina_flash", SEMI_EQUIPMENT_CASE, review)
    assert updated["importance"] == "high"
    assert updated["push_now"] is True
    assert updated["industry_hardline_override"] is True
    assert "来源分类不参与重要性判断" in updated["reason"]
    assert updated["raw"]["industry_topic_hard_variable"]["rule_id"] == "industry_quantified_hardline"


def test_source_identity_without_hard_variable_does_not_match() -> None:
    item = {
        "title": "SemiAnalysis weekly AI infrastructure report",
        "summary": "Research note on AI accelerator supply chains.",
    }
    assert topic_hard_variable_match(item) == {}
    assert industry_topic_hard_variable_rule("semianalysis", item) is None


def test_topic_and_hard_variable_are_both_required() -> None:
    topic_only = {"title": "AI infrastructure and semiconductor industry overview"}
    hard_variable_only = {"title": "Company raises 2027 revenue forecast by 20%"}
    assert topic_hard_variable_match(topic_only) == {}
    assert topic_hard_variable_match(hard_variable_only) == {}


def test_topics_and_hard_variables_cannot_be_combined_across_sections() -> None:
    item = {
        "title": "菜鸟战略落定：国内供应链划归阿里电商",
        "full_text": (
            "跨境订单团队划转至阿里电商事业群。<br />"
            "页面推荐：AI、半导体、存储和机器人行业周报。"
        ),
    }
    assert topic_hard_variable_match(item) == {}


def test_quantified_data_center_investment_is_a_hard_variable() -> None:
    item = {
        "title": "Meta commits an additional $40 billion investment in its Louisiana data center campus."
    }
    rule = industry_topic_hard_variable_rule("jin10_rsshub_important", item)
    assert rule is not None
    assert "AI基础设施" in rule["claim_topics"]
    assert "资本开支/投资" in rule["hard_variable_types"]
    assert "$40 billion" in rule["quantified_evidence"]


def test_ai_debt_issuance_is_not_treated_as_capex_investment() -> None:
    text = "Microsoft issued $40 billion of bonds to finance AI infrastructure."
    for source in ("cls_telegraph_api", "wallstreetcn_global"):
        assert industry_topic_hard_variable_rule(source, {"title": text}) is None


def test_investor_question_is_not_treated_as_an_investment_hard_variable() -> None:
    item = {
        "title": "公司参与字节、阿里全球数据中心建设吗？锐捷网络回应",
        "summary": "有投资者向锐捷网络（301165.SZ）提问，公司有参与全球数据中心建设吗？",
    }
    assert topic_hard_variable_match(item) == {}


def test_expansion_before_capacity_is_detected_in_the_same_sentence() -> None:
    item = {
        "title": "TSMC plans a 30x expansion of photonic integrated circuit capacity to 25,000 wafers by 2028."
    }
    rule = industry_topic_hard_variable_rule("news_media", item)
    assert rule is not None
    assert "光互联/CPO" in rule["claim_topics"]
    assert "产能/产量" in rule["hard_variable_types"]


def test_unquantified_roadmap_shift_is_a_hard_variable() -> None:
    item = {
        "title": "DeepSeek and Zhipu develop custom ASICs to bypass NVIDIA GPUs",
        "summary": "The AI accelerator roadmap is shifting away from merchant chips.",
    }
    rule = industry_topic_hard_variable_rule("news_media", item)
    assert rule is not None
    assert "AI基础设施" in rule["claim_topics"]
    assert "时间表/技术路线" in rule["hard_variable_types"]
    assert rule["quantified_evidence"] == []


def test_multi_topic_recap_preserves_multiple_hard_variables() -> None:
    item = {
        "title": "Semiconductor and AI infrastructure semi recap",
        "summary": (
            "PCB supply shortage is projected to persist until 2028. "
            "TSMC plans a 30x PIC capacity expansion to 25,000 wafers per month by 2028. "
            "NAND price hike reaches 5x while equipment suppliers start emergency investment. "
            "DeepSeek develops custom ASICs to bypass NVIDIA GPUs."
        ),
    }
    rule = industry_topic_hard_variable_rule("sina_flash", item)
    assert rule is not None
    assert {"AI基础设施", "半导体", "存储/HBM", "光互联/CPO", "PCB/电子制造"}.issubset(
        set(rule["claim_topics"])
    )
    assert {"供需缺口/瓶颈", "价格", "产能/产量", "资本开支/投资", "时间表/技术路线"}.issubset(
        set(rule["hard_variable_types"])
    )
    assert "30x" in rule["quantified_evidence"]
    assert "25,000 wafers" in rule["quantified_evidence"]


def test_explain_hardline_describes_content_not_source_family() -> None:
    note = explain_hardline(
        "digitimes_tw_semiconductors_components",
        ("AI server semiconductor equipment forecast raised 20%",),
    )
    assert "重点主题" in note
    assert "DIGITIMES" not in note


def main() -> int:
    test_same_topic_and_hard_variable_match_across_sources()
    test_review_override_is_content_based()
    test_source_identity_without_hard_variable_does_not_match()
    test_topic_and_hard_variable_are_both_required()
    test_topics_and_hard_variables_cannot_be_combined_across_sections()
    test_quantified_data_center_investment_is_a_hard_variable()
    test_ai_debt_issuance_is_not_treated_as_capex_investment()
    test_investor_question_is_not_treated_as_an_investment_hard_variable()
    test_expansion_before_capacity_is_detected_in_the_same_sentence()
    test_unquantified_roadmap_shift_is_a_hard_variable()
    test_multi_topic_recap_preserves_multiple_hard_variables()
    test_explain_hardline_describes_content_not_source_family()
    print("industry hardline checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
