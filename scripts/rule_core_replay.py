"""Side-effect-free comparison report for current outcomes versus rule-core-v1."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from market_item import NormalizedMarketItem
from rule_core_v1 import (
    PortfolioRuleConfig,
    RuleConfig,
    SourceAdmissionPolicy,
    evaluate_market_item,
)


REPORT_VERSION = "rule-core-replay-v1"
CURRENT_ADMISSION_STATUSES = {"admitted", "excluded", "not_applicable", "unknown"}
CURRENT_ACTIONS = {"push", "daily", "archive", "ignore", None}
VALID_FAMILIES = {"holding", "semiconductor_ai", "macro_data", "fed_policy", "trade_policy"}
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")


def _require_identifier(value: str, field: str, *, allow_empty: bool = False) -> None:
    if allow_empty and not value:
        return
    if not SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} must be a bounded stable identifier")


@dataclass(frozen=True)
class CurrentRuleOutcome:
    admission_status: str
    admission_reason: str = ""
    matched_families: tuple[str, ...] = ()
    action: str | None = None
    rule_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.admission_status not in CURRENT_ADMISSION_STATUSES:
            raise ValueError(f"invalid current admission status: {self.admission_status}")
        if self.action not in CURRENT_ACTIONS:
            raise ValueError(f"invalid current action: {self.action}")
        if self.admission_status == "admitted" and self.action is None:
            raise ValueError("current admitted outcome requires action")
        if self.admission_status in {"excluded", "not_applicable"} and self.action is not None:
            raise ValueError("current excluded/not_applicable outcome cannot have action")
        _require_identifier(self.admission_reason, "admission_reason", allow_empty=True)
        if set(self.matched_families) - VALID_FAMILIES:
            raise ValueError("current matched_families contains an unknown family")
        for rule_id in self.rule_ids:
            _require_identifier(rule_id, "rule_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "admission_status": self.admission_status,
            "admission_reason": self.admission_reason,
            "matched_families": list(self.matched_families),
            "action": self.action,
            "rule_ids": list(self.rule_ids),
        }


@dataclass(frozen=True)
class ReplayCase:
    replay_id: str
    equivalence_group: str
    item: NormalizedMarketItem
    source_policy: SourceAdmissionPolicy
    current: CurrentRuleOutcome

    def __post_init__(self) -> None:
        _require_identifier(self.replay_id, "replay_id")
        _require_identifier(self.equivalence_group, "equivalence_group", allow_empty=True)
        _require_identifier(self.item.source, "item.source")


def _candidate_payload(case: ReplayCase, *, evaluation: Any) -> dict[str, Any]:
    decision = evaluation.decision
    return {
        "admission_status": evaluation.admission.status,
        "admission_reason": evaluation.admission.reason_code,
        "matched_families": list(evaluation.admission.matched_families),
        "action": decision.action if decision else None,
        "rule_ids": [str(hit.get("rule_id") or "") for hit in decision.rule_hits] if decision else [],
    }


def build_replay_report(
    cases: Iterable[ReplayCase],
    *,
    rule_config: RuleConfig,
    portfolio: PortfolioRuleConfig,
    missing_configuration: Iterable[str] = (),
) -> dict[str, Any]:
    rows = tuple(cases)
    ids = [case.replay_id for case in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("replay_id values must be unique")
    missing = sorted({str(field).strip() for field in missing_configuration if str(field).strip()})
    if missing:
        return {
            "report_version": REPORT_VERSION,
            "status": "blocked",
            "config_version": rule_config.config_version,
            "case_count": len(rows),
            "changed_count": 0,
            "missing_configuration": missing,
            "changes": [],
            "source_invariance_violations": [],
        }

    report_rows: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    for case in rows:
        evaluation = evaluate_market_item(
            case.item,
            rule_config=rule_config,
            portfolio=portfolio,
            source_policy=case.source_policy,
        )
        candidate = _candidate_payload(case, evaluation=evaluation)
        current = case.current.to_dict()
        changed_fields = [
            field
            for field in (
                "admission_status", "admission_reason", "matched_families", "action", "rule_ids"
            )
            if current[field] != candidate[field]
        ]
        row = {
            "replay_id": case.replay_id,
            "source": case.item.source,
            "equivalence_group": case.equivalence_group,
            "current": current,
            "candidate": candidate,
            "changed_fields": changed_fields,
        }
        report_rows.append(row)
        if changed_fields:
            changes.append(row)
        if case.equivalence_group:
            groups.setdefault(case.equivalence_group, []).append(row)

    violations: list[dict[str, Any]] = []
    for group, members in sorted(groups.items()):
        outcomes = {
            (
                row["candidate"]["admission_status"],
                tuple(row["candidate"]["matched_families"]),
                row["candidate"]["action"],
                tuple(row["candidate"]["rule_ids"]),
            )
            for row in members
        }
        if len(members) > 1 and len(outcomes) > 1:
            violations.append(
                {
                    "equivalence_group": group,
                    "replay_ids": [row["replay_id"] for row in members],
                    "sources": [row["source"] for row in members],
                }
            )
    return {
        "report_version": REPORT_VERSION,
        "status": "ok",
        "config_version": rule_config.config_version,
        "case_count": len(report_rows),
        "changed_count": len(changes),
        "missing_configuration": [],
        "changes": changes,
        "source_invariance_violations": violations,
    }
