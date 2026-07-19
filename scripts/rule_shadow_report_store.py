"""Read bounded daily rule comparison report files for operator views."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any


REPORT_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DAILY_PREFIX = "rule-core-shadow-daily-"


def skipped_count(payload: dict[str, Any]) -> int:
    counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
    skipped = counts.get("skipped") if isinstance(counts.get("skipped"), dict) else {}
    return sum(int(value or 0) for value in skipped.values())


def push_change_count(payload: dict[str, Any]) -> int:
    counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
    pairs = counts.get("action_changes_by_pair") if isinstance(counts.get("action_changes_by_pair"), dict) else {}
    return sum(int(count or 0) for pair, count in pairs.items() if "push" in str(pair).split("->"))


def daily_paths(report_dir: Path, report_date: str) -> tuple[Path, Path]:
    if not REPORT_DATE_RE.fullmatch(report_date):
        raise ValueError("report date must use YYYY-MM-DD")
    try:
        date.fromisoformat(report_date)
    except ValueError as exc:
        raise ValueError("report date must be a valid calendar date") from exc
    return (
        report_dir / f"{DAILY_PREFIX}{report_date}.json",
        report_dir / f"{DAILY_PREFIX}{report_date}.md",
    )


def load_daily_report(report_dir: Path, report_date: str) -> dict[str, Any] | None:
    json_path, _ = daily_paths(report_dir, report_date)
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def list_daily_reports(report_dir: Path, *, limit: int = 31) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for path in sorted(report_dir.glob(f"{DAILY_PREFIX}*.json"), reverse=True):
        report_date = path.stem.removeprefix(DAILY_PREFIX)
        if not REPORT_DATE_RE.fullmatch(report_date):
            continue
        payload = load_daily_report(report_dir, report_date)
        if payload is None:
            continue
        counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
        reports.append(
            {
                "date": report_date,
                "generated_at": payload.get("generated_at") or "",
                "compared": int(counts.get("compared") or 0),
                "action_changes": int(counts.get("action_changes") or 0),
                "push_changes": push_change_count(payload),
                "skipped": skipped_count(payload),
                "notification_status": (payload.get("notification") or {}).get("status")
                if isinstance(payload.get("notification"), dict)
                else "",
            }
        )
        if len(reports) >= max(1, min(limit, 366)):
            break
    return reports
