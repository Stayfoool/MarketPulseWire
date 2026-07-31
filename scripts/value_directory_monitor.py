#!/usr/bin/env python3
"""ValueList international-bank stock-research index monitor."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from db_utils import update_seen_item_lifecycle
from market_item import NormalizedMarketItem, raw_item_id
from market_flow import normalize_market_item, process_market_item
from market_store import processing_failure_status, source_item_review_snapshot
from production_admission import admission_lifecycle_values, persist_production_admission_context, production_admission_context
from rss_monitor import DB_PATH, connect_db, save_new_items_with_retry
from source_health import record_source_failure, record_source_success
from source_profiles import source_profile_enabled
from value_directory_browser import (
    LIST_URL,
    SOURCE_ID,
    VALUE_DIRECTORY_SOURCES,
    ValueDirectorySource,
    collect_preview,
    collect_sources_with_previews,
    default_source_ids,
    source_config,
)
from value_directory_preview import apply_preview_to_item, extract_preview_facts
from x_check import load_env


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
REPORT_DIR = ROOT / "reports"
MONITOR = "value_directory"
RETRYABLE_LIFECYCLE_STATUSES = {"pending", "failed_retryable"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
    return row is not None


def load_seen_item_states(
    source_id: str = SOURCE_ID,
    db_path: Path | None = None,
) -> dict[str, tuple[str, str]]:
    db_path = db_path or DB_PATH
    if not db_path.exists():
        return {}
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            if not table_exists(conn, "seen_items"):
                return {}
            return {
                str(row[0] or ""): (str(row[1] or ""), str(row[2] or ""))
                for row in conn.execute(
                    """
                    SELECT item_id,processability_status,processing_status
                    FROM seen_items
                    WHERE source = ?
                    """,
                    (source_id,),
                )
            }
    except sqlite3.Error:
        return {}


def load_unpushed_review_ids(
    source_id: str = SOURCE_ID,
    db_path: Path | None = None,
) -> set[str]:
    db_path = db_path or DB_PATH
    if not db_path.exists():
        return set()
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            return {
                str(row[0] or "")
                for row in conn.execute(
                    """
                    SELECT m.source_item_id
                    FROM market_items m
                    JOIN market_reviews r
                      ON r.market_item_id=m.id AND r.task='production'
                     AND r.is_current=1 AND r.review_status='succeeded'
                    WHERE m.source = ?
                      AND NOT EXISTS (
                          SELECT 1 FROM deliveries d
                          WHERE d.market_item_id=m.id AND d.status='sent'
                      )
                    """,
                    (source_id,),
                )
            }
    except sqlite3.Error:
        return set()


def preview_enabled() -> bool:
    return os.getenv("VALUE_DIRECTORY_PREVIEW_ENABLED", "1").strip() != "0"


def push_on_preview_failure() -> bool:
    return os.getenv("VALUE_DIRECTORY_PUSH_ON_PREVIEW_FAILURE", "1").strip() != "0"


def recheck_unpushed_enabled() -> bool:
    return os.getenv("VALUE_DIRECTORY_RECHECK_UNPUSHED", "0").strip() == "1"


def recheck_unpushed_limit() -> int:
    raw = os.getenv("VALUE_DIRECTORY_RECHECK_UNPUSHED_LIMIT", "").strip()
    try:
        return max(0, min(100, int(raw))) if raw else 30
    except ValueError:
        return 30


def retryable_item_ids(source_id: str) -> set[str]:
    return {
        item_id
        for item_id, statuses in load_seen_item_states(source_id).items()
        if RETRYABLE_LIFECYCLE_STATUSES.intersection(statuses)
    }


def production_preview_selector(
    source_ids: list[str],
    *,
    notify_baseline: bool,
    recheck_item_id: str = "",
) -> Callable[[ValueDirectorySource, dict[str, Any]], bool]:
    states_by_source = {source_id: load_seen_item_states(source_id) for source_id in source_ids}
    unpushed_by_source = (
        {source_id: load_unpushed_review_ids(source_id) for source_id in source_ids}
        if recheck_unpushed_enabled()
        else {source_id: set() for source_id in source_ids}
    )
    recheck_counts = {source_id: 0 for source_id in source_ids}
    target_id = recheck_item_id.strip()

    def selected(source: ValueDirectorySource, item: dict[str, Any]) -> bool:
        if not preview_enabled():
            return False
        item_id = raw_item_id(item)
        states = states_by_source.get(source.source_id, {})
        if target_id and item_id == target_id:
            return True
        if item_id in states:
            if RETRYABLE_LIFECYCLE_STATUSES.intersection(states[item_id]):
                return True
            if item_id in unpushed_by_source.get(source.source_id, set()):
                if recheck_counts[source.source_id] < recheck_unpushed_limit():
                    recheck_counts[source.source_id] += 1
                    return True
            return False
        return notify_baseline or bool(states)

    return selected


def enrich_item_with_preview(item: dict[str, Any], preview: dict[str, Any] | None = None) -> dict[str, Any]:
    if not preview_enabled():
        return item
    preview = preview if preview is not None else collect_preview(str(item.get("url") or ""))
    facts = extract_preview_facts(item, preview)
    return apply_preview_to_item(item, preview, facts)


def preview_key(source: ValueDirectorySource, item: dict[str, Any]) -> tuple[str, str]:
    return source.source_id, raw_item_id(item)


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
        source_profile_id=source.source_id,
    )


def set_seen_item_lifecycle_if_present(source: str, item_id: str, **values: Any) -> None:
    with connect_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM seen_items WHERE source = ? AND item_id = ?",
            (source, item_id),
        ).fetchone()
        if not row:
            return
        update_seen_item_lifecycle(conn, source, item_id, **values)
        conn.commit()


def review_and_maybe_push(
    item: dict[str, Any],
    *,
    source: ValueDirectorySource | None = None,
    recheck_rules: bool = False,
    collected_previews: dict[tuple[str, str], dict[str, Any]] | None = None,
    preview_errors: dict[tuple[str, str], str] | None = None,
    browser_collection_complete: bool = False,
) -> bool:
    source = source or source_config()
    item_id = raw_item_id(item)
    existing = source_item_review_snapshot(source.source_id, item_id, db_path=DB_PATH)
    if existing and (existing.get("delivered") or not recheck_rules):
        return False

    if preview_enabled() and not has_preview_record(item):
        key = preview_key(source, item)
        try:
            if preview_errors and key in preview_errors:
                raise RuntimeError(preview_errors[key])
            if collected_previews is not None and key in collected_previews:
                item = enrich_item_with_preview(item, collected_previews[key])
            elif browser_collection_complete:
                raise RuntimeError("浏览器采集阶段未返回该研报的第一页预览")
            else:
                item = enrich_item_with_preview(item)
        except Exception as exc:  # noqa: BLE001
            item = with_preview_failure(item, exc)

    item = with_value_directory_policy(item)
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    preview = raw.get("value_directory_preview") if isinstance(raw.get("value_directory_preview"), dict) else {}
    facts = preview.get("facts") if isinstance(preview.get("facts"), dict) else {}
    preview_failed = str(facts.get("status") or "") == "failed"
    evaluated_at = utc_now()
    set_seen_item_lifecycle_if_present(
        source.source_id,
        item_id,
        processability_status=(
            "fallback" if preview_failed else "succeeded" if preview_enabled() else "not_required"
        ),
        processability_reason="preview_or_ocr_fallback" if preview_failed else "",
        admission_status="pending",
        admission_reason="",
        processing_status="not_applicable",
        processing_error="",
    )
    normalized = normalized_value_directory_item(item, source)
    admission_context = persist_production_admission_context(normalized, production_admission_context(normalized, db_path=DB_PATH), db_path=DB_PATH)
    admission = admission_context.result
    if admission.status != "admitted":
        set_seen_item_lifecycle_if_present(
            source.source_id,
            item_id,
            **admission_lifecycle_values(admission, processing_status="not_applicable"),
            processed_at=utc_now(),
        )
        return False
    set_seen_item_lifecycle_if_present(
        source.source_id,
        item_id,
        **admission_lifecycle_values(admission, processing_status="pending"),
    )
    try:
        outcome = process_market_item(
            normalized,
            item,
            db_path=DB_PATH,
            deliver=True,
            use_rule_dedup=True,
            reprocess_existing=existing is not None,
            production_admission=admission,
            production_portfolio=admission_context.portfolio,
            market_item_id=admission_context.market_item_id,
            market_review_id=admission_context.market_review_id,
        )
    except Exception as exc:
        status = processing_failure_status(exc)
        set_seen_item_lifecycle_if_present(
            source.source_id,
            item_id,
            processing_status=status,
            processing_error=f"{type(exc).__name__}: {str(exc)[:400]}",
            processed_at=None,
            lifecycle_updated_at=utc_now(),
        )
        if status == "insufficient_evidence":
            print(f"{source.source_id} 证据不足，当前条目终止处理：title={item.get('title', '')}", flush=True)
            return False
        raise
    set_seen_item_lifecycle_if_present(
        source.source_id,
        item_id,
        processing_status="succeeded",
        processing_error="",
        processed_at=utc_now(),
        lifecycle_updated_at=utc_now(),
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
    collected_previews: dict[tuple[str, str], dict[str, Any]] | None = None,
    preview_errors: dict[tuple[str, str], str] | None = None,
    browser_collection_complete: bool = False,
) -> dict[str, Any]:
    source = source or source_config()
    retryable_ids = retryable_item_ids(source.source_id)
    new_items = save_new_items_with_retry(
        source.source_id,
        entries,
        notify_baseline=notify_baseline,
        source_label=source.module,
    )
    pushed = 0
    reviewed = 0
    new_item_ids = {raw_item_id(item) for item in new_items}
    retryable_items = [
        item
        for item in entries
        if raw_item_id(item) in retryable_ids and raw_item_id(item) not in new_item_ids
    ]
    for item in [*new_items, *retryable_items]:
        reviewed += 1
        if review_and_maybe_push(
            item,
            source=source,
            recheck_rules=raw_item_id(item) in retryable_ids,
            collected_previews=collected_previews,
            preview_errors=preview_errors,
            browser_collection_complete=browser_collection_complete,
        ):
            pushed += 1
    rechecked = 0
    rechecked_item_ids: set[str] = set()
    retryable_item_id_set = {raw_item_id(item) for item in retryable_items}
    if recheck_unpushed_enabled():
        limit = recheck_unpushed_limit()
        unpushed_review_ids = load_unpushed_review_ids(source.source_id)
        for item in entries:
            if rechecked >= limit:
                break
            item_id = raw_item_id(item)
            if item_id in new_item_ids or item_id in retryable_item_id_set:
                continue
            if item_id not in unpushed_review_ids:
                continue
            rechecked += 1
            rechecked_item_ids.add(item_id)
            reviewed += 1
            if review_and_maybe_push(
                item,
                source=source,
                recheck_rules=True,
                collected_previews=collected_previews,
                preview_errors=preview_errors,
                browser_collection_complete=browser_collection_complete,
            ):
                pushed += 1
    target_id = recheck_item_id.strip()
    if (
        target_id
        and target_id not in new_item_ids
        and target_id not in retryable_item_id_set
        and target_id not in rechecked_item_ids
    ):
        for item in entries:
            if raw_item_id(item) != target_id:
                continue
            rechecked += 1
            reviewed += 1
            if review_and_maybe_push(
                item,
                source=source,
                recheck_rules=True,
                collected_previews=collected_previews,
                preview_errors=preview_errors,
                browser_collection_complete=browser_collection_complete,
            ):
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
            "retryable_items": len(retryable_items),
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
        f"retryable={counts.get('retryable_items', '-')} "
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
            f"new={child_counts.get('new_items', '-')} retryable={child_counts.get('retryable_items', '-')} "
            f"reviewed={child_counts.get('reviewed_items', '-')} "
            f"pushed={child_counts.get('pushed_items', '-')}",
            flush=True,
        )
    for error in payload.get("errors", []):
        print(f"[ERR] {error}", flush=True)


def source_payload_error(
    source: ValueDirectorySource,
    *,
    started_at: str,
    error: Exception | str,
) -> dict[str, Any]:
    error_text = f"{type(error).__name__}: {error}" if isinstance(error, Exception) else str(error)
    with connect_db() as conn:
        record_source_failure(conn, MONITOR, source.source_id, error_text)
    return {
        "ok": False,
        "mode": "production",
        "source": source.source_id,
        "url": source.list_url,
        "started_at": started_at,
        "finished_at": utc_now(),
        "counts": {"raw_items": 0},
        "errors": [error_text],
    }


def process_collected_source(
    source: ValueDirectorySource,
    entries: list[dict[str, Any]],
    *,
    notify_baseline: bool,
    started_at: str,
    recheck_item_id: str,
    collected_previews: dict[tuple[str, str], dict[str, Any]],
    preview_errors: dict[tuple[str, str], str],
) -> dict[str, Any]:
    try:
        payload = collect_production(
            entries,
            source=source,
            notify_baseline=notify_baseline,
            started_at=started_at,
            recheck_item_id=recheck_item_id,
            collected_previews=collected_previews,
            preview_errors=preview_errors,
            browser_collection_complete=True,
        )
        with connect_db() as conn:
            record_source_success(conn, MONITOR, source.source_id)
        return payload
    except Exception as exc:  # noqa: BLE001 - health state should capture every collector failure
        return source_payload_error(source, started_at=started_at, error=exc)


def run(
    *,
    limit: int,
    notify_baseline: bool,
    recheck_item_id: str = "",
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    started_at = utc_now()
    sources = source_ids or default_source_ids()
    source_configs = {source_id: source_config(source_id) for source_id in sources}
    enabled_sources = [source_id for source_id in sources if source_profile_enabled(source_id)]
    collection = None
    collection_error: Exception | None = None
    if enabled_sources:
        try:
            collection = collect_sources_with_previews(
                enabled_sources,
                limit=limit,
                preview_selector=production_preview_selector(
                    enabled_sources,
                    notify_baseline=notify_baseline,
                    recheck_item_id=recheck_item_id,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - one browser session owns all enabled sources.
            collection_error = exc

    payloads: list[dict[str, Any]] = []
    for source_id in sources:
        source = source_configs[source_id]
        source_started_at = started_at
        if source_id not in enabled_sources:
            payloads.append(
                {
                    "ok": True,
                    "mode": "production",
                    "skipped": True,
                    "reason": "source profile 已停用",
                    "source": source.source_id,
                    "url": source.list_url,
                    "started_at": source_started_at,
                    "finished_at": utc_now(),
                    "counts": {"raw_items": 0},
                    "errors": [],
                }
            )
            continue
        if collection_error is not None:
            payloads.append(
                source_payload_error(
                    source,
                    started_at=source_started_at,
                    error=collection_error,
                )
            )
            continue
        assert collection is not None
        if source_id in collection.source_errors:
            payloads.append(
                source_payload_error(
                    source,
                    started_at=source_started_at,
                    error=collection.source_errors[source_id],
                )
            )
            continue
        payloads.append(
            process_collected_source(
                source,
                collection.entries_by_source.get(source_id, []),
                notify_baseline=notify_baseline,
                started_at=source_started_at,
                recheck_item_id=recheck_item_id,
                collected_previews=collection.previews,
                preview_errors=collection.preview_errors,
            )
        )
    errors = [error for payload in payloads for error in payload.get("errors", [])]
    counts = {
        "raw_items": sum(int(payload.get("counts", {}).get("raw_items") or 0) for payload in payloads),
        "new_items": sum(int(payload.get("counts", {}).get("new_items") or 0) for payload in payloads),
        "retryable_items": sum(int(payload.get("counts", {}).get("retryable_items") or 0) for payload in payloads),
        "reviewed_items": sum(int(payload.get("counts", {}).get("reviewed_items") or 0) for payload in payloads),
        "rechecked_items": sum(int(payload.get("counts", {}).get("rechecked_items") or 0) for payload in payloads),
        "pushed_items": sum(int(payload.get("counts", {}).get("pushed_items") or 0) for payload in payloads),
    }
    return {
        "ok": all(payload.get("ok") for payload in payloads),
        "mode": "production",
        "sent_feishu": any(payload.get("sent_feishu") for payload in payloads),
        "ran_llm_review": False,
        "wrote_production_seen_items": True,
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
