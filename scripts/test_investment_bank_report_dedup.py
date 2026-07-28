#!/usr/bin/env python3
"""Regression checks for delivery-only investment-bank report identities."""

from __future__ import annotations

from investment_bank_report_dedup import (
    INVESTMENT_BANK_REPORT_RULE_ID,
    investment_bank_report_dedup_hit,
)
from market_item import DecisionResult


INSTITUTIONS = (("nomura", ("野村", "野村证券", "Nomura")),)


def decision(*quotes: str, rule_ids: tuple[str, ...] = ("equity_rating_revision",)) -> DecisionResult:
    return DecisionResult(
        action="push",
        importance="high",
        rule_hits=[
            {
                "rule_id": rule_id,
                "decision_action": "push",
                "evidence": [
                    {"evidence_id": f"B{index}", "field": "full_text", "quote": quote}
                    for index, quote in enumerate(quotes, start=1)
                ],
            }
            for rule_id in rule_ids
        ],
    )


def hit(text: str, *, published_at: str = "2026-07-27T02:41:20+00:00") -> dict | None:
    return investment_bank_report_dedup_hit(
        {"title": text, "published_at": published_at},
        decision(text),
        institutions=INSTITUTIONS,
    )


def test_same_nomura_report_converges_across_source_wording() -> None:
    first = hit("野村证券首次覆盖长鑫科技，给予买入评级和116元目标价。")
    rewrite = hit("野村对长鑫科技给出目标价为人民币116元，属于首次覆盖并给予买入评级。")
    english = hit("Nomura initiates coverage on 长鑫科技 with a CNY 116 target price and Buy rating.")
    assert first is not None and rewrite is not None and english is not None
    assert first["rule_id"] == INVESTMENT_BANK_REPORT_RULE_ID
    assert first["dedup_key"] == rewrite["dedup_key"] == english["dedup_key"]
    assert first["event_facts"]["target_price"] == "116"
    assert first["event_facts"]["subject"] == "长鑫科技"
    assert first["dedup_lookback_days"] == 7


def test_unlisted_mizuho_report_converges_without_admission_allowlist() -> None:
    admission = {
        "admission": {
            "evidence": [
                {
                    "reason_code": "holding_direct_identity",
                    "matched_subjects": ["长鑫科技"],
                }
            ]
        }
    }
    texts = (
        "瑞穗证券首次覆盖存储芯片厂商长鑫科技，将长鑫科技目标价定为70元。",
        "瑞穗证券：长鑫科技目标价为70元，第二季度业绩料将大幅超预期。",
        "瑞穗证券发布研报称，将长鑫科技目标价定为70元。",
        "瑞穗对长鑫科技首次覆盖，目标价人民币70元。",
    )
    hits = [
        investment_bank_report_dedup_hit(
            {"source": f"source-{index}", "title": text},
            DecisionResult(
                action="push",
                importance="high",
                rule_hits=decision(text).rule_hits,
                audit_json=admission,
            ),
            institutions=(),
        )
        for index, text in enumerate(texts)
    ]
    assert all(result is not None for result in hits)
    assert len({result["dedup_key"] for result in hits if result}) == 1
    assert hits[0]["event_facts"]["institution_name"] == "瑞穗"
    assert hits[0]["event_facts"]["target_currency"] == "CNY"
    assert hits[0]["event_facts"]["target_price"] == "70"


def test_unlisted_institution_must_be_unique() -> None:
    text = "甲方证券称，长鑫科技目标价为70元。乙方证券称，长鑫科技目标价为70元。"
    assert investment_bank_report_dedup_hit(
        {"title": text}, decision(text), institutions=()
    ) is None


def test_missing_evidence_details_are_completed_only_from_the_same_article() -> None:
    evidence = "长鑫科技股价相对IPO发行价有较大上涨空间，目标价116元人民币。"
    item = {
        "title": "野村看多长鑫科技",
        "full_text": "野村证券首次覆盖长鑫科技，给予买入评级，目标价116元人民币。",
        "published_at": "2026-07-27T02:41:20+00:00",
    }
    completed = investment_bank_report_dedup_hit(
        item, decision(evidence), institutions=INSTITUTIONS,
    )
    complete_evidence = hit("野村证券首次覆盖长鑫科技，给予买入评级和人民币116元目标价。")
    assert completed is not None and complete_evidence is not None
    assert completed["dedup_key"] == complete_evidence["dedup_key"]


def test_changed_report_facts_remain_independent() -> None:
    original = hit("野村证券首次覆盖长鑫科技，给予买入评级和116元目标价。")
    changed_target = hit("野村证券首次覆盖长鑫科技，给予买入评级和128元目标价。")
    explicit_revision = hit("野村证券上调长鑫科技目标价至128元，维持买入评级。")
    coverage_and_revision = hit("野村证券首次覆盖长鑫科技后，上调目标价至128元。")
    next_day = hit(
        "野村证券首次覆盖长鑫科技，给予买入评级和116元目标价。",
        published_at="2026-07-28T02:41:20+00:00",
    )
    assert original is not None and changed_target is not None and next_day is not None
    assert original["dedup_key"] != changed_target["dedup_key"]
    assert original["dedup_key"] == next_day["dedup_key"]
    assert explicit_revision is None
    assert coverage_and_revision is None


def test_same_target_identity_does_not_require_the_same_rating() -> None:
    buy = hit("野村证券首次覆盖长鑫科技，给予买入评级和116元目标价。")
    sell = hit("野村证券首次覆盖长鑫科技，给予卖出评级和116元目标价。")
    assert buy is not None and sell is not None
    assert buy["dedup_key"] == sell["dedup_key"]


def test_short_subject_alias_expands_from_the_same_article() -> None:
    evidence = "野村首次覆盖长鑫，目标价116元人民币。"
    item = {
        "title": "如何估值长鑫",
        "summary": "野村证券首次覆盖长鑫科技，给予买入评级，目标价116元人民币。",
        "published_at": "2026-07-27T02:41:20+00:00",
    }
    expanded = investment_bank_report_dedup_hit(
        item, decision(evidence), institutions=INSTITUTIONS,
    )
    canonical = hit("野村证券首次覆盖长鑫科技，给予买入评级和人民币116元目标价。")
    assert expanded is not None and canonical is not None
    assert expanded["dedup_key"] == canonical["dedup_key"]


def test_mixed_independent_push_fact_fails_open() -> None:
    mixed = decision(
        "野村证券首次覆盖长鑫科技，给予买入评级和116元目标价。",
        rule_ids=("equity_rating_revision", "capital_control_share_change"),
    )
    item = {"title": "野村首次覆盖；苹果开始验证长鑫DRAM", "published_at": "2026-07-27T02:41:20+00:00"}
    assert investment_bank_report_dedup_hit(item, mixed, institutions=INSTITUTIONS) is None


def test_legacy_rating_id_is_bounded_by_rating_evidence() -> None:
    old_rating = decision(
        "野村对长鑫科技给出116元目标价，属于首次覆盖。",
        rule_ids=("holding_rating_revision",),
    )
    legacy = decision(
        "野村对长鑫科技给出116元目标价，属于首次覆盖。",
        rule_ids=("investment_bank_allocation_change",),
    )
    theme = decision(
        "野村建议从芯片股轮动至云服务商。",
        rule_ids=("investment_bank_allocation_change",),
    )
    item = {"published_at": "2026-07-27T02:41:20+00:00"}
    assert investment_bank_report_dedup_hit(item, old_rating, institutions=INSTITUTIONS) is not None
    assert investment_bank_report_dedup_hit(item, legacy, institutions=INSTITUTIONS) is not None
    assert investment_bank_report_dedup_hit(item, theme, institutions=INSTITUTIONS) is None


def test_missing_local_identity_or_push_eligibility_fails_open() -> None:
    no_institution = hit("某机构首次覆盖长鑫科技，给予116元目标价。")
    no_subject = hit("野村证券首次覆盖该公司，给予116元目标价。")
    archive = DecisionResult(
        action="archive",
        rule_hits=[{"rule_id": "equity_rating_revision", "decision_action": "archive"}],
    )
    assert no_institution is None
    assert no_subject is None
    assert investment_bank_report_dedup_hit(
        {"published_at": "2026-07-27T02:41:20+00:00"},
        archive,
        institutions=INSTITUTIONS,
    ) is None


def main() -> int:
    test_same_nomura_report_converges_across_source_wording()
    test_unlisted_mizuho_report_converges_without_admission_allowlist()
    test_unlisted_institution_must_be_unique()
    test_missing_evidence_details_are_completed_only_from_the_same_article()
    test_changed_report_facts_remain_independent()
    test_same_target_identity_does_not_require_the_same_rating()
    test_short_subject_alias_expands_from_the_same_article()
    test_mixed_independent_push_fact_fails_open()
    test_legacy_rating_id_is_bounded_by_rating_evidence()
    test_missing_local_identity_or_push_eligibility_fails_open()
    print("investment-bank report dedup checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
