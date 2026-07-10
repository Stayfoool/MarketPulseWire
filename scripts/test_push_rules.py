#!/usr/bin/env python3
"""Regression checks for deterministic push rules."""

from __future__ import annotations

from push_rules import (
    apply_event_push_rules,
    direct_holding_hard_variable_rule,
    first_matching_push_rule,
    international_bank_theme_strategy_rule,
    official_company_hard_variable_rule,
)


GREEN = {"symbol": "688017.SH", "name": "绿的谐波", "full_name": "绿的谐波传动科技股份有限公司", "aliases": []}


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


def test_international_bank_multi_leg_strategy_rule() -> None:
    item = {
        "title": "摩根士丹利策略报告：超配 HBM、半导体设备与数据中心电力",
        "summary": "报告给出行业篮子，并指出这些环节存在估值错配和资金配置转向。",
    }
    rule = first_matching_push_rule(source="sina_stock_news", item=item, holdings=[])
    assert rule is not None
    assert rule["rule_id"] == "international_bank_theme_strategy"
    assert rule["action"] == "超配"


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
    test_international_bank_multi_leg_strategy_rule()
    test_direct_holding_hard_variable_rule()
    test_official_company_hard_variable_rule()
    test_macro_policy_rule_from_raw()
    print("push rule checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
