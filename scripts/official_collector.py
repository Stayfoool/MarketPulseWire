#!/usr/bin/env python3
"""Collector for official core-company news feeds.

By default this collector runs in shadow mode: it does not send Feishu cards,
run LLM interpretation, or write production seen/review tables. The explicit
``--production`` mode runs the RSS collector; every item then enters the shared
``process_market_item`` runtime facade.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from collector_runtime import filter_enabled_mapping_for_run, load_source_states, save_source_state
from collector_direct_shadow import attach_direct_decision_shadow, direct_shadow_counts, safe_load_shadow_holdings
from db_utils import connect_sqlite, ensure_source_state_table
from rss_monitor import DB_PATH, fetch_feed, filter_items, run_once as run_rss_once, strip_tags
from source_profiles import SOURCE_PROFILE_CONFIG_PATH, runtime_profile_map
from trendforce_sources import DEFAULT_RSS_FEEDS
from x_check import load_env


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
REPORT_DIR = ROOT / "reports"
OFFICIAL_CATEGORY = "official_company"
SHADOW_STATE_PREFIX = "official_shadow_feed"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def official_rss_feeds(config_path: Path = SOURCE_PROFILE_CONFIG_PATH) -> dict[str, str]:
    """Return enabled official-company RSS feeds from source profiles."""
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


def load_reviewed_item_ids(sources: Iterable[str], db_path: Path = DB_PATH) -> set[tuple[str, str]]:
    source_list = sorted({source for source in sources if source})
    if not source_list or not db_path.exists():
        return set()
    placeholders = ",".join("?" for _ in source_list)
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            if not table_exists(conn, "official_news_reviews"):
                return set()
            return {
                (str(row[0] or ""), str(row[1] or ""))
                for row in conn.execute(
                    f"SELECT source, item_id FROM official_news_reviews WHERE source IN ({placeholders})",
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


def candidate_from_item(
    source: str,
    item: dict[str, Any],
    seen_ids: set[tuple[str, str]],
    reviewed_ids: set[tuple[str, str]],
    *,
    direct_shadow: bool = False,
    direct_shadow_holdings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    item_id = str(item.get("id") or item.get("url") or item.get("title") or "")
    candidate = {
        "source": source,
        "id": item_id,
        "already_seen": (source, item_id) in seen_ids,
        "already_reviewed": (source, item_id) in reviewed_ids,
        "url": str(item.get("url") or ""),
        "title": str(item.get("title") or ""),
        "published_at": str(item.get("published_at") or ""),
        "summary": summarize_text(item.get("summary") or item.get("content") or ""),
        "categories": list(item.get("categories") or []),
        "pipeline": "official_company shadow -> decision layer / thin interpretation planned",
    }
    if not direct_shadow:
        return candidate
    return attach_direct_decision_shadow(
        candidate,
        source,
        item,
        source_category=OFFICIAL_CATEGORY,
        collector="official_collector.rss",
        content_type="official_news",
        holdings=direct_shadow_holdings,
    )


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


def collect_rss_shadow(
    feeds: dict[str, str],
    *,
    limit: int,
    seen_ids: set[tuple[str, str]],
    reviewed_ids: set[tuple[str, str]],
    save_shadow_state: bool = False,
    direct_shadow: bool = False,
    direct_shadow_holdings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    states = load_shadow_feed_states(feeds, save_shadow_state)
    max_workers = max(1, int(os.getenv("OFFICIAL_COLLECTOR_MAX_WORKERS", os.getenv("RSS_FETCH_MAX_WORKERS", "8")) or "8"))
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
                        [
                            candidate_from_item(
                                source,
                                item,
                                seen_ids,
                                reviewed_ids,
                                direct_shadow=direct_shadow,
                                direct_shadow_holdings=direct_shadow_holdings,
                            )
                            for item in filtered_items
                        ],
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


def collect_shadow(
    *,
    feeds: dict[str, str],
    limit: int = 5,
    compare_seen: bool = True,
    compare_reviews: bool = True,
    save_shadow_state: bool = False,
    direct_shadow: bool = False,
    direct_shadow_holdings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    started_at = utc_now()
    source_ids = list(feeds)
    seen_ids = load_seen_item_ids(source_ids) if compare_seen else set()
    reviewed_ids = load_reviewed_item_ids(source_ids) if compare_reviews else set()
    holdings_error = ""
    if direct_shadow and direct_shadow_holdings is None:
        direct_shadow_holdings, holdings_error = safe_load_shadow_holdings(DB_PATH)
    rss_rows = collect_rss_shadow(
        feeds,
        limit=limit,
        seen_ids=seen_ids,
        reviewed_ids=reviewed_ids,
        save_shadow_state=save_shadow_state,
        direct_shadow=direct_shadow,
        direct_shadow_holdings=direct_shadow_holdings,
    )
    errors = [row for row in rss_rows if not row.get("ok")]
    counts = {
        "rss_sources": len(rss_rows),
        "sources": len(rss_rows),
        "failed_sources": len(errors),
        "raw_items": sum(int(row.get("raw_count") or 0) for row in rss_rows),
        "candidates": sum(int(row.get("candidate_count") or 0) for row in rss_rows),
        "already_seen_candidates": sum(
            1
            for row in rss_rows
            for item in row.get("candidates", [])
            if item.get("already_seen")
        ),
        "already_reviewed_candidates": sum(
            1
            for row in rss_rows
            for item in row.get("candidates", [])
            if item.get("already_reviewed")
        ),
    }
    if direct_shadow:
        counts.update(direct_shadow_counts(rss_rows))
    return {
        "ok": not errors,
        "mode": "shadow_dry_run",
        "sent_feishu": False,
        "ran_llm_review": False,
        "ran_direct_decision_shadow": direct_shadow,
        "wrote_production_seen_items": False,
        "wrote_production_reviews": False,
        "save_shadow_state": save_shadow_state,
        "direct_shadow_holdings_error": holdings_error,
        "started_at": started_at,
        "finished_at": utc_now(),
        "counts": counts,
        "rss": rss_rows,
        "errors": errors,
    }


def collect_production(
    *,
    feeds: dict[str, str],
    notify_baseline: bool = False,
) -> dict[str, Any]:
    started_at = utc_now()
    errors: list[dict[str, str]] = []
    rss_new = 0
    if feeds:
        try:
            rss_new = run_rss_once(feeds, notify_baseline=notify_baseline)
        except Exception as exc:  # noqa: BLE001 - report the batch failure clearly
            errors.append({"stage": "rss", "error": f"{type(exc).__name__}: {exc}"})
    return {
        "ok": not errors,
        "mode": "production",
        "sent_feishu": True,
        "ran_llm_review": True,
        "wrote_production_seen_items": True,
        "wrote_production_reviews": True,
        "started_at": started_at,
        "finished_at": utc_now(),
        "counts": {
            "rss_sources": len(feeds),
            "new_items": rss_new,
        },
        "errors": errors,
    }


def write_report(payload: dict[str, Any], report_dir: Path = REPORT_DIR) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    mode = "production" if payload.get("mode") == "production" else "shadow"
    path = report_dir / f"official-collector-{mode}-{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def print_text_summary(payload: dict[str, Any]) -> None:
    counts = payload.get("counts", {})
    if payload.get("mode") == "production":
        print(
            "official_collector production: "
            f"rss_sources={counts.get('rss_sources', 0)} "
            f"new_items={counts.get('new_items', 0)} "
            f"errors={len(payload.get('errors', []))}",
            flush=True,
        )
        for error in payload.get("errors", []):
            print(f"[ERR] {error.get('stage')}: {error.get('error')}", flush=True)
        return
    print(
        "official_collector shadow: "
        f"sources={counts.get('sources', 0)} "
        f"failed={counts.get('failed_sources', 0)} "
        f"raw_items={counts.get('raw_items', 0)} "
        f"candidates={counts.get('candidates', 0)}"
        + (
            f" direct_push={counts.get('direct_shadow_push_candidates', 0)}"
            if payload.get("ran_direct_decision_shadow")
            else ""
        )
    )
    for row in payload.get("rss", []):
        status = "OK" if row.get("ok") else "ERR"
        print(
            f"[{status}] {row.get('source')}: "
            f"raw={row.get('raw_count', 0)} candidates={row.get('candidate_count', 0)}"
        )
        if row.get("error"):
            print(f"  error: {row.get('error')}")
        for item in row.get("candidates", [])[:3]:
            seen = "seen" if item.get("already_seen") else "new?"
            reviewed = "reviewed" if item.get("already_reviewed") else "unreviewed"
            direct = item.get("direct_shadow") if isinstance(item.get("direct_shadow"), dict) else {}
            decision = direct.get("decision") if isinstance(direct.get("decision"), dict) else {}
            action = f", direct={decision.get('action')}" if decision else ""
            print(f"  - ({seen}, {reviewed}{action}) {item.get('title')}")


def main() -> int:
    load_env(ENV_PATH)
    parser = argparse.ArgumentParser(description="Run official company news collector.")
    parser.add_argument("--source", action="append", default=[], help="只跑指定 source id，可重复。")
    parser.add_argument("--production", action="store_true", help="运行生产链路：入库、统一决策/解读、Skeptic/Tavily、飞书推送。")
    parser.add_argument("--notify-baseline", action="store_true", help="生产模式下首次建立基线时也发送通知。默认不发送旧条目。")
    parser.add_argument("--limit", type=int, default=5, help="每个 source 输出候选条数；0 表示不限制。")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON。")
    parser.add_argument("--write-report", action="store_true", help="把 JSON 报告写入 reports/。")
    parser.add_argument("--no-compare-seen", action="store_true", help="不读取生产库判断 already_seen。")
    parser.add_argument("--no-compare-reviews", action="store_true", help="不读取 official_news_reviews 判断 already_reviewed。")
    parser.add_argument("--direct-shadow", action="store_true", help="在 shadow 报告中附加统一 decision_engine 直连决策结果；不写库、不发飞书。")
    parser.add_argument("--strict-exit", action="store_true", help="任一 source 失败时返回非 0；默认只在报告中记录错误。")
    parser.add_argument(
        "--save-shadow-state",
        action="store_true",
        help="仅保存 official_shadow_feed:* 条件请求状态；不写生产 seen/review 表。",
    )
    args = parser.parse_args()
    if args.production and args.save_shadow_state:
        raise SystemExit("--production 不能与 --save-shadow-state 同时使用")

    feeds = selected_sources(args.source)
    if args.production:
        payload = collect_production(
            feeds=feeds,
            notify_baseline=args.notify_baseline or os.getenv("SURVEIL_NOTIFY_BASELINE", "") == "1",
        )
    else:
        payload = collect_shadow(
            feeds=feeds,
            limit=max(0, args.limit),
            compare_seen=not args.no_compare_seen,
            compare_reviews=not args.no_compare_reviews,
            save_shadow_state=args.save_shadow_state,
            direct_shadow=args.direct_shadow,
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
