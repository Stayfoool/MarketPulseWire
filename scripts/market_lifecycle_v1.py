"""Inactive v1 lifecycle and source-integration contracts.

The module is side-effect free and is not imported by production collectors,
stores, runtime, delivery, or Web code.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal, Mapping


CONTRACT_VERSION = "market-lifecycle-v1"
RULE_FAMILIES = (
    "holding",
    "semiconductor_ai",
    "macro_data",
    "fed_policy",
    "trade_policy",
)

StoreKind = Literal["article", "event"]
CollectionClass = Literal["baseline", "live", "legacy_unclassified"]
ProcessabilityStatus = Literal[
    "not_required",
    "pending",
    "succeeded",
    "fallback",
    "failed_retryable",
    "failed_terminal",
    "legacy_unclassified",
]
LifecycleAdmissionStatus = Literal[
    "pending", "admitted", "excluded", "not_applicable", "legacy_unclassified"
]
ProcessingStatus = Literal[
    "not_applicable",
    "pending",
    "succeeded",
    "failed_retryable",
    "failed_terminal",
    "legacy_unclassified",
]
RefetchMode = Literal["bounded_payload", "url", "source_item_id", "none"]

VALID_ACTIONS = {"push", "daily", "archive", "ignore"}
VALID_DELIVERY_STATUSES = {"sent", "duplicate", "skipped", "failed"}
RETRYABLE_STATUSES = {"pending", "failed_retryable"}


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _bounded(value: object, *, field: str, limit: int, required: bool = False) -> str:
    text = _clean(value)
    if required and not text:
        raise ValueError(f"{field} is required")
    if len(text) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")
    return text


@dataclass(frozen=True)
class DiscoveryRecord:
    store_kind: StoreKind
    source: str
    item_id: str
    title: str = ""
    summary: str = ""
    url: str = ""
    published_at: str = ""
    first_seen_at: str = ""
    refetch_key: str = ""

    def __post_init__(self) -> None:
        if self.store_kind not in {"article", "event"}:
            raise ValueError(f"invalid store_kind: {self.store_kind}")
        object.__setattr__(self, "source", _bounded(self.source, field="source", limit=200, required=True))
        object.__setattr__(self, "item_id", _bounded(self.item_id, field="item_id", limit=500, required=True))
        object.__setattr__(self, "title", _bounded(self.title, field="title", limit=1000))
        object.__setattr__(self, "summary", _bounded(self.summary, field="summary", limit=4000))
        object.__setattr__(self, "url", _bounded(self.url, field="url", limit=4000))
        object.__setattr__(self, "published_at", _bounded(self.published_at, field="published_at", limit=100))
        object.__setattr__(self, "first_seen_at", _bounded(self.first_seen_at, field="first_seen_at", limit=100))
        object.__setattr__(self, "refetch_key", _bounded(self.refetch_key, field="refetch_key", limit=1000))

    def to_dict(self) -> dict[str, str]:
        return {
            "store_kind": self.store_kind,
            "source": self.source,
            "item_id": self.item_id,
            "title": self.title,
            "summary": self.summary,
            "url": self.url,
            "published_at": self.published_at,
            "first_seen_at": self.first_seen_at,
            "refetch_key": self.refetch_key,
        }


@dataclass(frozen=True)
class LifecycleState:
    collection_class: CollectionClass
    processability_status: ProcessabilityStatus
    admission_status: LifecycleAdmissionStatus
    processing_status: ProcessingStatus
    processability_reason: str = ""
    admission_reason: str = ""
    processing_error: str = ""
    contract_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION:
            raise ValueError(f"unsupported lifecycle contract: {self.contract_version}")
        if self.collection_class not in {"baseline", "live", "legacy_unclassified"}:
            raise ValueError(f"invalid collection_class: {self.collection_class}")
        if self.processability_status not in {
            "not_required", "pending", "succeeded", "fallback", "failed_retryable",
            "failed_terminal", "legacy_unclassified",
        }:
            raise ValueError(f"invalid processability_status: {self.processability_status}")
        if self.admission_status not in {
            "pending", "admitted", "excluded", "not_applicable", "legacy_unclassified"
        }:
            raise ValueError(f"invalid admission_status: {self.admission_status}")
        if self.processing_status not in {
            "not_applicable", "pending", "succeeded", "failed_retryable",
            "failed_terminal", "legacy_unclassified",
        }:
            raise ValueError(f"invalid processing_status: {self.processing_status}")
        for field, limit in (
            ("processability_reason", 500),
            ("admission_reason", 500),
            ("processing_error", 1000),
        ):
            object.__setattr__(self, field, _bounded(getattr(self, field), field=field, limit=limit))
        self._validate_combination()

    def _validate_combination(self) -> None:
        if self.collection_class == "legacy_unclassified":
            if not all(
                value == "legacy_unclassified"
                for value in (
                    self.processability_status,
                    self.admission_status,
                    self.processing_status,
                )
            ):
                raise ValueError("legacy collection class requires every lifecycle axis unclassified")
            return
        if self.collection_class == "baseline":
            if self.admission_status != "not_applicable" or self.processing_status != "not_applicable":
                raise ValueError("baseline cannot enter admission or processing")
            if self.processability_status not in {
                "not_required", "succeeded", "fallback", "failed_terminal"
            }:
                raise ValueError("baseline processability must be terminal")
            return
        if self.processability_status in {"pending", "failed_retryable"}:
            if self.admission_status != "pending" or self.processing_status != "not_applicable":
                raise ValueError("unfinished processability cannot enter admission")
            return
        if self.processability_status in {"failed_terminal"}:
            if self.admission_status != "not_applicable" or self.processing_status != "not_applicable":
                raise ValueError("terminal processability failure is not a business exclusion")
            return
        if self.admission_status in {"excluded", "not_applicable"}:
            if self.processing_status != "not_applicable":
                raise ValueError("excluded/not-applicable admission cannot enter processing")
            return
        if self.admission_status == "pending":
            if self.processing_status != "not_applicable":
                raise ValueError("pending admission cannot enter processing")
            return
        if self.admission_status == "admitted" and self.processing_status == "not_applicable":
            raise ValueError("admitted item requires an explicit processing status")

    @property
    def retryable(self) -> bool:
        return (
            self.processability_status in RETRYABLE_STATUSES
            or self.processing_status in RETRYABLE_STATUSES
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "collection_class": self.collection_class,
            "processability_status": self.processability_status,
            "admission_status": self.admission_status,
            "processing_status": self.processing_status,
            "processability_reason": self.processability_reason,
            "admission_reason": self.admission_reason,
            "processing_error": self.processing_error,
            "contract_version": self.contract_version,
        }


@dataclass(frozen=True)
class AssessmentRecord:
    store_name: str
    item_id: str
    decision_action: str = ""
    delivery_status: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "store_name", _bounded(self.store_name, field="store_name", limit=100, required=True))
        object.__setattr__(self, "item_id", _bounded(self.item_id, field="assessment item_id", limit=500, required=True))
        if self.decision_action not in VALID_ACTIONS:
            raise ValueError(f"invalid decision action: {self.decision_action}")
        if self.delivery_status and self.delivery_status not in VALID_DELIVERY_STATUSES:
            raise ValueError(f"invalid delivery status: {self.delivery_status}")
        if self.delivery_status and self.decision_action != "push":
            raise ValueError("delivery status applies only to a push decision")

    def to_dict(self) -> dict[str, str]:
        return {
            "store_name": self.store_name,
            "item_id": self.item_id,
            "decision_action": self.decision_action,
            "delivery_status": self.delivery_status,
        }


@dataclass(frozen=True)
class LifecycleProjection:
    discovery: DiscoveryRecord
    lifecycle: LifecycleState
    assessment: AssessmentRecord | None = None

    def __post_init__(self) -> None:
        if self.assessment and self.lifecycle.processing_status != "succeeded":
            raise ValueError("assessment requires succeeded processing")
        if self.assessment and self.lifecycle.admission_status != "admitted":
            raise ValueError("assessment requires admitted lifecycle state")

    @property
    def user_label(self) -> str:
        if self.lifecycle.collection_class == "legacy_unclassified":
            return "历史未分类"
        if self.lifecycle.collection_class == "baseline":
            return "基线"
        if self.lifecycle.processability_status in {"pending"} or self.lifecycle.processing_status == "pending":
            return "等待处理"
        if self.lifecycle.processability_status == "failed_retryable" or self.lifecycle.processing_status == "failed_retryable":
            return "处理失败 / 可重试"
        if self.lifecycle.processability_status == "failed_terminal" or self.lifecycle.processing_status == "failed_terminal":
            return "处理失败 / 终止"
        if self.lifecycle.admission_status == "excluded":
            return "已采集 / 未准入"
        if not self.assessment:
            return "历史未分类"
        if self.assessment.decision_action == "push":
            suffix = {
                "sent": "已发送", "duplicate": "重复", "skipped": "跳过", "failed": "失败"
            }.get(self.assessment.delivery_status, "待投递")
            return f"Push / {suffix}"
        return self.assessment.decision_action.capitalize()

    def to_dict(self) -> dict[str, Any]:
        return {
            "discovery": self.discovery.to_dict(),
            "lifecycle": self.lifecycle.to_dict(),
            "assessment": self.assessment.to_dict() if self.assessment else None,
            "user_label": self.user_label,
        }


@dataclass(frozen=True)
class SourceIntegrationContract:
    source: str
    store_kind: StoreKind
    refetch_mode: RefetchMode
    enrichment_required: bool
    direct_admission_families: tuple[str, ...] = ()
    rule_families: tuple[str, ...] = RULE_FAMILIES
    contract_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _bounded(self.source, field="source", limit=200, required=True))
        if self.store_kind not in {"article", "event"}:
            raise ValueError(f"invalid store_kind: {self.store_kind}")
        if self.refetch_mode not in {"bounded_payload", "url", "source_item_id", "none"}:
            raise ValueError(f"invalid refetch_mode: {self.refetch_mode}")
        if self.rule_families != RULE_FAMILIES:
            raise ValueError("normalized sources must evaluate all five rule families")
        if set(self.direct_admission_families) - {"trade_policy"}:
            raise ValueError("only audited trade surfaces may use direct admission")
        if self.contract_version != CONTRACT_VERSION:
            raise ValueError(f"unsupported lifecycle contract: {self.contract_version}")


def start_live_lifecycle(*, enrichment_required: bool) -> LifecycleState:
    return LifecycleState(
        collection_class="live",
        processability_status="pending" if enrichment_required else "not_required",
        admission_status="pending",
        processing_status="not_applicable",
    )


def finish_processability(
    state: LifecycleState,
    status: Literal["succeeded", "fallback", "failed_retryable", "failed_terminal"],
    *,
    reason: str = "",
) -> LifecycleState:
    if state.collection_class != "live" or state.processability_status not in RETRYABLE_STATUSES:
        raise ValueError("processability can finish only from live pending/retryable state")
    admission = "not_applicable" if status == "failed_terminal" else "pending"
    return replace(
        state,
        processability_status=status,
        processability_reason=reason,
        admission_status=admission,
        processing_status="not_applicable",
    )


def finish_admission(
    state: LifecycleState,
    status: Literal["admitted", "excluded", "not_applicable"],
    *,
    reason: str,
) -> LifecycleState:
    if state.collection_class != "live":
        raise ValueError("only live items enter admission")
    if state.processability_status not in {"not_required", "succeeded", "fallback"}:
        raise ValueError("admission requires processable content")
    if state.admission_status != "pending":
        raise ValueError("admission is already final")
    return replace(
        state,
        admission_status=status,
        admission_reason=reason,
        processing_status="pending" if status == "admitted" else "not_applicable",
    )


def finish_processing(
    state: LifecycleState,
    status: Literal["succeeded", "failed_retryable", "failed_terminal"],
    *,
    error: str = "",
) -> LifecycleState:
    if state.admission_status != "admitted" or state.processing_status not in RETRYABLE_STATUSES:
        raise ValueError("processing can finish only for admitted pending/retryable items")
    return replace(state, processing_status=status, processing_error=error)


def begin_retry(state: LifecycleState) -> LifecycleState:
    if state.processability_status == "failed_retryable":
        return replace(
            state,
            processability_status="pending",
            processability_reason="",
            admission_status="pending",
            processing_status="not_applicable",
            processing_error="",
        )
    if state.processing_status == "failed_retryable":
        return replace(state, processing_status="pending", processing_error="")
    if state.processability_status == "pending" or state.processing_status == "pending":
        return state
    raise ValueError("only pending/failed_retryable states may retry")


def _discovery_from_mapping(row: Mapping[str, Any], *, store_kind: StoreKind) -> DiscoveryRecord:
    item_id = row.get("item_id") if store_kind == "article" else row.get("source_event_id")
    return DiscoveryRecord(
        store_kind=store_kind,
        source=str(row.get("source") or ""),
        item_id=str(item_id or ""),
        title=str(row.get("title") or ""),
        summary=str(row.get("summary") or ""),
        url=str(row.get("url") or ""),
        published_at=str(row.get("published_at") or ""),
        first_seen_at=str(row.get("first_seen_at") or ""),
        refetch_key=str(row.get("refetch_key") or ""),
    )


def _assessment_from_mapping(
    row: Mapping[str, Any] | None, *, store_name: str, fallback_item_id: str
) -> AssessmentRecord | None:
    if not row:
        return None
    action = _clean(row.get("decision_action") or row.get("action"))
    if action not in VALID_ACTIONS:
        return None
    return AssessmentRecord(
        store_name=store_name,
        item_id=str(row.get("item_id") or row.get("id") or fallback_item_id),
        decision_action=action,
        delivery_status=_clean(row.get("delivery_status")),
    )


def project_legacy_article(
    seen_row: Mapping[str, Any], review_row: Mapping[str, Any] | None
) -> LifecycleProjection:
    discovery = _discovery_from_mapping(seen_row, store_kind="article")
    assessment = _assessment_from_mapping(
        review_row, store_name="article_reviews", fallback_item_id=discovery.item_id
    )
    if assessment:
        state = LifecycleState("live", "succeeded", "admitted", "succeeded")
    else:
        state = LifecycleState(
            "legacy_unclassified", "legacy_unclassified", "legacy_unclassified", "legacy_unclassified"
        )
    return LifecycleProjection(discovery, state, assessment)


def project_legacy_event(
    event_row: Mapping[str, Any], analysis_row: Mapping[str, Any] | None
) -> LifecycleProjection:
    discovery = _discovery_from_mapping(event_row, store_kind="event")
    if bool(event_row.get("baseline_only")):
        return LifecycleProjection(
            discovery,
            LifecycleState("baseline", "not_required", "not_applicable", "not_applicable"),
        )
    assessment = _assessment_from_mapping(
        analysis_row, store_name="event_analyses", fallback_item_id=discovery.item_id
    )
    if assessment:
        state = LifecycleState("live", "succeeded", "admitted", "succeeded")
    else:
        state = LifecycleState(
            "legacy_unclassified", "legacy_unclassified", "legacy_unclassified", "legacy_unclassified"
        )
    return LifecycleProjection(discovery, state, assessment)


def retryable_projections(items: tuple[LifecycleProjection, ...]) -> tuple[LifecycleProjection, ...]:
    return tuple(item for item in items if item.lifecycle.retryable)
