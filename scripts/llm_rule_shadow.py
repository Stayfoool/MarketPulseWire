"""Build one bounded report-only comparison for the reviewed LLM rules."""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Iterable

from llm_analysis import ChatCompletionResponse, LLMBalanceInsufficientError
from llm_rule_catalog import CATALOG_VERSION, RULE_MATRIX_VERSION
from llm_rule_decision import (
    ENGINE_VERSION,
    PROMPT_VERSION,
    LLMRuleInputError,
    LLMRulePrompt,
    apply_source_admission_boundary,
    build_llm_rule_prompt,
    build_llm_rule_repair_prompt,
    needs_validation_retry,
    validate_llm_rule_response,
)
from market_item import AdmissionResult, DecisionResult, NormalizedMarketItem
from rule_core_v1 import (
    PortfolioRuleConfig,
    RuleConfig,
    SourceAdmissionPolicy,
    admit_market_item,
)


CONTRACT_VERSION = "llm-rule-shadow-v2"
VALID_CURRENT_ADMISSION_STATUSES = {"admitted", "excluded", "not_applicable", "unknown"}
VALID_CURRENT_ACTIONS = {"push", "daily", "archive", "ignore"}
ModelCaller = Callable[[LLMRulePrompt], ChatCompletionResponse]


def _clean(value: object, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _families(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value)))


def _rule_ids(decision: DecisionResult | None) -> list[str]:
    if decision is None:
        return []
    return list(
        dict.fromkeys(
            str(hit.get("rule_id") or "")
            for hit in decision.rule_hits
            if isinstance(hit, dict) and hit.get("rule_id")
        )
    )[:24]


def _rule_evidence(decision: DecisionResult | None) -> list[dict[str, str]]:
    if decision is None:
        return []
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for hit in decision.rule_hits:
        if not isinstance(hit, dict):
            continue
        rule_id = _clean(hit.get("rule_id"), 120)
        quotes: list[str] = []
        for key in ("evidence_quote", "matched_text", "quote"):
            value = hit.get(key)
            if isinstance(value, str) and value.strip():
                quotes.append(value)
        evidence = hit.get("evidence")
        if isinstance(evidence, list):
            for item in evidence:
                if isinstance(item, dict) and isinstance(item.get("quote"), str):
                    quotes.append(str(item["quote"]))
                elif isinstance(item, str):
                    quotes.append(item)
        for quote in quotes:
            cleaned = _clean(quote, 300)
            identity = (rule_id, cleaned)
            if not cleaned or identity in seen:
                continue
            seen.add(identity)
            result.append({"rule_id": rule_id, "quote": cleaned})
            if len(result) >= 8:
                return result
    return result


def _decision_summary(decision: DecisionResult | None) -> dict[str, Any]:
    if decision is None:
        return {
            "action": None,
            "importance": None,
            "brief_reason": "",
            "reason": "",
            "rule_ids": [],
            "rule_evidence": [],
        }
    return {
        "action": decision.action,
        "importance": decision.importance,
        "brief_reason": _clean(decision.brief_reason, 500),
        "reason": _clean(decision.reason, 800),
        "rule_ids": _rule_ids(decision),
        "rule_evidence": _rule_evidence(decision),
    }

def _admission_evidence(admission: AdmissionResult) -> list[dict[str, Any]]:
    return [
        {
            "rule_family": evidence.rule_family,
            "reason_code": evidence.reason_code,
            "matched_subjects": list(evidence.matched_subjects),
            "matched_term_ids": list(evidence.matched_term_ids),
            "relation": evidence.relation,
        }
        for evidence in admission.evidence[:8]
    ]


def _term_id(value: str) -> str:
    return f"term:{hashlib.sha256(value.casefold().encode('utf-8')).hexdigest()[:12]}"


def _matched_context(
    item: NormalizedMarketItem,
    admission: AdmissionResult,
    portfolio: PortfolioRuleConfig,
) -> dict[str, list[str]]:
    holding_evidence = [evidence for evidence in admission.evidence if evidence.rule_family == "holding"]
    holding_subjects = list(
        dict.fromkeys(subject for evidence in holding_evidence for subject in evidence.matched_subjects if subject)
    )
    matched_term_ids = {
        term_id for evidence in holding_evidence for term_id in evidence.matched_term_ids if term_id
    }
    matched_holdings = [
        holding
        for holding in portfolio.holdings
        if any(subject in holding.names for subject in holding_subjects)
    ]
    related_keywords = [
        keyword
        for holding in matched_holdings
        for keyword in holding.related_news_keywords
        if _term_id(keyword) in matched_term_ids
    ]
    immediate_keywords = [
        keyword for holding in matched_holdings for keyword in holding.immediate_alert_keywords
    ]
    trusted_ids: list[str] = []
    extraction = item.raw.get("_attributed_research")
    if isinstance(extraction, dict) and str(extraction.get("institution_id") or "").strip():
        trusted_ids.append(str(extraction["institution_id"]).strip())
    trusted_aliases = [
        subject
        for evidence in admission.evidence
        if evidence.rule_family in {"semiconductor_ai", "fed_policy"}
        for subject in evidence.matched_subjects
        if subject
    ]
    context = {
        "holding_subjects": holding_subjects,
        "holding_symbols": [holding.symbol for holding in matched_holdings],
        "matched_related_keywords": related_keywords,
        "immediate_alert_keywords": immediate_keywords,
        "trusted_institution_ids": trusted_ids,
        "trusted_institution_aliases": trusted_aliases,
    }
    return {key: list(dict.fromkeys(values)) for key, values in context.items() if values}


def _candidate_base(admission: AdmissionResult) -> dict[str, Any]:
    return {
        "admission_status": admission.status,
        "admission_reason": admission.reason_code,
        "matched_families": list(admission.matched_families),
        "admission_evidence": _admission_evidence(admission),
        "evaluation_status": "not_admitted" if admission.status != "admitted" else "pending",
        "failure_reason": "",
        "action": None,
        "importance": None,
        "brief_reason": "",
        "reason": "",
        "rule_ids": [],
        "rule_evidence": [],
        "rule_assessments": [],
        "model": "",
        "provider": "",
        "model_response_id": "",
        "usage": {},
        "attempts": 0,
        "model_calls": 0,
        "elapsed_seconds": 0.0,
        "input_text_scope": "",
        "provided_fields": [],
        "article_chars": 0,
        "body_original_chars": 0,
        "body_provided_chars": 0,
        "body_truncated": False,
        "prompt_chars": 0,
        "rule_matrix_version": RULE_MATRIX_VERSION,
        "rule_catalog_version": CATALOG_VERSION,
        "prompt_version": PROMPT_VERSION,
        "candidate_engine": ENGINE_VERSION,
    }


def _model_call_audit(
    prompt: LLMRulePrompt,
    *,
    response: ChatCompletionResponse | None = None,
    result: Any | None = None,
    transport_error: str = "",
    request_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validation = result.to_dict() if result is not None else {}
    return {
        "request": {
            "messages": prompt.messages(),
            "rule_ids": list(prompt.rule_ids),
            "input_text_scope": prompt.input_text_scope,
            "provided_fields": list(prompt.provided_fields),
            "body_original_chars": prompt.body_original_chars,
            "body_provided_chars": prompt.body_provided_chars,
            "body_truncated": prompt.body_truncated,
            "options": dict(request_options or {}),
        },
        "response": (
            {
                "content": response.content,
                "model": response.model,
                "provider": response.provider,
                "response_id": response.response_id,
                "usage": dict(response.usage),
                "attempts": response.attempts,
                "elapsed_seconds": response.elapsed_seconds,
            }
            if response is not None
            else None
        ),
        "validation": validation,
        "transport_error": transport_error,
    }


def _failure_candidate(
    admission: AdmissionResult,
    status: str,
    reason: str,
    *,
    prompt: LLMRulePrompt | None = None,
    response: ChatCompletionResponse | None = None,
    model_calls: int = 0,
    model_audit_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    candidate = _candidate_base(admission)
    candidate.update(
        {
            "evaluation_status": status,
            "failure_reason": _clean(reason, 500),
            "input_text_scope": prompt.input_text_scope if prompt else "",
            "provided_fields": list(prompt.provided_fields) if prompt else [],
            "article_chars": prompt.article_chars if prompt else 0,
            "body_original_chars": prompt.body_original_chars if prompt else 0,
            "body_provided_chars": prompt.body_provided_chars if prompt else 0,
            "body_truncated": prompt.body_truncated if prompt else False,
            "prompt_chars": prompt.input_chars if prompt else 0,
            "model_audit": {
                "retention_days": 30,
                "calls": list(model_audit_calls or []),
            }
            if model_audit_calls
            else {},
        }
    )
    if response is not None:
        candidate.update(
            {
                "model": _clean(response.model, 200),
                "provider": _clean(response.provider, 200),
                "model_response_id": _clean(response.response_id, 200),
                "usage": dict(response.usage),
                "attempts": response.attempts,
                "model_calls": model_calls,
                "elapsed_seconds": response.elapsed_seconds,
            }
        )
    return candidate


def _completed_candidate(
    admission: AdmissionResult,
    prompt: LLMRulePrompt,
    response: ChatCompletionResponse,
    result: Any,
    *,
    model_calls: int = 1,
    model_audit_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    decision = result.decision
    if decision is None:
        raise ValueError("completed model validation did not return DecisionResult")
    candidate = _candidate_base(admission)
    candidate.update(
        {
            **_decision_summary(decision),
            "evaluation_status": "completed",
            "failure_reason": "",
            "rule_assessments": [dict(assessment) for assessment in result.rule_assessments],
            "model": _clean(response.model, 200),
            "provider": _clean(response.provider, 200),
            "model_response_id": _clean(response.response_id, 200),
            "usage": dict(response.usage),
            "attempts": response.attempts,
            "model_calls": model_calls,
            "elapsed_seconds": response.elapsed_seconds,
            "input_text_scope": prompt.input_text_scope,
            "provided_fields": list(prompt.provided_fields),
            "article_chars": prompt.article_chars,
            "body_original_chars": prompt.body_original_chars,
            "body_provided_chars": prompt.body_provided_chars,
            "body_truncated": prompt.body_truncated,
            "prompt_chars": prompt.input_chars,
            "model_audit": {
                "retention_days": 30,
                "calls": list(model_audit_calls or []),
            },
        }
    )
    return candidate


def _combined_response(
    first: ChatCompletionResponse,
    second: ChatCompletionResponse,
) -> ChatCompletionResponse:
    usage = {
        key: int(first.usage.get(key, 0)) + int(second.usage.get(key, 0))
        for key in {"prompt_tokens", "completion_tokens", "total_tokens"}
        if key in first.usage or key in second.usage
    }
    return ChatCompletionResponse(
        content=second.content,
        model=second.model,
        provider=second.provider,
        response_id=second.response_id,
        usage=usage,
        attempts=first.attempts + second.attempts,
        elapsed_seconds=round(first.elapsed_seconds + second.elapsed_seconds, 6),
    )


def compare_llm_rule_candidate(
    item: NormalizedMarketItem,
    *,
    current_decision: DecisionResult | None,
    current_admission_status: str,
    current_admission_reason: str,
    current_matched_families: Iterable[str],
    rule_config: RuleConfig,
    portfolio: PortfolioRuleConfig,
    source_policy: SourceAdmissionPolicy,
    model_caller: ModelCaller,
    production_admission: AdmissionResult | None = None,
    input_text_scope: str | None = None,
    max_input_chars: int = 120_000,
) -> dict[str, Any]:
    if current_admission_status not in VALID_CURRENT_ADMISSION_STATUSES:
        raise ValueError(f"invalid current admission status: {current_admission_status}")
    if current_decision is not None and current_decision.action not in VALID_CURRENT_ACTIONS:
        raise ValueError(f"invalid current decision action: {current_decision.action}")

    admission = production_admission or apply_source_admission_boundary(
        item,
        admit_market_item(
            item,
            rule_config=rule_config,
            portfolio=portfolio,
            source_policy=source_policy,
        ),
    )
    current = {
        "admission_status": current_admission_status,
        "admission_reason": _clean(current_admission_reason, 500),
        "matched_families": _families(current_matched_families),
        **_decision_summary(current_decision),
    }
    if admission.status != "admitted":
        candidate = _candidate_base(admission)
    else:
        try:
            prompt = build_llm_rule_prompt(
                item,
                admission,
                input_text_scope=input_text_scope,
                matched_context=_matched_context(item, admission, portfolio),
                max_input_chars=max_input_chars,
            )
        except LLMRuleInputError as exc:
            candidate = _failure_candidate(admission, "insufficient_input", f"{exc.code}: {exc}")
        else:
            audit_calls: list[dict[str, Any]] = []
            request_options = getattr(model_caller, "audit_options", {})
            try:
                response = model_caller(prompt)
            except LLMBalanceInsufficientError:
                audit_calls.append(
                    _model_call_audit(
                        prompt,
                        transport_error="balance_insufficient",
                        request_options=request_options,
                    )
                )
                candidate = _failure_candidate(admission, "model_unavailable", "balance_insufficient", prompt=prompt, model_calls=1, model_audit_calls=audit_calls)
            except TimeoutError:
                audit_calls.append(
                    _model_call_audit(
                        prompt,
                        transport_error="timeout",
                        request_options=request_options,
                    )
                )
                candidate = _failure_candidate(admission, "model_unavailable", "timeout", prompt=prompt, model_calls=1, model_audit_calls=audit_calls)
            except Exception as exc:  # noqa: BLE001 - report-only failure must remain isolated.
                category = "not_configured" if "未配置" in str(exc) else "request_failed"
                audit_calls.append(
                    _model_call_audit(
                        prompt,
                        transport_error=category,
                        request_options=request_options,
                    )
                )
                candidate = _failure_candidate(admission, "model_unavailable", category, prompt=prompt, model_calls=1, model_audit_calls=audit_calls)
            else:
                result = validate_llm_rule_response(
                    response.content,
                    item,
                    admission,
                    input_text_scope=prompt.input_text_scope,
                    model=response.model,
                )
                audit_calls.append(
                    _model_call_audit(
                        prompt,
                        response=response,
                        result=result,
                        request_options=request_options,
                    )
                )
                model_calls = 1
                if needs_validation_retry(result):
                    repair_prompt: LLMRulePrompt | None = None
                    try:
                        repair_prompt = build_llm_rule_repair_prompt(
                            prompt,
                            previous_response=response.content,
                            validation_errors=result.validation_errors,
                            max_input_chars=max_input_chars,
                        )
                        repair_response = model_caller(repair_prompt)
                    except Exception:  # noqa: BLE001 - retain the validated first-call failure.
                        if repair_prompt is not None:
                            model_calls = 2
                            audit_calls.append(
                                _model_call_audit(
                                    repair_prompt,
                                    transport_error="repair_request_failed",
                                    request_options=request_options,
                                )
                            )
                    else:
                        response = _combined_response(response, repair_response)
                        prompt = repair_prompt
                        model_calls = 2
                        result = validate_llm_rule_response(
                            repair_response.content,
                            item,
                            admission,
                            input_text_scope=repair_prompt.input_text_scope,
                            model=repair_response.model,
                        )
                        audit_calls.append(
                            _model_call_audit(
                                repair_prompt,
                                response=repair_response,
                                result=result,
                                request_options=request_options,
                            )
                        )
                if result.evaluation_status == "completed":
                    candidate = _completed_candidate(
                        admission,
                        prompt,
                        response,
                        result,
                        model_calls=model_calls,
                        model_audit_calls=audit_calls,
                    )
                else:
                    candidate = _failure_candidate(
                        admission,
                        result.evaluation_status,
                        "; ".join(result.validation_errors),
                        prompt=prompt,
                        response=response,
                        model_calls=model_calls,
                        model_audit_calls=audit_calls,
                    )

    comparable = candidate.get("evaluation_status") == "completed"
    changed_fields = [
        field
        for field in ("admission_status", "admission_reason", "matched_families")
        if current[field] != candidate[field]
    ]
    if comparable:
        changed_fields.extend(
            field
            for field in ("action", "rule_ids")
            if current[field] != candidate[field]
        )
    return {
        "ok": True,
        "contract_version": CONTRACT_VERSION,
        "comparison_only": True,
        "affects_current_decision": False,
        "comparable": comparable,
        "current": current,
        "candidate": candidate,
        "changed_fields": changed_fields,
    }
