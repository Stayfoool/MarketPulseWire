#!/usr/bin/env python3
"""ValueList international-bank stock-research index monitor."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from article_gate import (
    article_item_id,
    mark_pushed as mark_article_pushed,
    review_exists as article_review_exists,
    rule_first_review,
    save_review as save_article_review,
)
from cards import build_article_card
from feishu import send_card
from rule_alert_dedup import confirm_rule_alert, release_rule_alert, reserve_rule_alert
from rss_monitor import DB_PATH, connect_db, save_new_items_with_retry
from source_health import record_source_failure, record_source_success
from source_profiles import source_profile_enabled
from value_directory_browser import LIST_URL, SOURCE_ID, SOURCE_MODULE, collect_entries
from x_check import load_env


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
REPORT_DIR = ROOT / "reports"
MONITOR = "value_directory"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
    return row is not None


def load_seen_item_ids(db_path: Path | None = None) -> set[str]:
    db_path = db_path or DB_PATH
    if not db_path.exists():
        return set()
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            if not table_exists(conn, "seen_items"):
                return set()
            return {
                str(row[0] or "")
                for row in conn.execute(
                    "SELECT item_id FROM seen_items WHERE source = ?",
                    (SOURCE_ID,),
                )
            }
    except sqlite3.Error:
        return set()


def load_reviewed_item_ids(db_path: Path | None = None) -> set[str]:
    db_path = db_path or DB_PATH
    if not db_path.exists():
        return set()
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            if not table_exists(conn, "article_reviews"):
                return set()
            return {
                str(row[0] or "")
                for row in conn.execute(
                    "SELECT item_id FROM article_reviews WHERE source = ?",
                    (SOURCE_ID,),
                )
            }
    except sqlite3.Error:
        return set()


def shadow_payload(entries: list[dict[str, Any]], *, started_at: str) -> dict[str, Any]:
    seen = load_seen_item_ids()
    reviewed = load_reviewed_item_ids()
    candidates = []
    for item in entries:
        item_id = article_item_id(item)
        candidates.append(
            {
                "source": SOURCE_ID,
                "id": item_id,
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "published_at": item.get("published_at", ""),
                "summary": item.get("summary", ""),
                "already_seen": item_id in seen,
                "already_reviewed": item_id in reviewed,
                "pipeline": "value_directory shadow -> rule-first article review planned",
            }
        )
    return {
        "ok": True,
        "mode": "shadow_dry_run",
        "sent_feishu": False,
        "ran_llm_review": False,
        "wrote_production_seen_items": False,
        "wrote_production_reviews": False,
        "source": SOURCE_ID,
        "url": LIST_URL,
        "started_at": started_at,
        "finished_at": utc_now(),
        "counts": {
            "raw_items": len(entries),
            "candidates": len(candidates),
            "already_seen_candidates": sum(1 for item in candidates if item["already_seen"]),
            "already_reviewed_candidates": sum(1 for item in candidates if item["already_reviewed"]),
        },
        "candidates": candidates,
        "errors": [],
    }


def rule_only_low_review(item: dict[str, Any]) -> dict[str, Any]:
    title = str(item.get("title") or "")
    return {
        "importance": "medium",
        "push_now": False,
        "market_impact": "",
        "incremental_classification": "规则未命中",
        "affected_targets": [],
        "daily_summary": title,
        "reason": "价值目录国际投行个股研报索引已入库；未命中直接持仓/观察标的的投行评级/目标价或重大主题策略硬规则，不即时推送。",
        "brief_reason": "未命中即时硬规则，仅入库观察。",
        "confidence": "规则",
        "model": "rule_only",
        "raw": {
            "llm_mode": "rule_only",
            "source": SOURCE_ID,
            "source_module": SOURCE_MODULE,
        },
    }


def review_and_maybe_push(item: dict[str, Any], *, recheck_rules: bool = False) -> bool:
    item_id = article_item_id(item)
    with connect_db() as conn:
        existing = article_review_exists(conn, SOURCE_ID, item_id)
    if existing:
        review = existing
        if recheck_rules and not review.get("pushed_at"):
            refreshed = rule_first_review(SOURCE_ID, item)
            if refreshed:
                review = refreshed
                with connect_db() as conn:
                    save_article_review(conn, SOURCE_ID, item, review)
    else:
        review = rule_first_review(SOURCE_ID, item) or rule_only_low_review(item)
        with connect_db() as conn:
            save_article_review(conn, SOURCE_ID, item, review)

    print(
        f"{SOURCE_ID} 规则门控：importance={review.get('importance')} "
        f"push={review.get('push_now')} title={item.get('title', '')}",
        flush=True,
    )
    if not review.get("push_now") or review.get("pushed_at"):
        return False

    reservation = reserve_rule_alert(
        review,
        source=SOURCE_ID,
        item_id=item_id,
        title=str(item.get("title") or ""),
        published_at=str(item.get("published_at") or ""),
        db_path=DB_PATH,
    )
    if reservation.get("duplicate"):
        first = reservation.get("first") or {}
        review = dict(review)
        review["push_now"] = False
        review["reason"] = (
            f"{review.get('reason') or ''}\n同一国际投行主题报告跨来源去重：已由 "
            f"{first.get('source') or '其他来源'} 在 {first.get('published_at') or '较早时间'} 提醒。"
        ).strip()
        raw = dict(review.get("raw") or {})
        raw["rule_alert_dedup"] = reservation
        review["raw"] = raw
        with connect_db() as conn:
            save_article_review(conn, SOURCE_ID, item, review)
        return False

    item = dict(item)
    item["article_review"] = review
    item["analysis_lines_prefix"] = [
        "来源：价值目录国际投行个股研报索引",
        str(review.get("brief_reason") or review.get("reason") or ""),
    ]
    sent = send_card(build_article_card(SOURCE_ID, item))
    if sent:
        confirm_rule_alert(reservation, db_path=DB_PATH)
        with connect_db() as conn:
            mark_article_pushed(conn, SOURCE_ID, item_id)
        return True
    release_rule_alert(reservation, db_path=DB_PATH)
    return False


def collect_production(
    entries: list[dict[str, Any]],
    *,
    notify_baseline: bool,
    started_at: str,
    recheck_item_id: str = "",
) -> dict[str, Any]:
    new_items = save_new_items_with_retry(
        SOURCE_ID,
        entries,
        notify_baseline=notify_baseline,
        source_label=SOURCE_MODULE,
    )
    pushed = 0
    reviewed = 0
    for item in new_items:
        reviewed += 1
        if review_and_maybe_push(item):
            pushed += 1
    rechecked = 0
    target_id = recheck_item_id.strip()
    if target_id and target_id not in {article_item_id(item) for item in new_items}:
        for item in entries:
            if article_item_id(item) != target_id:
                continue
            rechecked = 1
            reviewed += 1
            if review_and_maybe_push(item, recheck_rules=True):
                pushed += 1
            break
    return {
        "ok": True,
        "mode": "production",
        "sent_feishu": pushed > 0,
        "ran_llm_review": False,
        "wrote_production_seen_items": True,
        "wrote_production_reviews": reviewed > 0,
        "source": SOURCE_ID,
        "url": LIST_URL,
        "started_at": started_at,
        "finished_at": utc_now(),
        "counts": {
            "raw_items": len(entries),
            "new_items": len(new_items),
            "reviewed_items": reviewed,
            "rechecked_items": rechecked,
            "pushed_items": pushed,
        },
        "errors": [],
    }


def write_report(payload: dict[str, Any], report_dir: Path = REPORT_DIR) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    mode = "production" if payload.get("mode") == "production" else "shadow"
    path = report_dir / f"value-directory-{mode}-{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def print_summary(payload: dict[str, Any]) -> None:
    counts = payload.get("counts", {})
    print(
        f"value_directory {payload.get('mode')}: "
        f"raw={counts.get('raw_items', 0)} "
        f"new={counts.get('new_items', '-')} "
        f"reviewed={counts.get('reviewed_items', '-')} "
        f"pushed={counts.get('pushed_items', '-')}",
        flush=True,
    )
    if payload.get("mode") != "production":
        for item in payload.get("candidates", [])[:5]:
            seen = "seen" if item.get("already_seen") else "new?"
            print(f"  - ({seen}) {item.get('title')}", flush=True)
    for error in payload.get("errors", []):
        print(f"[ERR] {error}", flush=True)


def run(*, production: bool, limit: int, notify_baseline: bool, recheck_item_id: str = "") -> dict[str, Any]:
    started_at = utc_now()
    if not source_profile_enabled(SOURCE_ID):
        payload = {
            "ok": True,
            "mode": "production" if production else "shadow_dry_run",
            "skipped": True,
            "reason": "source profile 已停用",
            "started_at": started_at,
            "finished_at": utc_now(),
            "counts": {"raw_items": 0},
            "errors": [],
        }
        return payload
    try:
        entries = collect_entries(limit=limit)
        with connect_db() as conn:
            record_source_success(conn, MONITOR, SOURCE_ID)
        if production:
            return collect_production(
                entries,
                notify_baseline=notify_baseline,
                started_at=started_at,
                recheck_item_id=recheck_item_id,
            )
        return shadow_payload(entries, started_at=started_at)
    except Exception as exc:  # noqa: BLE001 - health state should capture every collector failure
        with connect_db() as conn:
            record_source_failure(conn, MONITOR, SOURCE_ID, exc)
        return {
            "ok": False,
            "mode": "production" if production else "shadow_dry_run",
            "source": SOURCE_ID,
            "url": LIST_URL,
            "started_at": started_at,
            "finished_at": utc_now(),
            "counts": {"raw_items": 0},
            "errors": [f"{type(exc).__name__}: {exc}"],
        }


def main() -> int:
    load_env(ENV_PATH)
    parser = argparse.ArgumentParser(description="Monitor ValueList international-bank stock research index.")
    parser.add_argument("--production", action="store_true", help="写入 seen_items/article_reviews 并按硬规则发送飞书。")
    parser.add_argument("--notify-baseline", action="store_true", help="首次建立基线时也处理旧条目。默认只建立基线。")
    parser.add_argument("--limit", type=int, default=30, help="读取列表页前 N 条。")
    parser.add_argument(
        "--recheck-item-id",
        default="",
        help="仅复核当前列表中指定的未推送 item ID；只会重跑确定性硬规则。",
    )
    parser.add_argument("--json", action="store_true", help="输出完整 JSON。")
    parser.add_argument("--write-report", action="store_true", help="把 JSON 报告写入 reports/。")
    parser.add_argument("--strict-exit", action="store_true", help="失败时返回非 0。")
    args = parser.parse_args()

    payload = run(
        production=args.production,
        limit=max(1, min(args.limit, 100)),
        notify_baseline=args.notify_baseline or os.getenv("SURVEIL_NOTIFY_BASELINE", "") == "1",
        recheck_item_id=args.recheck_item_id,
    )
    if args.write_report:
        payload["report_path"] = str(write_report(payload))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_summary(payload)
        if payload.get("report_path"):
            print(f"report: {payload['report_path']}")
    return 1 if args.strict_exit and not payload.get("ok") else 0


if __name__ == "__main__":
    raise SystemExit(main())
