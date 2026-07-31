"""Single production flow for normalized market items."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from attributed_research import prepare_item_for_decision
from db_utils import connect_sqlite
from decision_engine import decide_market_item_with_llm
from market_db import DEFAULT_DB_PATH
from market_delivery import deliver_market_item
from market_item import (
    AdmissionResult,
    DecisionResult,
    InterpretationResult,
    MarketFlowResult,
    NormalizedMarketItem,
    decision_result_from_dict,
    item_from_mapping,
)
from market_interpreter import INTERPRETER_VERSION, interpret_market_item
from market_store import (
    InsufficientEvidenceError,
    complete_market_review,
    fail_market_review,
    record_baseline_item,
    record_production_admission,
    market_review_snapshot,
    source_item_id,
)
from source_profiles import runtime_source_profile


FLOW_VERSION = "market_flow_v1"


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
    extra_notes: list[str] | None = None,
    user_agent: str = "surveil-market-flow/0.1",
    force_interpretation: bool = False,
    storage_ref: dict[str, Any] | None = None,
) -> MarketFlowResult:
    """Interpret one normalized item after its authoritative decision exists."""
    decision_item = item
    resolved_decision = decision
    should_interpret = source_interpretation is None and force_interpretation
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
        audit_json={
            "flow_version": FLOW_VERSION,
            "decision_supplied": decision is not None,
            "source_interpretation_supplied": source_interpretation is not None,
            "interpreter_called": should_interpret,
            "interpretation_failed": bool(interpretation_error),
            "interpretation_error": interpretation_error,
        },
    )

SOURCE_CATEGORY_DEFAULTS = {"trendforce_page": "research_industry_media"}


@dataclass
class MarketProcessOutcome:
    flow_result: MarketFlowResult
    inserted: bool
    storage_ref: dict[str, Any]
    delivery_status: str = "not_requested"
    market_item_id: int | None = None
    market_review_id: int | None = None

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
    source_profile_id: str | None = None,
) -> NormalizedMarketItem:
    """Build the canonical item at the collector/runtime boundary."""
    profile_id = str(source_profile_id or source)
    profile = _profile(profile_id)
    category = str(
        raw_item.get("source_category")
        or profile.get("category")
        or SOURCE_CATEGORY_DEFAULTS.get(source, "")
    )
    return item_from_mapping(
        source,
        raw_item,
        source_category=category,
        publisher_role=_publisher_role(raw_item, profile, category),
        collector=str(raw_item.get("collector") or profile.get("fetcher") or source),
        content_type=str(raw_item.get("content_type") or "unknown"),
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
    technical_action: str | None = None,
) -> MarketFlowResult:
    decision_payload = payload.get("decision_result")
    decision = decision_result_from_dict(decision_payload)
    if decision is None:
        if technical_action is None:
            raise RuntimeError("已成功 review 缺少有效 DecisionResult，已按关闭式策略停止处理")
        reason = "条目尚未进入决策阶段。"
        decision = DecisionResult(
            action=technical_action,
            reason=reason,
            brief_reason=reason,
            audit_json={"technical_action": technical_action},
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


def evaluate_item(
    item: NormalizedMarketItem,
    raw_item: dict[str, Any],
    decision: DecisionResult,
    *,
    storage_ref: dict[str, Any],
) -> MarketFlowResult:
    if not isinstance(decision, DecisionResult):
        raise RuntimeError("DecisionResult 决策结果缺失，已按关闭式策略停止处理")
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
        task="为一条已完成规则决策的市场信息生成极简实时摘要。",
        intro="请解读以下市场信息",
        extra_notes=["只可围绕 DecisionResult 的规则上下文解释，不得输出或改写推送开关。"],
        user_agent="surveil-market-flow/0.2",
        force_interpretation=not item.source.startswith("value_directory_"),
        storage_ref=storage_ref,
    )


def process_market_item(
    item: NormalizedMarketItem,
    raw_item: dict[str, Any],
    *,
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
    item_id = source_item_id(item)
    existed_before = market_item_id is not None
    if baseline_only and market_item_id is None:
        with connect_sqlite(db_path) as conn:
            existed_before = conn.execute(
                "SELECT 1 FROM market_items WHERE source=? AND source_item_id=?",
                (item.source, item_id),
            ).fetchone() is not None
        market_item_id = record_baseline_item(item, db_path=db_path)
    if production_admission is not None and (market_item_id is None or market_review_id is None):
        market_item_id, market_review_id = record_production_admission(
            item,
            production_admission,
            db_path=db_path,
        )
    elif production_admission is not None and reprocess_existing and market_review_id is not None:
        previous = market_review_snapshot(market_review_id, db_path=db_path)
        if previous and previous["review_status"] == "succeeded":
            market_item_id, market_review_id = record_production_admission(
                item,
                production_admission,
                db_path=db_path,
                force_new=True,
            )
    if market_item_id is None:
        raise RuntimeError("market information processing requires a market item identity")
    storage_ref = {
        "market_item_id": market_item_id,
        "market_review_id": market_review_id,
        "source": item.source,
        "source_item_id": item_id,
    }
    snapshot = market_review_snapshot(market_review_id, db_path=db_path) if market_review_id is not None else None
    if snapshot and snapshot["review_status"] == "insufficient_evidence":
        raise InsufficientEvidenceError("market review is already terminal with insufficient evidence")
    canonical_existing = bool(snapshot and snapshot["review_status"] == "succeeded")
    inserted = not canonical_existing if market_review_id is not None else not existed_before
    try:
        if baseline_only or not analyze:
            return MarketProcessOutcome(
                flow_result=_flow_result(
                    item,
                    {},
                    storage_ref,
                    technical_action="baseline" if baseline_only else "archive",
                ),
                inserted=inserted,
                storage_ref=storage_ref,
                delivery_status=(
                    "existing" if not inserted else ("baseline" if baseline_only else "not_analyzed")
                ),
                market_item_id=market_item_id,
                market_review_id=market_review_id,
            )
        if canonical_existing and not reprocess_existing:
            flow_result = _flow_result(item, dict(snapshot["payload"]), storage_ref)
            inserted = False
        else:
            if production_admission is None or production_portfolio is None or market_review_id is None:
                raise RuntimeError("market information processing requires admission, portfolio, and review identity")
            decision_item = prepare_item_for_decision(item)
            decision = decide_market_item_with_llm(
                decision_item,
                admission=production_admission,
                portfolio=production_portfolio,
                market_item_id=market_item_id,
                market_review_id=market_review_id,
            )
            flow_result = evaluate_item(
                decision_item,
                raw_item,
                decision,
                storage_ref=storage_ref,
            )
            complete_market_review(market_review_id, flow_result, db_path=db_path)
        if not deliver:
            status = "not_requested"
        elif snapshot and snapshot["delivered"]:
            status = "existing"
        elif market_review_id is None:
            status = "not_requested"
        else:
            status = deliver_market_item(
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
    except Exception as exc:
        if market_review_id is not None:
            fail_market_review(market_review_id, exc, db_path=db_path)
        raise
