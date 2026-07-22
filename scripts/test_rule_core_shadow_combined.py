#!/usr/bin/env python3
"""Regression checks for the combined rule-core shadow report."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from llm_rule_catalog import CATALOG_VERSION
from llm_rule_decision import ENGINE_VERSION as LLM_RULE_ENGINE_VERSION
from rule_core_shadow_combined import RULE_CORE_VERSION, build_combined_report, markdown_report, write_combined


def report_payload(source: str, current: str, candidate: str) -> dict:
    return {
        "ok": True,
        "comparison_only": True,
        "affects_current_decision": False,
        "generated_at": "2026-07-19T11:18:16+00:00",
        "counts": {
            "compared": 1,
            "comparison_errors": 0,
            "action_changes": 1 if current != candidate else 0,
            "skipped": {"missing_full_text_or_shadow": 1},
            "action_changes_by_pair": {f"{current}->{candidate}": 1} if current != candidate else {},
        },
        "items": [
            {
                "source": source,
                "item_id": "item-1",
                "title": "DRAM价格持续上涨，供应极度紧缺",
                "url": "https://example.test/item-1",
                "comparison": {
                    "current": {
                        "action": current,
                        "importance": "medium",
                        "reason": "旧规则理由",
                        "rule_ids": [],
                    },
                    "candidate": {
                        "action": candidate,
                        "importance": "low",
                        "reason": "新内核理由",
                        "admission_reason": "content_scope_match",
                        "admission_status": "admitted",
                        "rule_ids": ["semiconductor_ordinary"],
                    },
                    "changed_fields": ["action"] if current != candidate else [],
                },
            }
        ],
    }


def test_combined_report_merges_source_groups_and_writes_markdown() -> None:
    with TemporaryDirectory() as tmpdir:
        report_dir = Path(tmpdir)
        (report_dir / "rule-core-shadow-news-20260719-111816.json").write_text(
            json.dumps(report_payload("sina_finance_articles", "daily", "archive"), ensure_ascii=False),
            encoding="utf-8",
        )
        (report_dir / "rule-core-shadow-research-20260719-111745.json").write_text(
            json.dumps(report_payload("digitimes_tw_semiconductors", "archive", "archive"), ensure_ascii=False),
            encoding="utf-8",
        )
        payload = build_combined_report(
            report_dir=report_dir,
            hours=24,
            now=datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc),
        )
        assert payload["comparison_only"] is True
        assert payload["affects_current_decision"] is False
        assert payload["counts"]["reports"] == 2
        assert payload["counts"]["compared"] == 2
        assert payload["counts"]["action_changes_by_pair"] == {"daily->archive": 1}
        assert payload["counts"]["skipped"] == {"missing_full_text_or_shadow": 2}
        assert payload["latest_rule_core_version"] == RULE_CORE_VERSION
        assert payload["counts"]["latest_rule_items"] == 0
        text = markdown_report(payload)
        assert "sina_finance_articles" in text
        assert "digitimes_tw_semiconductors" in text
        assert "旧规则理由" in text
        assert "content_scope_match; 新内核理由" in text
        assert "DRAM价格持续上涨" in text
        output = write_combined(payload, report_dir)
        assert Path(output["json_path"]).is_file()
        assert Path(output["markdown_path"]).read_text(encoding="utf-8").startswith("# Rule Core Shadow")


def test_combined_report_explains_current_admission_exclusion() -> None:
    with TemporaryDirectory() as tmpdir:
        report_dir = Path(tmpdir)
        payload = report_payload("wallstreetcn_news", "archive", "daily")
        current = payload["items"][0]["comparison"]["current"]
        current["action"] = None
        current["importance"] = None
        current["reason"] = ""
        current["admission_status"] = "excluded"
        current["admission_reason"] = "investment_universe_no_match"
        payload["counts"]["action_changes_by_pair"] = {"none->daily": 1}
        (report_dir / "rule-core-shadow-news-20260719-111816.json").write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        combined = build_combined_report(
            report_dir=report_dir,
            hours=24,
            now=datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc),
        )
        assert combined["counts"]["action_changes_by_pair"] == {}
        assert combined["counts"]["admission_differences"] == 1
        assert combined["items"][0]["comparison_status"] == "admission_difference"
        text = markdown_report(combined)
        assert "`none`" in text
        assert "investment_universe_no_match" in text


def test_combined_report_marks_latest_explicit_and_legacy_records() -> None:
    with TemporaryDirectory() as tmpdir:
        report_dir = Path(tmpdir)
        cases = (
            ("before", "2026-07-21T02:32:50+00:00", "", False, "unconfirmed"),
            ("boundary", "2026-07-21T02:32:51+00:00", "", True, "inferred_from_deployment_time"),
            ("explicit-old", "2026-07-21T03:00:00+00:00", "rule-core-v1-old", False, "recorded"),
            ("explicit-latest", "2026-07-21T03:01:00+00:00", RULE_CORE_VERSION, True, "recorded"),
        )
        for suffix, generated_at, version, _, _ in cases:
            payload = report_payload("wallstreetcn_news", "daily", "daily")
            payload["generated_at"] = generated_at
            payload["rule_config_version"] = "private-test-v1"
            payload["application_revision"] = f"revision-{suffix}"
            payload["items"][0]["item_id"] = suffix
            if version:
                payload["rule_core_version"] = version
            (report_dir / f"rule-core-shadow-news-{suffix}.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )

        combined = build_combined_report(
            report_dir=report_dir,
            since=datetime(2026, 7, 21, 2, 0, tzinfo=timezone.utc),
            until=datetime(2026, 7, 21, 4, 0, tzinfo=timezone.utc),
        )
        rows = {item["item_id"]: item for item in combined["items"]}
        assert combined["counts"]["latest_rule_items"] == 2
        assert combined["counts"]["earlier_or_unconfirmed_rule_items"] == 2
        for suffix, generated_at, version, is_latest, source in cases:
            row = rows[suffix]
            assert row["comparison_generated_at"] == generated_at
            assert row["rule_core_version"] == (version or (RULE_CORE_VERSION if is_latest else ""))
            assert row["rule_core_version_source"] == source
            assert row["rule_config_version"] == "private-test-v1"
            assert row["application_revision"] == f"revision-{suffix}"
            assert row["is_latest_rule_core_version"] is is_latest


def test_llm_failure_remains_visible_without_becoming_action_downgrade() -> None:
    with TemporaryDirectory() as tmpdir:
        report_dir = Path(tmpdir)
        payload = report_payload("digitimes", "push", "archive")
        payload["candidate_engine"] = LLM_RULE_ENGINE_VERSION
        payload["candidate_version"] = CATALOG_VERSION
        payload["rule_core_version"] = ""
        payload["counts"] = {
            "compared": 0,
            "action_changes": 0,
            "action_changes_by_pair": {},
            "skipped": {"invalid_output": 1},
        }
        comparison = payload["items"][0]["comparison"]
        comparison["comparable"] = False
        comparison["changed_fields"] = []
        comparison["candidate"] = {
            "action": None,
            "importance": None,
            "reason": "",
            "admission_status": "admitted",
            "admission_reason": "content_scope_match",
            "rule_ids": [],
            "evaluation_status": "invalid_output",
            "failure_reason": "response is not valid JSON",
            "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105},
            "elapsed_seconds": 0.5,
        }
        (report_dir / "rule-core-shadow-research-llm-failure.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        combined = build_combined_report(
            report_dir=report_dir,
            hours=24,
            now=datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc),
        )
        assert combined["candidate_label"] == "大模型候选"
        assert combined["counts"]["items"] == 1
        assert combined["counts"]["compared"] == 0
        assert combined["counts"]["unable_to_compare"] == 1
        assert combined["counts"]["model_validation_failures"] == 1
        assert combined["counts"]["action_changes"] == 0
        assert combined["counts"]["evaluation_statuses"] == {"invalid_output": 1}
        assert combined["counts"]["usage"]["total_tokens"] == 105
        row = combined["items"][0]
        assert row["current_action"] == "push"
        assert row["candidate_action"] is None
        assert row["comparable"] is False
        assert row["is_latest_candidate_version"] is True
        assert "invalid_output" in markdown_report(combined)


def test_combined_report_separates_both_excluded_and_admission_difference() -> None:
    with TemporaryDirectory() as tmpdir:
        report_dir = Path(tmpdir)
        both = report_payload("wallstreetcn_news", "archive", "archive")
        both["generated_at"] = "2026-07-19T11:10:00+00:00"
        comparison = both["items"][0]["comparison"]
        comparison["comparable"] = False
        comparison["current"].update(
            {"action": None, "admission_status": "excluded", "admission_reason": "investment_universe_no_match"}
        )
        comparison["candidate"].update(
            {"action": None, "admission_status": "excluded", "evaluation_status": "not_admitted"}
        )
        difference = report_payload("sina_finance_articles", "daily", "archive")
        difference["generated_at"] = "2026-07-19T11:11:00+00:00"
        comparison = difference["items"][0]["comparison"]
        comparison["comparable"] = False
        comparison["current"]["admission_status"] = "admitted"
        comparison["candidate"].update(
            {"action": None, "admission_status": "excluded", "evaluation_status": "not_admitted"}
        )
        for name, payload in (("both", both), ("difference", difference)):
            (report_dir / f"rule-core-shadow-news-{name}.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        combined = build_combined_report(
            report_dir=report_dir,
            hours=24,
            now=datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc),
        )
        assert combined["counts"]["both_not_admitted"] == 1
        assert combined["counts"]["admission_differences"] == 1
        assert combined["counts"]["model_validation_failures"] == 0
        assert combined["counts"]["unable_to_compare"] == 0
        statuses = {row["source"]: row["comparison_status"] for row in combined["items"]}
        assert statuses == {
            "wallstreetcn_news": "both_not_admitted",
            "sina_finance_articles": "admission_difference",
        }
        both_row = next(row for row in combined["items"] if row["source"] == "wallstreetcn_news")
        assert both_row["evaluation_status"] == "not_admitted"


def test_llm_completed_row_preserves_bounded_audit_fields_without_body_or_raw_response() -> None:
    with TemporaryDirectory() as tmpdir:
        report_dir = Path(tmpdir)
        payload = report_payload("digitimes", "daily", "push")
        payload["candidate_engine"] = LLM_RULE_ENGINE_VERSION
        payload["candidate_version"] = CATALOG_VERSION
        payload["rule_core_version"] = ""
        comparison = payload["items"][0]["comparison"]
        comparison["comparable"] = True
        comparison["candidate"].update(
            {
                "evaluation_status": "completed",
                "model": "fixed-test-model",
                "provider": "provider.example",
                "model_response_id": "private-response-id",
                "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
                "attempts": 1,
                "elapsed_seconds": 0.75,
                "input_text_scope": "title_summary_full_text",
                "provided_fields": ["title", "summary", "full_text"],
                "article_chars": 1800,
                "body_original_chars": 4200,
                "body_provided_chars": 3000,
                "body_truncated": True,
                "prompt_chars": 5200,
                "rule_evidence": [
                    {
                        "rule_id": "semiconductor_price_supply_change",
                        "quote": "DRAM价格持续上涨，供应极度紧缺。",
                    }
                ],
                "rule_assessments": [
                    {
                        "rule_id": "semiconductor_price_supply_change",
                        "judgement": "matched",
                        "selected_action": "push",
                        "evidence": [
                            {"field": "full_text", "quote": "DRAM价格持续上涨，供应极度紧缺。"}
                        ],
                        "explanation": "价格与供应变化达到 push。",
                    }
                ],
            }
        )
        payload["items"][0]["input_evidence"] = {
            "title_chars": 12,
            "summary_chars": 20,
            "full_text_chars": 1800,
            "body_source": "DIGITIMES RSS description",
        }
        serialized = json.dumps(payload, ensure_ascii=False)
        assert "PRIVATE_COMPLETE_BODY" not in serialized
        (report_dir / "rule-core-shadow-research-llm-completed.json").write_text(
            serialized, encoding="utf-8"
        )
        combined = build_combined_report(
            report_dir=report_dir,
            hours=24,
            now=datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc),
        )
        assert combined["candidate_label"] == "大模型候选"
        assert combined["counts"]["compared"] == 1
        assert combined["counts"]["usage"] == {
            "prompt_tokens": 120,
            "completion_tokens": 40,
            "total_tokens": 160,
        }
        row = combined["items"][0]
        assert row["candidate_engine"] == LLM_RULE_ENGINE_VERSION
        assert row["candidate_version"] == CATALOG_VERSION
        assert row["model"] == "fixed-test-model"
        assert row["provider"] == "provider.example"
        assert row["provided_fields"] == ["title", "summary", "full_text"]
        assert row["body_original_chars"] == 4200
        assert row["body_provided_chars"] == 3000
        assert row["body_truncated"] is True
        assert row["body_source"] == "DIGITIMES RSS description"
        assert row["candidate_rule_evidence"][0]["quote"] == "DRAM价格持续上涨，供应极度紧缺。"
        combined_text = json.dumps(combined, ensure_ascii=False)
        assert "private-response-id" not in combined_text
        assert "PRIVATE_COMPLETE_BODY" not in combined_text


def main() -> int:
    test_combined_report_merges_source_groups_and_writes_markdown()
    test_combined_report_explains_current_admission_exclusion()
    test_combined_report_marks_latest_explicit_and_legacy_records()
    test_llm_failure_remains_visible_without_becoming_action_downgrade()
    test_combined_report_separates_both_excluded_and_admission_difference()
    test_llm_completed_row_preserves_bounded_audit_fields_without_body_or_raw_response()
    print("rule core shadow combined checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
