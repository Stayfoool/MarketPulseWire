#!/usr/bin/env python3
"""Regression checks for deterministic push rules."""

from __future__ import annotations

import push_rules
from push_rules import (
    apply_event_push_rules,
    direct_holding_hard_variable_rule,
    first_matching_push_rule,
    international_bank_theme_strategy_rule,
    official_company_hard_variable_rule,
)


GREEN = {"symbol": "688017.SH", "name": "绿的谐波", "full_name": "绿的谐波传动科技股份有限公司", "aliases": []}
SIFANGDA = {"symbol": "300179.SZ", "name": "四方达", "full_name": "", "aliases": []}


def test_investment_bank_target_price_rule_for_direct_holding() -> None:
    item = {
        "title": "高盛看衰绿地谐波：488股价 vs 138目标价",
        "summary": "高盛-绿的谐波(688017)：国内人形机器人需求攀升，产能扩张在即，测试当前定价的估值敏感度。",
    }
    rule = first_matching_push_rule(source="sina_stock_news", item=item, holdings=[GREEN], symbols={"688017.SH"})
    assert rule is not None
    assert rule["rule_id"] == "investment_bank_rating_target_direct_holding"
    assert rule["should_push"] is True
    assert rule["importance"] == "high"
    assert rule["target_gap"]["target_price"] == 138.0
    assert rule["target_gap"]["current_price"] == 488.0
    assert "LLM" in rule["reason"]


def test_event_rule_overrides_model_skip() -> None:
    event = {
        "source": "sina_stock_news",
        "title": "高盛看衰绿地谐波：488股价 vs 138目标价",
        "summary": "高盛-绿的谐波(688017)：目标价显著低于现价。",
        "full_text": "",
        "raw": {},
    }
    model_analysis = {
        "importance": "medium",
        "core_content": "高盛给予绿的谐波中性评级，目标价显著低于现价。",
        "push_decision": {"should_push": False, "reason": "模型认为已有预期。"},
    }
    updated = apply_event_push_rules(event, model_analysis, holdings=[GREEN], symbols={"688017.SH"})
    assert updated["importance"] == "high"
    assert updated["push_decision"]["should_push"] is True
    assert updated["push_decision"]["source"] == "rule"
    assert updated["rule_forced_push"] is True


def test_international_bank_theme_strategy_rule() -> None:
    item = {
        "title": "高盛发布《投资策略：做多中国 AI 价值链》",
        "summary": (
            "高盛认为中国 AI 公司市值与市场空间严重错配，资金正从韩国 AI 交易出现结构性资本轮动，"
            "建议做多中国 AI 价值链，覆盖算力、半导体和数据中心电力。"
        ),
        "published_at": "2026-07-09T03:57:30+00:00",
    }
    rule = international_bank_theme_strategy_rule(source="cls_telegraph_api", item=item, holdings=[])
    assert rule is not None
    assert rule["rule_id"] == "international_bank_theme_strategy"
    assert rule["should_push"] is True
    assert rule["importance"] == "high"
    assert rule["action"] == "做多"
    assert rule["evidence_score"] >= 4
    assert rule["source_tier"] == "媒体明确署名转述"
    assert rule["dedup_key"].startswith("ib_theme:")


def test_international_bank_theme_strategy_requires_action_and_evidence() -> None:
    no_action = international_bank_theme_strategy_rule(
        source="cls_telegraph_api",
        item={"title": "高盛长期看好 AI", "summary": "认为 AI 估值有上行空间。"},
        holdings=[],
    )
    assert no_action is None
    weak_evidence = international_bank_theme_strategy_rule(
        source="cls_telegraph_api",
        item={"title": "高盛做多 AI", "summary": "认为 AI 估值有上行空间。"},
        holdings=[],
    )
    assert weak_evidence is None


def test_international_bank_rotation_strategy_extracts_both_legs_across_sources() -> None:
    variants = (
        (
            "cls_telegraph_api",
            {
                "title": "摩根士丹利提示投资者从芯片股轮动到 AI 云服务商和超大规模云厂商",
                "summary": "该行给出最新行业配置策略。",
                "published_at": "2026-07-15T01:00:00+00:00",
            },
        ),
        (
            "alphabstract_summaries",
            {
                "title": "Morgan Stanley recommends investors rotate from chip stocks into AI cloud providers and hyperscalers",
                "summary": "The bank published its current sector allocation view.",
                "published_at": "2026-07-16T01:00:00+00:00",
            },
        ),
    )
    rules = [
        international_bank_theme_strategy_rule(source=source, item=item, holdings=[])
        for source, item in variants
    ]
    assert all(rule is not None for rule in rules)
    assert {rule["action"] for rule in rules if rule} == {"配置轮动"}
    assert {tuple(rule["from_themes"]) for rule in rules if rule} == {("semiconductor_equities",)}
    assert {tuple(rule["to_themes"]) for rule in rules if rule} == {("ai_cloud_hyperscalers",)}
    assert {rule["dedup_key"] for rule in rules if rule} == {rules[0]["dedup_key"]}
    assert rules[0]["dedup_key"].startswith("ib_rotation:")
    assert rules[0]["strategy_type"] == "rotation"
    assert rules[0]["retrospective"] is False
    assert rules[0]["evidence_quotes"]
    assert rules[0]["affected_targets"][0] == "芯片股 -> AI 云服务商/超大规模云"

    reverse = international_bank_theme_strategy_rule(
        source="cls_telegraph_api",
        item={"title": "摩根士丹利建议投资者从 AI 云服务商转向芯片股"},
        holdings=[],
    )
    assert reverse is not None
    assert reverse["dedup_key"] != rules[0]["dedup_key"]


def test_international_bank_rotation_supports_paired_actions_and_style_buckets() -> None:
    paired = international_bank_theme_strategy_rule(
        source="sina_finance_articles",
        item={"title": "花旗建议减配芯片股、增配 AI 应用和 AI 云服务商"},
        holdings=[],
    )
    assert paired is not None
    assert paired["from_themes"] == ["semiconductor_equities"]
    assert paired["to_themes"] == ["ai_cloud_hyperscalers", "ai_applications"]

    style = international_bank_theme_strategy_rule(
        source="sina_finance_articles",
        item={"title": "高盛投资策略建议从成长股转向价值股"},
        holdings=[],
    )
    assert style is not None
    assert style["from_themes"] == ["growth_equities"]
    assert style["to_themes"] == ["value_equities"]


def test_international_bank_rotation_rejects_ambiguous_or_non_current_views() -> None:
    negatives = (
        "摩根士丹利称 AI 商业模式正从资本开支 capex 转向运营开支 opex，并上调云厂商评级至买入。",
        "美银-中控技术：从资本开支向运营开支范式转型，上调评级至买入，工业 AI 驱动销售稳健增长。",
        "高盛回顾去年投资者从芯片股轮动到 AI 云服务商的过程。",
        "市场资金从芯片股轮动到 AI 云服务商，摩根士丹利另有一份行业报告。",
        "摩根士丹利报告称，芯片股上涨后 AI 云服务商出现补涨，属于股价轮动。",
        "摩根士丹利认为 AI 云服务商盈利前景比芯片股更好。",
        "花旗建议超配 AI 云服务商，认为芯片股估值偏高。",
        "花旗称封测行业估值体系从周期股切换到成长股。",
        "摩根士丹利并未建议投资者从芯片股转向 AI 云服务商。",
        "网传高盛建议投资者从芯片股轮动到 AI 云服务商，未经证实。",
    )
    for text in negatives:
        assert (
            international_bank_theme_strategy_rule(
                source="cls_telegraph_api",
                item={"title": text, "summary": ""},
                holdings=[],
            )
            is None
        ), text


def test_international_bank_aliases_use_word_boundaries_across_sources() -> None:
    noisy_text = (
        "AI infrastructure cities require substantial long-term electricity investment. "
        "The valuation market opportunity is large, but no investment bank is quoted."
    )
    variants = [
        ("alphabstract_summaries", {"title": noisy_text, "summary": "", "content_type": "research_summary"}),
        ("cls_telegraph_api", {"title": noisy_text, "summary": ""}),
    ]
    for source, item in variants:
        assert international_bank_theme_strategy_rule(source=source, item=item, holdings=[]) is None

    positive = international_bank_theme_strategy_rule(
        source="alphabstract_summaries",
        item={
            "title": "Citi says go long AI infrastructure basket",
            "summary": "Citigroup sees valuation and market mismatch across AI, semiconductors and data center power.",
            "published_at": "2026-07-13T00:00:00+00:00",
        },
        holdings=[],
    )
    assert positive is not None
    assert positive["banks"] == ["花旗"]
    assert positive["action"] == "做多"


def test_value_directory_strategy_title_is_index_evidence() -> None:
    item = {
        "title": "高盛-交易思路：做多中国人工智能价值链（GSXACART）",
        "summary": "高盛 Trade Idea: Long China AI Value Chain。",
        "published_at": "2026-07-09T16:00:00+00:00",
    }
    rule = international_bank_theme_strategy_rule(source="value_directory_ib_stocks", item=item, holdings=[])
    assert rule is not None
    assert rule["should_push"] is True
    assert rule["source_tier"] == "价值目录研报索引（仅标题元数据）"
    assert any(item["kind"] == "价值目录策略研报标题" for item in rule["evidence"])


def test_value_directory_industry_macro_strategy_source() -> None:
    item = {
        "title": "瑞银-亚太科技策略板块要点：智能体AI将进一步带动半导体与硬件上行-APAC Tech Strategy Sector Keys：Agentic AI to carry Semis&Hardware further-20260701【198页】",
        "summary": "第一页提取：瑞银认为智能体 AI 将继续推动半导体与硬件上行，覆盖半导体、硬件、数据中心等多环节。",
        "published_at": "2026-07-11T00:00:00+00:00",
        "raw": {
            "value_directory_preview": {
                "facts": {
                    "status": "ok",
                    "core_content": "瑞银认为智能体 AI 将继续推动半导体与硬件上行。",
                    "key_points": ["多环节主题覆盖", "半导体与硬件"],
                }
            }
        },
    }
    rule = first_matching_push_rule(source="value_directory_ib_industry_macro", item=item, holdings=[])
    assert rule is not None
    assert rule["should_push"] is True
    assert rule["rule_id"] == "value_directory_industry_macro_research"
    assert "AI/算力价值链" in rule["affected_targets"]


def test_international_bank_multi_leg_strategy_rule() -> None:
    item = {
        "title": "摩根士丹利策略报告：超配 HBM、半导体设备与数据中心电力",
        "summary": "报告给出行业篮子，并指出这些环节存在估值错配和资金配置转向。",
    }
    rule = first_matching_push_rule(source="sina_stock_news", item=item, holdings=[])
    assert rule is not None
    assert rule["rule_id"] == "international_bank_theme_strategy"
    assert rule["action"] == "超配"


def test_value_directory_peer_or_industry_relation_rule() -> None:
    item = {
        "title": "高盛-黄河旋风(600172.SH)：人造金刚石需求与产能展望",
        "summary": "国际投行个股研报索引。",
        "published_at": "2026-07-11T00:00:00+00:00",
    }
    original_matches = push_rules.portfolio_relation_matches
    original_enabled = push_rules.rule_enabled
    try:
        push_rules.portfolio_relation_matches = lambda *_args, **_kwargs: [
            {
                "holding_symbol": "300179.SZ",
                "holding_name": "四方达",
                "trigger_name": "黄河旋风",
                "matched_term": "黄河旋风",
                "relation_type": "人造金刚石/超硬材料同业",
                "impact_direction": "uncertain",
                "theme": "人造金刚石",
            }
        ]
        push_rules.rule_enabled = lambda rule_id: rule_id == "investment_bank_portfolio_relation"
        rule = first_matching_push_rule(source="value_directory_ib_stocks", item=item, holdings=[SIFANGDA])
    finally:
        push_rules.portfolio_relation_matches = original_matches
        push_rules.rule_enabled = original_enabled
    assert rule is not None
    assert rule["rule_id"] == "investment_bank_portfolio_relation"
    assert rule["should_push"] is True
    assert rule["affected_targets"] == ["四方达 300179.SZ"]
    assert "黄河旋风 -> 人造金刚石/超硬材料同业 -> 四方达" in rule["reason"]
    assert "LLM" in rule["reason"]


def test_holding_keyword_rule_pushes_direct_holding_without_hard_variable() -> None:
    item = {"title": "绿的谐波召开投资者交流会", "summary": "公司介绍近期业务进展。"}
    rule = first_matching_push_rule(source="news_media", item=item, holdings=[GREEN])
    assert rule is not None
    assert rule["rule_id"] == "holding_keyword_immediate_alert"
    assert rule["affected_targets"] == ["绿的谐波 688017.SH"]
    assert "直接持仓命中" in rule["reason"]


def test_holding_keyword_rule_pushes_related_holding_keyword() -> None:
    holding = {
        **SIFANGDA,
        "news_keywords": ["黄河旋风", "人造金刚石"],
        "news_exclude_keywords": [],
    }
    item = {
        "title": "高盛-黄河旋风(600172.SH)：人造金刚石需求与产能展望",
        "summary": "国际投行个股研报索引。",
    }
    rule = first_matching_push_rule(source="value_directory_ib_stocks", item=item, holdings=[holding])
    assert rule is not None
    assert rule["rule_id"] == "holding_keyword_immediate_alert"
    assert rule["affected_targets"] == ["四方达 300179.SZ"]
    assert "关联关键词命中：黄河旋风、人造金刚石 -> 四方达" in rule["reason"]


def test_holding_keyword_exclusion_only_blocks_keyword_association() -> None:
    holding = {
        **SIFANGDA,
        "news_keywords": ["黄河旋风"],
        "news_exclude_keywords": ["例行转载"],
    }
    keyword_only = first_matching_push_rule(
        source="news_media",
        item={"title": "黄河旋风例行转载行业新闻", "summary": ""},
        holdings=[holding],
    )
    assert keyword_only is None

    direct_holding = first_matching_push_rule(
        source="news_media",
        item={"title": "四方达例行转载行业新闻", "summary": ""},
        holdings=[holding],
    )
    assert direct_holding is not None
    assert direct_holding["rule_id"] == "holding_keyword_immediate_alert"
    assert "直接持仓命中" in direct_holding["reason"]


def test_direct_holding_hard_variable_rule() -> None:
    item = {"title": "绿的谐波获得机器人客户大额订单，产能扩张推进", "summary": ""}
    rule = direct_holding_hard_variable_rule(source="sina_stock_news", item=item, holdings=[GREEN])
    assert rule is not None
    assert rule["rule_id"] == "direct_holding_hard_variable"
    assert rule["push_now"] is True


def test_official_company_hard_variable_rule() -> None:
    item = {"title": "NVIDIA announces Rubin rack-scale AI platform with liquid cooling", "summary": ""}
    rule = official_company_hard_variable_rule(source="nvidia_blog", item=item, holdings=[])
    assert rule is not None
    assert rule["rule_id"] == "official_company_hard_variable"
    assert rule["push_now"] is True


def test_macro_policy_rule_from_raw() -> None:
    item = {
        "title": "美联储主席沃什讲话后，2年期美债收益率大跌",
        "raw": {"macro_policy_line": {"matched": True, "tier": "primary", "reason": "命中美联储主席讲话。"}},
    }
    rule = first_matching_push_rule(source="sina_flash", item=item, holdings=[])
    assert rule is not None
    assert rule["rule_id"] == "macro_policy_line"
    assert rule["push_now"] is True


def main() -> int:
    test_investment_bank_target_price_rule_for_direct_holding()
    test_event_rule_overrides_model_skip()
    test_international_bank_theme_strategy_rule()
    test_international_bank_theme_strategy_requires_action_and_evidence()
    test_international_bank_rotation_strategy_extracts_both_legs_across_sources()
    test_international_bank_rotation_supports_paired_actions_and_style_buckets()
    test_international_bank_rotation_rejects_ambiguous_or_non_current_views()
    test_international_bank_aliases_use_word_boundaries_across_sources()
    test_value_directory_strategy_title_is_index_evidence()
    test_value_directory_industry_macro_strategy_source()
    test_international_bank_multi_leg_strategy_rule()
    test_value_directory_peer_or_industry_relation_rule()
    test_holding_keyword_rule_pushes_direct_holding_without_hard_variable()
    test_holding_keyword_rule_pushes_related_holding_keyword()
    test_holding_keyword_exclusion_only_blocks_keyword_association()
    test_direct_holding_hard_variable_rule()
    test_official_company_hard_variable_rule()
    test_macro_policy_rule_from_raw()
    print("push rule checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
