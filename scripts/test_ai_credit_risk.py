#!/usr/bin/env python3
"""Regression checks for deterministic AI credit and funding-risk monitoring."""

from __future__ import annotations

from ai_credit_risk import ai_credit_risk_rule
from decision_engine import decide_market_item


COHORT_CASE = {
    "title": "AI infrastructure bond supply tests market capacity",
    "summary": (
        "NVIDIA, SpaceX and Amazon issued a combined $75 billion of bonds for AI infrastructure. "
        "The market struggled to absorb the supply, the new bonds weakened in secondary trading, "
        "and Amazon financing costs rose."
    ),
    "published_at": "2026-07-13T09:00:00+08:00",
}


def test_production_anchor_pushes_with_separate_evidence_families() -> None:
    rule = ai_credit_risk_rule("wallstreetcn_global", COHORT_CASE)
    assert rule is not None
    assert rule["decision_action"] == "push"
    assert rule["issuer_scope"] == "cohort"
    assert set(rule["issuers"]) == {"nvidia", "spacex", "amazon"}
    assert {"weak_absorption", "weak_secondary_performance", "higher_funding_cost"}.issubset(rule["stress_signals"])
    assert rule["extraction_mode"] == "deterministic_local_window"


def test_exact_chinese_production_anchor_has_three_evidence_families() -> None:
    text = (
        "科技巨头英伟达、SpaceX和亚马逊近期合计发行750亿美元债券为AI基础设施融资，"
        "但市场消化吃力，新债二级市场走弱，亚马逊融资成本上升。"
    )
    rule = ai_credit_risk_rule("jin10_rsshub_important", {"title": text})
    assert rule is not None
    assert rule["decision_action"] == "push"
    assert {"weak_absorption", "weak_secondary_performance", "higher_funding_cost"}.issubset(rule["stress_signals"])


def test_same_content_is_source_neutral_and_deduplicates() -> None:
    variants = (
        {**COHORT_CASE, "source": "wallstreetcn_global", "source_category": "news_media", "publisher_role": "news_media"},
        {**COHORT_CASE, "source": "cls_telegraph_api", "source_category": "news_media", "publisher_role": "news_media"},
    )
    decisions = [decide_market_item(item, holdings=[]) for item in variants]
    assert {decision.action for decision in decisions} == {"push"}
    assert {decision.dedup["dedup_key"] for decision in decisions} == {decisions[0].dedup["dedup_key"]}
    assert all(any(rule["rule_id"] == "ai_hyperscaler_credit_stress" for rule in decision.rule_hits) for decision in decisions)


def test_ordinary_large_issuance_is_daily_and_not_industry_push() -> None:
    item = {
        "source": "cls_telegraph_api",
        "title": "Microsoft issued $40 billion of bonds to finance AI infrastructure.",
        "published_at": "2026-07-14T08:00:00+08:00",
    }
    decision = decide_market_item(item, holdings=[])
    assert decision.action == "daily"
    assert [rule["rule_id"] for rule in decision.rule_hits] == ["ai_hyperscaler_credit_stress"]


def test_one_stress_family_remains_daily() -> None:
    cases = (
        "Oracle issued bonds for AI data centers, but market absorption was difficult.",
        "Meta debt for AI infrastructure increased leverage and put free cash flow under pressure.",
    )
    for text in cases:
        rule = ai_credit_risk_rule("sina_flash", {"title": text})
        assert rule is not None
        assert rule["decision_action"] == "daily"
        assert len(rule["stress_signals"]) == 1


def test_two_independent_market_stresses_push() -> None:
    rule = ai_credit_risk_rule(
        "jin10_rsshub_important",
        {"title": "Amazon issued bonds for AI infrastructure; investor demand was weak and the new bonds traded below issue price."},
    )
    assert rule is not None
    assert rule["decision_action"] == "push"
    assert {"weak_absorption", "weak_secondary_performance"}.issubset(rule["stress_signals"])


def test_absorption_and_fcf_pressure_for_same_issuer_push() -> None:
    item = {
        "title": "AI 数据中心债务规模扩大。",
        "summary": (
            "亚马逊250亿美元债券发行遭遇不同寻常的冷遇。"
            "亚马逊自由现金流转为负值。"
        ),
    }
    rule = ai_credit_risk_rule("cls_telegraph_api", item)
    assert rule is not None
    assert rule["decision_action"] == "push"
    assert rule["issuers"] == ["amazon"]
    assert {"weak_absorption", "leverage_or_fcf_pressure"}.issubset(rule["stress_signals"])


def test_hard_financing_and_capex_outcomes_push() -> None:
    cases = (
        "OpenAI postponed its AI infrastructure bond financing because investor demand was weak.",
        "Oracle cut AI capital expenditure after liquidity pressure forced it to scale back investment.",
    )
    for text in cases:
        rule = ai_credit_risk_rule("wallstreetcn_global", {"title": text})
        assert rule is not None
        assert rule["decision_action"] == "push"
        assert rule["hard_outcomes"]


def test_commentary_rumor_equity_and_non_ai_debt_do_not_match() -> None:
    cases = (
        "Investors debate whether the AI debt bubble can continue, with no issuer-specific event.",
        "A rumor says Microsoft may reportedly issue bonds for AI infrastructure.",
        "SK hynix shares fell 15% after a bearish report about memory chips.",
        "Microsoft issued bonds to refinance its general corporate debt.",
        "Oracle AI debt remains elevated while demand for its AI services was weak.",
    )
    for text in cases:
        assert ai_credit_risk_rule("news_media", {"title": text}) is None


def test_repeated_wording_in_one_family_cannot_combine_into_push() -> None:
    item = {
        "title": "Meta issued bonds for AI data centers.",
        "summary": "Market absorption was difficult. Investors also said absorption was weak and difficult.",
    }
    rule = ai_credit_risk_rule("cls_telegraph_api", item)
    assert rule is not None
    assert rule["decision_action"] == "daily"
    assert rule["stress_signals"] == ["weak_absorption"]


def test_stress_families_from_different_issuers_cannot_combine() -> None:
    item = {
        "title": "AI infrastructure debt update",
        "summary": (
            "Amazon issued bonds for AI infrastructure and investor demand was weak. "
            "Meta debt for AI infrastructure increased leverage and pressured free cash flow."
        ),
    }
    rule = ai_credit_risk_rule("news_media", item)
    assert rule is not None
    assert rule["decision_action"] == "daily"
    assert rule["issuer_scope"] == "single"


def main() -> int:
    test_production_anchor_pushes_with_separate_evidence_families()
    test_exact_chinese_production_anchor_has_three_evidence_families()
    test_same_content_is_source_neutral_and_deduplicates()
    test_ordinary_large_issuance_is_daily_and_not_industry_push()
    test_one_stress_family_remains_daily()
    test_two_independent_market_stresses_push()
    test_absorption_and_fcf_pressure_for_same_issuer_push()
    test_hard_financing_and_capex_outcomes_push()
    test_commentary_rumor_equity_and_non_ai_debt_do_not_match()
    test_repeated_wording_in_one_family_cannot_combine_into_push()
    test_stress_families_from_different_issuers_cannot_combine()
    print("AI credit-risk checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
