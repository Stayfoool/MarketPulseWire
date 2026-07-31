"""Delivery execution for decision-ready market information.

This module handles article, official-news, and event delivery state. It never
evaluates market rules: a unified DecisionResult must already be present before
delivery execution.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from cards import build_article_card, format_time
from company_event_dedup import COMPANY_EVENT_RULE_ID, company_event_dedup_hits
from feishu import send_card, send_card_with_response
from feishu_app import configured as feishu_app_configured
from feishu_app import feedback_enabled, send_interactive_card
from industry_fact_dedup import INDUSTRY_FACT_RULE_ID, industry_fact_dedup_hit
from investment_bank_report_dedup import (
    INVESTMENT_BANK_REPORT_RULE_ID,
    investment_bank_report_dedup_hit,
)
from market_card_view import decision_basis_reasons, interpretation_core, market_result_view
from market_db import DEFAULT_DB_PATH
from macro_event_dedup import MACRO_DEDUP_RULE_IDS, macro_event_dedup_hit
from market_item import (
    DecisionResult,
    MarketFlowResult,
    article_item_id,
    official_news_item_id,
)
from market_feedback import FeedbackIdentity, append_feedback_actions
from market_store import record_article_delivery, record_event_delivery, source_item_id
from market_move_dedup import MARKET_MOVE_RULE_ID, intraday_market_move_dedup_hit
from rule_alert_dedup import confirm_rule_alert, release_rule_alert, reserve_rule_alert, reserve_rule_alert_set


def compact_text(value: str, limit: int = 900) -> str:
    cleaned = " ".join(str(value or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def compact_event_analysis_lines(parsed: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    core = interpretation_core(parsed) or str(parsed.get("core_content") or "").strip()
    if core:
        lines.append(f"核心内容：{core}")
    push_basis, omitted_basis = decision_basis_reasons(parsed)
    if push_basis:
        lines.append("推送依据：")
        lines.extend(f"- {compact_text(reason, 180)}" for reason in push_basis)
        if omitted_basis:
            lines.append(f"- 另命中 {omitted_basis} 项同级决策规则")
    if not lines:
        fallback = str(parsed.get("initial_impact") or "模型未给出明确核心内容。")
        lines.append("核心内容：" + compact_text(fallback, 260))
    return lines


def feishu_webhook_fingerprint() -> str:
    webhook = os.getenv("FEISHU_WEBHOOK", "").strip()
    if not webhook:
        return ""
    return hashlib.sha256(webhook.encode("utf-8")).hexdigest()[:12]


def record_delivery(
    channel: str,
    status: str,
    payload: dict[str, Any],
    *,
    error: str = "",
    market_item_id: int | None = None,
    market_review_id: int | None = None,
    decision_action: str = "",
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    record_event_delivery(
        channel,
        status,
        payload,
        error=error,
        market_item_id=market_item_id,
        market_review_id=market_review_id,
        decision_action=decision_action,
        db_path=db_path,
    )


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


def _deliver_content_item(
    item_kind: str,
    source: str,
    item: dict[str, Any],
    flow_result: MarketFlowResult,
    *,
    market_item_id: int,
    market_review_id: int,
    db_path: Path = DEFAULT_DB_PATH,
    use_rule_dedup: bool = True,
    already_sent: bool = False,
) -> str:
    decision = flow_result.decision
    item_id = official_news_item_id(item) if item_kind == "official" else article_item_id(item)

    def persist(
        status: str,
        details: dict[str, Any] | None = None,
        *,
        error: str = "",
    ) -> None:
        record_article_delivery(
            market_item_id,
            market_review_id,
            status=status,
            decision_action=decision.action,
            payload={"item_kind": item_kind, "source": source, "item_id": item_id, **(details or {})},
            error=error,
            db_path=db_path,
        )

    if already_sent:
        return "existing"
    if not decision.should_push:
        persist("skipped", {"reason": "DecisionResult.action 不是 push"})
        return "skipped"
    reservation: dict[str, Any] = {}
    if use_rule_dedup:
        reservation = _reserve_delivery_alert(
            item,
            decision,
            source=source,
            item_id=item_id,
            db_path=db_path,
        )
        if reservation.get("duplicate"):
            persist("duplicate", _duplicate_delivery_payload(reservation))
            return "duplicate"
    prepared = dict(item)
    prepared["raw"] = dict(flow_result.item.raw)
    prepared["article_review"] = market_result_view(flow_result)
    card = build_article_card(source, prepared)
    try:
        if feedback_enabled():
            if not feishu_app_configured():
                release_rule_alert(reservation, db_path=db_path)
                persist("skipped", {"reason": "飞书反馈已启用但应用机器人配置不完整"})
                return "skipped"
            feedback_card = append_feedback_actions(card, FeedbackIdentity(item_kind, source, item_id))
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


def deliver_article_review(
    source: str,
    item: dict[str, Any],
    flow_result: MarketFlowResult,
    *,
    market_item_id: int,
    market_review_id: int,
    db_path: Path = DEFAULT_DB_PATH,
    use_rule_dedup: bool = True,
    already_sent: bool = False,
) -> str:
    return _deliver_content_item(
        "article",
        source=source,
        item=item,
        flow_result=flow_result,
        market_item_id=market_item_id,
        market_review_id=market_review_id,
        db_path=db_path,
        use_rule_dedup=use_rule_dedup,
        already_sent=already_sent,
    )


def deliver_official_review(
    source: str,
    item: dict[str, Any],
    flow_result: MarketFlowResult,
    *,
    market_item_id: int,
    market_review_id: int,
    db_path: Path = DEFAULT_DB_PATH,
    already_sent: bool = False,
) -> str:
    return _deliver_content_item(
        "official",
        source=source,
        item=item,
        flow_result=flow_result,
        market_item_id=market_item_id,
        market_review_id=market_review_id,
        db_path=db_path,
        already_sent=already_sent,
    )


def simple_event_card(
    source: str,
    title: str,
    text: str,
    url: str,
    published_at: str,
    analysis_lines: list[str],
) -> dict[str, Any]:
    from cards import div_markdown, md_escape, text_chunks

    elements: list[dict[str, Any]] = [
        div_markdown(f"**来源**：{md_escape(source)}"),
        div_markdown(f"**发布时间**：{md_escape(format_time(published_at))}"),
        div_markdown(f"**标题**\n{md_escape(title)}"),
    ]
    for index, chunk in enumerate(text_chunks(text or "", limit=1000), start=1):
        label = "原文/摘要" if index == 1 else f"原文/摘要（续 {index}）"
        elements.append(div_markdown(f"**{label}**\n{md_escape(chunk)}"))
    elements.append(div_markdown("**中文解读**\n" + md_escape("\n".join(analysis_lines))))
    if url:
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "打开原文"},
                        "type": "primary",
                        "multi_url": {"url": url, "pc_url": url, "ios_url": url, "android_url": url},
                    }
                ],
            }
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": title[:60] or source}},
        "elements": elements,
    }


def deliver_event(
    flow_result: MarketFlowResult,
    *,
    market_item_id: int | None = None,
    market_review_id: int | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> str:
    """Execute a precomputed event decision and persist its delivery outcome."""
    item = flow_result.item
    decision = flow_result.decision
    item_id = source_item_id(item)
    event_row = item.to_dict()
    def persist_delivery(status: str, payload: dict[str, Any], *, error: str = "") -> None:
        record_delivery(
            "feishu",
            status,
            payload,
            error=error,
            market_item_id=market_item_id,
            market_review_id=market_review_id,
            decision_action=decision.action,
            db_path=db_path,
        )

    if not decision.should_push:
        persist_delivery(
            "skipped",
            {"reason": "DecisionResult.action 不是 push", "decision_action": decision.action},
        )
        return "skipped"

    source = item.source
    title = item.title
    summary = item.summary
    full_text = item.full_text
    url = item.url
    published_at = item.published_at
    reservation = _reserve_delivery_alert(
        event_row,
        decision,
        source=source,
        item_id=item_id,
        db_path=db_path,
    )
    if reservation.get("duplicate"):
        first = reservation.get("first") or {}
        rule_id = str(reservation.get("rule_id") or "")
        market_move_duplicate = rule_id == MARKET_MOVE_RULE_ID
        macro_duplicate = rule_id in MACRO_DEDUP_RULE_IDS
        industry_fact_duplicate = rule_id == INDUSTRY_FACT_RULE_ID
        investment_bank_report_duplicate = rule_id == INVESTMENT_BANK_REPORT_RULE_ID
        company_event_duplicate = rule_id == COMPANY_EVENT_RULE_ID
        duplicate_status = (
            market_move_duplicate
            or macro_duplicate
            or industry_fact_duplicate
            or investment_bank_report_duplicate
            or company_event_duplicate
        )
        if market_move_duplicate:
            reason = "同一盘中行情事件跨来源去重"
            dedup_kind = "intraday_market_move"
        elif macro_duplicate:
            reason = "同一美国宏观/政策催化事件跨来源去重"
            dedup_kind = rule_id
        elif industry_fact_duplicate:
            reason = "同一产业事实跨来源去重"
            dedup_kind = "industry_fact"
        elif investment_bank_report_duplicate:
            reason = "同一投行个股评级/目标价报告跨来源去重"
            dedup_kind = "investment_bank_report"
        elif company_event_duplicate:
            reason = "同一公司事件事实跨来源去重"
            dedup_kind = "company_event_fact_set"
        else:
            reason = "同一规则观点跨来源去重"
            dedup_kind = "rule_alert"
        persist_delivery(
            "duplicate" if duplicate_status else "skipped",
            {
                "reason": reason,
                "first_source": first.get("source"),
                "first_item_id": first.get("item_id"),
                "first_published_at": first.get("published_at"),
                "dedup_key": reservation.get("dedup_key"),
                "dedup_keys": reservation.get("dedup_keys") or [reservation.get("dedup_key")],
                "dedup_kind": dedup_kind,
            },
        )
        return "duplicate" if duplicate_status else "skipped"
    if feedback_enabled() and not feishu_app_configured():
        release_rule_alert(reservation, db_path=db_path)
        persist_delivery(
            "skipped",
            {"reason": "飞书反馈已启用但应用机器人配置不完整"},
        )
        return "skipped"
    if not feedback_enabled() and not os.getenv("FEISHU_WEBHOOK", "").strip():
        release_rule_alert(reservation, db_path=db_path)
        persist_delivery("skipped", {"reason": "FEISHU_WEBHOOK 未配置"})
        return "skipped"

    lines = compact_event_analysis_lines(market_result_view(flow_result))
    display_text = compact_text(summary or full_text, 1000)
    card = simple_event_card(source, title, display_text, url, published_at, lines)
    try:
        if feedback_enabled():
            feedback_card = append_feedback_actions(card, FeedbackIdentity("event", source, item_id))
            response = send_interactive_card(feedback_card)
        else:
            response = send_card_with_response(card)
    except Exception as exc:  # noqa: BLE001 - delivery failures must not stop collectors
        release_rule_alert(reservation, db_path=db_path)
        persist_delivery(
            "failed",
            {"error": str(exc), "webhook_fingerprint": feishu_webhook_fingerprint()},
            error=str(exc),
        )
        return "failed"
    status = "sent" if response.ok else "skipped"
    if response.ok:
        confirm_rule_alert(reservation, db_path=db_path)
    else:
        release_rule_alert(reservation, db_path=db_path)
    persist_delivery(
        status,
        {
            "title": title,
            "webhook_fingerprint": feishu_webhook_fingerprint(),
            "feishu_code": response.code,
            "feishu_message": response.message,
            "feishu_message_id": getattr(response, "message_id", ""),
            "feishu_body": response.body[:1000],
            "_feedback_card_base": card if feedback_enabled() else {},
        },
    )
    return status
