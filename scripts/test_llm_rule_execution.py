#!/usr/bin/env python3
"""Fixed-response checks for LLM rule execution and strict validation."""

from __future__ import annotations

import json
from pathlib import Path

from llm_analysis import ChatCompletionResponse
from llm_rule_catalog import rules_for_families
from llm_rule_decision import apply_source_admission_boundary
from llm_rule_execution import execute_llm_rule_decision
from market_item import NormalizedMarketItem, RuleFamily
from admission_rules import SourceAdmissionPolicy, admit_market_item, parse_portfolio_config, parse_rule_config


ROOT = Path(__file__).resolve().parents[1]
CONFIG = parse_rule_config(
    json.loads((ROOT / "config" / "rule_core_v1.test.json").read_text(encoding="utf-8"))
)
QUOTE = "HBM产能扩张项目已确认进入执行阶段。"


def _assessment(rule_id: str, *, matched: bool, action: str | None = None) -> dict:
    if not matched:
        return {"rule_id": rule_id, "judgement": "not_matched"}
    return {
        "rule_id": rule_id,
        "judgement": "matched",
        "action": action,
        "evidence_ids": ["T1"],
        "reason": "原文证明产能扩张已进入执行。",
    }


def _response(family: RuleFamily, rule_id: str, action: str) -> str:
    return json.dumps(
        {
            "rule_results": [
                _assessment(rule.rule_id, matched=rule.rule_id == rule_id, action=action)
                for rule in rules_for_families((family,))
            ],
        },
        ensure_ascii=False,
    )


def _item(**overrides) -> NormalizedMarketItem:
    values = {
        "source": "digitimes",
        "source_category": "research_industry_media",
        "publisher_role": "research_publisher",
        "content_type": "article",
        "title": "HBM产能扩张",
        "summary": "项目进入执行。",
        "full_text": f"PRIVATE_BODY_START。{QUOTE}后续将影响供给。",
        "url": "https://example.test/hbm",
    }
    values.update(overrides)
    return NormalizedMarketItem(**values)


def _model_response(content: str) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        content=content,
        model="fixed-test-model",
        provider="provider.example",
        response_id="response-1",
        usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        attempts=1,
        elapsed_seconds=0.25,
    )


def _execute(item: NormalizedMarketItem, caller, *, portfolio=None):
    portfolio = portfolio or parse_portfolio_config([])
    admission = apply_source_admission_boundary(
        item,
        admit_market_item(
            item,
            rule_config=CONFIG,
            portfolio=portfolio,
            source_policy=SourceAdmissionPolicy(),
        ),
    )
    return execute_llm_rule_decision(
        item,
        admission=admission,
        portfolio=portfolio,
        model_caller=caller,
    )


def test_completed_execution_records_usage_and_private_model_audit() -> None:
    captured = {}

    def caller(prompt):
        captured["prompt"] = prompt
        return _model_response(
            _response("semiconductor_ai", "company_industry_execution_change", "push")
        )

    item = _item()
    execution = _execute(item, caller)
    evaluation = execution.evaluation
    assert execution.decision is not None
    assert execution.decision.action == "push"
    assert evaluation["evaluation_status"] == "completed"
    assert evaluation["action"] == "push"
    assert evaluation["model"] == "fixed-test-model"
    assert evaluation["provider"] == "provider.example"
    assert evaluation["usage"]["total_tokens"] == 150
    assert evaluation["attempts"] == 1
    assert evaluation["elapsed_seconds"] == 0.25
    assert evaluation["rule_ids"] == ["company_industry_execution_change"]
    assert "source_metadata" not in captured["prompt"].user_payload
    assert "current_decision" not in json.dumps(captured["prompt"].user_payload, ensure_ascii=False)
    assert evaluation["execution_engine"]
    audit = evaluation["model_audit"]
    assert "PRIVATE_BODY_START" in json.dumps(audit, ensure_ascii=False)
    assert "PRIVATE_BODY_START" not in json.dumps(evaluation["rule_evidence"], ensure_ascii=False)


def test_invalid_output_model_failure_and_missing_body_behavior() -> None:
    invalid_calls = []
    invalid = _execute(
        _item(),
        lambda prompt: invalid_calls.append(prompt) or _model_response("not-json"),
    )
    assert len(invalid_calls) == 2
    assert invalid.decision is None
    assert invalid.evaluation["evaluation_status"] == "invalid_output"
    assert invalid.evaluation["action"] is None
    assert len(invalid.evaluation["model_audit"]["calls"]) == 2
    assert invalid.evaluation["model_audit"]["calls"][0]["response"]["content"] == "not-json"
    assert invalid.evaluation["model_audit"]["calls"][0]["validation"]["validation_errors"]

    unavailable = _execute(_item(), lambda _prompt: (_ for _ in ()).throw(RuntimeError("provider down")))
    assert unavailable.decision is None
    assert unavailable.evaluation["evaluation_status"] == "model_unavailable"
    assert unavailable.evaluation["failure_reason"] == "request_failed"
    assert unavailable.evaluation["action"] is None
    assert unavailable.evaluation["model_audit"]["calls"][0]["response"] is None

    calls = []
    response = json.loads(_response("semiconductor_ai", "company_industry_execution_change", "push"))
    matched = next(result for result in response["rule_results"] if result["judgement"] == "matched")
    matched["evidence_ids"] = ["T1"]

    def title_summary_caller(prompt):
        calls.append(prompt)
        return _model_response(json.dumps(response, ensure_ascii=False))

    title_summary = _execute(_item(full_text=""), title_summary_caller)
    assert len(calls) == 1
    assert calls[0].input_text_scope == "title_summary"
    assert title_summary.evaluation["evaluation_status"] == "completed"
    assert title_summary.decision is not None and title_summary.decision.action == "push"


def test_excluded_item_does_not_call_model() -> None:
    calls = []
    execution = _execute(
        _item(title="普通生活资讯", summary="没有产业信息", full_text="普通生活资讯正文。"),
        lambda prompt: calls.append(prompt),
    )
    assert calls == []
    assert execution.decision is None
    assert execution.evaluation["admission_status"] == "excluded"
    assert execution.evaluation["evaluation_status"] == "not_admitted"
    assert execution.evaluation["action"] is None


def test_all_unmatched_response_completes_as_archive_without_retry() -> None:
    calls = []
    all_unmatched = json.dumps(
        {
            "rule_results": [
                {"rule_id": rule.rule_id, "judgement": "not_matched"}
                for rule in rules_for_families(("semiconductor_ai",))
            ]
        },
        ensure_ascii=False,
    )
    def caller(prompt):
        calls.append(prompt)
        return _model_response(all_unmatched)

    execution = _execute(_item(), caller)
    assert len(calls) == 1
    assert "validation_feedback" not in calls[0].user_payload
    evaluation = execution.evaluation
    assert execution.decision is not None
    assert evaluation["evaluation_status"] == "completed"
    assert evaluation["action"] == "archive"
    assert evaluation["model_calls"] == 1
    assert evaluation["attempts"] == 1
    assert evaluation["usage"]["total_tokens"] == 150


def test_no_match_with_uncertain_does_not_retry_or_create_decision() -> None:
    calls = []
    rules = rules_for_families(("semiconductor_ai",))
    response = json.dumps(
        {
            "rule_results": [
                (
                    {
                        "rule_id": rule.rule_id,
                        "judgement": "uncertain",
                        "counterevidence_ids": ["B1"],
                        "reason": "决定 action 所需事实仍有冲突。",
                    }
                    if rule.rule_id == "company_industry_execution_change"
                    else {"rule_id": rule.rule_id, "judgement": "not_matched"}
                )
                for rule in rules
            ]
        },
        ensure_ascii=False,
    )

    def caller(prompt):
        calls.append(prompt)
        return _model_response(response)

    execution = _execute(_item(), caller)
    assert len(calls) == 1
    assert execution.decision is None
    evaluation = execution.evaluation
    assert evaluation["evaluation_status"] == "uncertain"
    assert evaluation["action"] is None
    assert evaluation["model_calls"] == 1


def test_company_disclosure_receives_only_holding_rules_and_minimal_matched_context() -> None:
    portfolio = parse_portfolio_config(
        [
            {
                "symbol": "000001.SZ",
                "names": ["甲公司"],
                "related_news_keywords": ["HBM"],
                "exclude_keywords": [],
                "immediate_alert_keywords": ["临时停产"],
            }
        ]
    )
    captured = {}

    def caller(prompt):
        captured["prompt"] = prompt
        return _model_response(_response("holding", "company_industry_execution_change", "daily"))

    execution = _execute(
        _item(
            source="company_disclosures",
            source_category="company_disclosures",
            publisher_role="company_official",
            content_type="announcement",
            title="甲公司HBM项目更新",
            full_text=f"甲公司公告。{QUOTE}后续将影响供给。",
        ),
        caller,
        portfolio=portfolio,
    )
    prompt = captured["prompt"]
    assert prompt.rule_ids == tuple(
        rule.rule_id for rule in rules_for_families(("holding",))
    )
    assert "admission" not in prompt.user_payload
    assert prompt.user_payload["matched_context"] == {
        "holding_subjects": ["甲公司"],
        "holding_symbols": ["000001.SZ"],
        "matched_related_keywords": ["HBM"],
        "immediate_alert_keywords": ["临时停产"],
    }
    assert execution.decision is not None and execution.decision.action == "daily"


def main() -> int:
    test_completed_execution_records_usage_and_private_model_audit()
    test_invalid_output_model_failure_and_missing_body_behavior()
    test_excluded_item_does_not_call_model()
    test_all_unmatched_response_completes_as_archive_without_retry()
    test_no_match_with_uncertain_does_not_retry_or_create_decision()
    test_company_disclosure_receives_only_holding_rules_and_minimal_matched_context()
    print("LLM rule execution checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
