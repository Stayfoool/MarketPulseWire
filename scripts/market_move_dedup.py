"""Delivery-only identities for repeated intraday Chinese equity market moves."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from market_item import DecisionResult


MARKET_TIMEZONE = timezone(timedelta(hours=8))
MARKET_MOVE_RULE_ID = "intraday_market_move"
MARKET_MOVE_LOOKBACK_MINUTES = 45
HOLDING_MARKET_MOVE_RULE_IDS = {"holding_keyword_immediate_alert", "holding_immediate_alert"}

UP_MARKERS = ("直线拉升", "涨势扩大", "封涨停", "涨停", "涨超", "大涨", "拉升", "上涨", "走强")
DOWN_MARKERS = ("直线跳水", "跌势扩大", "封跌停", "跌停", "跌超", "大跌", "跳水", "下跌", "走弱")
CONCEPT_PATTERN = re.compile(r"(?:A股)?(?P<concept>[A-Za-z0-9\u4e00-\u9fff]{1,16})概念(?:股)?")


def _text(item: dict[str, Any]) -> str:
    return " ".join(
        str(item.get(key) or "").strip() for key in ("title", "summary", "content", "full_text")
    )


def _market_date(published_at: object) -> str:
    raw = str(published_at or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        return ""
    return parsed.astimezone(MARKET_TIMEZONE).date().isoformat()


def _direction(text: str) -> str:
    up = any(marker in text for marker in UP_MARKERS)
    down = any(marker in text for marker in DOWN_MARKERS)
    if up == down:
        return ""
    return "up" if up else "down"


def _concept(text: str) -> str:
    match = CONCEPT_PATTERN.search(text)
    if not match:
        return ""
    value = match.group("concept").strip().lower()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", value)


def _target_key(decision: DecisionResult) -> str:
    for hit in decision.rule_hits:
        if str(hit.get("rule_id") or "") not in HOLDING_MARKET_MOVE_RULE_IDS:
            continue
        targets = hit.get("related_targets") if isinstance(hit.get("related_targets"), list) else []
        for target in targets:
            if not isinstance(target, dict):
                continue
            name = str(target.get("name") or "").strip().lower()
            symbol = str(target.get("code") or "").strip().upper()
            value = re.sub(r"[^a-z0-9\u4e00-\u9fff.]", "", name or symbol.lower())
            if value:
                return value
    admission = decision.audit_json.get("admission")
    evidence = admission.get("evidence") if isinstance(admission, dict) else []
    for item in evidence if isinstance(evidence, list) else []:
        if not isinstance(item, dict) or item.get("rule_family") != "holding":
            continue
        for subject in item.get("matched_subjects") if isinstance(item.get("matched_subjects"), list) else []:
            value = re.sub(r"[^a-z0-9\u4e00-\u9fff.]", "", str(subject).strip().lower())
            if value:
                return value
    return ""


def intraday_market_move_dedup_hit(item: dict[str, Any], decision: DecisionResult) -> dict[str, Any] | None:
    """Return a conservative fact identity, or ``None`` when evidence is insufficient.

    The caller must already have a push DecisionResult. This function never
    affects that action and intentionally requires a holding-keyword rule hit,
    a China-market date, a directional move, a literal ``<concept>概念`` phrase,
    and one matched target. Different concepts or targets remain distinct.
    """
    if not decision.should_push:
        return None
    text = _text(item)
    session = _market_date(item.get("published_at"))
    direction = _direction(text)
    concept = _concept(text)
    target = _target_key(decision)
    if not all((session, direction, concept, target)):
        return None
    return {
        "rule_id": MARKET_MOVE_RULE_ID,
        "dedup_key": f"market_move:{session}:{direction}:{concept}:{target}",
        "dedup_lookback_minutes": MARKET_MOVE_LOOKBACK_MINUTES,
        "dedup_kind": "intraday_market_move",
        "event_facts": {
            "market_date": session,
            "direction": direction,
            "concept": concept,
            "matched_target": target,
        },
    }
