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
            "importance": self.importance if self.importance != "unknown" else "low",
            push_key: self.should_push,
            "reason": self.reason,
            "brief_reason": self.brief_reason,
            "raw": {
                "decision_result": self.to_dict(),
                "rule_hits": list(self.rule_hits),
            },
        }


def decision_result_from_payload(payload: Any) -> DecisionResult | None:
    """Read a DecisionResult from unified metadata, or return None for legacy data."""
    if not isinstance(payload, dict):
        return None
    candidates = [payload.get("decision_result"), payload.get("_decision_result")]
    raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
    analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
    candidates.extend(
        [
            raw.get("decision_result"),
            analysis.get("decision_result"),
            analysis.get("_decision_result"),
        ]
    )
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


def item_from_article_mapping(
    source: str,
    item: dict[str, Any],
    *,
    source_category: str = "",
    collector: str = "",
    content_type: str = "article",
) -> NormalizedMarketItem:
    raw = dict(item.get("raw") or {})
    raw.setdefault("id", item.get("id") or item.get("item_id") or item.get("url") or item.get("title") or "")
    return NormalizedMarketItem(
        source=source,
        source_category=source_category,
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
    collector: str = "",
) -> NormalizedMarketItem:
    raw = dict(event.get("raw") or {})
    raw.setdefault("source_event_id", event.get("source_event_id") or "")
    return NormalizedMarketItem(
        source=str(event.get("source") or ""),
        source_category=source_category,
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
