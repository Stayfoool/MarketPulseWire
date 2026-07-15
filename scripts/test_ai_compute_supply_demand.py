#!/usr/bin/env python3
"""Regression checks for deterministic AI compute supply/demand monitoring."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ai_compute_supply_demand import RULE_ID, ai_compute_supply_demand_rule
from decision_engine import decide_market_item
from market_item import NormalizedMarketItem
from rule_alert_dedup import confirm_rule_alert, reserve_rule_alert


META_ANCHOR = "Meta正在构建一项云业务，以出售其过剩的AI算力。"


def rule(text: str) -> dict | None:
    return ai_compute_supply_demand_rule("news_media", {"title": text})


def test_meta_excess_compute_anchor_pushes() -> None:
    match = rule(META_ANCHOR)
    assert match is not None
    assert match["decision_action"] == "push"
    assert match["event_type"] == "external_capacity_opened"
    assert match["direction"] == "market_supply_up"
    assert match["subjects"] == ["meta"]
    assert match["evidence_quotes"] == [META_ANCHOR]


def test_meta_cross_source_rewrites_share_action_and_identity() -> None:
    texts = (
        "Meta正在构建一项云业务，以出售其过剩的AI算力。",
        "据悉Meta正筹划云基础设施业务，计划对外出售富余算力。",
        "Meta is planning a cloud business to sell excess AI compute to external customers.",
    )
    matches = [ai_compute_supply_demand_rule(source, {"title": text}) for source, text in zip(
        ("cls_telegraph_api", "yicai_brief", "future_official_feed"), texts
    )]
    assert all(match is not None for match in matches)
    assert {match["decision_action"] for match in matches if match} == {"push"}
    assert {match["dedup_key"] for match in matches if match} == {matches[0]["dedup_key"]}


def test_google_capacity_rationing_and_project_delay_pushes() -> None:
    match = rule(
        "谷歌已开始限制Meta使用Gemini，因为Meta的算力需求超出谷歌现有承载能力，多个项目被迫推迟。"
    )
    assert match is not None
    assert match["decision_action"] == "push"
    assert match["event_type"] == "capacity_shortage_or_rationing"
    assert match["direction"] == "supply_tight"
    recap = rule("谷歌算力告急限制Meta使用Gemini；Meta需求超出谷歌承载能力，项目被迫推迟。")
    assert recap is not None
    assert recap["dedup_key"] == match["dedup_key"]


def test_binding_contract_and_repricing_pushes() -> None:
    match = rule(
        "行云科技公告，算力服务合同租金上调28.79亿元，较原签约金额提高79%，高端算力仍供不应求。"
    )
    assert match is not None
    assert match["decision_action"] == "push"
    assert match["event_type"] == "binding_demand_or_cancellation"
    assert {"28.79亿元", "79%"}.issubset(set(match["quantified_terms"]))


def test_equivalent_chinese_amounts_share_contract_identity() -> None:
    short = rule("行云科技：控股子公司签订55.08亿元算力服务合同。")
    detailed = rule(
        "行云科技：控股子公司签订55.08亿元算力服务合同，含税总金额为550848万元。"
    )
    assert short is not None and detailed is not None
    assert short["dedup_key"] == detailed["dedup_key"]


def test_quantified_capacity_expansion_and_cancellation_push() -> None:
    expansion = rule("Meta计划将AI计算能力由2026年约7GW提升至2027年14GW，实现整体算力翻倍。")
    cancellation = rule("CoreWeave取消一项AI算力租赁合同并缩减GPU云容量。")
    assert expansion is not None and cancellation is not None
    assert expansion["decision_action"] == cancellation["decision_action"] == "push"
    assert expansion["event_type"] == "capacity_addition_or_removal"
    assert cancellation["event_type"] == "binding_demand_or_cancellation"
    assert cancellation["direction"] == "demand_down"


def test_direct_operator_denial_is_a_deliverable_correction() -> None:
    match = rule("Meta否认算力过剩，并表示出租部分AI算力比自用更有价值。")
    assert match is not None
    assert match["decision_action"] == "push"
    assert match["event_type"] == "issuer_confirmation_or_correction"
    assert match["stage"] == "denied"


def test_power_or_site_constraint_pushes() -> None:
    match = rule("美国纽约州州长宣布暂停新建耗电50兆瓦及以上的超大规模数据中心。")
    assert match is not None
    assert match["decision_action"] == "push"
    assert match["event_type"] == "power_or_site_constraint"


def test_generic_opinions_and_forecasts_do_not_push() -> None:
    cases = (
        "联想高管：长期看AI算力需求巨大，算力没有过剩。",
        "中信证券研报认为国产算力需求旺盛，长期趋势没有改变。",
        "中电联预计到2030年全国算力用电量将达到8000亿千瓦时。",
    )
    for text in cases:
        match = rule(text)
        assert match is None or match["decision_action"] != "push", text


def test_downstream_earnings_marketing_and_price_moves_do_not_match() -> None:
    cases = (
        "光迅科技受益于AI算力投资浪潮，预计上半年净利润增长50%-65%。",
        "①AI算力需求爆发，这家公司产品已进入头部客户供应链，分析师建议关注。",
        "算力租赁概念震荡走低，利通电子触及跌停，多家公司跌超5%。",
    )
    for text in cases:
        assert rule(text) is None, text


def test_decision_engine_merges_with_industry_rule_and_keeps_compute_identity() -> None:
    decision = decide_market_item(
        {
            "source": "yicai_brief",
            "title": "行云科技公告，算力服务合同租金上调28.79亿元，较原签约金额提高79%，高端算力仍供不应求。",
        },
        holdings=[],
    )
    assert decision.action == "push"
    assert RULE_ID in {hit["rule_id"] for hit in decision.rule_hits}
    assert decision.dedup["rule_id"] == RULE_ID
    assert decision.dedup["dedup_key"].startswith("ai_compute:")


def test_decision_is_stable_across_transport_metadata() -> None:
    variants = (
        NormalizedMarketItem(
            source="cls_telegraph_api",
            source_category="news_media",
            publisher_role="news_media",
            collector="china_finance_media_monitor",
            content_type="article",
            title=META_ANCHOR,
        ),
        NormalizedMarketItem(
            source="future_official_feed",
            source_category="official_company",
            publisher_role="company_official",
            collector="rss_monitor",
            content_type="official_news",
            title=META_ANCHOR,
        ),
    )
    decisions = [decide_market_item(item, holdings=[]) for item in variants]
    assert {decision.action for decision in decisions} == {"push"}
    assert {decision.rule_hits[0]["rule_id"] for decision in decisions} == {RULE_ID}
    assert {decision.dedup["dedup_key"] for decision in decisions} == {decisions[0].dedup["dedup_key"]}


def test_cross_source_delivery_is_atomic_and_material_updates_bypass() -> None:
    first = ai_compute_supply_demand_rule("cls_telegraph_api", {"title": META_ANCHOR})
    rewrite = ai_compute_supply_demand_rule(
        "yicai_brief",
        {"title": "据悉Meta正筹划云基础设施业务，计划对外出售富余算力。"},
    )
    quantified_update = ai_compute_supply_demand_rule(
        "future_official_feed",
        {"title": "Meta确认其云业务将对外出售富余AI算力，首期开放2GW容量。"},
    )
    assert first is not None and rewrite is not None and quantified_update is not None
    assert first["dedup_key"] == rewrite["dedup_key"]
    assert quantified_update["dedup_key"] != first["dedup_key"]
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        reservation = reserve_rule_alert(
            {"rule_hits": [first]},
            source="cls_telegraph_api",
            item_id="2414547",
            title=META_ANCHOR,
            published_at="2026-07-01T12:35:04+00:00",
            db_path=db_path,
        )
        assert reservation["reserved"] is True
        confirm_rule_alert(reservation, db_path=db_path)
        duplicate = reserve_rule_alert(
            {"rule_hits": [rewrite]},
            source="yicai_brief",
            item_id="103256093",
            title=str(rewrite["evidence_quotes"][0]),
            published_at="2026-07-01T13:33:27+00:00",
            db_path=db_path,
        )
        assert duplicate["duplicate"] is True
        update = reserve_rule_alert(
            {"rule_hits": [quantified_update]},
            source="future_official_feed",
            item_id="meta-update-1",
            title=str(quantified_update["evidence_quotes"][0]),
            published_at="2026-07-02T10:00:00+00:00",
            db_path=db_path,
        )
        assert update["reserved"] is True


def main() -> int:
    test_meta_excess_compute_anchor_pushes()
    test_meta_cross_source_rewrites_share_action_and_identity()
    test_google_capacity_rationing_and_project_delay_pushes()
    test_binding_contract_and_repricing_pushes()
    test_equivalent_chinese_amounts_share_contract_identity()
    test_quantified_capacity_expansion_and_cancellation_push()
    test_direct_operator_denial_is_a_deliverable_correction()
    test_power_or_site_constraint_pushes()
    test_generic_opinions_and_forecasts_do_not_push()
    test_downstream_earnings_marketing_and_price_moves_do_not_match()
    test_decision_engine_merges_with_industry_rule_and_keeps_compute_identity()
    test_decision_is_stable_across_transport_metadata()
    test_cross_source_delivery_is_atomic_and_material_updates_bypass()
    print("AI compute supply/demand checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
