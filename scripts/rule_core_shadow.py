"""Compare the active decision with rule-core-v1 without changing delivery.

This module only evaluates an already normalized item with an explicitly
supplied v1 configuration. It does not persist a row, reserve a delivery key,
call a collector or send a notification. The returned candidate result is for
comparison records only; callers must continue using the active
``DecisionResult`` for review and delivery.
"""

from __future__ import annotations

from typing import Any, Iterable

from market_item import DecisionResult, NormalizedMarketItem
from rule_core_v1 import (
    PortfolioRuleConfig,
    RuleConfig,
    SourceAdmissionPolicy,
    evaluate_market_item,
)


CONTRACT_VERSION = "rule-core-shadow-v1"
VALID_CURRENT_ADMISSION_STATUSES = {"admitted", "excluded", "not_applicable", "unknown"}
VALID_ACTIONS = {"push", "daily", "archive", "ignore"}


def _clean(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _rule_ids(decision: DecisionResult | None) -> list[str]:
    if decision is None:
        return []
    return list(
        dict.fromkeys(
            str(hit.get("rule_id") or "")
            for hit in decision.rule_hits
            if isinstance(hit, dict) and hit.get("rule_id")
        )
    )


def _families(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value)))


def _decision_summary(decision: DecisionResult | None) -> dict[str, Any]:
    if decision is None:
        return {
            "action": None,
            "importance": None,
            "brief_reason": "",
            "reason": "",
            "rule_ids": [],
        }
    return {
        "action": decision.action,
        "importance": decision.importance,
        "brief_reason": _clean(decision.brief_reason, 500),
        "reason": _clean(decision.reason, 800),
        "rule_ids": _rule_ids(decision),
    }


def _admission_evidence(evidence: object) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in evidence if isinstance(evidence, tuple) else ():
        items.append(
            {
                "rule_family": item.rule_family,
                "reason_code": item.reason_code,
                "evidence_quote": _clean(item.evidence_quote, 300),
                "matched_subjects": list(item.matched_subjects),
                "matched_term_ids": list(item.matched_term_ids),
                "relation": item.relation,
            }
        )
    return items[:5]


def compare_rule_core(
    item: NormalizedMarketItem,
    *,
    current_decision: DecisionResult | None,
    current_admission_status: str = "unknown",
    current_admission_reason: str = "",
    current_matched_families: Iterable[str] = (),
    rule_config: RuleConfig,
    portfolio: PortfolioRuleConfig,
    source_policy: SourceAdmissionPolicy,
) -> dict[str, Any]:
    """Return a bounded current-v1 comparison for one normalized item.

    ``current_decision`` remains the active decision. This function never
    returns a delivery instruction and cannot promote or downgrade it.
    """
    if current_admission_status not in VALID_CURRENT_ADMISSION_STATUSES:
        raise ValueError(f"invalid current admission status: {current_admission_status}")
    if current_decision is not None and current_decision.action not in VALID_ACTIONS:
        raise ValueError(f"invalid current decision action: {current_decision.action}")

    evaluation = evaluate_market_item(
        item,
        rule_config=rule_config,
        portfolio=portfolio,
        source_policy=source_policy,
    )
    candidate_decision = evaluation.decision
    current = {
        "admission_status": current_admission_status,
        "admission_reason": _clean(current_admission_reason, 500),
        "matched_families": _families(current_matched_families),
        **_decision_summary(current_decision),
    }
    candidate = {
        "admission_status": evaluation.admission.status,
        "admission_reason": evaluation.admission.reason_code,
        "matched_families": list(evaluation.admission.matched_families),
        "admission_evidence": _admission_evidence(evaluation.admission.evidence),
        **_decision_summary(candidate_decision),
    }
    changed_fields = [
        field
        for field in ("admission_status", "admission_reason", "matched_families", "action", "rule_ids")
        if current[field] != candidate[field]
    ]
    return {
        "ok": True,
        "contract_version": CONTRACT_VERSION,
        "comparison_only": True,
        "affects_current_decision": False,
        "current": current,
        "candidate": candidate,
        "changed_fields": changed_fields,
    }


def safe_compare_rule_core(
    item: NormalizedMarketItem,
    *,
    current_decision: DecisionResult | None,
    current_admission_status: str = "unknown",
    current_admission_reason: str = "",
    current_matched_families: Iterable[str] = (),
    rule_config: RuleConfig,
    portfolio: PortfolioRuleConfig,
    source_policy: SourceAdmissionPolicy,
) -> dict[str, Any]:
    """Keep a candidate-core failure from changing the active item path."""
    try:
        return compare_rule_core(
            item,
            current_decision=current_decision,
            current_admission_status=current_admission_status,
            current_admission_reason=current_admission_reason,
            current_matched_families=current_matched_families,
            rule_config=rule_config,
            portfolio=portfolio,
            source_policy=source_policy,
        )
    except Exception as exc:  # noqa: BLE001 - comparison must be non-authoritative.
        return {
            "ok": False,
            "contract_version": CONTRACT_VERSION,
            "comparison_only": True,
            "affects_current_decision": False,
            "error": f"{type(exc).__name__}: {_clean(exc, 500)}",
        }
