#!/usr/bin/env python3
"""Regression checks for shadow collector digest reports."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from collector_shadow_digest import build_digest, markdown_digest, write_digest


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_shadow_digest_aggregates_reports_by_family_and_source() -> None:
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    with TemporaryDirectory() as tmpdir:
        report_dir = Path(tmpdir)
        write_json(
            report_dir / "research-collector-shadow-20260708-100000.json",
            {
                "ok": True,
                "finished_at": "2026-07-08T10:00:00+00:00",
                "rss": [
                    {
                        "source": "semianalysis",
                        "ok": True,
                        "raw_count": 2,
                        "candidate_count": 2,
                        "candidates": [
                            {
                                "title": "AI rack delay",
                                "url": "https://example.com/ai-rack",
                                "already_seen": False,
                                "already_reviewed": False,
                                "direct_shadow": {
                                    "ok": True,
                                    "decision": {
                                        "action": "push",
                                        "importance": "high",
                                        "rule_hit_ids": ["industry_quantified_hardline"],
                                    },
                                },
                            }
                        ],
                    }
                ],
            },
        )
        write_json(
            report_dir / "news-collector-shadow-20260708-101500.json",
            {
                "ok": False,
                "finished_at": "2026-07-08T10:15:00+00:00",
                "sources": [
                    {
                        "source": "cls_telegraph_api",
                        "ok": False,
                        "raw_count": 0,
                        "candidate_count": 0,
                        "focus_count": 0,
                        "candidates": [],
                        "error": "HTTP 503",
                    }
                ],
            },
        )
        write_json(
            report_dir / "official-collector-shadow-20260706-100000.json",
            {
                "ok": True,
                "finished_at": "2026-07-06T10:00:00+00:00",
                "rss": [{"source": "nvidia_blog", "ok": True, "raw_count": 99, "candidate_count": 99}],
            },
        )

        payload = build_digest(report_dir=report_dir, hours=24, now=now)
        assert payload["report_count"] == 2
        research = payload["families"]["research"]
        assert research["reports"] == 1
        assert research["sources"][0]["source"] == "semianalysis"
        assert research["sources"][0]["raw_items"] == 2
        assert research["sources"][0]["sample_new_candidates"] == 1
        assert research["sample_new_candidates"][0]["direct_action"] == "push"
        assert research["sample_new_candidates"][0]["direct_rule_ids"] == ["industry_quantified_hardline"]
        news = payload["families"]["news"]
        assert news["failed_reports"] == 1
        assert news["sources"][0]["last_error"] == "HTTP 503"
        assert payload["families"]["official"]["reports"] == 0

        markdown = markdown_digest(payload)
        assert "Research / Industry Media" in markdown
        assert "semianalysis" in markdown
        assert "direct=push" in markdown
        assert "industry_quantified_hardline" in markdown
        assert "HTTP 503" in markdown

        output = write_digest(payload, report_dir)
        assert Path(output["json_path"]).exists()
        assert Path(output["markdown_path"]).exists()


def main() -> int:
    test_shadow_digest_aggregates_reports_by_family_and_source()
    print("collector shadow digest checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
