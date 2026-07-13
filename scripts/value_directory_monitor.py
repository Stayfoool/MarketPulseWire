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

from decision_engine import decide_market_item
from market_item import NormalizedMarketItem
from market_review_store import article_item_id, article_review_exists
from market_runtime import normalize_market_item, process_market_item
from push_rules import load_enabled_holdings_for_rules
from rss_monitor import DB_PATH, connect_db, save_new_items_with_retry
from source_health import record_source_failure, record_source_success
from source_profiles import source_profile_enabled
from value_directory_browser import (
    LIST_URL,
    SOURCE_ID,
    VALUE_DIRECTORY_SOURCES,
    ValueDirectorySource,
    collect_entries_for_source,
    collect_preview,
    default_source_ids,
    source_config,
)
from value_directory_preview import apply_preview_to_item, extract_preview_facts
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


def load_seen_item_ids(source_id: str = SOURCE_ID, db_path: Path | None = None) -> set[str]:
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
                    (source_id,),
                )
            }
    except sqlite3.Error:
        return set()


def load_reviewed_item_ids(source_id: str = SOURCE_ID, db_path: Path | None = None) -> set[str]:
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
                    (source_id,),
                )
            }
    except sqlite3.Error:
        return set()


def shadow_payload(entries: list[dict[str, Any]], *, started_at: str, source: ValueDirectorySource | None = None) -> dict[str, Any]:
    source = source or source_config()
    seen = load_seen_item_ids(source.source_id)
    reviewed = load_reviewed_item_ids(source.source_id)
    candidates = []
    for item in entries:
        item_id = article_item_id(item)
        candidates.append(
            {
                "source": source.source_id,
                "id": item_id,
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "published_at": item.get("published_at", ""),
                "summary": item.get("summary", ""),
                "already_seen": item_id in seen,
                "already_reviewed": item_id in reviewed,
                "pipeline": "value_directory shadow only; production uses unified market flow",
            }
        )
    return {
        "ok": True,
        "mode": "shadow_dry_run",
        "sent_feishu": False,
        "ran_llm_review": False,
        "wrote_production_seen_items": False,
        "wrote_production_reviews": False,
        "source": source.source_id,
        "url": source.list_url,
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


def preview_enabled() -> bool:
    return os.getenv("VALUE_DIRECTORY_PREVIEW_ENABLED", "1").strip() != "0"


def push_on_preview_failure() -> bool:
    return os.getenv("VALUE_DIRECTORY_PUSH_ON_PREVIEW_FAILURE", "1").strip() != "0"


def recheck_unpushed_enabled() -> bool:
    return os.getenv("VALUE_DIRECTORY_RECHECK_UNPUSHED", "1").strip() != "0"


def recheck_unpushed_limit() -> int:
    raw = os.getenv("VALUE_DIRECTORY_RECHECK_UNPUSHED_LIMIT", "").strip()
    try:
        return max(0, min(100, int(raw))) if raw else 30
    except ValueError:
        return 30


def enrich_item_with_preview(item: dict[str, Any]) -> dict[str, Any]:
    if not preview_enabled():
        return item
    preview = collect_preview(str(item.get("url") or ""))
    facts = extract_preview_facts(item, preview)
    return apply_preview_to_item(item, preview, facts)


def has_preview_record(item: dict[str, Any]) -> bool:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    preview = raw.get("value_directory_preview") if isinstance(raw.get("value_directory_preview"), dict) else {}
    return bool(preview.get("facts"))


def with_preview_failure(item: dict[str, Any], error: Exception) -> dict[str, Any]:
    updated = dict(item)
    raw = dict(updated.get("raw") or {})
    raw["value_directory_preview"] = {
        "facts": {
            "status": "failed",
            "model": "preview_failed",
            "error": str(error)[:500],
        }
    }
    updated["raw"] = raw
    updated["preview_lines"] = [f"第一页提取：失败/不可用（{error}）"]
    return updated


def with_value_directory_policy(item: dict[str, Any]) -> dict[str, Any]:
    updated = dict(item)
    raw = dict(updated.get("raw") or {})
    raw["value_directory_policy"] = {
        "preview_enabled": preview_enabled(),
        "push_on_preview_failure": push_on_preview_failure(),
    }
    updated["raw"] = raw
    return updated


def normalized_value_directory_item(
    item: dict[str, Any],
    source: ValueDirectorySource,
) -> NormalizedMarketItem:
    prepared = dict(item)
    prepared["source_category"] = "research_industry_media"
    prepared["collector"] = "value_directory_monitor"
    prepared["content_type"] = "research_index"
    return normalize_market_item(
        source.source_id,
        prepared,
        store_kind="article",
        source_profile_id=source.source_id,
    )


def preview_candidate(item: dict[str, Any], source: ValueDirectorySource) -> bool:
    normalized = normalized_value_directory_item(item, source)
    decision = decide_market_item(
        normalized,
        holdings=load_enabled_holdings_for_rules(),
    )
    return decision.should_push


def review_and_maybe_push(
    item: dict[str, Any],
    *,
    source: ValueDirectorySource | None = None,
    recheck_rules: bool = False,
) -> bool:
    source = source or source_config()
    item_id = article_item_id(item)
    with connect_db() as conn:
        existing = article_review_exists(conn, source.source_id, item_id)
    if existing and (existing.get("pushed_at") or not recheck_rules):
        return False

    should_enrich = preview_candidate(item, source)
    if existing and recheck_rules and not should_enrich:
        return False
    if should_enrich and preview_enabled() and not has_preview_record(item):
        try:
            item = enrich_item_with_preview(item)
        except Exception as exc:  # noqa: BLE001
            item = with_preview_failure(item, exc)

    item = with_value_directory_policy(item)
    normalized = normalized_value_directory_item(item, source)
    outcome = process_market_item(
        normalized,
        item,
        store_kind="article",
        source_profile_id=source.source_id,
        db_path=DB_PATH,
        deliver=True,
        use_rule_dedup=True,
        reprocess_existing=existing is not None,
    )
    decision = outcome.flow_result.decision
    print(
        f"{source.source_id} 统一决策：importance={decision.importance} "
        f"action={decision.action} delivery={outcome.delivery_status} title={item.get('title', '')}",
        flush=True,
    )
    return outcome.delivery_status == "sent"


def collect_production(
    entries: list[dict[str, Any]],
    *,
    source: ValueDirectorySource | None = None,
    notify_baseline: bool,
    started_at: str,
    recheck_item_id: str = "",
) -> dict[str, Any]:
    source = source or source_config()
    new_items = save_new_items_with_retry(
        source.source_id,
        entries,
        notify_baseline=notify_baseline,
        source_label=source.module,
    )
    pushed = 0
    reviewed = 0
    for item in new_items:
        reviewed += 1
        if review_and_maybe_push(item, source=source):
            pushed += 1
    rechecked = 0
    rechecked_item_ids: set[str] = set()
    new_item_ids = {article_item_id(item) for item in new_items}
    if recheck_unpushed_enabled():
        limit = recheck_unpushed_limit()
        for item in entries:
            if rechecked >= limit:
                break
            item_id = article_item_id(item)
            if item_id in new_item_ids:
                continue
            with connect_db() as conn:
                existing = article_review_exists(conn, source.source_id, item_id)
            if not existing or existing.get("pushed_at"):
                continue
            rechecked += 1
            rechecked_item_ids.add(item_id)
            reviewed += 1
            if review_and_maybe_push(item, source=source, recheck_rules=True):
                pushed += 1
    target_id = recheck_item_id.strip()
    if target_id and target_id not in new_item_ids and target_id not in rechecked_item_ids:
        for item in entries:
            if article_item_id(item) != target_id:
                continue
            rechecked += 1
            reviewed += 1
            if review_and_maybe_push(item, source=source, recheck_rules=True):
                pushed += 1
            break
    return {
        "ok": True,
        "mode": "production",
        "sent_feishu": pushed > 0,
        "ran_llm_review": False,
        "wrote_production_seen_items": True,
        "wrote_production_reviews": reviewed > 0,
        "source": source.source_id,
        "url": source.list_url,
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
    for child in payload.get("sources", []):
        child_counts = child.get("counts", {})
        print(
            f"  {child.get('source')}: raw={child_counts.get('raw_items', 0)} "
            f"new={child_counts.get('new_items', '-')} reviewed={child_counts.get('reviewed_items', '-')} "
            f"pushed={child_counts.get('pushed_items', '-')}",
            flush=True,
        )
    for error in payload.get("errors", []):
        print(f"[ERR] {error}", flush=True)


def run_source(
    source_id: str,
    *,
    production: bool,
    limit: int,
    notify_baseline: bool,
    recheck_item_id: str = "",
) -> dict[str, Any]:
    started_at = utc_now()
    source = source_config(source_id)
    if not source_profile_enabled(source.source_id):
        payload = {
            "ok": True,
            "mode": "production" if production else "shadow_dry_run",
            "skipped": True,
            "reason": "source profile 已停用",
            "source": source.source_id,
            "url": source.list_url,
            "started_at": started_at,
            "finished_at": utc_now(),
            "counts": {"raw_items": 0},
            "errors": [],
        }
        return payload
    try:
        entries = collect_entries_for_source(source.source_id, limit=limit)
        with connect_db() as conn:
            record_source_success(conn, MONITOR, source.source_id)
        if production:
            return collect_production(
                entries,
                source=source,
                notify_baseline=notify_baseline,
                started_at=started_at,
                recheck_item_id=recheck_item_id,
            )
        return shadow_payload(entries, started_at=started_at, source=source)
    except Exception as exc:  # noqa: BLE001 - health state should capture every collector failure
        with connect_db() as conn:
            record_source_failure(conn, MONITOR, source.source_id, exc)
        return {
            "ok": False,
            "mode": "production" if production else "shadow_dry_run",
            "source": source.source_id,
            "url": source.list_url,
            "started_at": started_at,
            "finished_at": utc_now(),
            "counts": {"raw_items": 0},
            "errors": [f"{type(exc).__name__}: {exc}"],
        }


def run(
    *,
    production: bool,
    limit: int,
    notify_baseline: bool,
    recheck_item_id: str = "",
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    started_at = utc_now()
    sources = source_ids or default_source_ids()
    payloads = [
        run_source(
            source_id,
            production=production,
            limit=limit,
            notify_baseline=notify_baseline,
            recheck_item_id=recheck_item_id,
        )
        for source_id in sources
    ]
    errors = [error for payload in payloads for error in payload.get("errors", [])]
    counts = {
        "raw_items": sum(int(payload.get("counts", {}).get("raw_items") or 0) for payload in payloads),
        "new_items": sum(int(payload.get("counts", {}).get("new_items") or 0) for payload in payloads),
        "reviewed_items": sum(int(payload.get("counts", {}).get("reviewed_items") or 0) for payload in payloads),
        "rechecked_items": sum(int(payload.get("counts", {}).get("rechecked_items") or 0) for payload in payloads),
        "pushed_items": sum(int(payload.get("counts", {}).get("pushed_items") or 0) for payload in payloads),
    }
    return {
        "ok": all(payload.get("ok") for payload in payloads),
        "mode": "production" if production else "shadow_dry_run",
        "sent_feishu": any(payload.get("sent_feishu") for payload in payloads),
        "ran_llm_review": False,
        "wrote_production_seen_items": production,
        "wrote_production_reviews": counts["reviewed_items"] > 0,
        "source": "value_directory",
        "url": LIST_URL,
        "source_ids": sources,
        "started_at": started_at,
        "finished_at": utc_now(),
        "counts": counts,
        "sources": payloads,
        "errors": errors,
    }


def main() -> int:
    load_env(ENV_PATH)
    parser = argparse.ArgumentParser(description="Monitor ValueList international-bank stock research index.")
    parser.add_argument("--production", action="store_true", help="写入 seen_items/article_reviews 并按硬规则发送飞书。")
    parser.add_argument("--notify-baseline", action="store_true", help="首次建立基线时也处理旧条目。默认只建立基线。")
    parser.add_argument("--limit", type=int, default=30, help="读取列表页前 N 条。")
    parser.add_argument(
        "--source",
        action="append",
        choices=sorted(VALUE_DIRECTORY_SOURCES),
        help="只运行指定价值目录来源；可重复。不传则读取 VALUE_DIRECTORY_SOURCES 或默认全部。",
    )
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
        source_ids=args.source,
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
