"""Production persistence and fail-closed wrapper for the reviewed LLM decision."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from llm_analysis import call_chat_completion_raw_with_prompts_hard_deadline
from llm_rule_decision import LLMRulePrompt, resolve_input_text_scope
from llm_rule_shadow import LLMRuleExecution, execute_llm_rule_decision
from market_item import AdmissionResult, DecisionResult, NormalizedMarketItem
from market_store import application_revision
from rule_core_v1 import PortfolioRuleConfig


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT_DIR = ROOT / "reports" / "llm-decision-audits"
PRODUCTION_DECISION_TIMEOUT_SECONDS = 120
PRODUCTION_DECISION_CONTRACT_VERSION = "llm-production-decision-v1"
PRODUCTION_MAX_OUTPUT_TOKENS = 6000


class ProductionLLMDecisionError(RuntimeError):
    """Raised when an admitted item cannot produce an audited production decision."""


def _default_model_caller(deadline_monotonic: float):
    thinking = str(os.environ.get("RULE_COMPARISON_LLM_THINKING_TYPE") or "").strip() or None

    def call(prompt: LLMRulePrompt):
        return call_chat_completion_raw_with_prompts_hard_deadline(
            prompt.system_prompt,
            json.dumps(prompt.user_payload, ensure_ascii=False, separators=(",", ":")),
            deadline_monotonic=deadline_monotonic,
            user_agent="surveil-llm-production-decision/1.0",
            truncate_user_prompt=False,
            thinking_override=thinking,
            max_tokens_override=PRODUCTION_MAX_OUTPUT_TOKENS,
            temperature_override=0,
        )

    call.audit_options = {  # type: ignore[attr-defined]
        "temperature": 0,
        "max_output_tokens": PRODUCTION_MAX_OUTPUT_TOKENS,
        "thinking_type": thinking or "",
        "truncate_user_prompt": False,
        "total_deadline_seconds": PRODUCTION_DECISION_TIMEOUT_SECONDS,
    }
    return call


def _write_private_audit(
    execution: LLMRuleExecution,
    item: NormalizedMarketItem,
    admission: AdmissionResult,
    *,
    market_item_id: int,
    market_review_id: int,
    audit_dir: Path,
    generated_at: str,
    application_revision: str,
) -> Path:
    audit_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(audit_dir, 0o700)
    candidate = execution.candidate
    payload = {
        "contract_version": PRODUCTION_DECISION_CONTRACT_VERSION,
        "generated_at": generated_at,
        "retention_days": 30,
        "market_item_id": market_item_id,
        "market_review_id": market_review_id,
        "source": item.source,
        "source_item_id": str(item.raw.get("source_event_id") or item.raw.get("id") or item.dedupe_key),
        "application_revision": application_revision,
        "llm_decision_rule_version": candidate.get("llm_decision_rule_version") or "",
        "prompt_version": candidate.get("prompt_version") or "",
        "model": candidate.get("model") or "",
        "provider": candidate.get("provider") or "",
        "admission_config_version": admission.config_version,
        "item_digest": str(
            next(
                (
                    call.get("validation", {}).get("item_digest")
                    for call in candidate.get("model_audit", {}).get("calls", [])
                    if isinstance(call, dict) and isinstance(call.get("validation"), dict)
                ),
                "",
            )
        ),
        "evaluation_status": candidate.get("evaluation_status") or "",
        "failure_reason": candidate.get("failure_reason") or "",
        "decision": execution.decision.to_dict() if execution.decision else None,
        "model_audit": candidate.get("model_audit") or {},
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    path = audit_dir / (
        f"llm-decision-audit-{market_item_id}-{market_review_id}-{stamp}-{uuid.uuid4().hex[:8]}.json"
    )
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)
    return path


def decide_production_market_item(
    item: NormalizedMarketItem,
    *,
    admission: AdmissionResult,
    portfolio: PortfolioRuleConfig,
    market_item_id: int,
    market_review_id: int,
    audit_dir: Path = DEFAULT_AUDIT_DIR,
    model_caller: Callable[[LLMRulePrompt], Any] | None = None,
    now_monotonic: Callable[[], float] = time.monotonic,
) -> DecisionResult:
    """Return the only production degree decision or fail the current review."""
    if admission.status != "admitted":
        raise ValueError("production LLM decision requires admitted input")
    if not isinstance(portfolio, PortfolioRuleConfig):
        raise TypeError("production LLM decision requires the production PortfolioRuleConfig")
    started_at = now_monotonic()
    deadline = started_at + PRODUCTION_DECISION_TIMEOUT_SECONDS
    caller = model_caller or _default_model_caller(deadline)
    execution = execute_llm_rule_decision(
        item,
        admission=admission,
        portfolio=portfolio,
        model_caller=caller,
        input_text_scope=resolve_input_text_scope(item),
        deadline_monotonic=deadline,
        production_authority=True,
    )
    deployed_revision = application_revision()
    generated_at = datetime.now(timezone.utc).isoformat()
    audit_path = _write_private_audit(
        execution,
        item,
        admission,
        market_item_id=market_item_id,
        market_review_id=market_review_id,
        audit_dir=audit_dir,
        generated_at=generated_at,
        application_revision=deployed_revision,
    )
    if execution.decision is None:
        status = str(execution.candidate.get("evaluation_status") or "invalid_output")
        reason = str(execution.candidate.get("failure_reason") or "no valid DecisionResult")
        raise ProductionLLMDecisionError(f"LLM degree decision failed: {status}: {reason}")
    decision_audit = dict(execution.decision.audit_json)
    decision_audit.update(
        {
            "production_authority": True,
            "production_decision_contract_version": PRODUCTION_DECISION_CONTRACT_VERSION,
            "application_revision": deployed_revision,
            "market_item_id": market_item_id,
            "market_review_id": market_review_id,
            "audit_recorded": True,
            "decision_elapsed_seconds": round(now_monotonic() - started_at, 6),
        }
    )
    # The audit path is intentionally not stored in SQLite; the direct market ids
    # in the mode-0600 file provide the lookup in both directions.
    _ = audit_path
    return replace(execution.decision, audit_json=decision_audit)
