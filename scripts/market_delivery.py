"""Delivery execution for decision-ready market information."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cards import build_market_item_card
from company_event_dedup import COMPANY_EVENT_RULE_ID, company_event_dedup_hits
from feishu import send_card
from feishu_app import configured as feishu_app_configured
from feishu_app import feedback_enabled, send_interactive_card
from industry_fact_dedup import INDUSTRY_FACT_RULE_ID, industry_fact_dedup_hit
from investment_bank_report_dedup import (
    INVESTMENT_BANK_REPORT_RULE_ID,
    investment_bank_report_dedup_hit,
)
from market_card_view import market_result_view
from market_db import DEFAULT_DB_PATH
from macro_event_dedup import MACRO_DEDUP_RULE_IDS, macro_event_dedup_hit
from market_item import DecisionResult, MarketFlowResult
from market_feedback import FeedbackIdentity, append_feedback_actions
from market_store import record_delivery, source_item_id
from market_move_dedup import MARKET_MOVE_RULE_ID, intraday_market_move_dedup_hit
from rule_alert_dedup import confirm_rule_alert, release_rule_alert, reserve_rule_alert, reserve_rule_alert_set


def _duplicate_delivery_payload(reservation: dict[str, Any]) -> dict[str, Any]:
    first = reservation.get("first") or {}
    rule_id = str(reservation.get("rule_id") or "")
    if rule_id == MARKET_MOVE_RULE_ID:
        label = "同一盘中行情事件跨来源去重"
    elif rule_id in MACRO_DEDUP_RULE_IDS:
        label = "同一美国宏观/政策催化事件跨来源去重"
    elif rule_id == "ai_compute_supply_demand":
        label = "同一AI算力供需催化事件跨来源去重"
    elif rule_id == INDUSTRY_FACT_RULE_ID:
        label = "同一产业事实跨来源去重"
    elif rule_id == INVESTMENT_BANK_REPORT_RULE_ID:
        label = "同一投行个股评级/目标价报告跨来源去重"
    elif rule_id == COMPANY_EVENT_RULE_ID:
        label = "同一公司事件事实跨来源去重"
    else:
        label = "同一规则观点跨来源去重"
    return {
        "reason": label,
        "rule_id": reservation.get("rule_id"),
        "first_source": first.get("source"),
        "first_item_id": first.get("item_id"),
        "first_published_at": first.get("published_at"),
        "dedup_key": reservation.get("dedup_key"),
        "dedup_keys": reservation.get("dedup_keys") or [reservation.get("dedup_key")],
        "matched_dedup_key": reservation.get("matched_dedup_key"),
        "covered": reservation.get("covered") or [],
    }


def _reserve_delivery_alert(
    item: dict[str, Any],
    decision: DecisionResult,
    *,
    source: str,
    item_id: str,
    db_path: Path,
) -> dict[str, Any]:
    specialized_hit = (
        macro_event_dedup_hit(item, decision)
        or intraday_market_move_dedup_hit(item, decision)
        or industry_fact_dedup_hit(item, decision)
        or investment_bank_report_dedup_hit(item, decision)
    )
    reservation = reserve_rule_alert(
        decision,
        source=source,
        item_id=item_id,
        title=str(item.get("title") or ""),
        published_at=str(item.get("published_at") or ""),
        delivery_hit=specialized_hit,
        db_path=db_path,
    )
    if reservation.get("applicable"):
        return reservation
    return reserve_rule_alert_set(
        company_event_dedup_hits(item, decision),
        source=source,
        item_id=item_id,
        title=str(item.get("title") or ""),
        published_at=str(item.get("published_at") or ""),
        db_path=db_path,
    )


def deliver_market_item(
    raw_item: dict[str, Any],
    flow_result: MarketFlowResult,
    *,
    market_item_id: int,
    market_review_id: int,
    db_path: Path = DEFAULT_DB_PATH,
    use_rule_dedup: bool = True,
    already_sent: bool = False,
) -> str:
    """Deliver one normalized market item without source or content-type routing."""
    item = flow_result.item
    decision = flow_result.decision
    source = item.source
    item_id = source_item_id(item)

    def persist(
        status: str,
        details: dict[str, Any] | None = None,
        *,
        error: str = "",
    ) -> None:
        record_delivery(
            market_item_id,
            market_review_id,
            status=status,
            decision_action=decision.action,
            payload={
                "market_item_id": market_item_id,
                "source": source,
                "source_item_id": item_id,
                **(details or {}),
            },
            error=error,
            db_path=db_path,
        )

    if already_sent:
        return "existing"
    if decision.action != "push":
        persist("skipped", {"reason": "DecisionResult.action 不是 push"})
        return "skipped"
    reservation: dict[str, Any] = {}
    if use_rule_dedup:
        reservation = _reserve_delivery_alert(
            item.to_dict(),
            decision,
            source=source,
            item_id=item_id,
            db_path=db_path,
        )
        if reservation.get("duplicate"):
            persist("duplicate", _duplicate_delivery_payload(reservation))
            return "duplicate"
    prepared = dict(raw_item)
    prepared["raw"] = dict(flow_result.item.raw)
    prepared["review"] = market_result_view(flow_result)
    card = build_market_item_card(source, prepared, prepared["review"])
    try:
        if feedback_enabled():
            if not feishu_app_configured():
                release_rule_alert(reservation, db_path=db_path)
                persist("skipped", {"reason": "飞书反馈已启用但应用机器人配置不完整"})
                return "skipped"
            feedback_card = append_feedback_actions(card, FeedbackIdentity(market_item_id))
            response = send_interactive_card(feedback_card)
            sent = response.ok
        else:
            sent = send_card(card)
    except Exception as exc:  # noqa: BLE001 - delivery failures must not stop collectors
        release_rule_alert(reservation, db_path=db_path)
        persist("failed", {"reason": "飞书发送异常"}, error=str(exc))
        return "failed"
    if sent:
        confirm_rule_alert(reservation, db_path=db_path)
        persist("sent", {"_feedback_card_base": card if feedback_enabled() else {}})
        return "sent"
    release_rule_alert(reservation, db_path=db_path)
    persist("skipped", {"reason": "飞书发送失败"})
    return "skipped"
