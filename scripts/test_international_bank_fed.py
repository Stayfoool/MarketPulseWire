#!/usr/bin/env python3
"""Regression checks for international-bank Fed path revisions."""

from __future__ import annotations

import decision_engine
from decision_engine import decide_market_item
from international_bank_fed import international_bank_fed_rate_path_rule
from market_item import NormalizedMarketItem


BOFA_TEXT = (
    "美银证券此前预计美联储年内不会调整利率，现将预测改为2026年9月、10月和12月"
    "各加息25个基点，累计加息75个基点。"
)


def normalized(source: str, text: str) -> NormalizedMarketItem:
    return NormalizedMarketItem(
        source=source,
        title=text,
        summary=text,
        published_at="2026-06-23T00:42:00+00:00",
        source_category="news_media",
        publisher_role="news_media",
        content_type="article",
        raw={"id": f"{source}-1"},
    )


def test_bofa_direction_change_pushes_across_sources() -> None:
    decisions = [decide_market_item(normalized(source, BOFA_TEXT), holdings=[]) for source in ("wallstreetcn_news", "sina_finance_articles")]
    assert {decision.action for decision in decisions} == {"push"}
    hits = [decision.rule_hits[0] for decision in decisions]
    assert {hit["rule_id"] for hit in hits} == {"international_bank_fed_rate_path_revision"}
    assert {hit["banks"][0] for hit in hits} == {"美银"}
    assert {hit["revised_action"] for hit in hits} == {"hike"}
    assert {hit["revised_count"] for hit in hits} == {3}
    assert {hit["cumulative_bp"] for hit in hits} == {75}
    assert len({hit["dedup_key"] for hit in hits}) == 1
    assert all(hit["evidence_quotes"] for hit in hits)


def test_cut_count_revision_and_timing_shift_push() -> None:
    cut = international_bank_fed_rate_path_rule(
        "cls_telegraph_api",
        {
            "title": "高盛将美联储今年降息预期从3次下调至1次",
            "summary": "高盛最新报告预计美联储年内仅降息1次。",
            "published_at": "2026-07-14T00:00:00+00:00",
        },
    )
    assert cut is not None
    assert cut["decision_action"] == "push"
    assert cut["previous_count"] == 3
    assert cut["revised_count"] == 1
    assert cut["revised_action"] == "cut"

    timing = international_bank_fed_rate_path_rule(
        "jin10_rsshub_important",
        {
            "title": "JPMorgan pushes back its first Federal Reserve rate cut from September to December",
            "summary": "The bank now expects the FOMC to hold rates until December.",
            "published_at": "2026-07-14T00:00:00+00:00",
        },
    )
    assert timing is not None
    assert timing["decision_action"] == "push"
    assert timing["meeting_months"] == ["september", "december"]


def test_concrete_forecast_without_revision_is_daily() -> None:
    decision = decide_market_item(
        normalized("wallstreetcn_news", "巴克莱预计美联储将在2026年12月降息25个基点。"),
        holdings=[],
    )
    assert decision.action == "daily"
    assert decision.rule_hits[0]["revision_type"] == "current_forecast"


def test_basis_point_and_terminal_rate_revisions_push() -> None:
    bp = international_bank_fed_rate_path_rule(
        "wallstreetcn_news",
        {
            "title": "花旗将美联储年内降息幅度从75个基点下调至25个基点",
            "published_at": "2026-07-14T00:00:00+00:00",
        },
    )
    assert bp is not None
    assert bp["decision_action"] == "push"
    assert bp["previous_cumulative_bp"] == 75
    assert bp["cumulative_bp"] == 25

    terminal = international_bank_fed_rate_path_rule(
        "sina_finance_articles",
        {
            "title": "UBS raises its Federal Reserve terminal rate forecast to 4.5% from 4.0%",
            "published_at": "2026-07-14T00:00:00+00:00",
        },
    )
    assert terminal is not None
    assert terminal["decision_action"] == "push"
    assert terminal["terminal_rate"] == "4.5%"


def test_generic_or_invalid_views_do_not_match() -> None:
    negatives = (
        "华尔街普遍认为美联储偏鹰，利率可能更高。",
        "美联储官员表示当前不急于降息。",
        "美银维持此前美联储年内降息两次的预测不变。",
        "网传高盛可能上调美联储加息预测，未经证实。",
        "瑞银预计欧洲央行年内降息两次。",
        "期货市场将美联储9月降息概率从60%上调至80%。",
        "伯恩斯坦预计美联储年内加息一次。",
    )
    for text in negatives:
        assert international_bank_fed_rate_path_rule("wallstreetcn_news", {"title": text}) is None, text


def test_multi_bank_story_does_not_mix_forecasts() -> None:
    rule = international_bank_fed_rate_path_rule(
        "wallstreetcn_news",
        {
            "title": "多家大行上调美联储加息预期，RBC称未来一年可能加息两次",
            "full_text": (
                "美国银行证券将此前美联储降息预期改为2026年9月、10月和12月各加息25个基点，累计75个基点。"
                "巴克莱另行预计美联储可能只加息一次。"
            ),
            "published_at": "2026-06-23T00:00:00+00:00",
        },
    )
    assert rule is not None
    assert rule["banks"][0] == "美银"
    assert rule["revised_count"] == 3
    assert rule["cumulative_bp"] == 75
    assert rule["meeting_months"] == ["9", "10", "12"]

    assert international_bank_fed_rate_path_rule(
        "cls_telegraph_api",
        {"title": "花旗预计韩国央行加息25个基点，另一段市场评论提及美联储政策。"},
    ) is None


def test_daily_fed_candidate_cannot_hide_independent_push_rule() -> None:
    original = decision_engine.attributed_research_rule
    try:
        decision_engine.attributed_research_rule = lambda _item: {
            "matched": True,
            "rule_id": "attributed_research_hard_variable",
            "decision_action": "push",
            "importance": "high",
            "push_now": True,
            "should_push": True,
            "reason": "独立硬变量命中",
            "brief_reason": "独立硬变量命中",
        }
        decision = decide_market_item(
            normalized("wallstreetcn_news", "巴克莱预计美联储将在2026年12月降息25个基点。"),
            holdings=[],
        )
    finally:
        decision_engine.attributed_research_rule = original
    assert decision.action == "push"
    assert decision.rule_hits[0]["rule_id"] == "attributed_research_hard_variable"
    assert international_bank_fed_rate_path_rule(
        "yicai_brief",
        {"title": "摩根士丹利建议押注美联储加息预期消退，美债曲线目标为100个基点。"},
    ) is None


if __name__ == "__main__":
    test_bofa_direction_change_pushes_across_sources()
    test_cut_count_revision_and_timing_shift_push()
    test_concrete_forecast_without_revision_is_daily()
    test_basis_point_and_terminal_rate_revisions_push()
    test_generic_or_invalid_views_do_not_match()
    test_multi_bank_story_does_not_mix_forecasts()
    test_daily_fed_candidate_cannot_hide_independent_push_rule()
    print("international bank Fed path tests passed")
