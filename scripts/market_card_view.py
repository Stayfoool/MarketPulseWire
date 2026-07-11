"""Read unified market metadata for compact notification cards."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from market_interpreter import normalize_interpretation_payload


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def unique_strings(values: Iterable[str], *, limit: int = 5) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value or "").split())
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


def review_analysis(review: dict[str, Any]) -> dict[str, Any]:
    analysis = review.get("analysis")
    return analysis if isinstance(analysis, dict) else review


def decision_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("decision_result"), dict):
        return payload["decision_result"]
    if isinstance(payload.get("_decision_result"), dict):
        return payload["_decision_result"]
    raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
    if isinstance(raw.get("decision_result"), dict):
        return raw["decision_result"]
    analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
    if isinstance(analysis.get("_decision_result"), dict):
        return analysis["_decision_result"]
    return {}


def decision_rule_hits(payload: dict[str, Any]) -> list[dict[str, Any]]:
    decision = decision_payload(payload)
    hits = decision.get("rule_hits") if isinstance(decision.get("rule_hits"), list) else []
    return [item for item in hits if isinstance(item, dict)]


def decision_reason(payload: dict[str, Any]) -> str:
    decision = decision_payload(payload)
    for key in ("brief_reason", "reason"):
        text = str(decision.get(key) or "").strip()
        if text:
            return text
    hits = decision_rule_hits(payload)
    if hits:
        return str(hits[0].get("brief_reason") or hits[0].get("reason") or "").strip()
    return ""


def _target_label(item: Any) -> str:
    if isinstance(item, dict):
        name = str(item.get("name") or item.get("holding_name") or "").strip()
        code = str(item.get("code") or item.get("holding_symbol") or "").strip()
        return " ".join(part for part in (name, code) if part)
    return str(item or "").strip()


def decision_targets(payload: dict[str, Any]) -> list[str]:
    targets: list[str] = []
    decision = decision_payload(payload)
    for item in as_list(decision.get("rule_hits")):
        if not isinstance(item, dict):
            continue
        targets.extend(_target_label(target) for target in as_list(item.get("related_targets")))
        targets.extend(str(target or "") for target in as_list(item.get("affected_targets")))
    targets.extend(_target_label(target) for target in as_list(decision.get("related_targets")))
    return unique_strings(targets)


def interpretation_payload(review_or_analysis: dict[str, Any]) -> dict[str, Any]:
    analysis = review_analysis(review_or_analysis)
    raw = review_or_analysis.get("raw") if isinstance(review_or_analysis.get("raw"), dict) else {}
    source_base = raw if raw.get("core_content") or raw.get("related_targets") else analysis
    source = {
        "core_content": source_base.get("core_content"),
        "brief_reason": source_base.get("brief_reason"),
        "related_targets": source_base.get("related_targets"),
        "related_holdings": source_base.get("related_holdings"),
        "notes": source_base.get("notes"),
        "llm_judgement": source_base.get("llm_judgement"),
    }
    result = normalize_interpretation_payload(source).to_dict()
    if not result["core_content"] and review_or_analysis.get("daily_summary"):
        result["core_content"] = str(review_or_analysis.get("daily_summary") or "").strip()
    if not result["brief_reason"] and review_or_analysis.get("brief_reason"):
        result["brief_reason"] = str(review_or_analysis.get("brief_reason") or "").strip()
    return result


def interpretation_core(review_or_analysis: dict[str, Any]) -> str:
    return str(interpretation_payload(review_or_analysis).get("core_content") or "").strip()


def interpretation_reason(review_or_analysis: dict[str, Any]) -> str:
    return str(interpretation_payload(review_or_analysis).get("brief_reason") or "").strip()


def interpretation_targets(review_or_analysis: dict[str, Any]) -> list[str]:
    payload = interpretation_payload(review_or_analysis)
    return unique_strings(_target_label(item) for item in as_list(payload.get("related_targets")))


def card_push_reason(item: dict[str, Any], review_or_analysis: dict[str, Any]) -> str:
    explicit = str(item.get("push_reason") or "").strip()
    if explicit:
        return explicit
    reason = interpretation_reason(review_or_analysis)
    if reason:
        return reason
    reason = decision_reason(review_or_analysis)
    if reason:
        return reason
    return ""


def card_targets(review_or_analysis: dict[str, Any], *, fallback_targets: list[str] | None = None) -> list[str]:
    return unique_strings(
        [
            *interpretation_targets(review_or_analysis),
            *decision_targets(review_or_analysis),
            *(fallback_targets or []),
        ]
    )
