#!/usr/bin/env python3
"""Run enabled media and trade-policy sources through the unified market flow."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import china_finance_media_monitor as china_media
import trade_policy_monitor as trade_policy
from china_media_sources import CHINA_MEDIA_FEEDS
from collector_runtime import filter_enabled_mapping_for_run
from source_profiles import SOURCE_PROFILE_CONFIG_PATH, runtime_profile_map
from trade_policy_sources import TRADE_POLICY_SOURCES, TradePolicySource
from x_check import load_env


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
REPORT_DIR = ROOT / "reports"
NEWS_CATEGORY = "news_media"
TRADE_POLICY_CATEGORY = "official_policy"
NEWS_BATCH_SOURCES = {
    source: CHINA_MEDIA_FEEDS[source]
    for source in (
        "yicai_brief",
        "cls_telegraph_api",
        "star_market_daily_subject",
        "jin10_rsshub_important",
        "sina_finance_articles",
        "wallstreetcn_news",
    )
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def news_sources(config_path: Path = SOURCE_PROFILE_CONFIG_PATH) -> dict[str, str]:
    profiles = runtime_profile_map(config_path=config_path)
    sources = {
        source: url
        for source, url in NEWS_BATCH_SOURCES.items()
        if profiles.get(source, {}).get("category") == NEWS_CATEGORY
    }
    return filter_enabled_mapping_for_run(sources, label="新闻媒体批处理源", config_path=config_path)


def official_trade_policy_sources(config_path: Path = SOURCE_PROFILE_CONFIG_PATH) -> list[TradePolicySource]:
    profiles = runtime_profile_map(config_path=config_path)
    return [
        source
        for source in TRADE_POLICY_SOURCES
        if profiles.get(source.name, {}).get("category") == TRADE_POLICY_CATEGORY
        and profiles.get(source.name, {}).get("enabled", True)
    ]


def selected_source_groups(
    names: Iterable[str],
    *,
    config_path: Path = SOURCE_PROFILE_CONFIG_PATH,
) -> tuple[dict[str, str], list[TradePolicySource]]:
    requested = {str(name or "").strip() for name in names if str(name or "").strip()}
    media = news_sources(config_path=config_path)
    policy = official_trade_policy_sources(config_path=config_path)
    if not requested:
        return media, policy
    known = set(media) | {source.name for source in policy}
    missing = sorted(requested - known)
    if missing:
        raise SystemExit(f"未知或已停用的 news collector source：{', '.join(missing)}")
    return (
        {source: url for source, url in media.items() if source in requested},
        [source for source in policy if source.name in requested],
    )


def collect_production(
    *,
    sources: dict[str, str],
    policy_sources: list[TradePolicySource] | None = None,
    notify_baseline: bool = False,
) -> dict[str, Any]:
    started_at = utc_now()
    errors: list[dict[str, str]] = []
    media_new_items = 0
    policy_new_items = 0
    if sources:
        try:
            media_new_items = china_media.run_once(list(sources), notify_baseline=notify_baseline)
        except Exception as exc:  # noqa: BLE001 - retain a source-family result for service health
            errors.append({"stage": "news_media", "error": f"{type(exc).__name__}: {exc}"})
    if policy_sources:
        try:
            policy_new_items = trade_policy.run_once(policy_sources, notify_baseline=notify_baseline)
        except Exception as exc:  # noqa: BLE001 - retain a source-family result for service health
            errors.append({"stage": "official_trade_policy", "error": f"{type(exc).__name__}: {exc}"})
    return {
        "ok": not errors,
        "mode": "production",
        "started_at": started_at,
        "finished_at": utc_now(),
        "counts": {
            "sources": len(sources) + len(policy_sources or []),
            "news_media_sources": len(sources),
            "trade_policy_sources": len(policy_sources or []),
            "new_items": media_new_items + policy_new_items,
            "news_media_new_items": media_new_items,
            "trade_policy_new_items": policy_new_items,
        },
        "errors": errors,
    }


def write_report(payload: dict[str, Any], report_dir: Path = REPORT_DIR) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = report_dir / f"news-collector-{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def print_text_summary(payload: dict[str, Any]) -> None:
    counts = payload.get("counts", {})
    print(
        "news_collector: "
        f"sources={counts.get('sources', 0)} "
        f"new_items={counts.get('new_items', 0)} "
        f"errors={len(payload.get('errors', []))}",
        flush=True,
    )
    for error in payload.get("errors", []):
        print(f"[ERR] {error.get('stage')}: {error.get('error')}", flush=True)


def main() -> int:
    load_env(ENV_PATH)
    parser = argparse.ArgumentParser(description="Run media and trade-policy sources.")
    parser.add_argument("--source", action="append", default=[], help="只跑指定 source id，可重复。")
    parser.add_argument("--notify-baseline", action="store_true", help="首次建立基线时也发送通知。默认不发送旧条目。")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON。")
    parser.add_argument("--write-report", action="store_true", help="把 JSON 报告写入 reports/。")
    parser.add_argument("--strict-exit", action="store_true", help="任一 source 失败时返回非 0。")
    args = parser.parse_args()

    sources, policy_sources = selected_source_groups(args.source)
    payload = collect_production(
        sources=sources,
        policy_sources=policy_sources,
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
