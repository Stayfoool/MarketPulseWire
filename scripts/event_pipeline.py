"""Unified event ingestion and analysis helpers."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from feishu import send_card_with_response
from llm_analysis import call_chat_completion_with_prompts, format_llm_analysis
from market_db import DEFAULT_DB_PATH
from decision_engine import attach_decision_to_event_analysis
from market_card_view import card_targets, decision_reason, interpretation_core, interpretation_reason
from market_item import NormalizedMarketItem, item_from_event_mapping
from market_interpreter import thin_system_prompt, thin_user_prompt_template
from market_review_store import (
    event_content_hash,
    event_row_by_id,
    insert_event_analysis,
    latest_event_analysis,
    load_enabled_holdings as store_load_enabled_holdings,
    record_event_delivery,
    update_event_analysis,
    upsert_event_record,
)
from push_rules import apply_event_push_rules
from rule_alert_dedup import confirm_rule_alert, release_rule_alert, reserve_rule_alert


PORTFOLIO_EVENT_SYSTEM_PROMPT = thin_system_prompt(
    task="为一条已通过规则预筛的公告、研报、快讯或异动信息生成极简实时摘要。"
)


PORTFOLIO_EVENT_USER_PROMPT = thin_user_prompt_template(
    intro="请分析以下持仓事件",
    mode="holdings",
    forbidden_mode="event",
    extra_notes=["输入是包含事件、直接相关持仓和全部已配置持仓的 JSON；只可使用这些已给出的关系，不要自行扩展股票映射。"],
)


def content_hash(*parts: str) -> str:
    return event_content_hash(*parts)


EVENT_SOURCE_CONTEXT: dict[str, dict[str, str]] = {
    "sina_flash": {"source_category": "news_media", "collector": "sina_flash"},
    "sina_stock_news": {"source_category": "portfolio_stock_news", "collector": "sina_stock_news"},
    "ifind_notice": {"source_category": "company_disclosures", "collector": "ifind_batch"},
    "ifind_report": {"source_category": "company_disclosures", "collector": "ifind_batch"},
}


def event_source_context(source: str) -> dict[str, str]:
    source = str(source or "").strip()
    return dict(EVENT_SOURCE_CONTEXT.get(source, {"source_category": "", "collector": source}))


def _event_without_normalized_audit(event: dict[str, Any]) -> dict[str, Any]:
    updated = dict(event)
    raw = dict(updated.get("raw") or {})
    raw.pop("_normalized_market_item", None)
    updated["raw"] = raw
    return updated


def normalized_event_item(event: dict[str, Any]) -> NormalizedMarketItem:
    base = _event_without_normalized_audit(event)
    context = event_source_context(str(base.get("source") or ""))
    return item_from_event_mapping(
        base,
        source_category=context.get("source_category", ""),
        collector=context.get("collector", ""),
    )


def normalized_event_audit_payload(item: NormalizedMarketItem) -> dict[str, Any]:
    """Return a compact audit payload without duplicating raw/full_text."""
    raw_keys = sorted(str(key) for key in item.raw if key != "_normalized_market_item")
    return {
        "schema": "NormalizedMarketItem/v1",
        "source": item.source,
        "source_category": item.source_category,
        "collector": item.collector,
        "content_type": item.content_type,
        "title": item.title,
        "summary": item.summary,
        "url": item.url,
        "published_at": item.published_at,
        "first_seen_at": item.first_seen_at,
        "symbols": list(item.symbols),
        "themes": list(item.themes),
        "dedupe_key": item.dedupe_key,
        "source_event_id": str(item.raw.get("source_event_id") or ""),
        "access_note": item.access_note,
        "full_text_chars": len(item.full_text),
        "raw_keys": raw_keys,
    }


def event_with_normalized_market_item_audit(event: dict[str, Any]) -> dict[str, Any]:
    updated = _event_without_normalized_audit(event)
    raw = dict(updated.get("raw") or {})
    raw["_normalized_market_item"] = normalized_event_audit_payload(normalized_event_item(updated))
    updated["raw"] = raw
    return updated


def load_enabled_holdings(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    return store_load_enabled_holdings(db_path)


def upsert_event(event: dict[str, Any], db_path: Path = DEFAULT_DB_PATH) -> tuple[int, bool]:
    """Insert an event and return (event_id, inserted)."""
    return upsert_event_record(event_with_normalized_market_item_audit(event), db_path)


def analyze_event(event_id: int, task: str = "portfolio_event", db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    event_row = event_row_by_id(event_id, db_path)
    if not event_row:
        raise RuntimeError(f"事件不存在：{event_id}")
    existing = latest_event_analysis(event_id, task, db_path)
    if existing:
        parsed = existing["analysis"]
        updated = apply_event_rules_to_analysis(event_row, parsed, db_path=db_path)
        if updated != parsed:
            importance, classification, direction, impact_duration, should_push = analysis_record_fields(updated)
            update_event_analysis(
                int(existing["id"]),
                importance=importance,
                classification=classification,
                direction=direction,
                impact_duration=impact_duration,
                should_push=should_push,
                analysis=updated,
                db_path=db_path,
            )
        return updated

    text = build_portfolio_event_input(
        {
            "source": event_row["source"],
            "event_type": event_row["event_type"],
            "title": event_row["title"],
            "published_at": event_row["published_at"],
            "url": event_row["url"],
            "summary": event_row["summary"],
            "full_text": event_row["full_text"],
            "symbols_json": event_row["symbols_json"],
            "raw_json": event_row["raw_json"],
        },
        db_path=db_path,
    )
    parsed, model = call_chat_completion_with_prompts(
        PORTFOLIO_EVENT_SYSTEM_PROMPT,
        PORTFOLIO_EVENT_USER_PROMPT.replace("{source}", str(event_row["source"] or ""))
        .replace("{title}", str(event_row["title"] or ""))
        .replace("{published_at}", str(event_row["published_at"] or ""))
        .replace("{content}", text),
        user_agent="surveil-portfolio-event-llm/0.1",
    )
    parsed["_model"] = model
    parsed["llm_mode"] = "thin"
    parsed = apply_event_rules_to_analysis(event_row, parsed, db_path=db_path)
    importance, classification, direction, impact_duration, should_push = analysis_record_fields(parsed)
    insert_event_analysis(
        event_id,
        task,
        model,
        importance=importance,
        classification=classification,
        direction=direction,
        impact_duration=impact_duration,
        should_push=should_push,
        analysis=parsed,
        db_path=db_path,
    )
    return parsed


def analysis_record_fields(parsed: dict[str, Any]) -> tuple[str, str, str, str, int]:
    importance = infer_importance(parsed)
    classification = ""
    incremental = parsed.get("incremental_view")
    if isinstance(incremental, dict):
        classification = str(incremental.get("classification") or "")
    elif parsed.get("rule_forced_push"):
        classification = "规则命中"
    direction = ""
    impact_duration = ""
    price_impact = parsed.get("price_impact")
    if isinstance(price_impact, dict):
        direction = str(price_impact.get("direction") or "")
        impact_duration = str(price_impact.get("duration") or "")
    should_push = 1 if should_push_analysis(parsed, importance) else 0
    return importance, classification, direction, impact_duration, should_push


def apply_event_rules_to_analysis(
    event_row: dict[str, Any], analysis: dict[str, Any], *, db_path: Path = DEFAULT_DB_PATH
) -> dict[str, Any]:
    try:
        symbols = json.loads(str(event_row.get("symbols_json") or "[]"))
    except json.JSONDecodeError:
        symbols = []
    try:
        raw = json.loads(str(event_row.get("raw_json") or "{}"))
    except json.JSONDecodeError:
        raw = {}
    event = {
        "source": event_row.get("source"),
        "event_type": event_row.get("event_type"),
        "title": event_row.get("title"),
        "summary": event_row.get("summary"),
        "full_text": event_row.get("full_text"),
        "url": event_row.get("url"),
        "published_at": event_row.get("published_at"),
        "raw": raw if isinstance(raw, dict) else {},
    }
    symbol_set = {str(symbol).upper() for symbol in symbols if str(symbol).strip()}
    holdings = load_enabled_holdings(db_path)
    updated = apply_event_push_rules(event, analysis, holdings=holdings, symbols=symbol_set)
    return attach_decision_to_event_analysis(
        str(event.get("source") or ""),
        event,
        updated,
        holdings=holdings,
        symbols=symbol_set,
    )


def build_portfolio_event_input(event: dict[str, Any], db_path: Path = DEFAULT_DB_PATH) -> str:
    try:
        symbols = json.loads(str(event.get("symbols_json") or "[]"))
    except json.JSONDecodeError:
        symbols = []
    symbol_set = {str(symbol).upper() for symbol in symbols if str(symbol).strip()}
    holdings = load_enabled_holdings(db_path)
    related_holdings = [holding for holding in holdings if str(holding.get("symbol", "")).upper() in symbol_set]
    context = {
        "event": event,
        "event_symbols": sorted(symbol_set),
        "directly_related_holdings": related_holdings,
        "all_configured_holdings": [
            {
                "symbol": holding.get("symbol", ""),
                "name": holding.get("name", ""),
                "full_name": holding.get("full_name", ""),
                "aliases": holding.get("aliases", []),
            }
            for holding in holdings
        ],
    }
    return json.dumps(context, ensure_ascii=False, indent=2)


def infer_importance(parsed: dict[str, Any]) -> str:
    explicit = str(parsed.get("importance") or parsed.get("importance_level") or "").strip()
    if explicit:
        return explicit
    incremental = parsed.get("incremental_view")
    classification = ""
    surprise = ""
    if isinstance(incremental, dict):
        classification = str(incremental.get("classification") or "")
        surprise = str(incremental.get("surprise_level") or "")
    if "增量利好" in classification or "增量利空" in classification:
        return "high" if surprise == "高" else "medium"
    if "无法判断" in classification:
        return "low"
    return "medium" if parsed.get("a_share") or parsed.get("global_equity") else "low"


def normalize_importance(value: str) -> str:
    normalized = value.strip().lower()
    mapping = {
        "高": "high",
        "重要": "high",
        "中": "medium",
        "中等": "medium",
        "低": "low",
        "不重要": "low",
    }
    return mapping.get(normalized, normalized)


def should_push_analysis(parsed: dict[str, Any], importance: str | None = None) -> bool:
    normalized = normalize_importance(str(importance or infer_importance(parsed)))
    push_decision = parsed.get("push_decision")
    if isinstance(push_decision, dict) and "should_push" in push_decision:
        raw = push_decision.get("should_push")
        if isinstance(raw, bool):
            return raw and normalized in {"high", "medium"}
        if isinstance(raw, str):
            wants_push = raw.strip().lower() in {"true", "yes", "1", "y", "是", "推送"}
            return wants_push and normalized in {"high", "medium"}
        return bool(raw) and normalized in {"high", "medium"}
    if normalized in {"high", "medium"}:
        return True
    return False


def thin_event_card_enabled() -> bool:
    return os.getenv("SURVEIL_THIN_EVENT_CARD", "1").strip() != "0"


def compact_text(value: str, limit: int = 900) -> str:
    cleaned = " ".join(str(value or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def compact_targets(parsed: dict[str, Any]) -> list[str]:
    targets: list[str] = []
    related_targets = parsed.get("related_targets")
    if isinstance(related_targets, list):
        for item in related_targets:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                code = str(item.get("code") or "").strip()
                label = " ".join(part for part in (name, code) if part)
            else:
                label = str(item).strip()
            if label:
                targets.append(label)
    related = parsed.get("related_holdings")
    if isinstance(related, list):
        for item in related:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                code = str(item.get("code") or "").strip()
                label = " ".join(part for part in (name, code) if part)
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
                if isinstance(item, dict):
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
        lines.append("核心内容：" + compact_text(str(parsed.get("initial_impact") or "模型未给出明确核心内容。"), 260))
    return lines


def maybe_deliver_event(event_id: int, analysis: dict[str, Any], db_path: Path = DEFAULT_DB_PATH) -> str:
    """Deliver when Feishu is configured; otherwise record a skipped delivery."""
    event_row = event_row_by_id(event_id, db_path)
    if not event_row:
        raise RuntimeError(f"事件不存在：{event_id}")
    source = str(event_row["source"] or "")
    title = str(event_row["title"] or "")
    summary = str(event_row["summary"] or "")
    full_text = str(event_row["full_text"] or "")
    url = str(event_row["url"] or "")
    published_at = str(event_row["published_at"] or "")
    analysis = apply_event_rules_to_analysis(event_row, analysis, db_path=db_path)
    if not should_push_analysis(analysis):
        record_delivery(event_id, "feishu", "skipped", {"reason": "未命中强推规则，不即时推送"}, db_path=db_path)
        return "skipped"
    reservation = reserve_rule_alert(
        analysis,
        source=str(source),
        item_id=str(event_id),
        title=str(title),
        published_at=str(published_at or ""),
        db_path=db_path,
    )
    if reservation.get("duplicate"):
        first = reservation.get("first") or {}
        record_delivery(
            event_id,
            "feishu",
            "skipped",
            {
                "reason": "同一国际投行主题报告跨来源去重",
                "first_source": first.get("source"),
                "first_published_at": first.get("published_at"),
                "dedup_key": reservation.get("dedup_key"),
            },
            db_path=db_path,
        )
        return "skipped"
    webhook = os.getenv("FEISHU_WEBHOOK", "").strip()
    if not webhook:
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
        response = send_card_with_response(card)
    except Exception as exc:  # noqa: BLE001 - keep delivery failures isolated
        release_rule_alert(reservation, db_path=db_path)
        record_delivery(
            event_id,
            "feishu",
            "failed",
            {
                "error": str(exc),
                "webhook_fingerprint": feishu_webhook_fingerprint(),
            },
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
            "feishu_body": response.body[:1000],
        },
        db_path=db_path,
    )
    return status


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
        div_markdown(f"**发布时间**：{md_escape(published_at or '未知')}"),
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
