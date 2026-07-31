"""Single production flow for normalized market items."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from attributed_research import prepare_item_for_decision
from db_utils import connect_sqlite
from decision_engine import decide_market_item_with_llm
from holdings_store import load_enabled_holdings
from market_db import DEFAULT_DB_PATH
from market_delivery import deliver_article_review, deliver_event, deliver_official_review
from market_item import (
    AdmissionResult,
    DecisionResult,
    InterpretationResult,
    MarketFlowResult,
    NormalizedMarketItem,
    article_item_id,
    decision_result_from_dict,
    item_from_article_mapping,
    item_from_event_mapping,
    official_news_item_id,
)
from market_interpreter import INTERPRETER_VERSION, interpret_market_item
from market_store import (
    InsufficientEvidenceError,
    complete_market_review,
    ensure_market_item_alias,
    fail_market_review,
    record_baseline_item,
    record_production_admission,
    market_review_snapshot,
    source_item_id,
)
from source_profiles import runtime_source_profile


FLOW_VERSION = "market_flow_v1"
ForbiddenFieldMode = Literal["article", "official", "event"]


def interpretation_failure(error: Exception) -> InterpretationResult:
    reason = str(error).strip()[:500]
    return InterpretationResult(
        brief_reason=f"薄解读失败：{reason}",
        notes=[reason] if reason else [],
        llm_judgement="failed",
        model="interpretation_failed",
        prompt_version=INTERPRETER_VERSION,
    )


def rule_only_interpretation(item: NormalizedMarketItem, decision: DecisionResult) -> InterpretationResult:
    return InterpretationResult(
        core_content=item.summary or item.title,
        brief_reason=decision.brief_reason or decision.reason,
        related_targets=[],
        llm_judgement="not_needed",
        model="rule_only",
        prompt_version=INTERPRETER_VERSION,
    )


def evaluate_market_item(
    item: NormalizedMarketItem,
    *,
    decision: DecisionResult,
    source_interpretation: InterpretationResult | None = None,
    content: str = "",
    task: str = "为一条已完成规则决策的市场信息生成极简实时摘要。",
    intro: str = "请解读以下市场信息",
    forbidden_mode: ForbiddenFieldMode = "article",
    extra_notes: list[str] | None = None,
    user_agent: str = "surveil-market-flow/0.1",
    force_interpretation: bool = False,
    storage_ref: dict[str, Any] | None = None,
) -> MarketFlowResult:
    """Interpret one normalized item after its authoritative decision exists."""
    decision_item = item
    resolved_decision = decision
    should_interpret = bool(
        source_interpretation is None
        and (
            force_interpretation
            or resolved_decision.need_llm_interpretation
            or resolved_decision.need_limited_llm_judgement
        )
    )
    interpretation_error = ""
    if source_interpretation is not None:
        interpretation = source_interpretation
    elif should_interpret:
        try:
            interpretation = interpret_market_item(
                decision_item,
                resolved_decision,
                content=content,
                task=task,
                intro=intro,
                forbidden_mode=forbidden_mode,
                extra_notes=extra_notes,
                user_agent=user_agent,
            )
        except Exception as exc:  # noqa: BLE001 - interpretation must not erase the authoritative decision
            interpretation = interpretation_failure(exc)
            interpretation_error = str(exc).strip()[:500]
    else:
        interpretation = rule_only_interpretation(decision_item, resolved_decision)
    return MarketFlowResult(
        item=decision_item,
        decision=resolved_decision,
        interpretation=interpretation,
        storage_ref=dict(storage_ref or {}),
        delivery_intent={
            "action": resolved_decision.action,
            "should_deliver": resolved_decision.should_push,
            "dedup": dict(resolved_decision.dedup),
        },
        audit_json={
            "flow_version": FLOW_VERSION,
            "decision_supplied": decision is not None,
            "source_interpretation_supplied": source_interpretation is not None,
            "interpreter_called": should_interpret,
            "interpretation_failed": bool(interpretation_error),
            "interpretation_error": interpretation_error,
        },
    )

StoreKind = Literal["article", "official", "event"]

EVENT_SOURCE_CONTEXT: dict[str, tuple[str, str, str]] = {
    "sina_flash": ("news_media", "sina_flash", "flash"),
    "sina_stock_news": ("portfolio_stock_news", "sina_stock_news", "portfolio_news"),
    "ifind_notice": ("company_disclosures", "ifind_batch", "notice"),
    "company_disclosures": ("company_disclosures", "company_disclosures", "announcement"),
}
ARTICLE_SOURCE_CATEGORY_DEFAULTS = {"trendforce_page": "research_industry_media"}


@dataclass
class MarketProcessOutcome:
    flow_result: MarketFlowResult
    inserted: bool
    storage_ref: dict[str, Any]
    delivery_status: str = "not_requested"
    market_item_id: int | None = None
    market_review_id: int | None = None

    @property
    def event_id(self) -> int | None:
        return self.market_item_id


class MarketItemProcessingError(RuntimeError):
    def __init__(
        self,
        message: str,
        outcome: MarketProcessOutcome,
        *,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.outcome = outcome
        if cause is not None:
            for field in ("review_status", "processing_status"):
                value = getattr(cause, field, "")
                if value:
                    setattr(self, field, value)


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
        or ("official_company" if official else ARTICLE_SOURCE_CATEGORY_DEFAULTS.get(source, ""))
    )
    return item_from_article_mapping(
        source,
        raw_item,
        source_category=category,
        publisher_role=_publisher_role(raw_item, profile, category),
        collector=str(raw_item.get("collector") or profile.get("fetcher") or source),
        content_type=str(raw_item.get("content_type") or ("official_news" if official else "article")),
    )


def _interpretation_from_payload(payload: dict[str, Any]) -> InterpretationResult:
    raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
    analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
    source = (
        payload.get("interpretation_result")
        or payload.get("_interpretation_result")
        or raw.get("_interpretation_result")
        or analysis.get("_interpretation_result")
        or raw
        or analysis
    )
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
    decision_payload = payload.get("decision_result")
    decision = decision_result_from_dict(decision_payload)
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


def _source_enrichment_interpretation(item: NormalizedMarketItem) -> InterpretationResult | None:
    if not item.source.startswith("value_directory_"):
        return None
    preview = item.raw.get("value_directory_preview")
    if not isinstance(preview, dict):
        return None
    facts = preview.get("facts") if isinstance(preview.get("facts"), dict) else {}
    if facts.get("status") != "ok":
        return None
    return InterpretationResult(
        core_content=str(facts.get("core_content") or item.summary or item.title),
        model=str(facts.get("model") or "value_directory_preview"),
        prompt_version="value_directory_preview_v1",
    )


def build_portfolio_event_input(item: NormalizedMarketItem, db_path: Path = DEFAULT_DB_PATH) -> str:
    symbol_set = {str(symbol).upper() for symbol in item.symbols if str(symbol).strip()}
    holdings = load_enabled_holdings(db_path)
    related_holdings = [
        holding
        for holding in holdings
        if str(holding.get("symbol", "")).upper() in symbol_set
    ]
    context = {
        "event": item.to_dict(),
        "event_symbols": sorted(symbol_set),
        "directly_related_holdings": related_holdings,
        "all_configured_holdings": [
            {
                "symbol": holding.get("symbol", ""),
                "name": holding.get("name", ""),
                "full_name": holding.get("full_name", ""),
                "aliases": holding.get("aliases", []),
            }
            for holding in holdings
        ],
    }
    return json.dumps(context, ensure_ascii=False, indent=2)


def evaluate_content_item(
    item: NormalizedMarketItem,
    raw_item: dict[str, Any],
    decision: DecisionResult,
    *,
    official: bool,
    storage_ref: dict[str, Any],
) -> MarketFlowResult:
    source_interpretation = _source_enrichment_interpretation(item)
    return evaluate_market_item(
        item,
        decision=decision,
        source_interpretation=source_interpretation,
        content=str(
            raw_item.get("full_text")
            or raw_item.get("content")
            or raw_item.get("summary")
            or ""
        ).strip()[:12000],
        task=(
            "为一条已完成规则决策的核心产业链公司官网新闻生成极简实时摘要。"
            if official
            else "为一条已完成规则决策的资讯/报告生成极简实时摘要。"
        ),
        intro="请解读以下核心产业链公司官网新闻" if official else "请解读以下资讯/报告",
        forbidden_mode="official" if official else "article",
        extra_notes=["只可围绕 DecisionResult 的规则上下文解释，不得输出或改写推送开关。"],
        user_agent="surveil-official-content-flow/0.1" if official else "surveil-article-content-flow/0.1",
        force_interpretation=not item.source.startswith("value_directory_"),
        storage_ref=storage_ref,
    )


def _process_content_item(
    item: NormalizedMarketItem,
    raw_item: dict[str, Any],
    *,
    store_kind: Literal["article", "official"],
    db_path: Path,
    deliver: bool,
    use_rule_dedup: bool,
    reprocess_existing: bool,
    production_admission: AdmissionResult | None,
    production_portfolio: object | None,
    market_item_id: int | None,
    market_review_id: int | None,
) -> MarketProcessOutcome:
    source = item.source
    decision_item = item
    evaluated_flow_result: MarketFlowResult | None = None
    item_id = official_news_item_id(raw_item) if store_kind == "official" else article_item_id(raw_item)
    snapshot = market_review_snapshot(market_review_id, db_path=db_path) if market_review_id is not None else None
    if snapshot and snapshot["review_status"] == "insufficient_evidence":
        raise InsufficientEvidenceError("market review is already terminal with insufficient evidence")
    canonical_existing = bool(snapshot and snapshot["review_status"] == "succeeded")
    if canonical_existing and not reprocess_existing:
        evaluated_flow_result = _flow_result(decision_item, dict(snapshot["payload"]), {})
        inserted = False
    elif market_review_id is not None:
        if production_admission is None or production_portfolio is None:
            raise RuntimeError("content processing requires production admission and portfolio")
        decision_item = prepare_item_for_decision(item)
        if market_item_id is None:
            raise RuntimeError("production review is missing market item identity")
        production_decision = decide_market_item_with_llm(
            decision_item,
            admission=production_admission,
            portfolio=production_portfolio,
            market_item_id=market_item_id,
            market_review_id=market_review_id,
        )
        item_kind = "official" if store_kind == "official" else "article"
        item_storage_ref = {
            "store_kind": "market_reviews",
            "item_kind": item_kind,
            "source": source,
            "item_id": item_id,
        }
        evaluated_flow_result = evaluate_content_item(
            decision_item,
            raw_item,
            production_decision,
            official=store_kind == "official",
            storage_ref=item_storage_ref,
        )
        inserted = not canonical_existing
    else:
        raise RuntimeError("content processing requires a unified production review identity")
    storage_ref = {
        "store_kind": "market_reviews",
        "item_kind": "official" if store_kind == "official" else "article",
        "source": source,
        "item_id": item_id,
    }
    if evaluated_flow_result is None:
        raise RuntimeError("content processing did not produce a unified result")
    flow_result = evaluated_flow_result
    flow_result.storage_ref = storage_ref

    if (
        market_review_id is not None
        and not flow_result.decision.audit_json.get("contract_error")
        and (not canonical_existing or reprocess_existing)
    ):
        if market_item_id is None:
            raise RuntimeError("market review exists without its market item identity")
        item_kind = "official" if store_kind == "official" else "article"
        complete_market_review(
            market_review_id,
            flow_result,
            db_path=db_path,
            alias=(item_kind, source, item_id, "market_items"),
        )
    status = "not_requested"
    if deliver:
        already_sent = bool(snapshot and snapshot["delivered"])
        if already_sent:
            status = "existing"
        elif flow_result.decision.audit_json.get("contract_error") == "missing_decision_result":
            status = "missing_decision"
        elif store_kind == "official":
            if market_item_id is None or market_review_id is None:
                raise RuntimeError("official delivery requires unified item and review identities")
            status = deliver_official_review(
                source,
                raw_item,
                flow_result,
                market_item_id=market_item_id,
                market_review_id=market_review_id,
                already_sent=False,
                db_path=db_path,
            )
        else:
            if market_item_id is None or market_review_id is None:
                raise RuntimeError("article delivery requires unified item and review identities")
            status = deliver_article_review(
                source,
                raw_item,
                flow_result,
                market_item_id=market_item_id,
                market_review_id=market_review_id,
                db_path=db_path,
                use_rule_dedup=use_rule_dedup,
                already_sent=False,
            )
    return MarketProcessOutcome(
        flow_result=flow_result,
        inserted=inserted,
        storage_ref=storage_ref,
        delivery_status=status,
        market_item_id=market_item_id,
        market_review_id=market_review_id,
    )


def evaluate_event_item(
    item: NormalizedMarketItem,
    decision: DecisionResult | None,
    *,
    task: str,
    db_path: Path,
    storage_ref: dict[str, Any],
) -> MarketFlowResult:
    if decision is None:
        raise RuntimeError(f"事件决策结果缺失：{item.source}/{item.title}")
    return evaluate_market_item(
        item,
        decision=decision,
        content=build_portfolio_event_input(item, db_path=db_path),
        task="为一条已完成规则决策的公告、研报、快讯或异动信息生成极简实时摘要。",
        intro="请解读以下持仓事件",
        forbidden_mode="event",
        extra_notes=["输入包含直接相关持仓和全部已配置持仓；只可使用给定关系，不要自行扩展股票映射。"],
        user_agent="surveil-portfolio-event-llm/0.2",
        force_interpretation=True,
        storage_ref=storage_ref,
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
    production_admission: AdmissionResult | None,
    production_portfolio: object | None,
    market_item_id: int | None,
    market_review_id: int | None,
) -> MarketProcessOutcome:
    if market_item_id is None:
        raise RuntimeError("event processing requires a unified market item identity")
    item_id = source_item_id(item)
    storage_ref = {
        "store_kind": "market_reviews",
        "item_kind": "event",
        "source": item.source,
        "item_id": item_id,
        "market_item_id": market_item_id,
        "task": task,
    }
    with connect_sqlite(db_path) as conn:
        alias_exists = conn.execute(
            """
            SELECT 1 FROM market_item_aliases
            WHERE item_kind='event' AND source=? AND legacy_item_id=?
            """,
            (item.source, item_id),
        ).fetchone()
        ensure_market_item_alias(
            conn,
            market_item_id,
            item_kind="event",
            source=item.source,
            legacy_item_id=item_id,
            legacy_store_kind="market_items",
        )
        conn.commit()
    snapshot = market_review_snapshot(market_review_id, db_path=db_path) if market_review_id is not None else None
    if snapshot and snapshot["review_status"] == "insufficient_evidence":
        raise InsufficientEvidenceError("market review is already terminal with insufficient evidence")
    canonical_existing = bool(snapshot and snapshot["review_status"] == "succeeded")
    inserted = not canonical_existing if market_review_id is not None else not bool(alias_exists)
    if canonical_existing and not reprocess_existing:
        flow_result = _flow_result(item, dict(snapshot["payload"]), storage_ref)
        return MarketProcessOutcome(
            flow_result=flow_result,
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
                {},
                storage_ref,
                default_action="baseline" if baseline_only else "archive",
                missing_is_contract_error=False,
            ),
            inserted=inserted,
            storage_ref=storage_ref,
            delivery_status=(
                "existing"
                if not inserted
                else ("baseline" if baseline_only else "not_analyzed")
            ),
            market_item_id=market_item_id,
            market_review_id=market_review_id,
        )
    decision_item = prepare_item_for_decision(item)
    partial = MarketProcessOutcome(
        flow_result=_flow_result(decision_item, {}, storage_ref, missing_is_contract_error=False),
        inserted=inserted,
        storage_ref=storage_ref,
    )
    try:
        production_decision: DecisionResult | None = None
        if production_admission is not None:
            if production_portfolio is None or market_item_id is None or market_review_id is None:
                raise RuntimeError("production review is missing portfolio, market item, or review identity")
            production_decision = decide_market_item_with_llm(
                decision_item,
                admission=production_admission,
                portfolio=production_portfolio,
                market_item_id=market_item_id,
                market_review_id=market_review_id,
            )
        if production_decision is None:
            raise RuntimeError(f"事件决策结果缺失：{decision_item.source}/{decision_item.title}")
        flow_result = evaluate_event_item(
            decision_item,
            production_decision,
            task=task,
            db_path=db_path,
            storage_ref=storage_ref,
        )
    except Exception as exc:  # noqa: BLE001 - preserve the inserted event reference for batch recovery
        raise MarketItemProcessingError(str(exc), partial, cause=exc) from exc
    if market_review_id is not None and not flow_result.decision.audit_json.get("contract_error"):
        if market_item_id is None:
            raise RuntimeError("market review exists without its market item identity")
        complete_market_review(
            market_review_id,
            flow_result,
            db_path=db_path,
            alias=("event", item.source, item_id, "market_items"),
        )
    if not deliver:
        status = "not_requested"
    elif snapshot and snapshot["delivered"]:
        status = "existing"
    else:
        status = deliver_event(
            flow_result,
            db_path=db_path,
            market_item_id=market_item_id,
            market_review_id=market_review_id,
        )
    return MarketProcessOutcome(
        flow_result=flow_result,
        inserted=inserted,
        storage_ref=storage_ref,
        delivery_status=status,
        market_item_id=market_item_id,
        market_review_id=market_review_id,
    )


def process_market_item(
    item: NormalizedMarketItem,
    raw_item: dict[str, Any],
    *,
    store_kind: StoreKind,
    task: str = "portfolio_event",
    db_path: Path = DEFAULT_DB_PATH,
    baseline_only: bool = False,
    analyze: bool = True,
    deliver: bool = True,
    use_rule_dedup: bool = True,
    reprocess_existing: bool = False,
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
    elif production_admission is not None and reprocess_existing and market_review_id is not None:
        previous = market_review_snapshot(market_review_id, db_path=db_path)
        if previous and previous["review_status"] == "succeeded":
            market_item_id, market_review_id = record_production_admission(
                item,
                production_admission,
                db_path=db_path,
                task=task if store_kind == "event" else "production",
                force_new=True,
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
                production_admission=production_admission,
                production_portfolio=production_portfolio,
                market_item_id=market_item_id,
                market_review_id=market_review_id,
            )
        return _process_content_item(
            item,
            raw_item,
            store_kind=store_kind,
            db_path=db_path,
            deliver=deliver,
            use_rule_dedup=use_rule_dedup,
            reprocess_existing=reprocess_existing,
            production_admission=production_admission,
            production_portfolio=production_portfolio,
            market_item_id=market_item_id,
            market_review_id=market_review_id,
        )
    except Exception as exc:
        if market_review_id is not None:
            fail_market_review(market_review_id, exc, db_path=db_path)
        raise
