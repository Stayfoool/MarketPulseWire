#!/usr/bin/env python3
"""Fixed-response checks for the production LLM decision boundary."""

from __future__ import annotations

import asyncio
import json
import stat
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import llm_analysis
import market_content_adapter
from llm_analysis import ChatCompletionResponse
from llm_production_decision import ProductionLLMDecisionError, decide_production_market_item
from llm_rule_catalog import rules_for_families
from market_item import AdmissionEvidence, AdmissionResult, NormalizedMarketItem
from rule_core_v1 import parse_portfolio_config


QUOTE = "HBM产能扩张项目已确认进入执行阶段。"


def item() -> NormalizedMarketItem:
    return NormalizedMarketItem(
        source="digitimes",
        source_category="research_industry_media",
        publisher_role="research_publisher",
        content_type="article",
        title="HBM产能扩张",
        summary="项目进入执行。",
        full_text=f"PRIVATE_PRODUCTION_BODY。{QUOTE}后续将影响供给。",
        url="https://example.test/hbm",
        raw={"id": "production-1"},
    )


def admission() -> AdmissionResult:
    return AdmissionResult(
        status="admitted",
        reason_code="semiconductor_ai_scope_match",
        matched_families=("semiconductor_ai",),
        evidence=(
            AdmissionEvidence(
                rule_family="semiconductor_ai",
                reason_code="industry_keyword_match",
                evidence_quote="HBM",
                matched_subjects=("HBM",),
            ),
        ),
        config_version="test-config-v1",
    )


def response(action: str = "push") -> ChatCompletionResponse:
    payload = {
        "rule_results": [
            (
                {
                    "rule_id": rule.rule_id,
                    "judgement": "matched",
                    "action": action,
                    "evidence_ids": ["B2"],
                    "reason": "原文确认产能扩张进入执行阶段。",
                }
                if rule.rule_id == "semiconductor_material_change"
                else {"rule_id": rule.rule_id, "judgement": "not_matched"}
            )
            for rule in rules_for_families(("semiconductor_ai",))
        ]
    }
    return ChatCompletionResponse(
        content=json.dumps(payload, ensure_ascii=False),
        model="fixed-production-model",
        provider="provider.example",
        response_id="response-production-1",
        usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        attempts=1,
        elapsed_seconds=0.1,
    )


def test_valid_decisions_write_private_audits_and_keep_actions_authoritative() -> None:
    for index, action in enumerate(("push", "daily", "archive"), start=1):
        with TemporaryDirectory() as tmp:
            audit_dir = Path(tmp) / "audits"
            decision = decide_production_market_item(
                item(),
                admission=admission(),
                portfolio=parse_portfolio_config([]),
                market_item_id=10 + index,
                market_review_id=20 + index,
                audit_dir=audit_dir,
                model_caller=lambda _prompt, selected=action: response(selected),
            )
            assert decision.action == action
            assert decision.audit_json["production_authority"] is True
            assert decision.audit_json["market_item_id"] == 10 + index
            assert decision.audit_json["market_review_id"] == 20 + index
            paths = list(audit_dir.glob("llm-decision-audit-*.json"))
            assert len(paths) == 1
            assert stat.S_IMODE(audit_dir.stat().st_mode) == 0o700
            assert stat.S_IMODE(paths[0].stat().st_mode) == 0o600
            payload = json.loads(paths[0].read_text(encoding="utf-8"))
            assert payload["market_item_id"] == 10 + index
            assert payload["market_review_id"] == 20 + index
            assert payload["decision"]["action"] == action
            assert "PRIVATE_PRODUCTION_BODY" in json.dumps(payload["model_audit"], ensure_ascii=False)

            original_skeptic = market_content_adapter.apply_skeptic_review
            try:
                market_content_adapter.apply_skeptic_review = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("production LLM decisions must not enter skeptic postprocessing")
                )
                review = market_content_adapter.evaluate_article_review(
                    __import__("sqlite3").connect(":memory:"),
                    item().source,
                    item().raw | {"title": item().title, "summary": item().summary, "full_text": item().full_text},
                    normalized_item=item(),
                    decision=decision,
                )
            finally:
                market_content_adapter.apply_skeptic_review = original_skeptic
            assert review["raw"]["decision_result"]["action"] == action


def test_invalid_output_fails_closed_after_auditing() -> None:
    with TemporaryDirectory() as tmp:
        calls = []

        def invalid(prompt):
            calls.append(prompt)
            return ChatCompletionResponse(
                content="not-json",
                model="fixed-production-model",
                provider="provider.example",
                response_id="invalid",
                usage={},
                attempts=1,
                elapsed_seconds=0.1,
            )

        try:
            decide_production_market_item(
                item(),
                admission=admission(),
                portfolio=parse_portfolio_config([]),
                market_item_id=1,
                market_review_id=2,
                audit_dir=Path(tmp),
                model_caller=invalid,
            )
        except ProductionLLMDecisionError as exc:
            assert "invalid_output" in str(exc)
        else:
            raise AssertionError("invalid output must fail closed")
        assert len(calls) == 2
        audit = json.loads(next(Path(tmp).glob("llm-decision-audit-*.json")).read_text(encoding="utf-8"))
        assert audit["decision"] is None
        assert len(audit["model_audit"]["calls"]) == 2


def test_hard_deadline_cancels_inflight_http_request() -> None:
    original_config = llm_analysis.llm_config
    original_client = llm_analysis.httpx.AsyncClient
    original_retries = llm_analysis.retry_count

    class SlowResponse:
        status_code = 200
        is_error = False
        text = "{}"

        def json(self):
            return {}

    class SlowClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            await asyncio.sleep(0.2)
            return SlowResponse()

    try:
        llm_analysis.llm_config = lambda: ("secret", "https://provider.example/v1", "fixed")
        llm_analysis.httpx.AsyncClient = SlowClient
        llm_analysis.retry_count = lambda: 0
        started = time.monotonic()
        try:
            llm_analysis.call_chat_completion_raw_with_prompts_hard_deadline(
                "system",
                "user",
                deadline_monotonic=time.monotonic() + 0.02,
            )
        except TimeoutError:
            pass
        else:
            raise AssertionError("inflight request must be cancelled at the shared deadline")
        assert time.monotonic() - started < 0.15
    finally:
        llm_analysis.llm_config = original_config
        llm_analysis.httpx.AsyncClient = original_client
        llm_analysis.retry_count = original_retries


def main() -> int:
    test_valid_decisions_write_private_audits_and_keep_actions_authoritative()
    test_invalid_output_fails_closed_after_auditing()
    test_hard_deadline_cancels_inflight_http_request()
    print("production LLM decision checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
