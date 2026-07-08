#!/usr/bin/env python3
"""Collector for research institutions and industry media.

This is the first consolidation step for the research/industry-media source
family. By default it runs in shadow mode: it does not send Feishu cards, does
not run LLM review, and does not write production dedupe/review tables. The
explicit ``--production`` mode delegates to the existing production RSS and
page pipelines so the behavior stays aligned during the systemd migration.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from collector_runtime import (
    filter_enabled_mapping_for_run,
    filter_enabled_named_for_run,
    load_source_states,
    save_source_state,
)
from db_utils import connect_sqlite, ensure_source_state_table
from media_sources import OVERSEAS_MEDIA_FEEDS
from rss_monitor import CORE_COMPANY_FEEDS, DB_PATH, fetch_feed, filter_items, run_once as run_rss_once, strip_tags
from source_profiles import SOURCE_PROFILE_CONFIG_PATH, runtime_profile_map
from trendforce_page_monitor import extract_items as extract_page_items
from trendforce_page_monitor import run_once as run_page_once
from trendforce_sources import DEFAULT_RSS_FEEDS, PageSource, TREND_FORCE_PAGE_SOURCES
from x_check import load_env


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
REPORT_DIR = ROOT / "reports"
RESEARCH_CATEGORY = "research_industry_media"
SHADOW_STATE_PREFIX = "research_shadow_feed"
PRODUCTION_PAGE_STATE_PREFIX = "research_collector_page"
DEFAULT_PAGE_MIN_INTERVAL_SECONDS = 900


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_utc_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def research_rss_feeds(config_path: Path = SOURCE_PROFILE_CONFIG_PATH) -> dict[str, str]:
    """Return enabled research/industry RSS/RDF feed URLs from source profiles."""
    source_urls = {
        source: url
        for source, url in DEFAULT_RSS_FEEDS.items()
        if source not in CORE_COMPANY_FEEDS
    }
    source_urls.update(OVERSEAS_MEDIA_FEEDS)

    profiles = runtime_profile_map(config_path=config_path)
    feeds = {
        source: url
        for source, url in source_urls.items()
        if profiles.get(source, {}).get("category") == RESEARCH_CATEGORY
    }
    return filter_enabled_mapping_for_run(feeds, label="研究机构/行业媒体 RSS", config_path=config_path)


def research_page_sources(config_path: Path = SOURCE_PROFILE_CONFIG_PATH) -> list[PageSource]:
    """Return enabled research/industry list-page sources."""
    profiles = runtime_profile_map(config_path=config_path)
    sources = [
        source
        for source in TREND_FORCE_PAGE_SOURCES
        if profiles.get(source.name, {}).get("category") == RESEARCH_CATEGORY
    ]
    return filter_enabled_named_for_run(sources, label="研究机构/行业媒体页面", config_path=config_path)


def selected_sources(
    names: Iterable[str],
    *,
    include_rss: bool = True,
    include_pages: bool = True,
    config_path: Path = SOURCE_PROFILE_CONFIG_PATH,
) -> tuple[dict[str, str], list[PageSource]]:
    requested = {str(name or "").strip() for name in names if str(name or "").strip()}
    feeds = research_rss_feeds(config_path=config_path) if include_rss else {}
    pages = research_page_sources(config_path=config_path) if include_pages else []
    if not requested:
        return feeds, pages

    known = set(feeds) | {source.name for source in pages}
    missing = sorted(requested - known)
    if missing:
        raise SystemExit(f"未知或已停用的研究机构/行业媒体 source：{', '.join(missing)}")
    return (
        {source: url for source, url in feeds.items() if source in requested},
        [source for source in pages if source.name in requested],
    )


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
    return row is not None


def load_seen_item_ids(sources: Iterable[str], db_path: Path = DB_PATH) -> set[tuple[str, str]]:
    source_list = sorted({source for source in sources if source})
    if not source_list or not db_path.exists():
        return set()
    placeholders = ",".join("?" for _ in source_list)
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            if not table_exists(conn, "seen_items"):
                return set()
            return {
                (str(row[0] or ""), str(row[1] or ""))
                for row in conn.execute(
                    f"SELECT source, item_id FROM seen_items WHERE source IN ({placeholders})",
                    source_list,
                )
            }
    except sqlite3.Error:
        return set()


def summarize_text(value: Any, limit: int = 320) -> str:
    text = strip_tags(str(value or ""))
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def candidate_from_item(source: str, item: dict[str, Any], seen_ids: set[tuple[str, str]]) -> dict[str, Any]:
    item_id = str(item.get("id") or item.get("url") or item.get("title") or "")
    return {
        "source": source,
        "id": item_id,
        "already_seen": (source, item_id) in seen_ids,
        "url": str(item.get("url") or ""),
        "title": str(item.get("title") or ""),
        "published_at": str(item.get("published_at") or ""),
        "summary": summarize_text(item.get("summary") or item.get("content") or ""),
        "categories": list(item.get("categories") or []),
        "pipeline": "article_gate shadow",
    }


def limited(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return items
    return items[:limit]


def load_shadow_feed_states(feeds: dict[str, str], save_shadow_state: bool) -> dict[str, dict[str, Any]]:
    if not save_shadow_state or not feeds:
        return {source: {} for source in feeds}
    with connect_sqlite(DB_PATH) as conn:
        ensure_source_state_table(conn)
        return load_source_states(conn, feeds, prefix=SHADOW_STATE_PREFIX)


def save_shadow_feed_state(source: str, state: dict[str, Any], save_shadow_state: bool) -> None:
    if not save_shadow_state:
        return
    with connect_sqlite(DB_PATH) as conn:
        ensure_source_state_table(conn)
        save_source_state(conn, source, state, prefix=SHADOW_STATE_PREFIX)
        conn.commit()


def due_page_sources(
    sources: list[PageSource],
    *,
    min_interval_seconds: int = DEFAULT_PAGE_MIN_INTERVAL_SECONDS,
    force: bool = False,
) -> tuple[list[PageSource], list[dict[str, str]]]:
    """Return page sources due for production processing.

    The unified research collector can run every few minutes for RSS latency,
    while list-page sources should stay lower frequency. We keep that cadence in
    source_state instead of splitting the migration back into multiple services.
    """
    if force or min_interval_seconds <= 0 or not sources:
        return list(sources), []

    now = datetime.now(timezone.utc)
    with connect_sqlite(DB_PATH) as conn:
        ensure_source_state_table(conn)
        states = load_source_states(conn, [source.name for source in sources], prefix=PRODUCTION_PAGE_STATE_PREFIX)

    due: list[PageSource] = []
    skipped: list[dict[str, str]] = []
    min_delta = timedelta(seconds=min_interval_seconds)
    for source in sources:
        state = states.get(source.name, {})
        last_checked = parse_utc_datetime(state.get("last_checked_at"))
        if last_checked is None or now - last_checked >= min_delta:
            due.append(source)
            continue
        next_due = last_checked + min_delta
        skipped.append({"source": source.name, "next_due_at": next_due.isoformat()})
    return due, skipped


def mark_page_sources_checked(sources: list[PageSource]) -> None:
    if not sources:
        return
    now = utc_now()
    with connect_sqlite(DB_PATH) as conn:
        ensure_source_state_table(conn)
        for source in sources:
            save_source_state(conn, source.name, {"last_checked_at": now}, prefix=PRODUCTION_PAGE_STATE_PREFIX)
        conn.commit()


def collect_rss_shadow(
    feeds: dict[str, str],
    *,
    limit: int,
    seen_ids: set[tuple[str, str]],
    save_shadow_state: bool = False,
) -> list[dict[str, Any]]:
    states = load_shadow_feed_states(feeds, save_shadow_state)
    max_workers = max(1, int(os.getenv("RESEARCH_COLLECTOR_MAX_WORKERS", os.getenv("RSS_FETCH_MAX_WORKERS", "8")) or "8"))
    rows: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(feeds)))) as executor:
        futures = {
            executor.submit(fetch_feed, source, url, states.get(source, {})): (source, url)
            for source, url in feeds.items()
        }
        for future in as_completed(futures):
            source, url = futures[future]
            try:
                raw_items, next_state, not_modified = future.result()
                save_shadow_feed_state(source, next_state, save_shadow_state)
                filtered_items = filter_items(source, raw_items)
                rows[source] = {
                    "source": source,
                    "url": url,
                    "ok": True,
                    "not_modified": not_modified,
                    "raw_count": len(raw_items),
                    "candidate_count": len(filtered_items),
                    "candidates": limited(
                        [candidate_from_item(source, item, seen_ids) for item in filtered_items],
                        limit,
                    ),
                    "error": "",
                }
            except Exception as exc:  # noqa: BLE001 - one failing source must not hide the rest
                rows[source] = {
                    "source": source,
                    "url": url,
                    "ok": False,
                    "not_modified": False,
                    "raw_count": 0,
                    "candidate_count": 0,
                    "candidates": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }
    return [rows[source] for source in feeds if source in rows]


def collect_page_shadow(
    sources: list[PageSource],
    *,
    limit: int,
    seen_ids: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in sources:
        try:
            items = extract_page_items(source)
            rows.append(
                {
                    "source": source.name,
                    "url": source.url,
                    "ok": True,
                    "module": source.module,
                    "kind": source.kind,
                    "access_note": source.access_note,
                    "raw_count": len(items),
                    "candidate_count": len(items),
                    "candidates": limited(
                        [candidate_from_item(source.name, item, seen_ids) for item in items],
                        limit,
                    ),
                    "error": "",
                }
            )
        except Exception as exc:  # noqa: BLE001 - keep shadow report complete
            rows.append(
                {
                    "source": source.name,
                    "url": source.url,
                    "ok": False,
                    "module": source.module,
                    "kind": source.kind,
                    "access_note": source.access_note,
                    "raw_count": 0,
                    "candidate_count": 0,
                    "candidates": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return rows


def collect_shadow(
    *,
    feeds: dict[str, str],
    page_sources: list[PageSource],
    limit: int = 5,
    compare_seen: bool = True,
    save_shadow_state: bool = False,
) -> dict[str, Any]:
    started_at = utc_now()
    seen_ids = load_seen_item_ids([*feeds.keys(), *(source.name for source in page_sources)]) if compare_seen else set()
    rss_rows = collect_rss_shadow(
        feeds,
        limit=limit,
        seen_ids=seen_ids,
        save_shadow_state=save_shadow_state,
    )
    page_rows = collect_page_shadow(page_sources, limit=limit, seen_ids=seen_ids)
    all_rows = [*rss_rows, *page_rows]
    errors = [row for row in all_rows if not row.get("ok")]
    return {
        "ok": not errors,
        "mode": "shadow_dry_run",
        "sent_feishu": False,
        "ran_llm_review": False,
        "wrote_production_seen_items": False,
        "save_shadow_state": save_shadow_state,
        "started_at": started_at,
        "finished_at": utc_now(),
        "counts": {
            "rss_sources": len(rss_rows),
            "page_sources": len(page_rows),
            "sources": len(all_rows),
            "failed_sources": len(errors),
            "raw_items": sum(int(row.get("raw_count") or 0) for row in all_rows),
            "candidates": sum(int(row.get("candidate_count") or 0) for row in all_rows),
            "already_seen_candidates": sum(
                1
                for row in all_rows
                for item in row.get("candidates", [])
                if item.get("already_seen")
            ),
        },
        "rss": rss_rows,
        "pages": page_rows,
        "errors": errors,
    }


def collect_production(
    *,
    feeds: dict[str, str],
    page_sources: list[PageSource],
    notify_baseline: bool = False,
    page_min_interval_seconds: int = DEFAULT_PAGE_MIN_INTERVAL_SECONDS,
    force_pages: bool = False,
) -> dict[str, Any]:
    started_at = utc_now()
    errors: list[dict[str, str]] = []
    rss_new = 0
    page_new = 0
    due_pages: list[PageSource] = []
    skipped_pages: list[dict[str, str]] = []

    if feeds:
        try:
            rss_new = run_rss_once(feeds, notify_baseline=notify_baseline)
        except Exception as exc:  # noqa: BLE001 - report the batch failure clearly
            errors.append({"stage": "rss", "error": f"{type(exc).__name__}: {exc}"})

    if page_sources:
        try:
            due_pages, skipped_pages = due_page_sources(
                page_sources,
                min_interval_seconds=page_min_interval_seconds,
                force=force_pages,
            )
            if due_pages:
                page_new = run_page_once(due_pages, notify_baseline=notify_baseline)
                mark_page_sources_checked(due_pages)
            else:
                print("research_collector production: 页面源尚未到达抓取间隔，本轮跳过。", flush=True)
        except Exception as exc:  # noqa: BLE001 - keep the production summary explicit
            errors.append({"stage": "pages", "error": f"{type(exc).__name__}: {exc}"})

    return {
        "ok": not errors,
        "mode": "production",
        "sent_feishu": True,
        "ran_llm_review": True,
        "wrote_production_seen_items": True,
        "started_at": started_at,
        "finished_at": utc_now(),
        "counts": {
            "rss_sources": len(feeds),
            "page_sources": len(page_sources),
            "page_sources_due": len(due_pages),
            "page_sources_skipped_by_cadence": len(skipped_pages),
            "rss_new_items": rss_new,
            "page_new_items": page_new,
            "new_items": rss_new + page_new,
        },
        "skipped_pages": skipped_pages,
        "errors": errors,
    }


def write_report(payload: dict[str, Any], report_dir: Path = REPORT_DIR) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    mode = "production" if payload.get("mode") == "production" else "shadow"
    path = report_dir / f"research-collector-{mode}-{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def print_text_summary(payload: dict[str, Any]) -> None:
    counts = payload.get("counts", {})
    if payload.get("mode") == "production":
        print(
            "research_collector production: "
            f"rss_sources={counts.get('rss_sources', 0)} "
            f"page_sources={counts.get('page_sources', 0)} "
            f"pages_due={counts.get('page_sources_due', 0)} "
            f"new_items={counts.get('new_items', 0)} "
            f"errors={len(payload.get('errors', []))}",
            flush=True,
        )
        for skipped in payload.get("skipped_pages", [])[:10]:
            print(f"[SKIP] {skipped.get('source')}: next_due_at={skipped.get('next_due_at')}", flush=True)
        for error in payload.get("errors", []):
            print(f"[ERR] {error.get('stage')}: {error.get('error')}", flush=True)
        return
    print(
        "research_collector shadow: "
        f"sources={counts.get('sources', 0)} "
        f"failed={counts.get('failed_sources', 0)} "
        f"raw_items={counts.get('raw_items', 0)} "
        f"candidates={counts.get('candidates', 0)}"
    )
    for group in ("rss", "pages"):
        for row in payload.get(group, []):
            status = "OK" if row.get("ok") else "ERR"
            print(
                f"[{status}] {row.get('source')}: "
                f"raw={row.get('raw_count', 0)} candidates={row.get('candidate_count', 0)}"
            )
            if row.get("error"):
                print(f"  error: {row.get('error')}")
            for item in row.get("candidates", [])[:3]:
                seen = "seen" if item.get("already_seen") else "new?"
                print(f"  - ({seen}) {item.get('title')}")


def main() -> int:
    load_env(ENV_PATH)
    parser = argparse.ArgumentParser(description="Run research/industry-media collector.")
    parser.add_argument("--source", action="append", default=[], help="只跑指定 source id，可重复。")
    parser.add_argument("--rss-only", action="store_true", help="只跑 RSS/RDF 源。")
    parser.add_argument("--pages-only", action="store_true", help="只跑页面源。")
    parser.add_argument("--production", action="store_true", help="运行生产链路：入库、门控、Skeptic/Tavily、飞书推送。")
    parser.add_argument("--notify-baseline", action="store_true", help="生产模式下首次建立基线时也发送通知。默认不发送旧条目。")
    parser.add_argument(
        "--page-min-interval",
        type=int,
        default=int(os.getenv("RESEARCH_COLLECTOR_PAGE_MIN_INTERVAL_SECONDS", str(DEFAULT_PAGE_MIN_INTERVAL_SECONDS))),
        help="生产模式页面源最小抓取间隔秒数；默认 900。0 表示每轮都抓。",
    )
    parser.add_argument("--force-pages", action="store_true", help="生产模式下忽略页面源最小间隔。")
    parser.add_argument("--limit", type=int, default=5, help="每个 source 输出候选条数；0 表示不限制。")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON。")
    parser.add_argument("--write-report", action="store_true", help="把 JSON 报告写入 reports/。")
    parser.add_argument("--no-compare-seen", action="store_true", help="不读取生产库判断 already_seen。")
    parser.add_argument("--strict-exit", action="store_true", help="任一 source 失败时返回非 0；默认只在报告中记录错误。")
    parser.add_argument(
        "--save-shadow-state",
        action="store_true",
        help="仅保存 research_shadow_feed:* 条件请求状态；不写生产 seen/review 表。",
    )
    args = parser.parse_args()
    if args.rss_only and args.pages_only:
        raise SystemExit("--rss-only 和 --pages-only 不能同时使用")
    if args.production and args.save_shadow_state:
        raise SystemExit("--production 不能与 --save-shadow-state 同时使用")

    feeds, pages = selected_sources(
        args.source,
        include_rss=not args.pages_only,
        include_pages=not args.rss_only,
    )
    if args.production:
        payload = collect_production(
            feeds=feeds,
            page_sources=pages,
            notify_baseline=args.notify_baseline or os.getenv("SURVEIL_NOTIFY_BASELINE", "") == "1",
            page_min_interval_seconds=max(0, args.page_min_interval),
            force_pages=args.force_pages,
        )
    else:
        payload = collect_shadow(
            feeds=feeds,
            page_sources=pages,
            limit=max(0, args.limit),
            compare_seen=not args.no_compare_seen,
            save_shadow_state=args.save_shadow_state,
        )
    if args.write_report:
        payload["report_path"] = str(write_report(payload))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_text_summary(payload)
        if payload.get("report_path"):
            print(f"report: {payload['report_path']}")
    return 0 if payload.get("ok") or not args.strict_exit else 2


if __name__ == "__main__":
    raise SystemExit(main())
