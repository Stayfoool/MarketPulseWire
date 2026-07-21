#!/usr/bin/env python3
"""Fixed-response checks for the report-only LLM comparison module."""

from __future__ import annotations

import json
from pathlib import Path

from llm_analysis import ChatCompletionResponse
from llm_rule_catalog import RULE_MATRIX_VERSION, rules_for_families
from llm_rule_decision import SCHEMA_VERSION
from llm_rule_shadow import compare_llm_rule_candidate
from market_item import DecisionResult, NormalizedMarketItem, RuleFamily
from rule_core_v1 import SourceAdmissionPolicy, parse_portfolio_config, parse_rule_config


ROOT = Path(__file__).resolve().parents[1]
CONFIG = parse_rule_config(
    json.loads((ROOT / "config" / "rule_core_v1.test.json").read_text(encoding="utf-8"))
)
QUOTE = "HBM产能扩张项目已确认进入执行阶段。"


def _assessment(rule_id: str, *, matched: bool, action: str | None = None) -> dict:
    return {
        "rule_id": rule_id,
        "judgement": "matched" if matched else "not_matched",
        "selected_action": action if matched else None,
        "subjects": ["测试公司"] if matched else [],
        "change_object": "HBM产能" if matched else "",
        "direction": "扩张" if matched else "",
        "event_status": "executing" if matched else "unknown",
        "time_scope": "current" if matched else "unknown",
        "attribution": "公司公告" if matched else "",
        "evidence": [{"field": "full_text", "quote": QUOTE}] if matched else [],
        "counterevidence": [],
        "explanation": "原文证明产能扩张已进入执行。" if matched else "缺少该规则要求的事实。",
        "uncertainty_reason": "",
    }


def _response(family: RuleFamily, rule_id: str, action: str) -> str:
    return json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "rule_matrix_version": RULE_MATRIX_VERSION,
            "final_action": action,
            "rule_assessments": [
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


def _compare(item: NormalizedMarketItem, caller, *, portfolio=None):
    return compare_llm_rule_candidate(
        item,
        current_decision=DecisionResult(
            action="daily",
            importance="medium",
            reason="现有生产判断",
            rule_hits=[{"rule_id": "industry_quantified_hardline", "evidence_quote": QUOTE}],
        ),
        current_admission_status="admitted",
        current_admission_reason="current_scope_match",
        current_matched_families=("semiconductor_ai",),
        rule_config=CONFIG,
        portfolio=portfolio or parse_portfolio_config([]),
        source_policy=SourceAdmissionPolicy(),
        model_caller=caller,
    )


def test_completed_comparison_records_usage_and_bounded_evidence_without_body() -> None:
    captured = {}

    def caller(prompt):
        captured["prompt"] = prompt
        return _model_response(
            _response("semiconductor_ai", "semiconductor_material_change", "push")
        )

    item = _item()
    comparison = _compare(item, caller)
    assert comparison["ok"] is True
    assert comparison["comparison_only"] is True
    assert comparison["affects_current_decision"] is False
    assert comparison["comparable"] is True
    assert comparison["current"]["action"] == "daily"
    assert comparison["current"]["rule_evidence"] == [
        {"rule_id": "industry_quantified_hardline", "quote": QUOTE}
    ]
    candidate = comparison["candidate"]
    assert candidate["evaluation_status"] == "completed"
    assert candidate["action"] == "push"
    assert candidate["model"] == "fixed-test-model"
    assert candidate["provider"] == "provider.example"
    assert candidate["usage"]["total_tokens"] == 150
    assert candidate["attempts"] == 1
    assert candidate["elapsed_seconds"] == 0.25
    assert candidate["rule_ids"] == ["semiconductor_material_change"]
    assert "action" not in captured["prompt"].user_payload["source_metadata"]
    serialized = json.dumps(comparison, ensure_ascii=False)
    assert "PRIVATE_BODY_START" not in serialized


def test_invalid_output_model_failure_and_missing_text_never_create_candidate_action() -> None:
    invalid = _compare(_item(), lambda _prompt: _model_response("not-json"))
    assert invalid["comparable"] is False
    assert invalid["candidate"]["evaluation_status"] == "invalid_output"
    assert invalid["candidate"]["action"] is None
    assert invalid["current"]["action"] == "daily"

    unavailable = _compare(_item(), lambda _prompt: (_ for _ in ()).throw(RuntimeError("provider down")))
    assert unavailable["candidate"]["evaluation_status"] == "model_unavailable"
    assert unavailable["candidate"]["failure_reason"] == "request_failed"
    assert unavailable["candidate"]["action"] is None

    calls = []
    insufficient = _compare(_item(full_text=""), lambda prompt: calls.append(prompt))
    assert calls == []
    assert insufficient["candidate"]["evaluation_status"] == "insufficient_input"
    assert insufficient["candidate"]["action"] is None


def test_excluded_item_does_not_call_model() -> None:
    calls = []
    comparison = _compare(
        _item(title="普通生活资讯", summary="没有产业信息", full_text="普通生活资讯正文。"),
        lambda prompt: calls.append(prompt),
    )
    assert calls == []
    assert comparison["comparable"] is False
    assert comparison["candidate"]["admission_status"] == "excluded"
    assert comparison["candidate"]["evaluation_status"] == "not_admitted"
    assert comparison["candidate"]["action"] is None


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
        return _model_response(_response("holding", "holding_ordinary", "daily"))

    comparison = _compare(
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
    assert {rule_id.split("_")[0] for rule_id in prompt.rule_ids} == {"holding"}
    assert prompt.user_payload["admission"]["matched_families"] == ["holding"]
    assert prompt.user_payload["matched_context"] == {
        "holding_subjects": ["甲公司"],
        "holding_symbols": ["000001.SZ"],
        "immediate_alert_keywords": ["临时停产"],
    }
    assert comparison["candidate"]["action"] == "daily"


def main() -> int:
    test_completed_comparison_records_usage_and_bounded_evidence_without_body()
    test_invalid_output_model_failure_and_missing_text_never_create_candidate_action()
    test_excluded_item_does_not_call_model()
    test_company_disclosure_receives_only_holding_rules_and_minimal_matched_context()
    print("LLM rule shadow checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
