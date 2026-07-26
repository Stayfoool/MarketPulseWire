"""Single production boundary for the reviewed LLM degree decision."""

from __future__ import annotations

from typing import Any

from market_item import AdmissionResult, DecisionResult, NormalizedMarketItem


ENGINE_VERSION = "decision_engine_v2"


def decide_market_item_with_llm(
    item: NormalizedMarketItem,
    *,
    admission: AdmissionResult,
    portfolio: Any,
    market_item_id: int,
    market_review_id: int,
) -> DecisionResult:
    """Invoke the only production degree/action decision implementation."""
    from llm_production_decision import decide_production_market_item

    return decide_production_market_item(
        item,
        admission=admission,
        portfolio=portfolio,
        market_item_id=market_item_id,
        market_review_id=market_review_id,
    )


def decision_metadata(decision: DecisionResult, *, final_fields: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "decision_engine_version": ENGINE_VERSION,
        "decision_passthrough": True,
        "decision_result": decision.to_dict(),
        "decision_audit": dict(decision.audit_json),
        "decision_final_fields": dict(final_fields or {}),
    }


def _article_final_fields(review: dict[str, Any], push_key: str) -> dict[str, Any]:
    return {
        "importance": review.get("importance"),
        push_key: bool(review.get(push_key)),
        "reason": review.get("reason"),
    }


def _official_final_fields(review: dict[str, Any]) -> dict[str, Any]:
    return {
        "importance": review.get("importance"),
        "should_push_now": bool(review.get("should_push_now")),
        "reason": review.get("reason"),
    }


def _event_final_fields(analysis: dict[str, Any]) -> dict[str, Any]:
    push_decision = analysis.get("push_decision") if isinstance(analysis.get("push_decision"), dict) else {}
    return {
        "importance": analysis.get("importance"),
        "should_push": bool(push_decision.get("should_push") if push_decision else analysis.get("should_push")),
        "reason": push_decision.get("reason") if push_decision else analysis.get("brief_reason"),
    }


def attach_decision_result_to_article_review(
    decision: DecisionResult,
    review: dict[str, Any],
    *,
    push_key: str = "push_now",
) -> dict[str, Any]:
    """Attach an already-finalized decision without recomputing it."""
    updated = dict(review)
    raw = dict(updated.get("raw") or {})
    raw.update(decision_metadata(decision, final_fields=_article_final_fields(updated, push_key)))
    updated["raw"] = raw
    return updated


def ensure_article_decision_audit(
    source: str,
    item: NormalizedMarketItem | dict[str, Any],
    review: dict[str, Any],
    *,
    push_key: str = "push_now",
) -> dict[str, Any]:
    """Refresh compatibility fields only when an authoritative decision exists."""
    del source, item
    raw = review.get("raw") if isinstance(review.get("raw"), dict) else {}
    if not isinstance(raw.get("decision_result"), dict):
        raise RuntimeError("compatibility article review is missing DecisionResult")
    updated = dict(review)
    refreshed = dict(raw)
    refreshed["decision_final_fields"] = _article_final_fields(updated, push_key)
    updated["raw"] = refreshed
    return updated


def _prefixed_metadata(decision: DecisionResult, *, final_fields: dict[str, Any] | None = None) -> dict[str, Any]:
    return {f"_{key}": value for key, value in decision_metadata(decision, final_fields=final_fields).items()}


def attach_decision_result_to_official_review(
    decision: DecisionResult,
    review: dict[str, Any],
) -> dict[str, Any]:
    """Attach an already-finalized decision without recomputing it."""
    updated = dict(review)
    analysis = updated.get("analysis") if isinstance(updated.get("analysis"), dict) else {}
    refreshed = dict(analysis)
    refreshed.update(_prefixed_metadata(decision, final_fields=_official_final_fields(updated)))
    updated["analysis"] = refreshed
    return updated


def ensure_official_decision_audit(
    source: str,
    item: NormalizedMarketItem | dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    """Refresh compatibility fields only when an authoritative decision exists."""
    del source, item
    analysis = review.get("analysis") if isinstance(review.get("analysis"), dict) else {}
    if not isinstance(analysis.get("_decision_result"), dict):
        raise RuntimeError("compatibility official review is missing DecisionResult")
    updated = dict(review)
    refreshed = dict(analysis)
    refreshed["_decision_final_fields"] = _official_final_fields(updated)
    updated["analysis"] = refreshed
    return updated


def attach_decision_result_to_event_analysis(
    decision: DecisionResult,
    analysis: dict[str, Any],
) -> dict[str, Any]:
    """Attach an already-finalized event decision without recomputing it."""
    updated = dict(analysis)
    updated.update(
        _prefixed_metadata(
            decision,
            final_fields={
                "importance": decision.importance,
                "should_push": decision.should_push,
                "reason": decision.brief_reason or decision.reason,
            },
        )
    )
    return updated
