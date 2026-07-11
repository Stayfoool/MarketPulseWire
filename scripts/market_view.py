"""Unified read-only view over legacy market item review tables."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from market_card_view import card_targets, decision_payload, decision_reason, interpretation_core, interpretation_reason


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _json_loads(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return default


def _row_value(row: Any, key: str, default: Any = "") -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _label_from_target(value: Any) -> str:
    if isinstance(value, dict):
        name = _clean_text(value.get("name") or value.get("holding_name") or "")
        code = _clean_text(value.get("code") or value.get("holding_symbol") or value.get("symbol") or "")
        return " ".join(part for part in (name, code) if part)
    return _clean_text(value)


def _unique(values: list[str], limit: int = 8) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


@dataclass
class MarketViewItem:
    source_table: str
    source_id: str
    kind: str
    source: str
    source_label: str = ""
    title: str = ""
    summary: str = ""
    url: str = ""
    published_at: str = ""
    seen_at: str = ""
    pushed_at: str = ""
    importance: str = ""
    classification: str = ""
    push: bool = False
    delivery_status: str = ""
    baseline_only: bool = False
    decision_action: str = ""
    decision_reason: str = ""
    core_content: str = ""
    brief_reason: str = ""
    related_targets: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def web_summary(self) -> str:
        return self.core_content or self.summary or self.brief_reason or self.decision_reason

    def to_web_row(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source": self.source_label or self.source,
            "source_id": self.source,
            "id": self.source_id,
            "title": self.title,
            "summary": self.web_summary(),
            "url": self.url,
            "published_at": self.published_at,
            "seen_at": self.seen_at,
            "importance": self.importance,
            "classification": self.classification or self.decision_action,
            "push": self.push,
            "delivery_status": self.delivery_status,
            "baseline_only": self.baseline_only,
            "decision_action": self.decision_action,
            "decision_reason": self.decision_reason,
            "core_content": self.core_content,
            "brief_reason": self.brief_reason,
            "related_targets": list(self.related_targets),
        }


def _payload_fields(payload: dict[str, Any], fallback_targets: list[str] | None = None) -> dict[str, Any]:
    decision = decision_payload(payload)
    return {
        "decision_action": str(decision.get("action") or ""),
        "decision_reason": decision_reason(payload),
        "core_content": interpretation_core(payload),
        "brief_reason": interpretation_reason(payload),
        "related_targets": card_targets(payload, fallback_targets=fallback_targets or []),
    }


def article_view_from_row(row: Any) -> MarketViewItem:
    gate = _json_loads(_row_value(row, "gate_json"), {})
    if not isinstance(gate, dict):
        gate = {}
    affected = _json_loads(_row_value(row, "affected_targets_json"), [])
    fallback_targets = [str(item) for item in affected] if isinstance(affected, list) else []
    fields = _payload_fields(gate, fallback_targets=fallback_targets)
    summary = str(_row_value(row, "daily_summary") or _row_value(row, "reason") or "")
    return MarketViewItem(
        source_table="article_reviews",
        source_id=str(_row_value(row, "item_id")),
        kind="article",
        source=str(_row_value(row, "source")),
        source_label=str(_row_value(row, "source_module") or _row_value(row, "source")),
        title=str(_row_value(row, "title") or ""),
        summary=summary,
        url=str(_row_value(row, "url") or ""),
        published_at=str(_row_value(row, "published_at") or ""),
        seen_at=str(_row_value(row, "created_at") or ""),
        pushed_at=str(_row_value(row, "pushed_at") or ""),
        importance=str(_row_value(row, "importance") or ""),
        classification=str(_row_value(row, "incremental_classification") or ""),
        push=bool(_row_value(row, "push_now")),
        delivery_status="sent" if _row_value(row, "pushed_at") else "daily",
        decision_action=fields["decision_action"],
        decision_reason=fields["decision_reason"],
        core_content=fields["core_content"],
        brief_reason=fields["brief_reason"],
        related_targets=fields["related_targets"],
        raw={"gate": gate, "affected_targets": fallback_targets},
    )


def official_view_from_row(row: Any) -> MarketViewItem:
    analysis = _json_loads(_row_value(row, "analysis_json"), {})
    if not isinstance(analysis, dict):
        analysis = {}
    fields = _payload_fields(analysis)
    summary = str(_row_value(row, "daily_summary") or _row_value(row, "reason") or "")
    return MarketViewItem(
        source_table="official_news_reviews",
        source_id=str(_row_value(row, "item_id")),
        kind="official_news",
        source=str(_row_value(row, "source")),
        source_label=str(_row_value(row, "source")),
        title=str(_row_value(row, "title") or ""),
        summary=summary,
        url=str(_row_value(row, "url") or ""),
        published_at=str(_row_value(row, "published_at") or ""),
        seen_at=str(_row_value(row, "created_at") or ""),
        pushed_at=str(_row_value(row, "pushed_at") or ""),
        importance=str(_row_value(row, "importance") or ""),
        classification="",
        push=bool(_row_value(row, "should_push_now") or _row_value(row, "pushed_at")),
        delivery_status="sent" if _row_value(row, "pushed_at") else "daily",
        decision_action=fields["decision_action"],
        decision_reason=fields["decision_reason"],
        core_content=fields["core_content"],
        brief_reason=fields["brief_reason"],
        related_targets=fields["related_targets"],
        raw={"analysis": analysis, "reason": _row_value(row, "reason"), "daily_summary": _row_value(row, "daily_summary")},
    )


def event_view_from_row(row: Any) -> MarketViewItem:
    analysis = _json_loads(_row_value(row, "analysis_json"), {})
    if not isinstance(analysis, dict):
        analysis = {}
    themes = _json_loads(_row_value(row, "themes_json"), [])
    fallback_targets = [str(item) for item in themes] if isinstance(themes, list) else []
    for item in analysis.get("related_holdings") if isinstance(analysis.get("related_holdings"), list) else []:
        label = _label_from_target(item)
        if label:
            fallback_targets.append(label)
    fields = _payload_fields(analysis, fallback_targets=fallback_targets)
    pushed_at = str(_row_value(row, "pushed_at") or "")
    delivery_status = str(_row_value(row, "delivery_status") or "")
    return MarketViewItem(
        source_table="events",
        source_id=str(_row_value(row, "id")),
        kind=str(_row_value(row, "event_type") or "event"),
        source=str(_row_value(row, "source")),
        source_label=str(_row_value(row, "source")),
        title=str(_row_value(row, "title") or ""),
        summary=str(_row_value(row, "summary") or ""),
        url=str(_row_value(row, "url") or ""),
        published_at=str(_row_value(row, "published_at") or ""),
        seen_at=str(_row_value(row, "first_seen_at") or ""),
        pushed_at=pushed_at,
        importance=str(_row_value(row, "importance") or analysis.get("importance") or ""),
        classification=str(_row_value(row, "classification") or ""),
        push=bool(_row_value(row, "should_push") or pushed_at),
        delivery_status=delivery_status or ("sent" if pushed_at else ""),
        baseline_only=bool(_row_value(row, "baseline_only")),
        decision_action=fields["decision_action"],
        decision_reason=fields["decision_reason"],
        core_content=fields["core_content"],
        brief_reason=fields["brief_reason"],
        related_targets=fields["related_targets"],
        raw={"analysis": analysis, "themes": fallback_targets},
    )
