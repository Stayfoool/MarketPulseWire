#!/usr/bin/env python3
"""Regression checks for the combined rule-core shadow report."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from rule_core_shadow_combined import build_combined_report, markdown_report, write_combined


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
                    "current": {"action": current},
                    "candidate": {
                        "action": candidate,
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
        payload = build_combined_report(report_dir=report_dir, hours=24)
        assert payload["comparison_only"] is True
        assert payload["affects_current_decision"] is False
        assert payload["counts"]["reports"] == 2
        assert payload["counts"]["compared"] == 2
        assert payload["counts"]["action_changes_by_pair"] == {"daily->archive": 1}
        assert payload["counts"]["skipped"] == {"missing_full_text_or_shadow": 2}
        text = markdown_report(payload)
        assert "sina_finance_articles" in text
        assert "digitimes_tw_semiconductors" in text
        assert "DRAM价格持续上涨" in text
        output = write_combined(payload, report_dir)
        assert Path(output["json_path"]).is_file()
        assert Path(output["markdown_path"]).read_text(encoding="utf-8").startswith("# Rule Core Shadow")


def main() -> int:
    test_combined_report_merges_source_groups_and_writes_markdown()
    print("rule core shadow combined checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
