"""Unified event ingestion and analysis helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from market_db import DEFAULT_DB_PATH
from decision_engine import attach_decision_to_event_analysis
from market_delivery import (
    compact_event_analysis_lines,
    deliver_event,
    feishu_webhook_fingerprint,
    record_delivery,
    simple_event_card,
)
from market_item import NormalizedMarketItem, decision_result_from_payload, item_from_event_mapping
from market_interpreter import interpret_market_item
from market_review_store import (
    event_content_hash,
    event_row_by_id,
    insert_event_analysis,
    latest_event_analysis,
    load_enabled_holdings as store_load_enabled_holdings,
    update_event_analysis,
    upsert_event_record,
)
from push_rules import apply_event_push_rules


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

    event = event_mapping_from_row(event_row)
    decision_fields = apply_event_rules_to_analysis(event_row, {}, db_path=db_path)
    decision = decision_result_from_payload(decision_fields)
    if decision is None:
        raise RuntimeError(f"事件决策结果缺失：{event_id}")
    interpretation = interpret_market_item(
        normalized_event_item(event),
        decision,
        content=build_portfolio_event_input(event_row, db_path=db_path),
        task="为一条已完成规则决策的公告、研报、快讯或异动信息生成极简实时摘要。",
        intro="请解读以下持仓事件",
        mode="holdings",
        forbidden_mode="event",
        extra_notes=["输入包含直接相关持仓和全部已配置持仓；只可使用给定关系，不要自行扩展股票映射。"],
        user_agent="surveil-portfolio-event-llm/0.2",
    )
    interpretation_payload = interpretation.to_dict()
    parsed = {
        **decision_fields,
        "core_content": interpretation.core_content,
        "brief_reason": interpretation.brief_reason,
        "related_holdings": list(interpretation.related_targets),
        "notes": list(interpretation.notes),
        "llm_judgement": interpretation.llm_judgement,
        "_interpretation_result": interpretation_payload,
        "_model": interpretation.model,
        "llm_mode": "thin",
    }
    importance, classification, direction, impact_duration, should_push = analysis_record_fields(parsed)
    insert_event_analysis(
        event_id,
        task,
        interpretation.model,
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
    decision = decision_result_from_payload(parsed)
    importance = decision.importance if decision and decision.importance != "unknown" else infer_importance(parsed)
    classification = ""
    incremental = parsed.get("incremental_view")
    if isinstance(incremental, dict):
        classification = str(incremental.get("classification") or "")
    elif parsed.get("rule_forced_push") or (decision and decision.rule_hits):
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
        refresh=True,
    )


def event_mapping_from_row(event_row: dict[str, Any]) -> dict[str, Any]:
    try:
        symbols = json.loads(str(event_row.get("symbols_json") or "[]"))
    except json.JSONDecodeError:
        symbols = []
    try:
        raw = json.loads(str(event_row.get("raw_json") or "{}"))
    except json.JSONDecodeError:
        raw = {}
    return {
        "source": event_row.get("source"),
        "source_event_id": raw.get("source_event_id") if isinstance(raw, dict) else "",
        "event_type": event_row.get("event_type"),
        "title": event_row.get("title"),
        "summary": event_row.get("summary"),
        "full_text": event_row.get("full_text"),
        "url": event_row.get("url"),
        "published_at": event_row.get("published_at"),
        "symbols": symbols if isinstance(symbols, list) else [],
        "raw": raw if isinstance(raw, dict) else {},
    }


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
    decision = decision_result_from_payload(parsed)
    if decision is not None:
        return decision.should_push
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


def maybe_deliver_event(event_id: int, analysis: dict[str, Any], db_path: Path = DEFAULT_DB_PATH) -> str:
    """Compatibility wrapper: refresh decision, then delegate delivery execution."""
    event_row = event_row_by_id(event_id, db_path)
    if not event_row:
        raise RuntimeError(f"事件不存在：{event_id}")
    analysis = apply_event_rules_to_analysis(event_row, analysis, db_path=db_path)
    return deliver_event(event_id, analysis, db_path=db_path)
