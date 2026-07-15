#!/usr/bin/env python3
"""Regression checks for delivery-only US macro event identities."""

from __future__ import annotations

from macro_event_dedup import (
    FED_POLICY_REACTION_RULE_ID,
    MACRO_PREVIEW_RULE_ID,
    MACRO_REACTION_RULE_ID,
    MACRO_RELEASE_RULE_ID,
    macro_event_dedup_hit,
)
from market_item import DecisionResult


MACRO_DECISION = DecisionResult(
    action="push",
    importance="high",
    rule_hits=[{"rule_id": "macro_policy_line"}],
)


def hit(title: str, *, published_at: str = "2026-07-14T12:32:47+00:00") -> dict | None:
    return macro_event_dedup_hit({"title": title, "published_at": published_at}, MACRO_DECISION)


def test_release_identity_is_source_neutral_and_period_specific() -> None:
    cls = hit("美国6月CPI环比下降0.4%，同比增长3.5%，均低于预期。")
    sina = hit("美国6月消费者价格指数同比增长3.5%，环比下降0.4%。")
    may = hit("美国5月CPI同比增长4.2%，环比增长0.5%。")
    assert cls is not None and sina is not None and may is not None
    assert cls["rule_id"] == MACRO_RELEASE_RULE_ID
    assert cls["dedup_key"] == sina["dedup_key"] == "macro:release:US:CPI:2026-06"
    assert may["dedup_key"] == "macro:release:US:CPI:2026-05"


def test_nearest_month_binds_to_indicator_not_publication_or_meeting_date() -> None:
    result = hit("金十数据7月14日讯，美国6月核心CPI年率录得2.6%，低于预期。")
    no_period = hit("财联社7月14日电，受弱于预期的美国CPI数据影响，比特币上涨。")
    assert result is not None
    assert result["event_facts"]["reference_period"] == "2026-06"
    assert no_period is not None
    assert no_period["rule_id"] == MACRO_REACTION_RULE_ID
    assert no_period["event_facts"]["reference_period"] == "2026-06"
    assert no_period["event_facts"]["reference_period_inferred"] is True
    assert no_period["event_facts"]["reaction_session"] == "2026-07-14"

    meeting_month = hit("美债在美国CPI降温后走强，市场将7月美联储加息押注下调至20%。")
    assert meeting_month is None

    overnight = hit(
        "美债在美国CPI降温后走强，市场削减加息押注。",
        published_at="2026-07-14T16:09:00+00:00",
    )
    assert overnight is not None
    assert overnight["dedup_key"] == "macro:market_reaction:US:CPI:2026-06"


def test_preview_and_market_reaction_have_independent_identities() -> None:
    preview = hit("美国6月CPI将于今晚公布，市场预期同比增长3.8%。")
    table_preview = hit("美国6月季调后CPI月率（前值：+0.5%；预期值：-0.1%）")
    reaction = hit("美国6月CPI环比下降0.4%超预期，美股期货跳涨，纳指期货涨1.3%。")
    assert preview is not None and table_preview is not None and reaction is not None
    assert preview["rule_id"] == MACRO_PREVIEW_RULE_ID
    assert preview["dedup_key"] == "macro:preview:US:CPI:2026-06"
    assert table_preview["rule_id"] == MACRO_PREVIEW_RULE_ID
    assert reaction["rule_id"] == MACRO_REACTION_RULE_ID
    assert reaction["dedup_key"] == "macro:market_reaction:US:CPI:2026-06"
    assert reaction["dedup_alias_keys"][:2] == [
        "macro:market_reaction:US:CPI:2026-07-14",
        "macro:market_reaction:US:CPI:2026-07-13",
    ]

    reaction_without_period = hit("受弱于预期的美国CPI数据影响，比特币上涨3.2%。")
    assert reaction_without_period is not None
    assert reaction_without_period["dedup_key"] == reaction["dedup_key"]

    commentary = hit("美国6月CPI通胀数据的意义有限，能源价格仍是未来变数。")
    assert commentary is not None and commentary["rule_id"] == MACRO_PREVIEW_RULE_ID

    terse_calendar = hit("周二数据：美国6月CPI及核心CPI数据。")
    assert terse_calendar is not None and terse_calendar["rule_id"] == MACRO_PREVIEW_RULE_ID

    preview_then_actual = macro_event_dedup_hit(
        {
            "title": "美国6月CPI将于今晚公布。",
            "full_text": "美国6月CPI环比下降0.4%，同比增长3.5%。",
            "published_at": "2026-07-14T12:32:47+00:00",
        },
        MACRO_DECISION,
    )
    assert preview_then_actual is not None and preview_then_actual["rule_id"] == MACRO_RELEASE_RULE_ID


def test_direct_warsh_statement_and_correction_are_not_suppressed() -> None:
    warsh = hit("美国6月CPI环比下降0.4%。美联储主席沃什表示，不会容忍通胀过高。")
    scheduled = hit("美国6月CPI环比下降0.4%。沃什将于今晚出席国会听证会。")
    correction = hit("更正：美国6月CPI环比下降0.3%，此前数据误报为下降0.4%。")
    warsh_response = hit("美国国债价格回吐涨幅，沃什淡化美国6月CPI通胀数据的意义。")
    warsh_colon = hit("美联储主席沃什：美国6月CPI数据出炉后，任务并未完成。")
    mixed_reaction = hit("美联储主席沃什表示通胀任务未完成；美国6月CPI公布后美元走低。")
    assert warsh is None
    assert warsh_response is None
    assert warsh_colon is None
    assert mixed_reaction is None
    assert correction is None
    assert scheduled is not None and scheduled["rule_id"] == MACRO_RELEASE_RULE_ID


def test_cross_asset_fed_policy_reactions_share_one_catalyst_identity() -> None:
    gold = hit("美联储降息预期升温，黄金上涨1.5%。")
    bitcoin = hit("交易员重新定价美联储降息路径，比特币上涨3.2%。")
    tightening = hit("美联储加息预期升温，美元指数上涨0.6%。")
    assert gold is not None and bitcoin is not None and tightening is not None
    assert gold["rule_id"] == bitcoin["rule_id"] == FED_POLICY_REACTION_RULE_ID
    assert gold["dedup_key"] == bitcoin["dedup_key"] == (
        "macro:fed_policy_market_reaction:US:FED_POLICY:easing"
    )
    assert tightening["dedup_key"] == "macro:fed_policy_market_reaction:US:FED_POLICY:tightening"
    assert gold["dedup_lookback_days"] == 14
    unrelated_brief = hit("A股指数下跌1%。美联储尚未加息，三季度风险偏好仍然较高。")
    assert unrelated_brief is None
    quantified_repricing = hit("交易员将美联储降息概率从40%上调至65%，黄金上涨1.2%。")
    assert quantified_repricing is None


def test_pce_nonfarm_and_year_rollover_are_supported() -> None:
    pce = hit("美国6月PCE物价指数同比增长2.4%，环比增长0.2%。")
    payroll = hit("美国6月非农新增就业人数录得12万人，低于预期。")
    december = hit("美国12月CPI同比增长3.1%。", published_at="2027-01-14T12:30:00+00:00")
    assert pce is not None and pce["dedup_key"] == "macro:release:US:PCE:2026-06"
    assert payroll is not None and payroll["dedup_key"] == "macro:release:US:NONFARM:2026-06"
    assert december is not None and december["dedup_key"] == "macro:release:US:CPI:2026-12"


def test_fail_closed_without_macro_push_or_local_period() -> None:
    archive = DecisionResult(action="archive", rule_hits=[{"rule_id": "macro_policy_line"}])
    other_push = DecisionResult(action="push", rule_hits=[{"rule_id": "holding_keyword_immediate_alert"}])
    item = {"title": "美国CPI环比下降0.4%", "published_at": "2026-07-14T12:30:00+00:00"}
    assert macro_event_dedup_hit(item, archive) is None
    assert macro_event_dedup_hit(item, other_push) is None
    assert macro_event_dedup_hit(item, MACRO_DECISION) is None


def main() -> int:
    test_release_identity_is_source_neutral_and_period_specific()
    test_nearest_month_binds_to_indicator_not_publication_or_meeting_date()
    test_preview_and_market_reaction_have_independent_identities()
    test_direct_warsh_statement_and_correction_are_not_suppressed()
    test_cross_asset_fed_policy_reactions_share_one_catalyst_identity()
    test_pce_nonfarm_and_year_rollover_are_supported()
    test_fail_closed_without_macro_push_or_local_period()
    print("macro event dedup checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
