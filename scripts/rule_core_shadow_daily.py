#!/usr/bin/env python3
"""Generate one dated daily rule-core comparison report and reminder."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from cards import div_markdown, md_escape
from env_utils import load_env
from feishu import send_card
from rule_core_shadow_combined import REPORT_DIR, build_combined_report, markdown_report, write_combined
from rule_shadow_report_store import (
    REPORT_DATE_RE,
    daily_paths,
    list_daily_reports,
    load_daily_report,
    push_change_count,
    skipped_count,
)


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
BEIJING = ZoneInfo("Asia/Shanghai")


def review_window(
    *,
    now: datetime | None = None,
    report_date: str = "",
) -> tuple[str, datetime, datetime]:
    local_now = (now or datetime.now(timezone.utc)).astimezone(BEIJING)
    if report_date:
        if not REPORT_DATE_RE.fullmatch(report_date):
            raise ValueError("report date must use YYYY-MM-DD")
        end_local = datetime.strptime(report_date, "%Y-%m-%d").replace(
            hour=15,
            minute=30,
            tzinfo=BEIJING,
        )
    else:
        end_local = local_now.replace(hour=15, minute=30, second=0, microsecond=0)
        if local_now < end_local:
            end_local -= timedelta(days=1)
    start_local = end_local - timedelta(days=1)
    return (
        end_local.strftime("%Y-%m-%d"),
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
    )


def needs_review(payload: dict[str, Any]) -> bool:
    counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
    return int(counts.get("compared") or 0) > 0 or skipped_count(payload) > 0


def sort_review_items(payload: dict[str, Any]) -> None:
    items = payload.get("items") if isinstance(payload.get("items"), list) else []

    def rank(item: object) -> tuple[int, int, str, str]:
        row = item if isinstance(item, dict) else {}
        current = str(row.get("current_action") or "none")
        candidate = str(row.get("candidate_action") or "none")
        comparable = bool(row.get("comparable", True))
        changed = current != candidate
        involves_push = "push" in {current, candidate}
        return (
            0 if not comparable and current == "push" else 1 if changed and involves_push else 2 if not comparable else 3 if changed else 4,
            0 if candidate == "push" else 1,
            str(row.get("source") or ""),
            str(row.get("title") or ""),
        )

    items.sort(key=rank)


def build_reminder_card(payload: dict[str, Any]) -> dict[str, Any]:
    counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
    pairs = counts.get("action_changes_by_pair") if isinstance(counts.get("action_changes_by_pair"), dict) else {}
    pair_lines = [f"- `{md_escape(str(pair))}`：{int(count)}" for pair, count in sorted(pairs.items())]
    details = [
        f"**审阅日期**：{md_escape(str(payload.get('review_date') or ''))}",
        "**统计区间**：上一日 15:30 至当日 15:30（北京时间）",
        f"**可比较文章**：{int(counts.get('compared') or 0)}",
        f"**新旧 action 不一致**：{int(counts.get('action_changes') or 0)}",
        f"**涉及 push 的差异**：{push_change_count(payload)}",
        f"**无法比较**：{skipped_count(payload)}",
    ]
    if pair_lines:
        details.extend(["", "**action 变化**：", *pair_lines[:12]])
    details.extend(["", "请在 Surveil 工作台的“规则对比报告”中审阅。"])
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange" if int(counts.get("action_changes") or 0) else "blue",
            "title": {
                "tag": "plain_text",
                "content": f"现有生产规则与{payload.get('candidate_label') or '对比判断'}每日对比报告",
            },
        },
        "elements": [div_markdown("\n".join(details))],
    }


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _write_outputs(
    payload: dict[str, Any],
    report_dir: Path,
    *,
    update_latest: bool = True,
) -> dict[str, str]:
    json_path, markdown_path = daily_paths(report_dir, str(payload.get("review_date") or ""))
    _atomic_write(json_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(markdown_path, markdown_report(payload))
    if update_latest:
        write_combined(payload, report_dir)
    return {"json_path": str(json_path), "markdown_path": str(markdown_path)}


def run_daily_report(
    *,
    report_dir: Path = REPORT_DIR,
    now: datetime | None = None,
    report_date: str = "",
    deliver: bool = True,
    force_rebuild: bool = False,
    sender: Callable[[dict[str, Any]], bool] = send_card,
) -> dict[str, Any]:
    review_date, start, end = review_window(now=now, report_date=report_date)
    previous = load_daily_report(report_dir, review_date) or {}
    previous_notification = (
        previous.get("notification") if isinstance(previous.get("notification"), dict) else {}
    )
    already_sent = previous_notification.get("status") == "sent"
    if already_sent and not force_rebuild:
        output = {
            "json_path": str(daily_paths(report_dir, review_date)[0]),
            "markdown_path": str(daily_paths(report_dir, review_date)[1]),
        }
        write_combined(previous, report_dir)
        return {
            "ok": True,
            "review_date": review_date,
            "notification_status": "already_sent",
            "counts": previous.get("counts") or {},
            "output": output,
        }

    payload = build_combined_report(
        report_dir=report_dir,
        now=now or datetime.now(timezone.utc),
        since=start,
        until=end,
    )
    payload.update(
        {
            "report_kind": "daily_review",
            "report_title": f"现有生产规则与{payload.get('candidate_label') or '对比判断'}每日对比报告",
            "review_date": review_date,
            "notification": {"status": "pending"},
        }
    )
    if force_rebuild:
        previous_counts = previous.get("counts") if isinstance(previous.get("counts"), dict) else {}
        current_counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
        previous_reports = int(previous_counts.get("reports") or 0)
        retained_reports = int(current_counts.get("reports") or 0)
        if previous_reports and retained_reports < previous_reports:
            raise RuntimeError(
                f"historical rebuild requires all retained comparison reports: "
                f"found {retained_reports} of {previous_reports}"
            )
        payload["rebuild"] = {
            "rebuilt_at": (now or datetime.now(timezone.utc)).isoformat(),
            "source": "stored_comparison_reports",
            "candidate_re_evaluated": False,
        }
    sort_review_items(payload)

    if already_sent:
        payload["notification"] = {
            **previous_notification,
            "status": "sent",
            "rebuild_notification": "not_sent",
        }
        output = _write_outputs(payload, report_dir, update_latest=not force_rebuild)
        return {
            "ok": True,
            "review_date": review_date,
            "notification_status": "preserved_sent",
            "counts": payload.get("counts") or {},
            "output": output,
        }

    output = _write_outputs(payload, report_dir, update_latest=not force_rebuild)

    if not needs_review(payload):
        payload["notification"] = {"status": "not_sent_no_content"}
        notification_status = "not_sent_no_content"
    elif not deliver:
        payload["notification"] = {"status": "dry_run"}
        notification_status = "dry_run"
    else:
        sent = bool(sender(build_reminder_card(payload)))
        payload["notification"] = {
            "status": "sent" if sent else "failed",
            "attempted_at": datetime.now(timezone.utc).isoformat(),
        }
        notification_status = payload["notification"]["status"]

    output = _write_outputs(payload, report_dir, update_latest=not force_rebuild)
    return {
        "ok": notification_status != "failed",
        "review_date": review_date,
        "notification_status": notification_status,
        "counts": payload.get("counts") or {},
        "output": output,
    }


def main() -> int:
    load_env(ENV_PATH)
    parser = argparse.ArgumentParser(description="Generate the daily rule-core comparison review.")
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--date", default="", help="report end date in Beijing, YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="write reports without sending Feishu")
    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        help="rebuild from stored comparisons without re-evaluating candidate rules or resending a prior reminder",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = run_daily_report(
        report_dir=args.report_dir,
        report_date=args.date,
        deliver=not args.dry_run,
        force_rebuild=args.force_rebuild,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"规则对比日报 {result['review_date']}："
            f"notification={result['notification_status']} counts={result['counts']}"
        )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
