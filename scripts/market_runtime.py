"""Single production runtime facade for normalized market items."""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

from attributed_research import prepare_item_for_decision
from db_utils import connect_sqlite
from market_db import DEFAULT_DB_PATH
from market_delivery import deliver_article_review, deliver_official_review
from market_item import (
    AdmissionResult,
    DecisionResult,
    InterpretationResult,
    MarketFlowResult,
    NormalizedMarketItem,
    decision_result_from_payload,
    item_from_article_mapping,
    item_from_event_mapping,
)
from market_store import (
    complete_market_review,
    ensure_market_item_alias,
    fail_market_review,
    link_latest_event_delivery,
    record_article_delivery,
    record_baseline_item,
    record_production_admission,
)
from market_review_store import (
    article_item_id,
    article_review_exists,
    latest_event_analysis,
    official_news_item_id,
    official_review_exists,
)
from source_profiles import runtime_source_profile


StoreKind = Literal["article", "official", "event"]

EVENT_SOURCE_CONTEXT: dict[str, tuple[str, str, str]] = {
    "sina_flash": ("news_media", "sina_flash", "flash"),
    "sina_stock_news": ("portfolio_stock_news", "sina_stock_news", "portfolio_news"),
    "ifind_notice": ("company_disclosures", "ifind_batch", "notice"),
    "company_disclosures": ("company_disclosures", "company_disclosures", "announcement"),
}
ARTICLE_COMPAT_SOURCE_CATEGORIES = {"trendforce_page": "research_industry_media"}


@dataclass
class MarketProcessOutcome:
    flow_result: MarketFlowResult
    inserted: bool
    storage_ref: dict[str, Any]
    payload: dict[str, Any] = field(default_factory=dict)
    delivery_status: str = "not_requested"
    market_item_id: int | None = None
    market_review_id: int | None = None

    @property
    def event_id(self) -> int | None:
        value = self.storage_ref.get("event_id")
        return int(value) if value is not None else None


class MarketItemProcessingError(RuntimeError):
    def __init__(self, message: str, outcome: MarketProcessOutcome) -> None:
        super().__init__(message)
        self.outcome = outcome


def record_rule_comparison(
    item: NormalizedMarketItem,
    current_decision: DecisionResult | None,
    storage_ref: dict[str, Any],
    *,
    current_admission_status: str = "unknown",
    current_admission_reason: str = "current_runtime_does_not_expose_admission",
    current_matched_families: tuple[str, ...] = (),
    production_admission: AdmissionResult | None = None,
    production_portfolio: object | None = None,
) -> None:
    """Run the optional comparison without making it part of runtime correctness."""
    if str(os.environ.get("RULE_CORE_SHADOW_AUTORUN") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    try:
        module = importlib.import_module("rule_core_runtime_shadow")
        if production_admission is not None:
            current_admission_status = production_admission.status
            current_admission_reason = production_admission.reason_code
            current_matched_families = production_admission.matched_families
        result = module.record_runtime_comparison(
            item,
            current_decision,
            storage_ref,
            current_admission_status=current_admission_status,
            current_admission_reason=current_admission_reason,
            current_matched_families=current_matched_families,
            production_admission=production_admission,
            production_portfolio=production_portfolio,
        )
        if result.get("status") == "failed":
            print(f"rule core comparison failed: {result.get('reason')}", file=sys.stderr, flush=True)
    except Exception as exc:  # noqa: BLE001 - optional reporting cannot change storage or delivery.
        print(f"rule core comparison failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


def _record_rule_comparison(
    item: NormalizedMarketItem,
    flow_result: MarketFlowResult,
    storage_ref: dict[str, Any],
) -> None:
    """Compatibility hook retained for focused runtime tests."""
    record_rule_comparison(item, flow_result.decision, storage_ref)


def is_official_news_source(source: str) -> bool:
    return str(_profile(source).get("category") or "") == "official_company"


def _profile(source_profile_id: str) -> dict[str, Any]:
    try:
        return runtime_source_profile(source_profile_id) or {}
    except Exception:
        return {}


def _publisher_role(raw_item: dict[str, Any], profile: dict[str, Any], category: str) -> str:
    explicit = str(raw_item.get("publisher_role") or profile.get("publisher_role") or "").strip()
    if explicit:
        return explicit
    return "news_media" if category in {"news_media", "portfolio_stock_news"} else ""


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
            publisher_role=_publisher_role(raw_item, profile, str(raw_item.get("source_category") or category)),
            collector=str(raw_item.get("collector") or collector),
        )
    official = store_kind == "official"
    category = str(
        raw_item.get("source_category")
        or profile.get("category")
        or ("official_company" if official else ARTICLE_COMPAT_SOURCE_CATEGORIES.get(source, ""))
    )
    return item_from_article_mapping(
        source,
        raw_item,
        source_category=category,
        publisher_role=_publisher_role(raw_item, profile, category),
        collector=str(raw_item.get("collector") or profile.get("fetcher") or source),
        content_type=str(raw_item.get("content_type") or ("official_news" if official else "article")),
    )


def _selected_module(store_kind: StoreKind) -> ModuleType:
    if store_kind == "event":
        return importlib.import_module("market_event_adapter")
    return importlib.import_module("market_content_adapter")


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
    missing_is_contract_error: bool = True,
) -> MarketFlowResult:
    decision = decision_result_from_payload(payload)
    if decision is None:
        reason = (
            "缺少统一 DecisionResult，已按关闭式策略禁止推送。"
            if missing_is_contract_error
            else "条目尚未进入决策阶段。"
        )
        decision = DecisionResult(
            action=default_action,
            importance=payload.get("importance") or "unknown",
            reason=reason,
            brief_reason=reason,
            audit_json=(
                {"contract_error": "missing_decision_result"}
                if missing_is_contract_error
                else {"technical_action": default_action}
            ),
        )
    return MarketFlowResult(
        item=item,
        decision=decision,
        interpretation=_interpretation_from_payload(payload),
        storage_ref=storage_ref,
        audit_json={"runtime_path": "unified"},
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
    reprocess_existing: bool,
    current_admission_status: str,
    current_admission_reason: str,
    current_matched_families: tuple[str, ...],
    production_admission: AdmissionResult | None,
    production_portfolio: object | None,
    market_item_id: int | None,
    market_review_id: int | None,
) -> MarketProcessOutcome:
    module = _selected_module(store_kind)
    source = item.source
    decision_item = item
    item_id = official_news_item_id(raw_item) if store_kind == "official" else article_item_id(raw_item)
    with connect_sqlite(db_path) as conn:
        existing = (
            official_review_exists(conn, source, item_id)
            if store_kind == "official"
            else article_review_exists(conn, source, item_id)
        )
        if existing is not None and not reprocess_existing:
            payload = existing
            inserted = False
        elif store_kind == "official":
            decision_item = prepare_item_for_decision(item)
            payload = module.process_official_review(
                conn,
                source,
                raw_item,
                source_profile_id=source_profile_id,
                normalized_item=decision_item,
            )
            inserted = existing is None
        else:
            decision_item = prepare_item_for_decision(item)
            payload = module.process_article_review(
                conn,
                source,
                raw_item,
                source_profile_id=source_profile_id,
                normalized_item=decision_item,
            )
            inserted = existing is None
        if existing is not None and existing.get("pushed_at"):
            payload = dict(payload)
            payload["pushed_at"] = existing["pushed_at"]
    storage_ref = {
        "store_kind": "official_news_reviews" if store_kind == "official" else "article_reviews",
        "source": source,
        "item_id": item_id,
    }
    flow_result = _flow_result(decision_item, payload, storage_ref)
    if market_review_id is not None and not flow_result.decision.audit_json.get("contract_error"):
        if market_item_id is None:
            raise RuntimeError("market review exists without its market item identity")
        item_kind = "official" if store_kind == "official" else "article"
        complete_market_review(
            market_review_id,
            flow_result,
            db_path=db_path,
            legacy_store_kind=storage_ref["store_kind"],
            legacy_store_id=f"{source}:{item_id}",
            legacy_payload=payload,
        )
        with connect_sqlite(db_path) as conn:
            ensure_market_item_alias(
                conn,
                market_item_id,
                item_kind=item_kind,
                source=source,
                legacy_item_id=item_id,
                legacy_store_kind=storage_ref["store_kind"],
            )
            conn.commit()
    if inserted and not flow_result.decision.audit_json.get("contract_error"):
        if production_admission is None and (
            current_admission_status == "unknown"
            and current_admission_reason == "current_runtime_does_not_expose_admission"
            and not current_matched_families
        ):
            _record_rule_comparison(decision_item, flow_result, storage_ref)
        else:
            record_rule_comparison(
                decision_item,
                flow_result.decision,
                storage_ref,
                current_admission_status=current_admission_status,
                current_admission_reason=current_admission_reason,
                current_matched_families=current_matched_families,
                production_admission=production_admission,
                production_portfolio=production_portfolio,
            )
    status = "not_requested"
    if deliver:
        if flow_result.decision.audit_json.get("contract_error") == "missing_decision_result":
            status = "missing_decision"
        elif store_kind == "official":
            status = deliver_official_review(
                source,
                raw_item,
                payload,
                decision=flow_result.decision,
                analysis_lines=module.analysis_lines_from_review(payload),
                db_path=db_path,
            )
        else:
            status = deliver_article_review(
                source,
                raw_item,
                payload,
                decision=flow_result.decision,
                db_path=db_path,
                analysis_lines_prefix=module.gate_lines(payload),
                use_rule_dedup=use_rule_dedup,
            )
        if market_item_id is not None and market_review_id is not None:
            record_article_delivery(
                market_item_id,
                market_review_id,
                status=status,
                decision_action=flow_result.decision.action,
                payload={"store_kind": storage_ref["store_kind"], "source": source, "item_id": item_id},
                db_path=db_path,
            )
    return MarketProcessOutcome(
        flow_result=flow_result,
        inserted=inserted,
        storage_ref=storage_ref,
        payload=payload,
        delivery_status=status,
        market_item_id=market_item_id,
        market_review_id=market_review_id,
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
    reprocess_existing: bool,
    current_admission_status: str,
    current_admission_reason: str,
    current_matched_families: tuple[str, ...],
    production_admission: AdmissionResult | None,
    production_portfolio: object | None,
    market_item_id: int | None,
    market_review_id: int | None,
) -> MarketProcessOutcome:
    module = _selected_module("event")
    event_id, inserted = module.upsert_event(raw_item, db_path, normalized_item=item)
    storage_ref = {"store_kind": "event_analyses", "event_id": event_id, "task": task}
    if market_item_id is not None:
        with connect_sqlite(db_path) as conn:
            ensure_market_item_alias(
                conn,
                market_item_id,
                item_kind="event",
                source=item.source,
                legacy_item_id=str(event_id),
                legacy_store_kind="events",
            )
            conn.commit()
    empty_payload: dict[str, Any] = {}
    if not inserted and not reprocess_existing:
        return MarketProcessOutcome(
            flow_result=_flow_result(item, empty_payload, storage_ref, missing_is_contract_error=False),
            inserted=False,
            storage_ref=storage_ref,
            delivery_status="existing",
            market_item_id=market_item_id,
            market_review_id=market_review_id,
        )
    if not inserted and latest_event_analysis(event_id, task, db_path) is not None:
        return MarketProcessOutcome(
            flow_result=_flow_result(item, empty_payload, storage_ref, missing_is_contract_error=False),
            inserted=False,
            storage_ref=storage_ref,
            delivery_status="existing",
            market_item_id=market_item_id,
            market_review_id=market_review_id,
        )
    if baseline_only or not analyze:
        return MarketProcessOutcome(
            flow_result=_flow_result(
                item,
                empty_payload,
                storage_ref,
                default_action="baseline" if baseline_only else "archive",
                missing_is_contract_error=False,
            ),
            inserted=True,
            storage_ref=storage_ref,
            delivery_status="baseline" if baseline_only else "not_analyzed",
            market_item_id=market_item_id,
            market_review_id=market_review_id,
        )
    decision_item = prepare_item_for_decision(item)
    partial = MarketProcessOutcome(
        flow_result=_flow_result(decision_item, empty_payload, storage_ref, missing_is_contract_error=False),
        inserted=inserted,
        storage_ref=storage_ref,
    )
    try:
        analysis = module.analyze_event(event_id, task=task, db_path=db_path, normalized_item=decision_item)
    except Exception as exc:  # noqa: BLE001 - preserve the inserted event reference for batch recovery
        raise MarketItemProcessingError(str(exc), partial) from exc
    flow_result = _flow_result(decision_item, analysis, storage_ref)
    if market_review_id is not None and not flow_result.decision.audit_json.get("contract_error"):
        if market_item_id is None:
            raise RuntimeError("market review exists without its market item identity")
        stored_analysis = latest_event_analysis(event_id, task, db_path)
        legacy_analysis_id = str(stored_analysis["id"]) if stored_analysis else f"{event_id}:{task}"
        complete_market_review(
            market_review_id,
            flow_result,
            db_path=db_path,
            legacy_store_kind="event_analyses",
            legacy_store_id=legacy_analysis_id,
            legacy_payload=analysis,
        )
    if not flow_result.decision.audit_json.get("contract_error"):
        record_rule_comparison(
            decision_item,
            flow_result.decision,
            storage_ref,
            current_admission_status=current_admission_status,
            current_admission_reason=current_admission_reason,
            current_matched_families=current_matched_families,
            production_admission=production_admission,
            production_portfolio=production_portfolio,
        )
    status = module.maybe_deliver_event(event_id, analysis, db_path=db_path) if deliver else "not_requested"
    if deliver and market_item_id is not None and market_review_id is not None:
        link_latest_event_delivery(
            event_id,
            market_item_id,
            market_review_id,
            decision_action=flow_result.decision.action,
            db_path=db_path,
        )
    return MarketProcessOutcome(
        flow_result=flow_result,
        inserted=inserted,
        storage_ref=storage_ref,
        payload=analysis,
        delivery_status=status,
        market_item_id=market_item_id,
        market_review_id=market_review_id,
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
    reprocess_existing: bool = False,
    current_admission_status: str = "unknown",
    current_admission_reason: str = "current_runtime_does_not_expose_admission",
    current_matched_families: tuple[str, ...] = (),
    production_admission: AdmissionResult | None = None,
    production_portfolio: object | None = None,
    market_item_id: int | None = None,
    market_review_id: int | None = None,
) -> MarketProcessOutcome:
    """Persist, decide, interpret, and optionally deliver one normalized item."""
    if production_admission is not None and production_admission.status != "admitted":
        raise ValueError("process_market_item requires an admitted production AdmissionResult")
    if baseline_only and market_item_id is None:
        market_item_id = record_baseline_item(item, db_path=db_path)
    if production_admission is not None and (market_item_id is None or market_review_id is None):
        market_item_id, market_review_id = record_production_admission(
            item,
            production_admission,
            db_path=db_path,
            task=task if store_kind == "event" else "production",
        )
    try:
        if store_kind == "event":
            return _process_event_item(
                item,
                raw_item,
                task=task,
                db_path=db_path,
                baseline_only=baseline_only,
                analyze=analyze,
                deliver=deliver,
                reprocess_existing=reprocess_existing,
                current_admission_status=current_admission_status,
                current_admission_reason=current_admission_reason,
                current_matched_families=current_matched_families,
                production_admission=production_admission,
                production_portfolio=production_portfolio,
                market_item_id=market_item_id,
                market_review_id=market_review_id,
            )
        return _process_content_item(
            item,
            raw_item,
            store_kind=store_kind,
            source_profile_id=source_profile_id,
            db_path=db_path,
            deliver=deliver,
            use_rule_dedup=use_rule_dedup,
            reprocess_existing=reprocess_existing,
            current_admission_status=current_admission_status,
            current_admission_reason=current_admission_reason,
            current_matched_families=current_matched_families,
            production_admission=production_admission,
            production_portfolio=production_portfolio,
            market_item_id=market_item_id,
            market_review_id=market_review_id,
        )
    except Exception as exc:
        if market_review_id is not None:
            fail_market_review(market_review_id, exc, db_path=db_path)
        raise
