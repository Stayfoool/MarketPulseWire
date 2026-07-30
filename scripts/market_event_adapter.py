"""Legacy payload and store adapter for event-shaped market items."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from decision_engine import attach_decision_result_to_event_analysis
from market_flow_adapters import event_with_ingestion_audit, normalized_item_audit_payload
from market_db import DEFAULT_DB_PATH
from market_delivery import deliver_event, record_delivery
from holdings_store import load_enabled_holdings as store_load_enabled_holdings
from market_item import (
    DecisionResult,
    MarketFlowResult,
    NormalizedMarketItem,
    decision_result_from_payload,
    event_content_hash,
    item_from_event_mapping,
)
from source_profiles import runtime_source_profile


EVENT_SOURCE_CONTEXT: dict[str, dict[str, str]] = {
    "sina_flash": {"source_category": "news_media", "publisher_role": "news_media", "collector": "sina_flash"},
    "sina_stock_news": {"source_category": "portfolio_stock_news", "publisher_role": "news_media", "collector": "sina_stock_news"},
    "ifind_notice": {"source_category": "company_disclosures", "collector": "ifind_batch"},
    "company_disclosures": {
        "source_category": "company_disclosures",
        "publisher_role": "company_official",
        "collector": "company_disclosures",
    },
}


def content_hash(*parts: str) -> str:
    return event_content_hash(*parts)


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
    source = str(base.get("source") or "")
    context = event_source_context(source)
    profile = runtime_source_profile(source) or {}
    source_category = str(base.get("source_category") or context.get("source_category") or profile.get("category") or "")
    publisher_role = str(base.get("publisher_role") or context.get("publisher_role") or profile.get("publisher_role") or "")
    if not publisher_role and source_category in {"news_media", "portfolio_stock_news"}:
        publisher_role = "news_media"
    return item_from_event_mapping(
        base,
        source_category=source_category,
        publisher_role=publisher_role,
        collector=str(base.get("collector") or context.get("collector") or profile.get("fetcher") or source),
    )


def normalized_event_audit_payload(item: NormalizedMarketItem) -> dict[str, Any]:
    return normalized_item_audit_payload(item)


def event_with_normalized_market_item_audit(event: dict[str, Any]) -> dict[str, Any]:
    updated = _event_without_normalized_audit(event)
    return event_with_ingestion_audit(updated, normalized_event_item(updated))


def load_enabled_holdings(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    return store_load_enabled_holdings(db_path)


def project_event_analysis(flow_result: MarketFlowResult) -> dict[str, Any]:
    """Project an authoritative flow result into the legacy event-analysis shape."""
    decision_fields = attach_decision_result_to_event_analysis(flow_result.decision, {})
    interpretation = flow_result.interpretation
    parsed = {
        **decision_fields,
        "core_content": interpretation.core_content,
        "_interpretation_result": interpretation.to_dict(),
        "_model": interpretation.model,
        "_market_flow_result": flow_result.audit_payload(),
        "llm_mode": "thin",
    }
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


def build_portfolio_event_input(item: NormalizedMarketItem, db_path: Path = DEFAULT_DB_PATH) -> str:
    symbol_set = {str(symbol).upper() for symbol in item.symbols if str(symbol).strip()}
    holdings = load_enabled_holdings(db_path)
    related_holdings = [holding for holding in holdings if str(holding.get("symbol", "")).upper() in symbol_set]
    context = {
        "event": item.to_dict(),
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
    return bool(decision and decision.should_push)


def maybe_deliver_event(
    item: NormalizedMarketItem,
    analysis: dict[str, Any],
    db_path: Path = DEFAULT_DB_PATH,
    *,
    decision: DecisionResult | None = None,
    market_item_id: int | None = None,
    market_review_id: int | None = None,
) -> str:
    """Delegate delivery execution for an event already stored in unified storage."""
    updated = analysis
    decision = decision or decision_result_from_payload(updated)
    if decision is None:
        record_delivery(
            "feishu",
            "skipped",
            {"reason": "缺少统一 DecisionResult", "contract_error": "missing_decision_result"},
            market_item_id=market_item_id,
            market_review_id=market_review_id,
            db_path=db_path,
        )
        return "missing_decision"
    return deliver_event(
        item,
        updated,
        decision=decision,
        market_item_id=market_item_id,
        market_review_id=market_review_id,
        db_path=db_path,
    )
