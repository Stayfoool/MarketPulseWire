#!/usr/bin/env python3
"""Regression checks for the passive unified decision engine."""

from __future__ import annotations

from decision_engine import decide_market_item
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


def test_short_industry_hardline_uses_event_first_decision() -> None:
    item = {
        "source": "trendforce_page",
        "page_source": "semi_prnewswire_semiconductors",
        "title": "SEMI Raises 2026 Front-End Equipment Forecast to $152.2 Billion",
        "summary": "SEMI raised growth from 16.5% to 23.5%.",
    }
    decision = decide_market_item(item, holdings=[])
    assert decision.action == "push"
    assert decision.rule_hits[0]["rule_id"] == "event_first_hardline"
    assert decision.audit_json["source_stage"] == "industry_hardline_event_first"
    assert decision.rule_hits[0]["raw"]["event_first_policy"] == "research_industry_media_short_hard_variable"


def test_long_semianalysis_uses_source_priority_decision() -> None:
    item = {
        "source": "semianalysis",
        "title": "SemiAnalysis weekly AI infrastructure report",
        "summary": "Research note on AI accelerator supply chains. " * 20,
    }
    decision = decide_market_item(item, holdings=[])
    assert decision.action == "push"
    assert decision.rule_hits[0]["rule_id"] == "source_priority_semianalysis"
    assert decision.rule_hits[0]["raw"]["source_priority_override"] == "semianalysis"


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
    test_short_industry_hardline_uses_event_first_decision()
    test_long_semianalysis_uses_source_priority_decision()
    test_macro_primary_text_decides_push_without_raw_event_marker()
    test_macro_secondary_match_becomes_limited_judgement_candidate()
    test_no_deterministic_match_archives_for_legacy_gate_to_continue()
    print("decision engine checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
