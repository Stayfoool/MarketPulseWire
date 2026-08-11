#!/usr/bin/env python3
"""Run enabled company-feed sources through the unified market flow."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from collector_runtime import filter_enabled_mapping_for_run
from rss_monitor import run_once as run_rss_once
from source_profiles import SOURCE_PROFILE_CONFIG_PATH, runtime_profile_map
from trendforce_sources import DEFAULT_RSS_FEEDS
from x_check import load_env


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
REPORT_DIR = ROOT / "reports"
OFFICIAL_CATEGORY = "official_company"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def official_rss_feeds(config_path: Path = SOURCE_PROFILE_CONFIG_PATH) -> dict[str, str]:
    profiles = runtime_profile_map(config_path=config_path)
    feeds = {
        source: url
        for source, url in DEFAULT_RSS_FEEDS.items()
        if profiles.get(source, {}).get("category") == OFFICIAL_CATEGORY
    }
    return filter_enabled_mapping_for_run(feeds, label="公司官网 RSS", config_path=config_path)


def selected_sources(
    names: Iterable[str],
    *,
    config_path: Path = SOURCE_PROFILE_CONFIG_PATH,
) -> dict[str, str]:
    requested = {str(name or "").strip() for name in names if str(name or "").strip()}
    feeds = official_rss_feeds(config_path=config_path)
    if not requested:
        return feeds
    missing = sorted(requested - set(feeds))
    if missing:
        raise SystemExit(f"未知或已停用的公司官网 source：{', '.join(missing)}")
    return {source: url for source, url in feeds.items() if source in requested}


def collect_production(
    *,
    feeds: dict[str, str],
    notify_baseline: bool = False,
) -> dict[str, Any]:
    started_at = utc_now()
    errors: list[dict[str, str]] = []
    new_items = 0
    processing_failed_items = 0
    if feeds:
        try:
            new_items = run_rss_once(feeds, notify_baseline=notify_baseline)
        except Exception as exc:  # noqa: BLE001 - retain a source-family result for service health
            new_items = int(getattr(exc, "completed_items", new_items) or 0)
            processing_failed_items += int(getattr(exc, "failed_items", 0) or 0)
            errors.append({"stage": "rss", "error": f"{type(exc).__name__}: {exc}"})
    return {
        "ok": not errors,
        "mode": "production",
        "started_at": started_at,
        "finished_at": utc_now(),
        "counts": {
            "rss_sources": len(feeds),
            "new_items": new_items,
            "processing_failed_items": processing_failed_items,
        },
        "errors": errors,
    }


def write_report(payload: dict[str, Any], report_dir: Path = REPORT_DIR) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = report_dir / f"official-collector-{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def print_text_summary(payload: dict[str, Any]) -> None:
    counts = payload.get("counts", {})
    print(
        "official_collector: "
        f"rss_sources={counts.get('rss_sources', 0)} "
        f"new_items={counts.get('new_items', 0)} "
        f"processing_failed={counts.get('processing_failed_items', 0)} "
        f"errors={len(payload.get('errors', []))}",
        flush=True,
    )
    for error in payload.get("errors", []):
        print(f"[ERR] {error.get('stage')}: {error.get('error')}", flush=True)


def main() -> int:
    load_env(ENV_PATH)
    parser = argparse.ArgumentParser(description="Run company-feed sources.")
    parser.add_argument("--source", action="append", default=[], help="只跑指定 source id，可重复。")
    parser.add_argument("--notify-baseline", action="store_true", help="首次建立基线时也发送通知。默认不发送旧条目。")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON。")
    parser.add_argument("--write-report", action="store_true", help="把 JSON 报告写入 reports/。")
    parser.add_argument("--strict-exit", action="store_true", help="任一 source 失败时返回非 0。")
    args = parser.parse_args()
    if args.strict_exit:
        os.environ["SURVEIL_STRICT_PROCESSING"] = "1"

    payload = collect_production(
        feeds=selected_sources(args.source),
        notify_baseline=args.notify_baseline or os.getenv("SURVEIL_NOTIFY_BASELINE", "") == "1",
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
