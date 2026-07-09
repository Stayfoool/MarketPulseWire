#!/usr/bin/env python3
"""Regression checks for deterministic push rules."""

from __future__ import annotations

from push_rules import (
    apply_event_push_rules,
    direct_holding_hard_variable_rule,
    first_matching_push_rule,
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
    test_direct_holding_hard_variable_rule()
    test_official_company_hard_variable_rule()
    test_macro_policy_rule_from_raw()
    print("push rule checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
