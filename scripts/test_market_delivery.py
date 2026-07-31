#!/usr/bin/env python3
"""Regression checks for the single market-information delivery path."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import market_delivery
from market_db import init_db
from market_item import (
    AdmissionEvidence,
    AdmissionResult,
    DecisionResult,
    InterpretationResult,
    MarketFlowResult,
    NormalizedMarketItem,
)
from market_store import record_production_admission


def admitted() -> AdmissionResult:
    return AdmissionResult(
        status="admitted",
        reason_code="semiconductor_ai_match",
        matched_families=("semiconductor_ai",),
        evidence=(AdmissionEvidence("semiconductor_ai", "term", "HBM"),),
        config_version="test-v1",
    )


def decision(
    action: str = "push",
    *,
    rule_hits: list[dict] | None = None,
) -> DecisionResult:
    return DecisionResult(
        action=action,
        reason="固定测试决策。",
        rule_hits=rule_hits or [],
    )


def item(
    source: str,
    source_item_id: str,
    *,
    source_category: str = "news_media",
    content_type: str = "unknown",
    title: str = "HBM 产能扩张",
) -> NormalizedMarketItem:
    return NormalizedMarketItem(
        source=source,
        source_category=source_category,
        publisher_role="news_media",
        collector="test",
        content_type=content_type,
        title=title,
        summary="公司确认新增 HBM 产线。",
        full_text="公司确认新增 HBM 产线并扩大产能。",
        url=f"https://example.com/{source_item_id}",
        published_at="2026-07-31T00:00:00+00:00",
        raw={"id": source_item_id},
    )


def deliver(
    db_path: Path,
    normalized: NormalizedMarketItem,
    result: DecisionResult,
    *,
    use_rule_dedup: bool = True,
) -> tuple[str, int, int]:
    market_item_id, review_id = record_production_admission(
        normalized,
        admitted(),
        db_path=db_path,
    )
    flow = MarketFlowResult(
        item=normalized,
        decision=result,
        interpretation=InterpretationResult(core_content=normalized.summary),
    )
    status = market_delivery.deliver_market_item(
        normalized.to_dict(),
        flow,
        market_item_id=market_item_id,
        market_review_id=review_id,
        db_path=db_path,
        use_rule_dedup=use_rule_dedup,
    )
    return status, market_item_id, review_id


def delivery_rows(db_path: Path) -> list[tuple]:
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT market_item_id,market_review_id,status,decision_action,error,payload_json "
            "FROM deliveries ORDER BY id"
        ).fetchall()


def holding_rule() -> dict:
    return {
        "rule_id": "ai_compute_supply_demand",
        "decision_action": "push",
        "reason": "持仓公司确认 HBM 扩产。",
        "dedup_key": "ai_compute_supply_demand:test-company:hbm-expansion",
        "dedup_lookback_minutes": 60,
        "related_targets": [
            {"name": "测试公司", "code": "000001.SZ", "relation": "直接持仓"}
        ],
    }


def test_non_push_action_is_recorded_without_sending() -> None:
    original_send = market_delivery.send_card
    calls = 0

    def unexpected_send(_card):
        nonlocal calls
        calls += 1
        return True

    market_delivery.send_card = unexpected_send
    try:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "db.sqlite3"
            init_db(path).close()
            status, market_item_id, review_id = deliver(
                path,
                item("source-a", "daily-1"),
                decision("daily"),
            )
            row = delivery_rows(path)[0]
        assert status == "skipped"
        assert row[:4] == (market_item_id, review_id, "skipped", "daily")
        assert json.loads(row[5])["reason"] == "DecisionResult.action 不是 push"
        assert calls == 0
    finally:
        market_delivery.send_card = original_send


def test_push_uses_direct_unified_identity_and_one_card_builder() -> None:
    original_send = market_delivery.send_card
    cards: list[dict] = []
    market_delivery.send_card = lambda card: cards.append(card) or True
    try:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "db.sqlite3"
            init_db(path).close()
            status, market_item_id, review_id = deliver(
                path,
                item("source-b", "push-1", content_type="flash_news"),
                decision(),
                use_rule_dedup=False,
            )
            row = delivery_rows(path)[0]
        assert status == "sent"
        assert row[:4] == (market_item_id, review_id, "sent", "push")
        payload = json.loads(row[5])
        assert payload["market_item_id"] == market_item_id
        assert payload["source_item_id"] == "push-1"
        assert len(cards) == 1
        card_text = json.dumps(cards[0], ensure_ascii=False)
        assert "HBM 产能扩张" in card_text
        assert "公司确认新增 HBM 产线" in card_text
    finally:
        market_delivery.send_card = original_send


def test_different_source_metadata_does_not_select_a_delivery_path() -> None:
    original_send = market_delivery.send_card
    cards: list[dict] = []
    market_delivery.send_card = lambda card: cards.append(card) or True
    try:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "db.sqlite3"
            init_db(path).close()
            first = deliver(
                path,
                item("research-source", "same-1", content_type="research_index"),
                decision(),
                use_rule_dedup=False,
            )
            second = deliver(
                path,
                item(
                    "company-source",
                    "same-2",
                    source_category="official_company",
                    content_type="company_update",
                ),
                decision(),
                use_rule_dedup=False,
            )
        assert first[0] == second[0] == "sent"
        assert len(cards) == 2
        assert [card["config"] for card in cards] == [
            {"wide_screen_mode": True},
            {"wide_screen_mode": True},
        ]
    finally:
        market_delivery.send_card = original_send


def test_duplicate_reservation_blocks_second_send_without_changing_action() -> None:
    original_send = market_delivery.send_card
    calls = 0

    def send(_card):
        nonlocal calls
        calls += 1
        return True

    market_delivery.send_card = send
    try:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "db.sqlite3"
            init_db(path).close()
            result = decision(rule_hits=[holding_rule()])
            first = deliver(path, item("source-c", "dup-1"), result)
            second = deliver(path, item("source-d", "dup-2"), result)
            rows = delivery_rows(path)
        assert first[0] == "sent"
        assert second[0] == "duplicate"
        assert calls == 1
        assert [row[2:4] for row in rows] == [("sent", "push"), ("duplicate", "push")]
        duplicate_payload = json.loads(rows[1][5])
        assert duplicate_payload["first_source"] == "source-c"
    finally:
        market_delivery.send_card = original_send


def test_send_exception_releases_reservation_for_retry() -> None:
    original_send = market_delivery.send_card
    attempts = 0

    def send(_card):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary Feishu failure")
        return True

    market_delivery.send_card = send
    try:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "db.sqlite3"
            init_db(path).close()
            result = decision(rule_hits=[holding_rule()])
            first = deliver(path, item("source-e", "retry-1"), result)
            second = deliver(path, item("source-f", "retry-2"), result)
            rows = delivery_rows(path)
        assert first[0] == "failed"
        assert second[0] == "sent"
        assert attempts == 2
        assert [row[2] for row in rows] == ["failed", "sent"]
        assert "temporary Feishu failure" in rows[0][4]
    finally:
        market_delivery.send_card = original_send


def test_feedback_card_uses_market_item_id_and_retains_card_base() -> None:
    original_enabled = market_delivery.feedback_enabled
    original_configured = market_delivery.feishu_app_configured
    original_send = market_delivery.send_interactive_card
    original_append = market_delivery.append_feedback_actions
    sent_cards: list[dict] = []
    identities = []
    market_delivery.feedback_enabled = lambda: True
    market_delivery.feishu_app_configured = lambda: True
    market_delivery.append_feedback_actions = (
        lambda card, identity: identities.append(identity) or {**card, "elements": [*(card.get("elements") or []), {"tag": "action", "actions": []}]}
    )
    market_delivery.send_interactive_card = lambda card: sent_cards.append(card) or SimpleNamespace(ok=True)
    try:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "db.sqlite3"
            init_db(path).close()
            status, market_item_id, _ = deliver(
                path,
                item("source-g", "feedback-1"),
                decision(),
                use_rule_dedup=False,
            )
            row = delivery_rows(path)[0]
        assert status == "sent"
        assert len(sent_cards) == 1
        assert identities[0].market_item_id == market_item_id
        payload = json.loads(row[5])
        assert payload["market_item_id"] == market_item_id
        assert payload["_feedback_card_base"]["config"] == {"wide_screen_mode": True}
    finally:
        market_delivery.feedback_enabled = original_enabled
        market_delivery.feishu_app_configured = original_configured
        market_delivery.send_interactive_card = original_send
        market_delivery.append_feedback_actions = original_append


def main() -> int:
    test_non_push_action_is_recorded_without_sending()
    test_push_uses_direct_unified_identity_and_one_card_builder()
    test_different_source_metadata_does_not_select_a_delivery_path()
    test_duplicate_reservation_blocks_second_send_without_changing_action()
    test_send_exception_releases_reservation_for_retry()
    test_feedback_card_uses_market_item_id_and_retains_card_base()
    print("market delivery checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
