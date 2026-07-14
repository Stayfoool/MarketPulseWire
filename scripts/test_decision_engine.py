#!/usr/bin/env python3
"""Regression checks for the passive unified decision engine."""

from __future__ import annotations

from decision_engine import _decision_from_rule, decide_market_item
from market_item import NormalizedMarketItem


GREEN = {"symbol": "688017.SH", "name": "绿的谐波", "full_name": "绿的谐波传动科技股份有限公司", "aliases": []}


def test_direct_holding_bank_rule_matches_legacy_push_rule() -> None:
    item = NormalizedMarketItem(
        source="sina_stock_news",
        content_type="article",
        title="高盛看衰绿地谐波：488股价 vs 138目标价",
        summary="高盛-绿的谐波(688017)：目标价显著低于现价。",
        symbols=["688017.SH"],
    )
    decision = decide_market_item(item, holdings=[GREEN])
    assert decision.action == "push"
    assert decision.should_push is True
    assert decision.importance == "high"
    assert decision.rule_hits[0]["rule_id"] == "investment_bank_rating_target_direct_holding"
    assert decision.rule_hits[0]["target_gap"]["target_price"] == 138.0
    legacy = decision.legacy_push_fields("should_push_now")
    assert legacy["should_push_now"] is True
    assert legacy["raw"]["rule_hits"][0]["rule_id"] == "investment_bank_rating_target_direct_holding"


def test_international_bank_theme_rule_reports_delivery_dedup_metadata_only() -> None:
    item = {
        "source": "cls_telegraph_api",
        "title": "高盛发布《投资策略：做多中国 AI 价值链》",
        "summary": (
            "高盛认为中国 AI 公司市值与市场空间严重错配，资金正从韩国 AI 交易出现结构性资本轮动，"
            "建议做多中国 AI 价值链，覆盖算力、半导体和数据中心电力。"
        ),
        "published_at": "2026-07-09T03:57:30+00:00",
    }
    decision = decide_market_item(item, holdings=[])
    assert decision.action == "push"
    assert decision.rule_hits[0]["rule_id"] == "international_bank_theme_strategy"
    assert decision.dedup["rule_alert_reservation_required"] is True
    assert decision.dedup["dedup_key"].startswith("ib_theme:")
    assert "reservation stays in the delivery layer" in decision.dedup["note"]


def test_industry_hard_variable_is_source_neutral() -> None:
    item = {
        "source": "trendforce_page",
        "page_source": "semi_prnewswire_semiconductors",
        "title": "SEMI Raises 2026 Front-End Equipment Forecast to $152.2 Billion",
        "summary": "SEMI raised growth from 16.5% to 23.5%.",
    }
    decision = decide_market_item(item, holdings=[])
    assert decision.action == "push"
    assert decision.rule_hits[0]["rule_id"] == "industry_quantified_hardline"
    assert decision.audit_json["source_stage"] == "industry_topic_hard_variable"
    sina_decision = decide_market_item({**item, "source": "sina_flash"}, holdings=[])
    assert sina_decision.action == "push"
    assert sina_decision.rule_hits[0]["hard_variable_types"] == decision.rule_hits[0]["hard_variable_types"]


def test_transport_metadata_does_not_change_content_importance() -> None:
    text = "HBM supply shortage will persist until 2028 and prices are projected to double."
    variants = (
        NormalizedMarketItem(
            source="trendforce_page",
            source_category="research_industry_media",
            publisher_role="research_publisher",
            content_type="article",
            title=text,
        ),
        NormalizedMarketItem(
            source="sina_flash",
            source_category="news_media",
            publisher_role="news_media",
            content_type="flash",
            title=text,
        ),
        NormalizedMarketItem(
            source="company_blog",
            source_category="official_company",
            publisher_role="official_company",
            content_type="official_news",
            title=text,
        ),
    )
    decisions = [decide_market_item(item, holdings=[]) for item in variants]
    assert {decision.action for decision in decisions} == {"push"}
    assert {decision.importance for decision in decisions} == {"high"}
    assert {decision.rule_hits[0]["rule_id"] for decision in decisions} == {"industry_quantified_hardline"}


def test_semianalysis_source_identity_alone_does_not_raise_importance() -> None:
    item = {
        "source": "semianalysis",
        "title": "SemiAnalysis weekly AI infrastructure report",
        "summary": "Research note on AI accelerator supply chains. " * 20,
    }
    decision = decide_market_item(item, holdings=[])
    assert decision.action == "archive"
    assert decision.importance == "unknown"
    assert decision.rule_hits == []


def test_news_media_attributed_semianalysis_hard_variable_pushes() -> None:
    item = NormalizedMarketItem(
        source="sina_flash",
        source_category="news_media",
        publisher_role="news_media",
        content_type="flash",
        title="SemiAnalysis创始人Dylan Patel表示，存储存在结构性短缺，CPO落地推迟至2028年。",
    )
    decision = decide_market_item(item, holdings=[])
    assert decision.action == "push"
    assert decision.importance == "high"
    assert decision.rule_hits[0]["rule_id"] == "attributed_research_hard_variable"
    assert decision.rule_hits[0]["transport_source"] == "sina_flash"
    assert decision.dedup["dedup_key"].startswith("attributed_research:semianalysis:")


def test_holding_and_attributed_research_rules_are_preserved_together() -> None:
    item = NormalizedMarketItem(
        source="sina_stock_news",
        source_category="portfolio_stock_news",
        publisher_role="news_media",
        content_type="portfolio_news",
        title="SemiAnalysis表示，绿的谐波获得CPO设备订单，交付规模预计增长20%。",
    )
    decision = decide_market_item(item, holdings=[GREEN])
    assert decision.action == "push"
    assert [rule["rule_id"] for rule in decision.rule_hits] == [
        "holding_keyword_immediate_alert",
        "attributed_research_hard_variable",
        "industry_quantified_hardline",
    ]
    assert decision.audit_json["source_stage"] == "combined_content_rules"
    assert decision.dedup["dedup_key"].startswith("attributed_research:semianalysis:")


def test_macro_primary_text_decides_push_without_raw_event_marker() -> None:
    item = {"source": "news_media", "title": "美国CPI数据大幅低于市场预期，2年期美债收益率大跌"}
    decision = decide_market_item(item, holdings=[])
    assert decision.action == "push"
    assert decision.rule_hits[0]["rule_id"] == "macro_policy_line"
    assert decision.rule_hits[0]["macro_policy_line"]["tier"] == "primary"


def test_macro_secondary_match_becomes_limited_judgement_candidate() -> None:
    item = {"source": "news_media", "title": "美国ADP就业人数大幅不及预期，2年期美债收益率大跌8个基点"}
    decision = decide_market_item(item, holdings=[])
    assert decision.action == "daily"
    assert decision.importance == "medium"
    assert decision.need_limited_llm_judgement is True
    assert decision.candidate_rules[0]["rule_id"] == "macro_policy_line"
    assert decision.candidate_rules[0]["macro_policy_line"]["tier"] == "secondary_major"


def test_trade_friction_rule_is_source_neutral() -> None:
    text = "USTR seeks public comment on proposed Section 301 tariffs covering China semiconductor imports."
    variants = (
        NormalizedMarketItem(
            source="federal_register_trade_policy",
            source_category="official_policy",
            publisher_role="government_official",
            collector="trade_policy_monitor",
            content_type="official_policy",
            title=text,
        ),
        NormalizedMarketItem(
            source="digitimes_en_daily",
            source_category="research_industry_media",
            publisher_role="news_media",
            collector="rss_monitor",
            content_type="article",
            title=text,
        ),
    )
    decisions = [decide_market_item(item, holdings=[]) for item in variants]
    assert {decision.action for decision in decisions} == {"push"}
    assert {decision.rule_hits[0]["rule_id"] for decision in decisions} == {"trade_friction_escalation"}


def test_weak_trade_tension_becomes_daily() -> None:
    decision = decide_market_item(
        {"source": "news_media", "title": "EU tariffs push Chinese carmakers to seek deeper ties in Europe."},
        holdings=[],
    )
    assert decision.action == "daily"
    assert decision.rule_hits[0]["rule_id"] == "trade_friction_escalation"
    assert decision.need_limited_llm_judgement is True


def test_rule_business_action_does_not_override_delivery_action() -> None:
    push_decision = _decision_from_rule(
        {
            "rule_id": "business_action_example",
            "action": "daily",
            "push_now": True,
            "importance": "high",
            "reason": "业务动作字段与投递动作字段相互独立。",
        },
        audit_json={},
        source_stage="test",
    )
    daily_decision = _decision_from_rule(
        {
            "rule_id": "explicit_delivery_action_example",
            "decision_action": "daily",
            "push_now": False,
            "importance": "medium",
            "reason": "显式投递动作使用独立字段。",
        },
        audit_json={},
        source_stage="test",
    )
    assert push_decision.action == "push"
    assert daily_decision.action == "daily"


def test_no_deterministic_match_archives_for_legacy_gate_to_continue() -> None:
    item = {"source": "news_media", "title": "普通行业早报", "summary": "多家公司发布常规动态。"}
    decision = decide_market_item(item, holdings=[])
    assert decision.action == "archive"
    assert decision.rule_hits == []
    assert decision.need_limited_llm_judgement is True
    assert decision.audit_json["source_stage"] == "no_deterministic_match"


def main() -> int:
    test_direct_holding_bank_rule_matches_legacy_push_rule()
    test_international_bank_theme_rule_reports_delivery_dedup_metadata_only()
    test_industry_hard_variable_is_source_neutral()
    test_transport_metadata_does_not_change_content_importance()
    test_semianalysis_source_identity_alone_does_not_raise_importance()
    test_news_media_attributed_semianalysis_hard_variable_pushes()
    test_holding_and_attributed_research_rules_are_preserved_together()
    test_macro_primary_text_decides_push_without_raw_event_marker()
    test_macro_secondary_match_becomes_limited_judgement_candidate()
    test_trade_friction_rule_is_source_neutral()
    test_weak_trade_tension_becomes_daily()
    test_rule_business_action_does_not_override_delivery_action()
    test_no_deterministic_match_archives_for_legacy_gate_to_continue()
    print("decision engine checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
