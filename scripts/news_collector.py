#!/usr/bin/env python3
"""Collector for domestic news-media sources.

This consolidates the current first batch of news-media feeds
(`yicai_brief`, `cls_telegraph_api`, `star_market_daily_subject`,
`jin10_rsshub_important`). By default it runs in shadow mode: it does not send
Feishu cards, does not run LLM interpretation, and does not write production
seen/review tables. The explicit ``--production`` mode runs the domestic media
collector; every item then enters the shared ``process_market_item`` runtime
facade.
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

import china_finance_media_monitor as china_media
import trade_policy_monitor as trade_policy
from china_media_sources import CHINA_MEDIA_FEEDS, CHINA_MEDIA_LABELS
from collector_direct_shadow import attach_direct_decision_shadow, direct_shadow_counts, safe_load_shadow_holdings
from collector_runtime import filter_enabled_mapping_for_run
from market_review_store import article_item_id
from rss_monitor import DB_PATH, strip_tags
from source_profiles import SOURCE_PROFILE_CONFIG_PATH, runtime_profile_map
from trade_policy_sources import TRADE_POLICY_SOURCES, TradePolicySource
from x_check import load_env


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
REPORT_DIR = ROOT / "reports"
NEWS_CATEGORY = "news_media"
TRADE_POLICY_CATEGORY = "official_policy"
NEWS_BATCH_SOURCES = {
    "yicai_brief": CHINA_MEDIA_FEEDS["yicai_brief"],
    "cls_telegraph_api": CHINA_MEDIA_FEEDS["cls_telegraph_api"],
    "star_market_daily_subject": CHINA_MEDIA_FEEDS["star_market_daily_subject"],
    "jin10_rsshub_important": CHINA_MEDIA_FEEDS["jin10_rsshub_important"],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def news_sources(config_path: Path = SOURCE_PROFILE_CONFIG_PATH) -> dict[str, str]:
    """Return enabled domestic news-media sources for the first shadow batch."""
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


def selected_sources(
    names: Iterable[str],
    *,
    config_path: Path = SOURCE_PROFILE_CONFIG_PATH,
) -> dict[str, str]:
    requested = {str(name or "").strip() for name in names if str(name or "").strip()}
    sources = news_sources(config_path=config_path)
    if not requested:
        return sources
    missing = sorted(requested - set(sources))
    if missing:
        raise SystemExit(f"未知或已停用的新闻媒体 source：{', '.join(missing)}")
    return {source: url for source, url in sources.items() if source in requested}


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
            if not table_exists(conn, "article_reviews"):
                return set()
            return {
                (str(row[0] or ""), str(row[1] or ""))
                for row in conn.execute(
                    f"SELECT source, item_id FROM article_reviews WHERE source IN ({placeholders})",
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
    item_id = article_item_id(item)
    would_focus = china_media.should_focus_item(dict(item, full_text=item.get("content") or item.get("summary") or ""))
    mandatory_push = china_media.is_mandatory_yicai_morning_brief(source, item)
    candidate = {
        "source": source,
        "id": item_id,
        "already_seen": (source, item_id) in seen_ids,
        "already_reviewed": (source, item_id) in reviewed_ids,
        "would_focus": would_focus,
        "mandatory_push": "yicai_morning_brief" if mandatory_push else "",
        "url": str(item.get("url") or ""),
        "title": str(item.get("title") or ""),
        "published_at": str(item.get("published_at") or ""),
        "summary": summarize_text(item.get("summary") or item.get("content") or ""),
        "source_module": str(item.get("source_module") or CHINA_MEDIA_LABELS.get(source, source)),
        "body_source": str(item.get("body_source") or ""),
        "pipeline": "news_media shadow -> decision layer / thin interpretation planned",
    }
    if not direct_shadow:
        return candidate
    return attach_direct_decision_shadow(
        candidate,
        source,
        item,
        source_category=NEWS_CATEGORY,
        collector="news_collector",
        content_type="article",
        holdings=direct_shadow_holdings,
    )


def limited(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return items
    return items[:limit]


def fetch_source_items(source: str, *, respect_prod_cls_state: bool = False) -> list[dict[str, Any]]:
    persist_state = False
    force = not respect_prod_cls_state
    return china_media.source_items(source, persist_state=persist_state, force=force)


def collect_source_shadow(
    source: str,
    url: str,
    *,
    limit: int,
    seen_ids: set[tuple[str, str]],
    reviewed_ids: set[tuple[str, str]],
    respect_prod_cls_state: bool = False,
    direct_shadow: bool = False,
    direct_shadow_holdings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        raw_items = fetch_source_items(source, respect_prod_cls_state=respect_prod_cls_state)
        candidates = [
            candidate_from_item(
                source,
                item,
                seen_ids,
                reviewed_ids,
                direct_shadow=direct_shadow,
                direct_shadow_holdings=direct_shadow_holdings,
            )
            for item in raw_items
        ]
        return {
            "source": source,
            "url": url,
            "ok": True,
            "label": CHINA_MEDIA_LABELS.get(source, source),
            "raw_count": len(raw_items),
            "candidate_count": len(candidates),
            "focus_count": sum(1 for item in candidates if item.get("would_focus")),
            "mandatory_count": sum(1 for item in candidates if item.get("mandatory_push")),
            "candidates": limited(candidates, limit),
            "error": "",
        }
    except Exception as exc:  # noqa: BLE001 - one failing source must not hide the rest
        return {
            "source": source,
            "url": url,
            "ok": False,
            "label": CHINA_MEDIA_LABELS.get(source, source),
            "raw_count": 0,
            "candidate_count": 0,
            "focus_count": 0,
            "mandatory_count": 0,
            "candidates": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def collect_shadow(
    *,
    sources: dict[str, str],
    limit: int = 5,
    compare_seen: bool = True,
    compare_reviews: bool = True,
    respect_prod_cls_state: bool = False,
    direct_shadow: bool = False,
    direct_shadow_holdings: list[dict[str, Any]] | None = None,
    policy_sources: list[TradePolicySource] | None = None,
) -> dict[str, Any]:
    started_at = utc_now()
    source_ids = list(sources)
    seen_ids = load_seen_item_ids(source_ids) if compare_seen else set()
    reviewed_ids = load_reviewed_item_ids(source_ids) if compare_reviews else set()
    holdings_error = ""
    if direct_shadow and direct_shadow_holdings is None:
        direct_shadow_holdings, holdings_error = safe_load_shadow_holdings(DB_PATH)
    max_workers = max(1, int(os.getenv("NEWS_COLLECTOR_MAX_WORKERS", os.getenv("CHINA_MEDIA_FETCH_MAX_WORKERS", "3")) or "3"))
    rows_by_source: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(sources)))) as executor:
        futures = {
            executor.submit(
                collect_source_shadow,
                source,
                url,
                limit=limit,
                seen_ids=seen_ids,
                reviewed_ids=reviewed_ids,
                respect_prod_cls_state=respect_prod_cls_state,
                direct_shadow=direct_shadow,
                direct_shadow_holdings=direct_shadow_holdings,
            ): source
            for source, url in sources.items()
        }
        for future in as_completed(futures):
            source = futures[future]
            rows_by_source[source] = future.result()

    rows = [rows_by_source[source] for source in sources if source in rows_by_source]
    if policy_sources:
        policy_payload = trade_policy.shadow_collect(policy_sources, limit=limit)
        for policy_row in policy_payload.get("rows", []):
            candidates = []
            for item in policy_row.get("candidates", []):
                decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
                action = str(decision.get("action") or "archive")
                candidates.append(
                    {
                        **item,
                        "already_seen": False,
                        "already_reviewed": False,
                        "would_focus": action in {"push", "daily"},
                        "mandatory_push": "",
                        "summary": "",
                        "source_module": policy_row.get("label", policy_row.get("source", "")),
                        "body_source": "official policy shadow",
                        "pipeline": "official policy shadow -> source-neutral decision engine",
                        "direct_shadow": {"decision": decision},
                    }
                )
            rows.append(
                {
                    "source": policy_row.get("source", ""),
                    "url": "",
                    "ok": bool(policy_row.get("ok")),
                    "label": policy_row.get("label", ""),
                    "raw_count": int(policy_row.get("raw_count") or 0),
                    "candidate_count": len(candidates),
                    "focus_count": sum(1 for item in candidates if item.get("would_focus")),
                    "mandatory_count": 0,
                    "candidates": candidates,
                    "error": str(policy_row.get("error") or ""),
                }
            )
    errors = [row for row in rows if not row.get("ok")]
    counts = {
        "sources": len(rows),
        "failed_sources": len(errors),
        "raw_items": sum(int(row.get("raw_count") or 0) for row in rows),
        "candidates": sum(int(row.get("candidate_count") or 0) for row in rows),
        "focus_candidates": sum(int(row.get("focus_count") or 0) for row in rows),
        "mandatory_candidates": sum(int(row.get("mandatory_count") or 0) for row in rows),
        "already_seen_candidates": sum(
            1
            for row in rows
            for item in row.get("candidates", [])
            if item.get("already_seen")
        ),
        "already_reviewed_candidates": sum(
            1
            for row in rows
            for item in row.get("candidates", [])
            if item.get("already_reviewed")
        ),
    }
    if direct_shadow:
        counts.update(direct_shadow_counts(rows))
    return {
        "ok": not errors,
        "mode": "shadow_dry_run",
        "sent_feishu": False,
        "ran_llm_review": False,
        "ran_direct_decision_shadow": direct_shadow,
        "wrote_production_seen_items": False,
        "wrote_production_reviews": False,
        "touched_production_source_state": False,
        "respect_prod_cls_state": respect_prod_cls_state,
        "direct_shadow_holdings_error": holdings_error,
        "started_at": started_at,
        "finished_at": utc_now(),
        "counts": counts,
        "sources": rows,
        "errors": errors,
    }


def collect_production(
    *,
    sources: dict[str, str],
    policy_sources: list[TradePolicySource] | None = None,
    notify_baseline: bool = False,
) -> dict[str, Any]:
    started_at = utc_now()
    errors: list[dict[str, str]] = []
    new_items = 0
    media_new_items = 0
    policy_new_items = 0
    if sources:
        try:
            media_new_items = china_media.run_once(list(sources), notify_baseline=notify_baseline)
        except Exception as exc:  # noqa: BLE001 - report the batch failure clearly
            errors.append({"stage": "news_media", "error": f"{type(exc).__name__}: {exc}"})
    if policy_sources:
        try:
            policy_new_items = trade_policy.run_once(policy_sources, notify_baseline=notify_baseline)
        except Exception as exc:  # noqa: BLE001 - report the source family failure clearly
            errors.append({"stage": "official_trade_policy", "error": f"{type(exc).__name__}: {exc}"})
    new_items = media_new_items + policy_new_items
    return {
        "ok": not errors,
        "mode": "production",
        "sent_feishu": True,
        "ran_llm_review": True,
        "wrote_production_seen_items": True,
        "wrote_production_reviews": True,
        "touched_production_source_state": True,
        "started_at": started_at,
        "finished_at": utc_now(),
        "counts": {
            "sources": len(sources) + len(policy_sources or []),
            "news_media_sources": len(sources),
            "trade_policy_sources": len(policy_sources or []),
            "new_items": new_items,
            "news_media_new_items": media_new_items,
            "trade_policy_new_items": policy_new_items,
        },
        "errors": errors,
    }


def write_report(payload: dict[str, Any], report_dir: Path = REPORT_DIR) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    mode = "production" if payload.get("mode") == "production" else "shadow"
    path = report_dir / f"news-collector-{mode}-{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def print_text_summary(payload: dict[str, Any]) -> None:
    counts = payload.get("counts", {})
    if payload.get("mode") == "production":
        print(
            "news_collector production: "
            f"sources={counts.get('sources', 0)} "
            f"new_items={counts.get('new_items', 0)} "
            f"errors={len(payload.get('errors', []))}",
            flush=True,
        )
        for error in payload.get("errors", []):
            print(f"[ERR] {error.get('stage')}: {error.get('error')}", flush=True)
        return
    print(
        "news_collector shadow: "
        f"sources={counts.get('sources', 0)} "
        f"failed={counts.get('failed_sources', 0)} "
        f"raw_items={counts.get('raw_items', 0)} "
        f"candidates={counts.get('candidates', 0)} "
        f"focus={counts.get('focus_candidates', 0)}"
        + (
            f" direct_push={counts.get('direct_shadow_push_candidates', 0)}"
            if payload.get("ran_direct_decision_shadow")
            else ""
        )
    )
    for row in payload.get("sources", []):
        status = "OK" if row.get("ok") else "ERR"
        print(
            f"[{status}] {row.get('source')}: "
            f"raw={row.get('raw_count', 0)} candidates={row.get('candidate_count', 0)} "
            f"focus={row.get('focus_count', 0)}"
        )
        if row.get("error"):
            print(f"  error: {row.get('error')}")
        for item in row.get("candidates", [])[:3]:
            seen = "seen" if item.get("already_seen") else "new?"
            reviewed = "reviewed" if item.get("already_reviewed") else "unreviewed"
            focus = "focus" if item.get("would_focus") else "non-focus"
            direct = item.get("direct_shadow") if isinstance(item.get("direct_shadow"), dict) else {}
            decision = direct.get("decision") if isinstance(direct.get("decision"), dict) else {}
            action = f", direct={decision.get('action')}" if decision else ""
            print(f"  - ({seen}, {reviewed}, {focus}{action}) {item.get('title')}")


def main() -> int:
    load_env(ENV_PATH)
    parser = argparse.ArgumentParser(description="Run news-media and official trade-policy collector.")
    parser.add_argument("--source", action="append", default=[], help="只跑指定 source id，可重复。")
    parser.add_argument("--production", action="store_true", help="运行生产链路：入库、统一决策/解读、Skeptic/Tavily、飞书推送。")
    parser.add_argument("--notify-baseline", action="store_true", help="生产模式下首次建立基线时也发送通知。默认不发送旧条目。")
    parser.add_argument("--limit", type=int, default=5, help="每个 source 输出候选条数；0 表示不限制。")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON。")
    parser.add_argument("--write-report", action="store_true", help="把 JSON 报告写入 reports/。")
    parser.add_argument("--no-compare-seen", action="store_true", help="不读取生产库判断 already_seen。")
    parser.add_argument("--no-compare-reviews", action="store_true", help="不读取 article_reviews 判断 already_reviewed。")
    parser.add_argument(
        "--respect-prod-cls-state",
        action="store_true",
        help="让财联社 shadow 抓取尊重生产 CLS_MIN_POLL_SECONDS；默认不读写生产轮询状态。",
    )
    parser.add_argument("--direct-shadow", action="store_true", help="在 shadow 报告中附加统一 decision_engine 直连决策结果；不写库、不发飞书。")
    parser.add_argument("--strict-exit", action="store_true", help="任一 source 失败时返回非 0；默认只在报告中记录错误。")
    args = parser.parse_args()

    sources, policy_sources = selected_source_groups(args.source)
    if args.production:
        payload = collect_production(
            sources=sources,
            policy_sources=policy_sources,
            notify_baseline=args.notify_baseline or os.getenv("SURVEIL_NOTIFY_BASELINE", "") == "1",
        )
    else:
        payload = collect_shadow(
            sources=sources,
            limit=max(0, args.limit),
            compare_seen=not args.no_compare_seen,
            compare_reviews=not args.no_compare_reviews,
            respect_prod_cls_state=args.respect_prod_cls_state,
            direct_shadow=args.direct_shadow,
            policy_sources=policy_sources,
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
