"""Shared market item data structures.

This module is intentionally passive: it defines normalized objects and legacy
adapter helpers, but it does not call LLMs, send Feishu cards, write SQLite, or
change production routing. The first migration step is to let old article,
official-news, and event paths describe their inputs and decisions with the
same shapes before any behavior is changed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal


DecisionAction = Literal["push", "daily", "archive", "ignore", "baseline"]
Importance = Literal["high", "medium", "low", "unknown"]
AdmissionStatus = Literal["admitted", "excluded", "not_applicable"]
RuleFamily = Literal["holding", "semiconductor_ai", "macro_data", "fed_policy", "trade_policy"]
EvidenceScope = Literal["holding", "semiconductor_ai", "macro_data", "fed_policy", "trade_policy", "global"]
LLMJudgement = Literal[
    "not_needed",
    "confirm",
    "weak_confirm",
    "not_match",
    "counter_evidence",
    "possibly_stale_or_priced_in",
    "failed",
]

VALID_ACTIONS: set[str] = {"push", "daily", "archive", "ignore", "baseline"}
VALID_IMPORTANCE: set[str] = {"high", "medium", "low", "unknown"}
VALID_LLM_JUDGEMENTS: set[str] = {
    "not_needed",
    "confirm",
    "weak_confirm",
    "not_match",
    "counter_evidence",
    "possibly_stale_or_priced_in",
    "failed",
}
VALID_ADMISSION_STATUSES: set[str] = {"admitted", "excluded", "not_applicable"}
VALID_RULE_FAMILIES: set[str] = {
    "holding",
    "semiconductor_ai",
    "macro_data",
    "fed_policy",
    "trade_policy",
}
VALID_EVIDENCE_SCOPES: set[str] = {*VALID_RULE_FAMILIES, "global"}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def normalize_action(value: Any, default: DecisionAction = "archive") -> DecisionAction:
    text = str(value or "").strip().lower()
    if text in VALID_ACTIONS:
        return text  # type: ignore[return-value]
    return default


def normalize_importance(value: Any, default: Importance = "unknown") -> Importance:
    text = str(value or "").strip().lower()
    mapping = {
        "高": "high",
        "重要": "high",
        "中": "medium",
        "中等": "medium",
        "低": "low",
        "不重要": "low",
    }
    text = mapping.get(text, text)
    if text in VALID_IMPORTANCE:
        return text  # type: ignore[return-value]
    return default


def normalize_llm_judgement(value: Any, default: LLMJudgement = "not_needed") -> LLMJudgement:
    text = str(value or "").strip().lower()
    if text in VALID_LLM_JUDGEMENTS:
        return text  # type: ignore[return-value]
    return default


def stable_dedupe_key(
    *,
    source: str,
    content_type: str = "",
    source_event_id: str = "",
    url: str = "",
    title: str = "",
    published_at: str = "",
) -> str:
    """Build a stable best-effort key for audit and cross-path adapters."""
    if source_event_id:
        return f"{source}:{source_event_id}"
    if url:
        return f"{source}:{url}"
    raw = "\n".join([source, content_type, title, published_at])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{source}:{digest}"


@dataclass
class NormalizedMarketItem:
    source: str
    source_category: str = ""
    publisher_role: str = ""
    collector: str = ""
    content_type: str = "unknown"
    title: str = ""
    summary: str = ""
    full_text: str = ""
    url: str = ""
    published_at: str = ""
    first_seen_at: str = ""
    symbols: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    dedupe_key: str = ""
    access_note: str = ""

    def __post_init__(self) -> None:
        self.source = _clean_text(self.source)
        self.source_category = _clean_text(self.source_category)
        self.publisher_role = _clean_text(self.publisher_role)
        self.collector = _clean_text(self.collector)
        self.content_type = _clean_text(self.content_type) or "unknown"
        self.title = _clean_text(self.title)
        self.summary = _clean_text(self.summary)
        self.full_text = str(self.full_text or "").strip()
        self.url = str(self.url or "").strip()
        self.published_at = _clean_text(self.published_at)
        self.first_seen_at = _clean_text(self.first_seen_at)
        self.symbols = _string_list(self.symbols)
        self.themes = _string_list(self.themes)
        self.raw = _dict_value(self.raw)
        self.access_note = _clean_text(self.access_note)
        if not self.dedupe_key:
            self.dedupe_key = stable_dedupe_key(
                source=self.source,
                content_type=self.content_type,
                source_event_id=str(self.raw.get("source_event_id") or self.raw.get("id") or ""),
                url=self.url,
                title=self.title,
                published_at=self.published_at,
            )

    @property
    def text_for_rules(self) -> str:
        return "\n".join(part for part in (self.title, self.summary, self.full_text) if part)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_category": self.source_category,
            "publisher_role": self.publisher_role,
            "collector": self.collector,
            "content_type": self.content_type,
            "title": self.title,
            "summary": self.summary,
            "full_text": self.full_text,
            "url": self.url,
            "published_at": self.published_at,
            "first_seen_at": self.first_seen_at,
            "symbols": list(self.symbols),
            "themes": list(self.themes),
            "raw": dict(self.raw),
            "dedupe_key": self.dedupe_key,
            "access_note": self.access_note,
        }


@dataclass(frozen=True)
class AdmissionEvidence:
    rule_family: EvidenceScope
    reason_code: str
    evidence_quote: str
    matched_subjects: tuple[str, ...] = ()
    matched_term_ids: tuple[str, ...] = ()
    relation: str = ""

    def __post_init__(self) -> None:
        if self.rule_family not in VALID_EVIDENCE_SCOPES:
            raise ValueError(f"invalid evidence scope: {self.rule_family}")
        if not str(self.reason_code or "").strip():
            raise ValueError("admission evidence reason_code is required")
        if not str(self.evidence_quote or "").strip():
            raise ValueError("admission evidence quote is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_family": self.rule_family,
            "reason_code": self.reason_code,
            "evidence_quote": self.evidence_quote,
            "matched_subjects": list(self.matched_subjects),
            "matched_term_ids": list(self.matched_term_ids),
            "relation": self.relation,
        }


@dataclass(frozen=True)
class AdmissionResult:
    status: AdmissionStatus
    reason_code: str
    matched_families: tuple[RuleFamily, ...]
    evidence: tuple[AdmissionEvidence, ...]
    config_version: str
    rule_contract_version: str = "rule-core-v1"

    def __post_init__(self) -> None:
        if self.status not in VALID_ADMISSION_STATUSES:
            raise ValueError(f"invalid admission status: {self.status}")
        if not str(self.reason_code or "").strip():
            raise ValueError("admission reason_code is required")
        if not str(self.config_version or "").strip():
            raise ValueError("admission config_version is required")
        if self.rule_contract_version != "rule-core-v1":
            raise ValueError(f"unsupported rule contract: {self.rule_contract_version}")
        if any(family not in VALID_RULE_FAMILIES for family in self.matched_families):
            raise ValueError("admission contains an invalid rule family")
        if len(set(self.matched_families)) != len(self.matched_families):
            raise ValueError("admission contains duplicate rule families")
        if self.status == "admitted" and not self.matched_families:
            raise ValueError("admitted result requires at least one matched family")
        if self.status != "admitted" and self.matched_families:
            raise ValueError("non-admitted result cannot expose matched families")
        if self.status == "not_applicable" and self.evidence:
            raise ValueError("not_applicable result cannot expose admission evidence")
        if self.status == "admitted" and any(
            item.rule_family != "global" and item.rule_family not in self.matched_families
            for item in self.evidence
        ):
            raise ValueError("admission evidence does not belong to a matched family")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "matched_families": list(self.matched_families),
            "evidence": [item.to_dict() for item in self.evidence],
            "config_version": self.config_version,
            "rule_contract_version": self.rule_contract_version,
        }


@dataclass
class DecisionResult:
    action: DecisionAction = "archive"
    importance: Importance = "unknown"
    reason: str = ""
    brief_reason: str = ""
    rule_hits: list[dict[str, Any]] = field(default_factory=list)
    candidate_rules: list[dict[str, Any]] = field(default_factory=list)
    skeptic: dict[str, Any] = field(default_factory=dict)
    dedup: dict[str, Any] = field(default_factory=dict)
    need_llm_interpretation: bool = False
    need_limited_llm_judgement: bool = False
    audit_json: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.action = normalize_action(self.action)
        self.importance = normalize_importance(self.importance)
        self.reason = str(self.reason or "").strip()
        self.brief_reason = str(self.brief_reason or "").strip()
        self.rule_hits = [item for item in self.rule_hits if isinstance(item, dict)]
        self.candidate_rules = [item for item in self.candidate_rules if isinstance(item, dict)]
        self.skeptic = _dict_value(self.skeptic)
        self.dedup = _dict_value(self.dedup)
        self.audit_json = _dict_value(self.audit_json)

    @property
    def should_push(self) -> bool:
        return self.action == "push"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "importance": self.importance,
            "reason": self.reason,
            "brief_reason": self.brief_reason,
            "rule_hits": list(self.rule_hits),
            "candidate_rules": list(self.candidate_rules),
            "skeptic": dict(self.skeptic),
            "dedup": dict(self.dedup),
            "need_llm_interpretation": self.need_llm_interpretation,
            "need_limited_llm_judgement": self.need_limited_llm_judgement,
            "audit_json": dict(self.audit_json),
        }

    def legacy_push_fields(self, push_key: str = "push_now") -> dict[str, Any]:
        return {
            "importance": self.importance,
            push_key: self.should_push,
            "reason": self.reason,
            "brief_reason": self.brief_reason,
            "raw": {
                "decision_result": self.to_dict(),
                "rule_hits": list(self.rule_hits),
            },
        }


@dataclass(frozen=True)
class RuleEvaluation:
    admission: AdmissionResult
    decision: DecisionResult | None

    def __post_init__(self) -> None:
        if self.admission.status == "admitted" and self.decision is None:
            raise ValueError("admitted evaluation requires a DecisionResult")
        if self.admission.status != "admitted" and self.decision is not None:
            raise ValueError("excluded/not_applicable evaluation cannot contain a DecisionResult")

    def to_dict(self) -> dict[str, Any]:
        return {
            "admission": self.admission.to_dict(),
            "decision": self.decision.to_dict() if self.decision is not None else None,
        }


def decision_result_from_payload(payload: Any) -> DecisionResult | None:
    """Read a DecisionResult from unified metadata, or return None for legacy data."""
    if not isinstance(payload, dict):
        return None
    candidates: list[Any] = []
    containers = [payload]
    seen: set[int] = set()
    index = 0
    while index < len(containers) and index < 8:
        container = containers[index]
        index += 1
        identity = id(container)
        if identity in seen:
            continue
        seen.add(identity)
        candidates.extend([container.get("decision_result"), container.get("_decision_result")])
        for key in ("raw", "analysis"):
            nested = container.get(key)
            if isinstance(nested, dict):
                containers.append(nested)
    for candidate in candidates:
        if not isinstance(candidate, dict) or "action" not in candidate:
            continue
        return DecisionResult(
            action=candidate.get("action", "archive"),
            importance=candidate.get("importance", "unknown"),
            reason=candidate.get("reason", ""),
            brief_reason=candidate.get("brief_reason", ""),
            rule_hits=candidate.get("rule_hits") or [],
            candidate_rules=candidate.get("candidate_rules") or [],
            skeptic=candidate.get("skeptic") or {},
            dedup=candidate.get("dedup") or {},
            need_llm_interpretation=bool(candidate.get("need_llm_interpretation")),
            need_limited_llm_judgement=bool(candidate.get("need_limited_llm_judgement")),
            audit_json=candidate.get("audit_json") or {},
        )
    return None


@dataclass
class InterpretationResult:
    core_content: str = ""
    brief_reason: str = ""
    related_targets: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    llm_judgement: LLMJudgement = "not_needed"
    model: str = ""
    prompt_version: str = ""

    def __post_init__(self) -> None:
        self.core_content = _clean_text(self.core_content)
        self.brief_reason = _clean_text(self.brief_reason)
        self.related_targets = [item for item in self.related_targets if isinstance(item, dict)]
        self.notes = _string_list(self.notes)
        self.llm_judgement = normalize_llm_judgement(self.llm_judgement)
        self.model = _clean_text(self.model)
        self.prompt_version = _clean_text(self.prompt_version)

    def to_dict(self) -> dict[str, Any]:
        return {
            "core_content": self.core_content,
            "brief_reason": self.brief_reason,
            "related_targets": list(self.related_targets),
            "notes": list(self.notes),
            "llm_judgement": self.llm_judgement,
            "model": self.model,
            "prompt_version": self.prompt_version,
        }


@dataclass
class MarketFlowResult:
    item: NormalizedMarketItem
    decision: DecisionResult
    interpretation: InterpretationResult
    storage_ref: dict[str, Any] = field(default_factory=dict)
    delivery_intent: dict[str, Any] = field(default_factory=dict)
    audit_json: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.storage_ref = dict(_dict_value(self.storage_ref))
        self.delivery_intent = dict(_dict_value(self.delivery_intent))
        self.audit_json = dict(_dict_value(self.audit_json))
        if not self.delivery_intent:
            self.delivery_intent = {
                "action": self.decision.action,
                "should_deliver": self.decision.should_push,
                "dedup": dict(self.decision.dedup),
            }

    def audit_payload(self) -> dict[str, Any]:
        return {
            "schema": "MarketFlowResult/v1",
            "item": {
                "source": self.item.source,
                "source_category": self.item.source_category,
                "publisher_role": self.item.publisher_role,
                "collector": self.item.collector,
                "content_type": self.item.content_type,
                "dedupe_key": self.item.dedupe_key,
            },
            "decision": {
                "action": self.decision.action,
                "importance": self.decision.importance,
                "rule_ids": [
                    str(rule.get("rule_id") or "")
                    for rule in self.decision.rule_hits
                    if rule.get("rule_id")
                ],
                "candidate_rule_ids": [
                    str(rule.get("rule_id") or "")
                    for rule in self.decision.candidate_rules
                    if rule.get("rule_id")
                ],
                "need_llm_interpretation": self.decision.need_llm_interpretation,
                "need_limited_llm_judgement": self.decision.need_limited_llm_judgement,
            },
            "interpretation": {
                "llm_judgement": self.interpretation.llm_judgement,
                "model": self.interpretation.model,
                "prompt_version": self.interpretation.prompt_version,
            },
            "storage_ref": dict(self.storage_ref),
            "delivery_intent": dict(self.delivery_intent),
            "audit": dict(self.audit_json),
        }


def item_from_article_mapping(
    source: str,
    item: dict[str, Any],
    *,
    source_category: str = "",
    publisher_role: str = "",
    collector: str = "",
    content_type: str = "article",
) -> NormalizedMarketItem:
    raw = dict(item.get("raw") or {})
    raw.setdefault("id", item.get("id") or item.get("item_id") or item.get("url") or item.get("title") or "")
    return NormalizedMarketItem(
        source=source,
        source_category=source_category,
        publisher_role=publisher_role,
        collector=collector,
        content_type=content_type,
        title=str(item.get("title") or ""),
        summary=str(item.get("summary") or item.get("content") or ""),
        full_text=str(item.get("full_text") or ""),
        url=str(item.get("url") or ""),
        published_at=str(item.get("published_at") or ""),
        first_seen_at=str(item.get("first_seen_at") or ""),
        symbols=_string_list(item.get("symbols") or item.get("related_symbols") or []),
        themes=_string_list(item.get("themes") or []),
        raw=raw,
        dedupe_key=str(item.get("dedupe_key") or ""),
        access_note=str(item.get("access_note") or ""),
    )

def item_from_event_mapping(
    event: dict[str, Any],
    *,
    source_category: str = "",
    publisher_role: str = "",
    collector: str = "",
) -> NormalizedMarketItem:
    raw = dict(event.get("raw") or {})
    raw.setdefault("source_event_id", event.get("source_event_id") or "")
    return NormalizedMarketItem(
        source=str(event.get("source") or ""),
        source_category=source_category,
        publisher_role=publisher_role,
        collector=collector,
        content_type=str(event.get("event_type") or "event"),
        title=str(event.get("title") or ""),
        summary=str(event.get("summary") or ""),
        full_text=str(event.get("full_text") or ""),
        url=str(event.get("url") or ""),
        published_at=str(event.get("published_at") or ""),
        first_seen_at=str(event.get("first_seen_at") or ""),
        symbols=_string_list(event.get("symbols") or []),
        themes=_string_list(event.get("themes") or []),
        raw=raw,
        dedupe_key=str(event.get("dedupe_key") or ""),
        access_note=str(event.get("access_note") or ""),
    )
