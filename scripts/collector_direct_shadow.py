"""Report-only direct decision shadow helpers for collectors."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from decision_engine import decide_market_item
from market_db import DEFAULT_DB_PATH
from market_event_adapter import normalized_event_item
from market_item import NormalizedMarketItem, item_from_article_mapping


def safe_load_shadow_holdings(db_path: Path = DEFAULT_DB_PATH) -> tuple[list[dict[str, Any]], str]:
    """Load holdings for report-only decision shadow.

    The returned error string is non-empty when loading fails. Callers can still
    run direct shadow with an empty holdings list to keep the report complete.
    """
    if not db_path.exists():
        return [], ""
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'portfolio_holdings'"
            ).fetchone()
            if not table:
                return [], ""
            rows = conn.execute(
                """
                SELECT symbol, name, full_name, aliases_json, raw_json
                FROM portfolio_holdings
                WHERE enabled = 1
                ORDER BY symbol
                """
            ).fetchall()
    except sqlite3.Error as exc:
        return [], f"{type(exc).__name__}: {exc}"

    holdings: list[dict[str, Any]] = []
    for symbol, name, full_name, aliases_json, raw_json in rows:
        try:
            aliases = json.loads(aliases_json or "[]")
        except json.JSONDecodeError:
            aliases = []
        try:
            raw = json.loads(raw_json or "{}")
        except json.JSONDecodeError:
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        holdings.append(
            {
                "symbol": symbol,
                "name": name,
                "full_name": full_name or "",
                "aliases": aliases if isinstance(aliases, list) else [],
                "news_keywords": raw.get("news_keywords") if isinstance(raw.get("news_keywords"), list) else [],
                "news_exclude_keywords": (
                    raw.get("news_exclude_keywords") if isinstance(raw.get("news_exclude_keywords"), list) else []
                ),
                "raw": raw,
            }
        )
    return holdings, ""


def rule_ids(rules: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for rule in rules:
        rule_id = str(rule.get("rule_id") or "").strip()
        if rule_id and rule_id not in result:
            result.append(rule_id)
    return result


def direct_decision_payload(
    source: str,
    item: dict[str, Any],
    *,
    source_category: str,
    collector: str,
    content_type: str,
    holdings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized = item_from_article_mapping(
        source,
        item,
        source_category=source_category,
        collector=collector,
        content_type=content_type,
    )
    decision = decide_market_item(normalized, holdings=holdings or [])
    return {
        "ok": True,
        "normalized_item": compact_normalized_item(normalized),
        "decision": {
            "action": decision.action,
            "importance": decision.importance,
            "should_push": decision.should_push,
            "brief_reason": decision.brief_reason,
            "reason": decision.reason,
            "rule_hit_ids": rule_ids(decision.rule_hits),
            "candidate_rule_ids": rule_ids(decision.candidate_rules),
            "dedup": dict(decision.dedup),
            "need_llm_interpretation": decision.need_llm_interpretation,
            "need_limited_llm_judgement": decision.need_limited_llm_judgement,
            "audit": dict(decision.audit_json),
        },
    }


def _target_labels(rules: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    for rule in rules:
        for target in rule.get("affected_targets") or []:
            label = str(target or "").strip()
            if label:
                labels.append(label)
        for target in rule.get("related_targets") or []:
            if not isinstance(target, dict):
                continue
            name = str(target.get("name") or "").strip()
            code = str(target.get("code") or "").strip()
            label = " ".join(part for part in (name, code) if part)
            if label:
                labels.append(label)
    return list(dict.fromkeys(labels))[:10]


def _compact_rule(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_id": str(rule.get("rule_id") or ""),
        "importance": str(rule.get("importance") or ""),
        "brief_reason": str(rule.get("brief_reason") or rule.get("reason") or ""),
        "targets": _target_labels([rule]),
        "dedup_key": str(rule.get("dedup_key") or ""),
        "dedup_lookback_days": rule.get("dedup_lookback_days"),
    }


def direct_event_decision_payload(
    event: dict[str, Any],
    *,
    holdings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate an event without LLM, delivery, dedup reservation, or writes."""
    normalized = normalized_event_item(event)
    decision = decide_market_item(normalized, holdings=holdings or [])
    would_send = decision.action == "push"
    return {
        "ok": True,
        "normalized_item": compact_normalized_item(normalized),
        "decision": {
            "action": decision.action,
            "importance": decision.importance,
            "brief_reason": decision.brief_reason,
            "reason": decision.reason,
            "rule_hit_ids": rule_ids(decision.rule_hits),
            "candidate_rule_ids": rule_ids(decision.candidate_rules),
            "matched_rules": [_compact_rule(rule) for rule in decision.rule_hits],
            "targets": _target_labels(decision.rule_hits),
            "symbols": list(normalized.symbols),
            "need_llm_interpretation": decision.need_llm_interpretation,
            "need_limited_llm_judgement": decision.need_limited_llm_judgement,
            "audit": dict(decision.audit_json),
        },
        "delivery_intent": {
            "would_send": would_send,
            "would_skip": not would_send,
            "reason": "DecisionResult.action == push" if would_send else f"DecisionResult.action == {decision.action}",
        },
        "dedup_intent": {
            **dict(decision.dedup),
            "reservation_attempted": False,
            "reservation_reason": "event direct dry-run never reserves delivery dedup keys",
        },
    }


def safe_direct_event_decision_payload(
    event: dict[str, Any],
    *,
    holdings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        return direct_event_decision_payload(event, holdings=holdings)
    except Exception as exc:  # noqa: BLE001 - keep the remaining dry-run report auditable.
        return {
            "ok": False,
            "source": str(event.get("source") or ""),
            "source_event_id": str(event.get("source_event_id") or ""),
            "error": f"{type(exc).__name__}: {exc}",
        }


def safe_direct_decision_payload(
    source: str,
    item: dict[str, Any],
    *,
    source_category: str,
    collector: str,
    content_type: str,
    holdings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        return direct_decision_payload(
            source,
            item,
            source_category=source_category,
            collector=collector,
            content_type=content_type,
            holdings=holdings,
        )
    except Exception as exc:  # noqa: BLE001 - one bad item must not fail a shadow collector run.
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def compact_normalized_item(item: NormalizedMarketItem) -> dict[str, Any]:
    return {
        "source": item.source,
        "source_category": item.source_category,
        "collector": item.collector,
        "content_type": item.content_type,
        "dedupe_key": item.dedupe_key,
        "title": item.title,
        "url": item.url,
        "published_at": item.published_at,
        "symbols": list(item.symbols),
        "themes": list(item.themes),
        "access_note": item.access_note,
    }


def attach_direct_decision_shadow(
    candidate: dict[str, Any],
    source: str,
    item: dict[str, Any],
    *,
    source_category: str,
    collector: str,
    content_type: str,
    holdings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    updated = dict(candidate)
    updated["direct_shadow"] = safe_direct_decision_payload(
        source,
        item,
        source_category=source_category,
        collector=collector,
        content_type=content_type,
        holdings=holdings,
    )
    return updated


def direct_shadow_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    candidates = [
        item
        for row in rows
        for item in (row.get("candidates") if isinstance(row.get("candidates"), list) else [])
        if isinstance(item, dict) and isinstance(item.get("direct_shadow"), dict)
    ]
    decisions = [
        item["direct_shadow"].get("decision")
        for item in candidates
        if item["direct_shadow"].get("ok") and isinstance(item["direct_shadow"].get("decision"), dict)
    ]
    return {
        "direct_shadow_candidates": len(candidates),
        "direct_shadow_errors": sum(1 for item in candidates if not item["direct_shadow"].get("ok")),
        "direct_shadow_push_candidates": sum(1 for decision in decisions if decision.get("action") == "push"),
        "direct_shadow_daily_candidates": sum(1 for decision in decisions if decision.get("action") == "daily"),
        "direct_shadow_archive_candidates": sum(1 for decision in decisions if decision.get("action") == "archive"),
    }
