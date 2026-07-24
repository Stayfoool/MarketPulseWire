#!/usr/bin/env python3
"""Regression checks for the daily rule-core comparison review."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from llm_rule_catalog import CATALOG_VERSION
from llm_rule_decision import ENGINE_VERSION as LLM_RULE_ENGINE_VERSION
from rule_core_shadow_daily import (
    build_reminder_card,
    list_daily_reports,
    load_daily_report,
    redact_expired_model_audits,
    review_window,
    run_daily_report,
)


def comparison_report(generated_at: str, item_id: str) -> dict:
    return {
        "generated_at": generated_at,
        "counts": {"skipped": {}},
        "items": [
            {
                "source": "sina_finance_articles",
                "item_id": item_id,
                "title": f"测试文章 {item_id}",
                "url": f"https://example.test/{item_id}",
                "comparison": {
                    "current": {
                        "action": "daily",
                        "importance": "medium",
                        "reason": "旧规则理由",
                        "rule_ids": [],
                    },
                    "candidate": {
                        "action": "push",
                        "importance": "high",
                        "reason": "新内核理由",
                        "admission_reason": "content_scope_match",
                        "admission_status": "admitted",
                        "rule_ids": ["semiconductor_material_change"],
                    },
                    "changed_fields": ["action", "importance"],
                },
            }
        ],
    }


def write_comparison(report_dir: Path, generated_at: str, suffix: str) -> None:
    path = report_dir / f"rule-core-shadow-news-{suffix}.json"
    path.write_text(json.dumps(comparison_report(generated_at, suffix)), encoding="utf-8")


def test_review_window_uses_consecutive_beijing_1530_boundaries() -> None:
    review_date, start, end = review_window(now=datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc))
    assert review_date == "2026-07-19"
    assert start == datetime(2026, 7, 18, 7, 30, tzinfo=timezone.utc)
    assert end == datetime(2026, 7, 19, 7, 30, tzinfo=timezone.utc)

    earlier_date, _, earlier_end = review_window(now=datetime(2026, 7, 19, 7, 0, tzinfo=timezone.utc))
    assert earlier_date == "2026-07-18"
    assert earlier_end == datetime(2026, 7, 18, 7, 30, tzinfo=timezone.utc)


def test_daily_report_is_bounded_dated_and_notified_once() -> None:
    with TemporaryDirectory() as tmpdir:
        report_dir = Path(tmpdir)
        write_comparison(report_dir, "2026-07-18T07:30:00+00:00", "at-start")
        write_comparison(report_dir, "2026-07-19T07:29:59+00:00", "before-end")
        write_comparison(report_dir, "2026-07-19T07:30:00+00:00", "at-end")
        cards: list[dict] = []

        result = run_daily_report(
            report_dir=report_dir,
            now=datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc),
            sender=lambda card: cards.append(card) or True,
        )
        assert result["ok"] is True
        assert result["notification_status"] == "sent"
        assert result["counts"]["compared"] == 2
        assert result["counts"]["action_changes"] == 2
        assert len(cards) == 1

        payload = load_daily_report(report_dir, "2026-07-19")
        assert payload is not None
        assert payload["window_start"] == "2026-07-18T07:30:00+00:00"
        assert payload["window_end"] == "2026-07-19T07:30:00+00:00"
        assert payload["notification"]["status"] == "sent"
        original_generated_at = payload["generated_at"]
        assert (report_dir / "rule-core-shadow-daily-2026-07-19.md").is_file()
        assert (report_dir / "rule-core-shadow-combined-latest.md").is_file()
        assert "Current Reason" in (report_dir / "rule-core-shadow-daily-2026-07-19.md").read_text()

        again = run_daily_report(
            report_dir=report_dir,
            now=datetime(2026, 7, 19, 9, 0, tzinfo=timezone.utc),
            sender=lambda card: cards.append(card) or True,
        )
        assert again["notification_status"] == "already_sent"
        assert len(cards) == 1
        assert load_daily_report(report_dir, "2026-07-19")["generated_at"] == original_generated_at
        assert list_daily_reports(report_dir)[0]["push_changes"] == 2

        write_comparison(report_dir, "2026-07-19T07:00:00+00:00", "late-retained-report")
        rebuilt = run_daily_report(
            report_dir=report_dir,
            now=datetime(2026, 7, 19, 10, 0, tzinfo=timezone.utc),
            force_rebuild=True,
            sender=lambda card: cards.append(card) or True,
        )
        assert rebuilt["notification_status"] == "preserved_sent"
        assert rebuilt["counts"]["compared"] == 3
        assert len(cards) == 1
        rebuilt_payload = load_daily_report(report_dir, "2026-07-19")
        assert rebuilt_payload is not None
        assert rebuilt_payload["notification"]["status"] == "sent"
        assert rebuilt_payload["notification"]["rebuild_notification"] == "not_sent"
        assert rebuilt_payload["rebuild"] == {
            "rebuilt_at": "2026-07-19T10:00:00+00:00",
            "source": "stored_comparison_reports",
            "candidate_re_evaluated": False,
        }
        assert "candidate rules were not re-evaluated" in (
            report_dir / "rule-core-shadow-daily-2026-07-19.md"
        ).read_text(encoding="utf-8")
        latest_payload = json.loads(
            (report_dir / "rule-core-shadow-combined-latest.json").read_text(encoding="utf-8")
        )
        assert latest_payload["counts"]["compared"] == 2

        card_text = json.dumps(build_reminder_card(payload), ensure_ascii=False)
        assert "规则对比报告" in card_text
        assert "涉及 push 的差异" in card_text


def test_empty_daily_report_writes_files_without_notification() -> None:
    with TemporaryDirectory() as tmpdir:
        calls: list[dict] = []
        result = run_daily_report(
            report_dir=Path(tmpdir),
            now=datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc),
            sender=lambda card: calls.append(card) or True,
        )
        assert result["ok"] is True
        assert result["notification_status"] == "not_sent_no_content"
        assert calls == []
        payload = load_daily_report(Path(tmpdir), "2026-07-19")
        assert payload is not None
        assert payload["notification"]["status"] == "not_sent_no_content"


def test_llm_daily_report_uses_dynamic_label_and_counts_failed_comparisons() -> None:
    with TemporaryDirectory() as tmpdir:
        report_dir = Path(tmpdir)
        payload = comparison_report("2026-07-19T07:00:00+00:00", "llm-failure")
        payload["candidate_engine"] = LLM_RULE_ENGINE_VERSION
        payload["candidate_version"] = CATALOG_VERSION
        payload["counts"] = {"compared": 0, "skipped": {"model_unavailable": 1}}
        comparison = payload["items"][0]["comparison"]
        comparison["comparable"] = False
        comparison["changed_fields"] = []
        comparison["candidate"] = {
            "action": None,
            "importance": None,
            "reason": "",
            "admission_reason": "content_scope_match",
            "admission_status": "admitted",
            "rule_ids": [],
            "evaluation_status": "model_unavailable",
            "failure_reason": "timeout",
        }
        (report_dir / "rule-core-shadow-news-llm-failure.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        cards: list[dict] = []
        result = run_daily_report(
            report_dir=report_dir,
            now=datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc),
            sender=lambda card: cards.append(card) or True,
        )
        assert result["notification_status"] == "sent"
        assert result["counts"]["compared"] == 0
        assert result["counts"]["unable_to_compare"] == 1
        payload = load_daily_report(report_dir, "2026-07-19")
        assert payload is not None
        assert payload["candidate_label"] == "大模型候选"
        assert payload["report_title"] == "现有生产规则与大模型候选每日对比报告"
        card_text = json.dumps(cards[0], ensure_ascii=False)
        assert "现有生产规则与大模型候选每日对比报告" in card_text
        assert "大模型判断或校验失败**：1" in card_text


def test_historical_rebuild_fails_when_retained_reports_are_missing() -> None:
    with TemporaryDirectory() as tmpdir:
        report_dir = Path(tmpdir)
        write_comparison(report_dir, "2026-07-19T07:00:00+00:00", "retained")
        first = run_daily_report(
            report_dir=report_dir,
            now=datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc),
            sender=lambda _card: True,
        )
        assert first["notification_status"] == "sent"
        original = load_daily_report(report_dir, "2026-07-19")
        assert original is not None and original["counts"]["reports"] == 1
        (report_dir / "rule-core-shadow-news-retained.json").unlink()

        try:
            run_daily_report(
                report_dir=report_dir,
                now=datetime(2026, 7, 19, 10, 0, tzinfo=timezone.utc),
                force_rebuild=True,
                deliver=False,
            )
        except RuntimeError as exc:
            assert "found 0 of 1" in str(exc)
        else:
            raise AssertionError("incomplete retained reports must fail historical rebuild")
        assert load_daily_report(report_dir, "2026-07-19") == original


def test_systemd_timer_and_installer_use_beijing_1530() -> None:
    root = Path(__file__).resolve().parents[1]
    timer = (root / "systemd" / "surveil-llm-decision-audit-cleanup.timer").read_text(encoding="utf-8")
    service = (root / "systemd" / "surveil-llm-decision-audit-cleanup.service").read_text(encoding="utf-8")
    installer = (root / "scripts" / "install_remote_systemd.sh").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 15:30:00 Asia/Shanghai" in timer
    assert "Persistent=true" in timer
    assert "llm_decision_audit_cleanup.py" in service
    assert "systemctl enable --now surveil-llm-decision-audit-cleanup.timer" in installer
    assert "systemctl disable --now surveil-rule-shadow-daily.timer" in installer

    try:
        load_daily_report(root / "reports", "2026-99-99")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid calendar dates must be rejected")


def test_model_audit_retention_removes_sensitive_payload_only_after_30_days() -> None:
    with TemporaryDirectory() as tmpdir:
        report_dir = Path(tmpdir)
        old_path = report_dir / "rule-core-shadow-news-old.json"
        recent_path = report_dir / "rule-core-shadow-news-recent.json"
        for path, generated_at, marker in (
            (old_path, "2026-06-01T00:00:00+00:00", "PRIVATE_OLD"),
            (recent_path, "2026-07-20T00:00:00+00:00", "PRIVATE_RECENT"),
        ):
            payload = comparison_report(generated_at, path.stem)
            payload["items"][0]["comparison"]["candidate"]["model_audit"] = {
                "retention_days": 30,
                "calls": [{"request": {"messages": [marker]}, "response": {"content": marker}}],
            }
            payload["items"][0]["comparison"]["candidate"]["model_response_id"] = marker
            path.write_text(json.dumps(payload), encoding="utf-8")
        redacted = redact_expired_model_audits(
            report_dir,
            now=datetime(2026, 7, 23, tzinfo=timezone.utc),
        )
        assert redacted == 1
        old_text = old_path.read_text(encoding="utf-8")
        recent_text = recent_path.read_text(encoding="utf-8")
        assert "PRIVATE_OLD" not in old_text
        assert '"status": "expired"' in old_text
        assert '"model_response_id": ""' in old_text
        assert "PRIVATE_RECENT" in recent_text
        assert (old_path.stat().st_mode & 0o777) == 0o600


def main() -> None:
    test_review_window_uses_consecutive_beijing_1530_boundaries()
    test_daily_report_is_bounded_dated_and_notified_once()
    test_empty_daily_report_writes_files_without_notification()
    test_llm_daily_report_uses_dynamic_label_and_counts_failed_comparisons()
    test_historical_rebuild_fails_when_retained_reports_are_missing()
    test_model_audit_retention_removes_sensitive_payload_only_after_30_days()
    test_systemd_timer_and_installer_use_beijing_1530()
    print("rule core shadow daily checks passed")


if __name__ == "__main__":
    main()
