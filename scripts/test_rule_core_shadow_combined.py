#!/usr/bin/env python3
"""Regression checks for the combined rule-core shadow report."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

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
        assert combined["counts"]["action_changes_by_pair"] == {"none->daily": 1}
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


def main() -> int:
    test_combined_report_merges_source_groups_and_writes_markdown()
    test_combined_report_explains_current_admission_exclusion()
    test_combined_report_marks_latest_explicit_and_legacy_records()
    print("rule core shadow combined checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
