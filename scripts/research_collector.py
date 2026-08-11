#!/usr/bin/env python3
"""Run enabled research sources through the unified market flow."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from alphabstract_monitor import ALPHAABSTRACT_SOURCES, AlphaAbstractSource
from alphabstract_monitor import run_once as run_alphabstract_once
from collector_runtime import (
    filter_enabled_mapping_for_run,
    filter_enabled_named_for_run,
    load_source_states,
    save_source_state,
)
from db_utils import connect_sqlite
from media_sources import OVERSEAS_MEDIA_FEEDS
from rss_monitor import CORE_COMPANY_FEEDS, DB_PATH, run_once as run_rss_once
from source_profiles import SOURCE_PROFILE_CONFIG_PATH, runtime_profile_map
from trendforce_page_monitor import run_once as run_page_once
from trendforce_sources import DEFAULT_RSS_FEEDS, PageSource, TREND_FORCE_PAGE_SOURCES
from x_check import load_env


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
REPORT_DIR = ROOT / "reports"
RESEARCH_CATEGORY = "research_industry_media"
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
    profiles = runtime_profile_map(config_path=config_path)
    sources = [
        source
        for source in TREND_FORCE_PAGE_SOURCES
        if profiles.get(source.name, {}).get("category") == RESEARCH_CATEGORY
    ]
    return filter_enabled_named_for_run(sources, label="研究机构/行业媒体页面", config_path=config_path)


def research_alphabstract_sources(config_path: Path = SOURCE_PROFILE_CONFIG_PATH) -> list[AlphaAbstractSource]:
    profiles = runtime_profile_map(config_path=config_path)
    sources = [
        source
        for source in ALPHAABSTRACT_SOURCES
        if profiles.get(source.name, {}).get("category") == RESEARCH_CATEGORY
    ]
    return filter_enabled_named_for_run(sources, label="AlphaAbstract 摘要源", config_path=config_path)


def selected_sources(
    names: Iterable[str],
    *,
    include_rss: bool = True,
    include_pages: bool = True,
    config_path: Path = SOURCE_PROFILE_CONFIG_PATH,
) -> tuple[dict[str, str], list[PageSource], list[AlphaAbstractSource]]:
    requested = {str(name or "").strip() for name in names if str(name or "").strip()}
    feeds = research_rss_feeds(config_path=config_path) if include_rss else {}
    pages = research_page_sources(config_path=config_path) if include_pages else []
    alphabstract = research_alphabstract_sources(config_path=config_path) if include_pages else []
    if not requested:
        return feeds, pages, alphabstract
    known = set(feeds) | {source.name for source in pages} | {source.name for source in alphabstract}
    missing = sorted(requested - known)
    if missing:
        raise SystemExit(f"未知或已停用的研究机构/行业媒体 source：{', '.join(missing)}")
    return (
        {source: url for source, url in feeds.items() if source in requested},
        [source for source in pages if source.name in requested],
        [source for source in alphabstract if source.name in requested],
    )


def due_page_sources(
    sources: list[Any],
    *,
    min_interval_seconds: int = DEFAULT_PAGE_MIN_INTERVAL_SECONDS,
    force: bool = False,
) -> tuple[list[Any], list[dict[str, str]]]:
    if force or min_interval_seconds <= 0 or not sources:
        return list(sources), []
    now = datetime.now(timezone.utc)
    with connect_sqlite(DB_PATH) as conn:
        states = load_source_states(conn, [source.name for source in sources], prefix=PRODUCTION_PAGE_STATE_PREFIX)
    due: list[Any] = []
    skipped: list[dict[str, str]] = []
    min_delta = timedelta(seconds=min_interval_seconds)
    for source in sources:
        last_checked = parse_utc_datetime(states.get(source.name, {}).get("last_checked_at"))
        if last_checked is None or now - last_checked >= min_delta:
            due.append(source)
        else:
            skipped.append({"source": source.name, "next_due_at": (last_checked + min_delta).isoformat()})
    return due, skipped


def mark_page_sources_checked(sources: list[Any]) -> None:
    if not sources:
        return
    now = utc_now()
    with connect_sqlite(DB_PATH) as conn:
        for source in sources:
            save_source_state(conn, source.name, {"last_checked_at": now}, prefix=PRODUCTION_PAGE_STATE_PREFIX)
        conn.commit()


def collect_production(
    *,
    feeds: dict[str, str],
    page_sources: list[PageSource],
    alphabstract_sources: list[AlphaAbstractSource] | None = None,
    notify_baseline: bool = False,
    page_min_interval_seconds: int = DEFAULT_PAGE_MIN_INTERVAL_SECONDS,
    force_pages: bool = False,
) -> dict[str, Any]:
    started_at = utc_now()
    alphabstract_sources = alphabstract_sources or []
    errors: list[dict[str, str]] = []
    rss_new = 0
    page_new = 0
    alphabstract_new = 0
    processing_failed_items = 0
    processing_aborted_due_global_failure = False
    due_pages: list[PageSource] = []
    due_alphabstract: list[AlphaAbstractSource] = []
    skipped_pages: list[dict[str, str]] = []
    skipped_alphabstract: list[dict[str, str]] = []

    if feeds:
        try:
            rss_new = run_rss_once(feeds, notify_baseline=notify_baseline)
        except Exception as exc:  # noqa: BLE001 - retain a source-family result for service health
            rss_new = int(getattr(exc, "completed_items", rss_new) or 0)
            processing_failed_items += int(getattr(exc, "failed_items", 0) or 0)
            processing_aborted_due_global_failure |= bool(getattr(exc, "aborted_due_global_failure", False))
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
        except Exception as exc:  # noqa: BLE001 - retain a source-family result for service health
            errors.append({"stage": "pages", "error": f"{type(exc).__name__}: {exc}"})
    if alphabstract_sources:
        try:
            due_alpha, skipped_alphabstract = due_page_sources(
                alphabstract_sources,
                min_interval_seconds=page_min_interval_seconds,
                force=force_pages,
            )
            due_alphabstract = list(due_alpha)
            if due_alphabstract:
                alphabstract_new = run_alphabstract_once(due_alphabstract, notify_baseline=notify_baseline)
                mark_page_sources_checked(due_alphabstract)
        except Exception as exc:  # noqa: BLE001 - retain a source-family result for service health
            alphabstract_new = int(getattr(exc, "completed_items", alphabstract_new) or 0)
            processing_failed_items += int(getattr(exc, "failed_items", 0) or 0)
            processing_aborted_due_global_failure |= bool(getattr(exc, "aborted_due_global_failure", False))
            errors.append({"stage": "alphabstract", "error": f"{type(exc).__name__}: {exc}"})

    return {
        "ok": not errors,
        "mode": "production",
        "started_at": started_at,
        "finished_at": utc_now(),
        "counts": {
            "rss_sources": len(feeds),
            "page_sources": len(page_sources),
            "alphabstract_sources": len(alphabstract_sources),
            "page_sources_due": len(due_pages),
            "alphabstract_sources_due": len(due_alphabstract),
            "page_sources_skipped_by_cadence": len(skipped_pages),
            "alphabstract_sources_skipped_by_cadence": len(skipped_alphabstract),
            "rss_new_items": rss_new,
            "page_new_items": page_new,
            "alphabstract_new_items": alphabstract_new,
            "new_items": rss_new + page_new + alphabstract_new,
            "processing_failed_items": processing_failed_items,
            "processing_aborted_due_global_failure": processing_aborted_due_global_failure,
        },
        "skipped_pages": skipped_pages,
        "skipped_alphabstract": skipped_alphabstract,
        "errors": errors,
    }


def write_report(payload: dict[str, Any], report_dir: Path = REPORT_DIR) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = report_dir / f"research-collector-{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def print_text_summary(payload: dict[str, Any]) -> None:
    counts = payload.get("counts", {})
    print(
        "research_collector: "
        f"rss_sources={counts.get('rss_sources', 0)} "
        f"page_sources={counts.get('page_sources', 0)} "
        f"alphabstract_sources={counts.get('alphabstract_sources', 0)} "
        f"new_items={counts.get('new_items', 0)} "
        f"processing_failed={counts.get('processing_failed_items', 0)} "
        f"errors={len(payload.get('errors', []))}",
        flush=True,
    )
    for skipped in payload.get("skipped_pages", []) + payload.get("skipped_alphabstract", []):
        print(f"[SKIP] {skipped.get('source')}: next_due_at={skipped.get('next_due_at')}", flush=True)
    for error in payload.get("errors", []):
        print(f"[ERR] {error.get('stage')}: {error.get('error')}", flush=True)


def main() -> int:
    load_env(ENV_PATH)
    parser = argparse.ArgumentParser(description="Run research sources.")
    parser.add_argument("--source", action="append", default=[], help="只跑指定 source id，可重复。")
    parser.add_argument("--rss-only", action="store_true", help="只跑 RSS/RDF 源。")
    parser.add_argument("--pages-only", action="store_true", help="只跑页面源。")
    parser.add_argument("--notify-baseline", action="store_true", help="首次建立基线时也发送通知。默认不发送旧条目。")
    parser.add_argument(
        "--page-min-interval",
        type=int,
        default=int(os.getenv("RESEARCH_COLLECTOR_PAGE_MIN_INTERVAL_SECONDS", str(DEFAULT_PAGE_MIN_INTERVAL_SECONDS))),
        help="页面源最小抓取间隔秒数；默认 900。0 表示每轮都抓。",
    )
    parser.add_argument("--force-pages", action="store_true", help="忽略页面源最小间隔。")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON。")
    parser.add_argument("--write-report", action="store_true", help="把 JSON 报告写入 reports/。")
    parser.add_argument("--strict-exit", action="store_true", help="任一 source 失败时返回非 0。")
    args = parser.parse_args()
    if args.strict_exit:
        os.environ["SURVEIL_STRICT_PROCESSING"] = "1"
    if args.rss_only and args.pages_only:
        raise SystemExit("--rss-only 和 --pages-only 不能同时使用")

    feeds, pages, alphabstract = selected_sources(
        args.source,
        include_rss=not args.pages_only,
        include_pages=not args.rss_only,
    )
    payload = collect_production(
        feeds=feeds,
        page_sources=pages,
        alphabstract_sources=alphabstract,
        notify_baseline=args.notify_baseline or os.getenv("SURVEIL_NOTIFY_BASELINE", "") == "1",
        page_min_interval_seconds=max(0, args.page_min_interval),
        force_pages=args.force_pages,
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
