#!/usr/bin/env python3
"""Regression checks for source-neutral trade-friction early warning."""

from __future__ import annotations

from market_item import NormalizedMarketItem
from trade_friction import RULE_ID, trade_friction_rule


def variants(text: str) -> tuple[NormalizedMarketItem, NormalizedMarketItem]:
    return (
        NormalizedMarketItem(
            source="ustr_press_releases",
            source_category="official_policy",
            publisher_role="government_official",
            collector="trade_policy_monitor",
            content_type="official_policy",
            title=text,
        ),
        NormalizedMarketItem(
            source="cls_telegraph_api",
            source_category="news_media",
            publisher_role="news_media",
            collector="china_finance_media_monitor",
            content_type="article",
            title=text,
        ),
    )


def test_section_301_public_comment_pushes_across_sources() -> None:
    text = "USTR seeks public comment on a proposed Section 301 tariff action concerning China semiconductor imports."
    rules = [trade_friction_rule(item) for item in variants(text)]
    assert all(rule is not None for rule in rules)
    assert {rule["decision_action"] for rule in rules if rule} == {"push"}
    assert {rule["rule_id"] for rule in rules if rule} == {RULE_ID}


def test_eu_china_ev_anti_subsidy_investigation_pushes() -> None:
    text = "European Commission initiates an anti-subsidy investigation into battery electric vehicles from China."
    rule = trade_friction_rule({"title": text})
    assert rule is not None
    assert rule["decision_action"] == "push"
    assert rule["corridors"] == ["china_eu"]
    assert "汽车/电动车" in rule["affected_sectors"]


def test_mofcom_us_entity_control_pushes() -> None:
    text = "商务部新闻发言人宣布将10家美国实体列入出口管制管控名单，并表示将采取必要措施。"
    rule = trade_friction_rule({"title": text})
    assert rule is not None
    assert rule["decision_action"] == "push"
    assert rule["corridors"] == ["china_us"]


def test_explicit_trade_friction_is_an_early_push() -> None:
    text = "Netherlands presses China talks on Nexperia and ASML trade frictions."
    rule = trade_friction_rule({"title": text})
    assert rule is not None
    assert rule["decision_action"] == "push"
    assert rule["corridors"] == ["china_eu"]


def test_existing_tariff_analysis_is_daily_without_new_action_stage() -> None:
    text = "EU tariffs push Chinese carmakers to seek deeper ties in Europe."
    rule = trade_friction_rule({"title": text})
    assert rule is not None
    assert rule["decision_action"] == "daily"


def test_cordial_bilateral_meeting_does_not_match() -> None:
    text = "中国商务部与美国商会代表团会面，就中美经贸关系和企业合作交换意见。"
    assert trade_friction_rule({"title": text}) is None


def test_unrelated_third_country_trade_case_does_not_match() -> None:
    text = "Korea issues a final antidumping determination on seamless copper tubes from Thailand."
    assert trade_friction_rule({"title": text}) is None


def test_historical_background_without_current_action_is_daily_not_push() -> None:
    text = "The report reviews U.S. tariffs on China semiconductors and the long-running trade dispute."
    rule = trade_friction_rule({"title": text})
    assert rule is not None
    assert rule["decision_action"] == "daily"


def test_routine_antidumping_administration_does_not_alert() -> None:
    text = (
        "U.S. Commerce Department: Certain Aluminum Foil From the People's Republic of China: "
        "Preliminary Results of Antidumping Duty Administrative Review; 2024-2025."
    )
    assert trade_friction_rule({"title": text}) is None


def test_postponement_of_preliminary_determination_does_not_push() -> None:
    text = (
        "U.S. Commerce Department announces postponement of preliminary determination in the "
        "countervailing duty investigation of imports from China."
    )
    assert trade_friction_rule({"title": text}) is None


def test_unrelated_sentences_cannot_form_a_trade_alert() -> None:
    text = (
        "China announces a temporary helium export control for global supply reasons. "
        "Separately, the United States and European Union discussed human-rights policy."
    )
    assert trade_friction_rule({"title": text}) is None


def test_non_trade_diplomatic_objection_does_not_alert() -> None:
    text = "China firmly opposes concerns raised by the United States and European Union about ethnic policy."
    assert trade_friction_rule({"title": text}) is None


def test_routine_section_337_final_determination_does_not_alert() -> None:
    text = "U.S. ITC issues a Section 337 final determination involving display glass products from China."
    assert trade_friction_rule({"title": text}) is None


def test_new_trade_remedy_investigation_pushes() -> None:
    text = (
        "United States International Trade Commission: institution of investigations into "
        "antidumping and countervailing duties on glyphosate imports from China."
    )
    rule = trade_friction_rule({"title": text})
    assert rule is not None
    assert rule["decision_action"] == "push"


def main() -> int:
    test_section_301_public_comment_pushes_across_sources()
    test_eu_china_ev_anti_subsidy_investigation_pushes()
    test_mofcom_us_entity_control_pushes()
    test_explicit_trade_friction_is_an_early_push()
    test_existing_tariff_analysis_is_daily_without_new_action_stage()
    test_cordial_bilateral_meeting_does_not_match()
    test_unrelated_third_country_trade_case_does_not_match()
    test_historical_background_without_current_action_is_daily_not_push()
    test_routine_antidumping_administration_does_not_alert()
    test_postponement_of_preliminary_determination_does_not_push()
    test_unrelated_sentences_cannot_form_a_trade_alert()
    test_non_trade_diplomatic_objection_does_not_alert()
    test_routine_section_337_final_determination_does_not_alert()
    test_new_trade_remedy_investigation_pushes()
    print("trade friction checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
