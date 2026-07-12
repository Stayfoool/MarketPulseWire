"""Single production runtime facade for normalized market items."""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

from db_utils import connect_sqlite
from market_db import DEFAULT_DB_PATH
from market_delivery import deliver_article_review, deliver_official_review
from market_item import (
    DecisionResult,
    InterpretationResult,
    MarketFlowResult,
    NormalizedMarketItem,
    decision_result_from_payload,
    item_from_article_mapping,
    item_from_event_mapping,
)
from market_review_store import article_item_id, article_review_exists, official_news_item_id, official_review_exists
from source_profiles import runtime_source_profile


DIRECT_PATH_ENV = "SURVEIL_MARKET_FLOW_DIRECT_PATH"
LEGACY_DIRECT_PATH_ENVS = ("SURVEIL_CONTENT_DIRECT_PATH", "SURVEIL_EVENT_DIRECT_PATH")
StoreKind = Literal["article", "official", "event"]

EVENT_SOURCE_CONTEXT: dict[str, tuple[str, str, str]] = {
    "sina_flash": ("news_media", "sina_flash", "flash"),
    "sina_stock_news": ("portfolio_stock_news", "sina_stock_news", "portfolio_news"),
    "ifind_notice": ("company_disclosures", "ifind_batch", "notice"),
}
ARTICLE_COMPAT_SOURCE_CATEGORIES = {"trendforce_page": "research_industry_media"}


@dataclass
class MarketProcessOutcome:
    flow_result: MarketFlowResult
    inserted: bool
    storage_ref: dict[str, Any]
    payload: dict[str, Any] = field(default_factory=dict)
    delivery_status: str = "not_requested"

    @property
    def event_id(self) -> int | None:
        value = self.storage_ref.get("event_id")
        return int(value) if value is not None else None


class MarketItemProcessingError(RuntimeError):
    def __init__(self, message: str, outcome: MarketProcessOutcome) -> None:
        super().__init__(message)
        self.outcome = outcome


def _env_flag(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def market_flow_direct_path_enabled() -> bool:
    """Resolve one global route; conflicting legacy aliases fall back together."""
    explicit = os.getenv(DIRECT_PATH_ENV)
    if explicit is not None and explicit.strip():
        return _env_flag(explicit)
    legacy = [os.getenv(key) for key in LEGACY_DIRECT_PATH_ENVS]
    configured = [_env_flag(value) for value in legacy if value is not None and value.strip()]
    if not configured:
        return False
    if all(value == configured[0] for value in configured):
        return configured[0]
    return False


def runtime_path_name() -> str:
    return "direct" if market_flow_direct_path_enabled() else "compat"


def is_official_news_source(source: str) -> bool:
    return str(_profile(source).get("category") or "") == "official_company"


def _profile(source_profile_id: str) -> dict[str, Any]:
    try:
        return runtime_source_profile(source_profile_id) or {}
    except Exception:
        return {}


def normalize_market_item(
    source: str,
    raw_item: dict[str, Any],
    *,
    store_kind: StoreKind,
    source_profile_id: str | None = None,
) -> NormalizedMarketItem:
    """Build the canonical item at the collector/runtime boundary."""
    profile_id = str(source_profile_id or source)
    profile = _profile(profile_id)
    if store_kind == "event":
        category, collector, content_type = EVENT_SOURCE_CONTEXT.get(
            source,
            (str(profile.get("category") or ""), str(profile.get("fetcher") or source), "event"),
        )
        normalized_input = dict(raw_item)
        normalized_input["event_type"] = str(raw_item.get("content_type") or content_type)
        return item_from_event_mapping(
            normalized_input,
            source_category=str(raw_item.get("source_category") or category),
            collector=str(raw_item.get("collector") or collector),
        )
    official = store_kind == "official"
    return item_from_article_mapping(
        source,
        raw_item,
        source_category=str(
            raw_item.get("source_category")
            or profile.get("category")
            or ("official_company" if official else ARTICLE_COMPAT_SOURCE_CATEGORIES.get(source, ""))
        ),
        collector=str(raw_item.get("collector") or profile.get("fetcher") or source),
        content_type=str(raw_item.get("content_type") or ("official_news" if official else "article")),
    )


def _selected_module(store_kind: StoreKind) -> ModuleType:
    direct = market_flow_direct_path_enabled()
    if store_kind == "event":
        return importlib.import_module("market_event_adapter" if direct else "event_pipeline")
    if direct:
        return importlib.import_module("market_content_adapter")
    return importlib.import_module("official_news_gate" if store_kind == "official" else "article_gate")


def _interpretation_from_payload(payload: dict[str, Any]) -> InterpretationResult:
    raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
    analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
    source = raw.get("_interpretation_result") or analysis.get("_interpretation_result") or raw or analysis
    if not isinstance(source, dict):
        source = {}
    return InterpretationResult(
        core_content=str(source.get("core_content") or payload.get("daily_summary") or ""),
        brief_reason=str(source.get("brief_reason") or payload.get("brief_reason") or payload.get("reason") or ""),
        related_targets=source.get("related_targets") or source.get("related_holdings") or [],
        notes=source.get("notes") or [],
        llm_judgement=str(source.get("llm_judgement") or "not_needed"),
        model=str(source.get("model") or payload.get("model") or payload.get("_model") or ""),
        prompt_version=str(source.get("prompt_version") or ""),
    )


def _flow_result(
    item: NormalizedMarketItem,
    payload: dict[str, Any],
    storage_ref: dict[str, Any],
    *,
    default_action: str = "archive",
) -> MarketFlowResult:
    decision = decision_result_from_payload(payload)
    if decision is None:
        push = bool(payload.get("push_now") or payload.get("should_push_now"))
        decision = DecisionResult(
            action="push" if push else default_action,
            importance=payload.get("importance") or "unknown",
            reason=str(payload.get("reason") or ""),
            brief_reason=str(payload.get("brief_reason") or payload.get("reason") or ""),
        )
    return MarketFlowResult(
        item=item,
        decision=decision,
        interpretation=_interpretation_from_payload(payload),
        storage_ref=storage_ref,
        audit_json={"runtime_path": runtime_path_name()},
    )


def _process_content_item(
    item: NormalizedMarketItem,
    raw_item: dict[str, Any],
    *,
    store_kind: Literal["article", "official"],
    source_profile_id: str | None,
    db_path: Path,
    deliver: bool,
    use_rule_dedup: bool,
) -> MarketProcessOutcome:
    module = _selected_module(store_kind)
    source = item.source
    item_id = official_news_item_id(raw_item) if store_kind == "official" else article_item_id(raw_item)
    with connect_sqlite(db_path) as conn:
        existing = (
            official_review_exists(conn, source, item_id)
            if store_kind == "official"
            else article_review_exists(conn, source, item_id)
        )
        if existing is not None:
            payload = existing
            inserted = False
        elif store_kind == "official":
            payload = module.process_official_review(
                conn,
                source,
                raw_item,
                source_profile_id=source_profile_id,
                normalized_item=item,
            )
            inserted = True
        else:
            payload = module.process_article_review(
                conn,
                source,
                raw_item,
                source_profile_id=source_profile_id,
                normalized_item=item,
            )
            inserted = True
    storage_ref = {
        "store_kind": "official_news_reviews" if store_kind == "official" else "article_reviews",
        "source": source,
        "item_id": item_id,
    }
    status = "not_requested"
    if deliver:
        if store_kind == "official":
            status = deliver_official_review(
                source,
                raw_item,
                payload,
                analysis_lines=module.analysis_lines_from_review(payload),
                db_path=db_path,
            )
        else:
            status = deliver_article_review(
                source,
                raw_item,
                payload,
                db_path=db_path,
                analysis_lines_prefix=module.gate_lines(payload),
                use_rule_dedup=use_rule_dedup,
            )
    return MarketProcessOutcome(
        flow_result=_flow_result(item, payload, storage_ref),
        inserted=inserted,
        storage_ref=storage_ref,
        payload=payload,
        delivery_status=status,
    )


def _process_event_item(
    item: NormalizedMarketItem,
    raw_item: dict[str, Any],
    *,
    task: str,
    db_path: Path,
    baseline_only: bool,
    analyze: bool,
    deliver: bool,
) -> MarketProcessOutcome:
    module = _selected_module("event")
    event_id, inserted = module.upsert_event(raw_item, db_path, normalized_item=item)
    storage_ref = {"store_kind": "event_analyses", "event_id": event_id, "task": task}
    empty_payload: dict[str, Any] = {}
    if not inserted:
        return MarketProcessOutcome(
            flow_result=_flow_result(item, empty_payload, storage_ref),
            inserted=False,
            storage_ref=storage_ref,
            delivery_status="existing",
        )
    if baseline_only or not analyze:
        return MarketProcessOutcome(
            flow_result=_flow_result(
                item,
                empty_payload,
                storage_ref,
                default_action="baseline" if baseline_only else "archive",
            ),
            inserted=True,
            storage_ref=storage_ref,
            delivery_status="baseline" if baseline_only else "not_analyzed",
        )
    partial = MarketProcessOutcome(
        flow_result=_flow_result(item, empty_payload, storage_ref),
        inserted=True,
        storage_ref=storage_ref,
    )
    try:
        analysis = module.analyze_event(event_id, task=task, db_path=db_path)
    except Exception as exc:  # noqa: BLE001 - preserve the inserted event reference for batch recovery
        raise MarketItemProcessingError(str(exc), partial) from exc
    status = module.maybe_deliver_event(event_id, analysis, db_path=db_path) if deliver else "not_requested"
    return MarketProcessOutcome(
        flow_result=_flow_result(item, analysis, storage_ref),
        inserted=True,
        storage_ref=storage_ref,
        payload=analysis,
        delivery_status=status,
    )


def process_market_item(
    item: NormalizedMarketItem,
    raw_item: dict[str, Any],
    *,
    store_kind: StoreKind,
    source_profile_id: str | None = None,
    task: str = "portfolio_event",
    db_path: Path = DEFAULT_DB_PATH,
    baseline_only: bool = False,
    analyze: bool = True,
    deliver: bool = True,
    use_rule_dedup: bool = True,
) -> MarketProcessOutcome:
    """Persist, decide, interpret, and optionally deliver one normalized item."""
    if store_kind == "event":
        return _process_event_item(
            item,
            raw_item,
            task=task,
            db_path=db_path,
            baseline_only=baseline_only,
            analyze=analyze,
            deliver=deliver,
        )
    return _process_content_item(
        item,
        raw_item,
        store_kind=store_kind,
        source_profile_id=source_profile_id,
        db_path=db_path,
        deliver=deliver,
        use_rule_dedup=use_rule_dedup,
    )
