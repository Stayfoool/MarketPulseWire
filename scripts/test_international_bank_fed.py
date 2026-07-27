#!/usr/bin/env python3
"""Regression checks for international-bank Fed path revisions."""

from __future__ import annotations

from international_bank_fed import classify_trusted_financial_leader_macro_judgement


def test_trusted_financial_leader_material_judgement_is_structured() -> None:
    item = {
        "title": "摩根大通戴蒙：市场低估风险",
        "full_text": (
            "摩根大通首席执行官杰米·戴蒙警告市场低估地缘政治和财政风险。"
            "他表示不会在当前价位买入股票或长期美国国债，并预测预算赤字可能导致利率进一步走高。"
            "即便通胀回落至美联储2%的目标水平，10年期国债收益率可能仍应维持在4%至4.5%之间。"
        ),
    }
    classification = classify_trusted_financial_leader_macro_judgement(
        item,
        allowed_banks={"摩根大通"},
    )
    assert classification is not None
    assert classification["rule_id"] == "financial_institution_fed_policy_judgement"
    assert classification["institutions"] == ["摩根大通"]
    assert set(classification["material_signals"]) == {
        "cross_asset_allocation_stance",
        "explicit_rate_or_yield_view",
        "material_cross_asset_risk_view",
    }
    assert "不会在当前价位买入股票或长期美国国债" in classification["evidence_quotes"][0]


def test_trusted_financial_leader_material_judgement_rejects_weak_attribution_or_scope() -> None:
    negatives = (
        "摩根大通分析师认为当前股票估值较高，建议谨慎。",
        "摩根大通首席执行官表示当前股票估值较高。",
        "普通科技公司首席执行官警告市场低估风险，不会买入股票或长期美国国债，并预计利率走高。",
        "摩根大通首席执行官表示美国经济仍有韧性。",
    )
    for text in negatives:
        assert classify_trusted_financial_leader_macro_judgement(
            {"title": text},
            allowed_banks={"摩根大通"},
        ) is None, text


if __name__ == "__main__":
    test_trusted_financial_leader_material_judgement_is_structured()
    test_trusted_financial_leader_material_judgement_rejects_weak_attribution_or_scope()
    print("international bank Fed path tests passed")
