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
from db_utils import connect_sqlite
from feishu import send_card, send_card_with_response
from feishu_app import configured as feishu_app_configured
from feishu_app import feedback_enabled, send_interactive_card
from industry_fact_dedup import INDUSTRY_FACT_RULE_ID, industry_fact_dedup_hit
from llm_analysis import format_llm_analysis
from market_card_view import card_targets, decision_reason, interpretation_core, interpretation_reason
from market_db import DEFAULT_DB_PATH
from macro_event_dedup import MACRO_DEDUP_RULE_IDS, macro_event_dedup_hit
from market_item import DecisionResult
from market_feedback import FeedbackIdentity, append_feedback_actions
from market_move_dedup import MARKET_MOVE_RULE_ID, intraday_market_move_dedup_hit
from market_review_store import (
    article_item_id,
    event_row_by_id,
    mark_article_pushed,
    mark_official_pushed,
    official_news_item_id,
    record_event_delivery,
    save_article_review,
)
from rule_alert_dedup import confirm_rule_alert, release_rule_alert, reserve_rule_alert


def thin_event_card_enabled() -> bool:
    return os.getenv("SURVEIL_THIN_EVENT_CARD", "1").strip() != "0"


def compact_text(value: str, limit: int = 900) -> str:
    cleaned = " ".join(str(value or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def compact_targets(parsed: dict[str, Any]) -> list[str]:
    targets: list[str] = []
    for key in ("related_targets", "related_holdings"):
        values = parsed.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                code = str(item.get("code") or "").strip()
                label = " ".join(part for part in (name, code) if part)
            else:
                label = str(item).strip()
            if label:
                targets.append(label)
    for section_key in ("a_share", "global_equity"):
        section = parsed.get(section_key)
        if not isinstance(section, dict):
            continue
        for direction in ("positive", "negative"):
            values = section.get(direction)
            if not isinstance(values, list):
                continue
            for item in values:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                code = str(item.get("code") or "").strip()
                label = " ".join(part for part in (name, code) if part)
                if label:
                    targets.append(label)
    result: list[str] = []
    seen: set[str] = set()
    for target in targets:
        key = target.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(target)
        if len(result) >= 5:
            break
    return result


def compact_event_analysis_lines(parsed: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    core = interpretation_core(parsed) or str(parsed.get("core_content") or "").strip()
    if core:
        lines.append(f"核心内容：{core}")
    reason = interpretation_reason(parsed) or str(parsed.get("brief_reason") or "").strip()
    push_decision = parsed.get("push_decision")
    if not reason and isinstance(push_decision, dict):
        reason = str(push_decision.get("reason") or "").strip()
    if not reason:
        reason = decision_reason(parsed)
    if reason:
        lines.append("为什么推送：" + compact_text(reason, 260))
    targets = card_targets(parsed, fallback_targets=compact_targets(parsed))
    if targets:
        lines.append("相关标的：" + "；".join(targets))
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
    event_id: int,
    channel: str,
    status: str,
    payload: dict[str, Any],
    *,
    error: str = "",
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    record_event_delivery(event_id, channel, status, payload, error=error, db_path=db_path)


def _duplicate_article_review(
    source: str,
    item: dict[str, Any],
    review: dict[str, Any],
    reservation: dict[str, Any],
    db_path: Path,
) -> None:
    first = reservation.get("first") or {}
    rule_id = str(reservation.get("rule_id") or "")
    if rule_id == MARKET_MOVE_RULE_ID:
        note = (
            "同一盘中行情事件跨来源去重：已由 "
            f"{first.get('source') or '其他来源'} 在 {first.get('published_at') or '较早时间'} 提醒。"
        )
    elif rule_id in MACRO_DEDUP_RULE_IDS:
        note = (
            "同一美国宏观/政策催化事件跨来源去重：已由 "
            f"{first.get('source') or '其他来源'} 在 {first.get('published_at') or '较早时间'} 提醒。"
        )
    elif rule_id == "ai_compute_supply_demand":
        note = (
            "同一AI算力供需催化事件跨来源去重：已由 "
            f"{first.get('source') or '其他来源'} 在 {first.get('published_at') or '较早时间'} 提醒。"
        )
    elif rule_id == INDUSTRY_FACT_RULE_ID:
        note = (
            "同一产业事实跨来源去重：已由 "
            f"{first.get('source') or '其他来源'} 在 {first.get('published_at') or '较早时间'} 提醒。"
        )
    else:
        note = (
            "同一规则观点跨来源去重：已由 "
            f"{first.get('source') or '其他来源'} 在 {first.get('published_at') or '较早时间'} 提醒。"
        )
    updated = dict(review)
    updated["push_now"] = False
    updated["reason"] = f"{updated.get('reason') or ''}\n{note}".strip()
    raw = dict(updated.get("raw") or {})
    raw["rule_alert_dedup"] = reservation
    updated["raw"] = raw
    with connect_sqlite(db_path) as conn:
        save_article_review(conn, source, item, updated)


def _reserve_delivery_alert(
    payload: dict[str, Any],
    item: dict[str, Any],
    decision: DecisionResult,
    *,
    source: str,
    item_id: str,
    db_path: Path,
) -> dict[str, Any]:
    return reserve_rule_alert(
        payload,
        source=source,
        item_id=item_id,
        title=str(item.get("title") or ""),
        published_at=str(item.get("published_at") or ""),
        delivery_hit=(
            macro_event_dedup_hit(item, decision)
            or intraday_market_move_dedup_hit(item, decision)
            or industry_fact_dedup_hit(item, decision)
        ),
        db_path=db_path,
    )


def deliver_article_review(
    source: str,
    item: dict[str, Any],
    review: dict[str, Any],
    *,
    decision: DecisionResult,
    db_path: Path = DEFAULT_DB_PATH,
    analysis_lines_prefix: list[str] | None = None,
    use_rule_dedup: bool = True,
) -> str:
    """Deliver a pre-decided article review and update compatibility state."""
    if not decision.should_push or review.get("pushed_at"):
        return "skipped"
    item_id = article_item_id(item)
    reservation: dict[str, Any] = {}
    if use_rule_dedup:
        reservation = _reserve_delivery_alert(
            review,
            item,
            decision,
            source=source,
            item_id=item_id,
            db_path=db_path,
        )
        if reservation.get("duplicate"):
            _duplicate_article_review(source, item, review, reservation, db_path)
            return "duplicate"
    prepared = dict(item)
    prepared["article_review"] = review
    prepared["analysis_thinking"] = "enabled"
    prepared["analysis_max_tokens"] = int(os.getenv("LLM_HIGH_IMPORTANCE_MAX_OUTPUT_TOKENS", "1800"))
    if analysis_lines_prefix:
        prepared["analysis_lines_prefix"] = list(analysis_lines_prefix)
    card = build_article_card(source, prepared)
    if feedback_enabled():
        if not feishu_app_configured():
            release_rule_alert(reservation, db_path=db_path)
            return "skipped"
        response = send_interactive_card(
            append_feedback_actions(card, FeedbackIdentity("article", source, item_id))
        )
        sent = response.ok
    else:
        sent = send_card(card)
    if sent:
        confirm_rule_alert(reservation, db_path=db_path)
        with connect_sqlite(db_path) as conn:
            mark_article_pushed(conn, source, item_id)
        return "sent"
    release_rule_alert(reservation, db_path=db_path)
    return "skipped"


def deliver_official_review(
    source: str,
    item: dict[str, Any],
    review: dict[str, Any],
    *,
    decision: DecisionResult,
    analysis_lines: list[str],
    db_path: Path = DEFAULT_DB_PATH,
) -> str:
    """Deliver a pre-decided official-news review and update compatibility state."""
    if not decision.should_push or review.get("pushed_at"):
        return "skipped"
    prepared = dict(item)
    prepared["article_review"] = review
    prepared["analysis_lines"] = list(analysis_lines)
    item_id = official_news_item_id(item)
    card = build_article_card(source, prepared)
    if feedback_enabled():
        if not feishu_app_configured():
            return "skipped"
        response = send_interactive_card(
            append_feedback_actions(card, FeedbackIdentity("official", source, item_id))
        )
        sent = response.ok
    else:
        sent = send_card(card)
    if not sent:
        return "skipped"
    with connect_sqlite(db_path) as conn:
        mark_official_pushed(conn, source, item_id)
    return "sent"


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
    event_id: int,
    analysis: dict[str, Any],
    *,
    decision: DecisionResult,
    db_path: Path = DEFAULT_DB_PATH,
) -> str:
    """Execute a precomputed event decision and persist its delivery outcome."""
    event_row = event_row_by_id(event_id, db_path)
    if not event_row:
        raise RuntimeError(f"事件不存在：{event_id}")
    if not decision.should_push:
        record_delivery(
            event_id,
            "feishu",
            "skipped",
            {"reason": "DecisionResult.action 不是 push", "decision_action": decision.action},
            db_path=db_path,
        )
        return "skipped"

    source = str(event_row["source"] or "")
    title = str(event_row["title"] or "")
    summary = str(event_row["summary"] or "")
    full_text = str(event_row["full_text"] or "")
    url = str(event_row["url"] or "")
    published_at = str(event_row["published_at"] or "")
    reservation = _reserve_delivery_alert(
        analysis,
        event_row,
        decision,
        source=source,
        item_id=str(event_id),
        db_path=db_path,
    )
    if reservation.get("duplicate"):
        first = reservation.get("first") or {}
        rule_id = str(reservation.get("rule_id") or "")
        market_move_duplicate = rule_id == MARKET_MOVE_RULE_ID
        macro_duplicate = rule_id in MACRO_DEDUP_RULE_IDS
        industry_fact_duplicate = rule_id == INDUSTRY_FACT_RULE_ID
        duplicate_status = market_move_duplicate or macro_duplicate or industry_fact_duplicate
        if market_move_duplicate:
            reason = "同一盘中行情事件跨来源去重"
            dedup_kind = "intraday_market_move"
        elif macro_duplicate:
            reason = "同一美国宏观/政策催化事件跨来源去重"
            dedup_kind = rule_id
        elif industry_fact_duplicate:
            reason = "同一产业事实跨来源去重"
            dedup_kind = "industry_fact"
        else:
            reason = "同一规则观点跨来源去重"
            dedup_kind = "rule_alert"
        record_delivery(
            event_id,
            "feishu",
            "duplicate" if duplicate_status else "skipped",
            {
                "reason": reason,
                "first_source": first.get("source"),
                "first_item_id": first.get("item_id"),
                "first_published_at": first.get("published_at"),
                "dedup_key": reservation.get("dedup_key"),
                "dedup_kind": dedup_kind,
            },
            db_path=db_path,
        )
        return "duplicate" if duplicate_status else "skipped"
    if feedback_enabled() and not feishu_app_configured():
        release_rule_alert(reservation, db_path=db_path)
        record_delivery(
            event_id,
            "feishu",
            "skipped",
            {"reason": "飞书反馈已启用但应用机器人配置不完整"},
            db_path=db_path,
        )
        return "skipped"
    if not feedback_enabled() and not os.getenv("FEISHU_WEBHOOK", "").strip():
        release_rule_alert(reservation, db_path=db_path)
        record_delivery(event_id, "feishu", "skipped", {"reason": "FEISHU_WEBHOOK 未配置"}, db_path=db_path)
        return "skipped"

    if thin_event_card_enabled():
        lines = compact_event_analysis_lines(analysis)
        display_text = compact_text(summary or full_text, 1000)
    else:
        lines = format_llm_analysis(analysis, str(analysis.get("_model") or "llm"))
        display_text = summary or full_text
    card = simple_event_card(source, title, display_text, url, published_at, lines)
    try:
        if feedback_enabled():
            response = send_interactive_card(
                append_feedback_actions(card, FeedbackIdentity("event", source, str(event_id)))
            )
        else:
            response = send_card_with_response(card)
    except Exception as exc:  # noqa: BLE001 - delivery failures must not stop collectors
        release_rule_alert(reservation, db_path=db_path)
        record_delivery(
            event_id,
            "feishu",
            "failed",
            {"error": str(exc), "webhook_fingerprint": feishu_webhook_fingerprint()},
            error=str(exc),
            db_path=db_path,
        )
        return "failed"
    status = "sent" if response.ok else "skipped"
    if response.ok:
        confirm_rule_alert(reservation, db_path=db_path)
    else:
        release_rule_alert(reservation, db_path=db_path)
    record_delivery(
        event_id,
        "feishu",
        status,
        {
            "title": title,
            "webhook_fingerprint": feishu_webhook_fingerprint(),
            "feishu_code": response.code,
            "feishu_message": response.message,
            "feishu_message_id": getattr(response, "message_id", ""),
            "feishu_body": response.body[:1000],
        },
        db_path=db_path,
    )
    return status
