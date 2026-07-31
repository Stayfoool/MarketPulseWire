#!/usr/bin/env python3
"""Daily digest for market information that was not pushed instantly."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from cards import div_markdown, md_escape
from env_utils import load_env
from feishu import send_card
from market_db import DEFAULT_DB_PATH as DB_PATH
from market_canonical_reader import canonical_digest_rows


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
BJ = ZoneInfo("Asia/Shanghai")


def day_window(day: str) -> tuple[str, str]:
    if day:
        start_local = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=BJ)
    else:
        start_local = datetime.now(BJ).replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc).isoformat(), end_local.astimezone(timezone.utc).isoformat()


def fetch_digest_rows(conn: sqlite3.Connection, day: str) -> list[sqlite3.Row]:
    start_utc, end_utc = day_window(day)
    conn.row_factory = sqlite3.Row
    return canonical_digest_rows(conn, start_utc=start_utc, end_utc=end_utc)  # type: ignore[return-value]


def targets_text(row: sqlite3.Row) -> str:
    try:
        parsed = json.loads(row["affected_targets_json"] or "[]")
    except json.JSONDecodeError:
        parsed = []
    if not isinstance(parsed, list):
        return ""
    return "；".join(str(item).strip() for item in parsed if str(item).strip())


def build_digest_card(rows: list[sqlite3.Row], day: str) -> dict:
    display_day = day or datetime.now(BJ).strftime("%Y-%m-%d")
    elements = [
        div_markdown(f"**日期**：{md_escape(display_day)}"),
        div_markdown("**范围**：已开通信息源中未即时推送的市场信息"),
        div_markdown(f"**条数**：{len(rows)}"),
        {"tag": "hr"},
    ]
    if not rows:
        elements.append(div_markdown("今日暂无需要汇总的市场信息。"))
    for index, row in enumerate(rows[:40], start=1):
        targets = targets_text(row)
        parts = [
            f"**{index}. {md_escape(row['title'])}**",
            f"来源：{md_escape(row['source_module'] or row['source'])}",
            f"程度决策：{md_escape(row['decision_action'])}",
        ]
        if row["daily_summary"]:
            parts.append(f"摘要：{md_escape(row['daily_summary'])}")
        if targets:
            parts.append(f"涉及标的/环节：{md_escape(targets)}")
        if row["reason"]:
            parts.append(f"分流理由：{md_escape(row['reason'])}")
        if row["url"]:
            parts.append(f"[打开原文]({row['url']})")
        elements.append(div_markdown("\n".join(parts)))
    if len(rows) > 40:
        elements.append(div_markdown(f"其余 {len(rows) - 40} 条已省略，可在 Web 信息中心查看。"))
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "市场信息日报"},
        },
        "elements": elements,
    }


def main() -> int:
    load_env(ENV_PATH)
    parser = argparse.ArgumentParser(description="发送市场信息日报")
    parser.add_argument("--date", default="", help="北京时间日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with sqlite3.connect(DB_PATH) as conn:
        rows = fetch_digest_rows(conn, args.date)
    card = build_digest_card(rows, args.date)
    if args.dry_run:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return 0
    send_card(card)
    print(f"已发送市场信息日报：{len(rows)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
