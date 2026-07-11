#!/usr/bin/env python3
"""Local-only web UI for portfolio holdings management."""

from __future__ import annotations

import argparse
import html
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from db_utils import connect_sqlite
from env_utils import load_env
from holdings_store import (
    HoldingsError,
    holdings_diff,
    normalized_holdings,
    normalize_holdings_for_save,
    save_holdings,
    validate_holdings,
)
from market_db import DEFAULT_DB_PATH
from media_keyword_config import media_keyword_payload, save_media_keyword_config
from investment_bank_theme_config import config_payload as investment_bank_theme_config_payload
from investment_bank_theme_config import save_config as save_investment_bank_theme_config
from rule_center import list_rule_audit, rule_center_payload, save_rule_center_config, simulate_rules
from settings_store import save_settings, settings_payload
from signals_extract import extract_signals
from source_profiles import save_source_profile_config, source_profiles_payload
from stock_relations import (
    DEFAULT_CONFIG_PATH as STOCK_RELATIONS_CONFIG_PATH,
    accept_relation_suggestion,
    delete_relation,
    diff_relations,
    export_relations,
    import_relations,
    list_relation_suggestions,
    list_relations,
    reject_relation_suggestion,
    save_relation,
    set_relation_enabled,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
BJ = ZoneInfo("Asia/Shanghai")

SERVICE_UNITS = [
    "surveil-x-stream.service",
    "surveil-rss-monitor.service",
    "surveil-trendforce-page-monitor.service",
    "surveil-sina-flash.service",
    "surveil-overseas-media.service",
    "surveil-china-media.service",
    "surveil-sina-stock-news.service",
    "surveil-article-daily.service",
    "surveil-signals-extract.service",
    "surveil-signal-outcome.service",
    "surveil-signal-review.service",
    "surveil-signal-digest.service",
    "surveil-research-collector.service",
    "surveil-official-collector.service",
    "surveil-news-collector.service",
    "surveil-value-directory.service",
    "surveil-research-collector-shadow.service",
    "surveil-official-collector-shadow.service",
    "surveil-news-collector-shadow.service",
    "surveil-collector-shadow-digest.service",
    "surveil-holdings-web.service",
    "surveil-proxy.service",
]

TIMER_UNITS = [
    "surveil-sina-stock-news.timer",
    "surveil-overseas-media.timer",
    "surveil-china-media.timer",
    "surveil-article-daily.timer",
    "surveil-signals-extract.timer",
    "surveil-signal-outcome.timer",
    "surveil-signal-review.timer",
    "surveil-signal-digest.timer",
    "surveil-ifind-notice.timer",
    "surveil-ifind-report.timer",
    "surveil-jygs-actions.timer",
    "surveil-research-collector.timer",
    "surveil-official-collector.timer",
    "surveil-news-collector.timer",
    "surveil-value-directory.timer",
    "surveil-research-collector-shadow.timer",
    "surveil-official-collector-shadow.timer",
    "surveil-news-collector-shadow.timer",
    "surveil-collector-shadow-digest.timer",
]

RUN_ONCE_TARGETS = {
    "surveil-sina-stock-news.timer": "surveil-sina-stock-news.service",
    "surveil-overseas-media.timer": "surveil-overseas-media.service",
    "surveil-china-media.timer": "surveil-china-media.service",
    "surveil-article-daily.timer": "surveil-article-daily.service",
    "surveil-signals-extract.timer": "surveil-signals-extract.service",
    "surveil-signal-outcome.timer": "surveil-signal-outcome.service",
    "surveil-signal-review.timer": "surveil-signal-review.service",
    "surveil-signal-digest.timer": "surveil-signal-digest.service",
    "surveil-ifind-notice.timer": "surveil-ifind-notice.service",
    "surveil-ifind-report.timer": "surveil-ifind-report.service",
    "surveil-jygs-actions.timer": "surveil-jygs-actions.service",
    "surveil-research-collector.timer": "surveil-research-collector.service",
    "surveil-official-collector.timer": "surveil-official-collector.service",
    "surveil-news-collector.timer": "surveil-news-collector.service",
    "surveil-value-directory.timer": "surveil-value-directory.service",
    "surveil-research-collector-shadow.timer": "surveil-research-collector-shadow.service",
    "surveil-official-collector-shadow.timer": "surveil-official-collector-shadow.service",
    "surveil-news-collector-shadow.timer": "surveil-news-collector-shadow.service",
    "surveil-collector-shadow-digest.timer": "surveil-collector-shadow-digest.service",
}

ALLOWED_SYSTEMD_UNITS = set(SERVICE_UNITS) | set(TIMER_UNITS) | set(RUN_ONCE_TARGETS.values())

LEGACY_CUTOVER_UNITS = {
    "surveil-rss-monitor.service",
    "surveil-trendforce-page-monitor.service",
    "surveil-overseas-media.service",
    "surveil-overseas-media.timer",
    "surveil-china-media.service",
    "surveil-china-media.timer",
}

SHADOW_UNITS = {
    "surveil-research-collector-shadow.service",
    "surveil-research-collector-shadow.timer",
    "surveil-official-collector-shadow.service",
    "surveil-official-collector-shadow.timer",
    "surveil-news-collector-shadow.service",
    "surveil-news-collector-shadow.timer",
    "surveil-collector-shadow-digest.service",
    "surveil-collector-shadow-digest.timer",
}

LEGACY_REPLACEMENTS = {
    "surveil-rss-monitor.service": "surveil-research-collector.timer / surveil-official-collector.timer",
    "surveil-trendforce-page-monitor.service": "surveil-research-collector.timer",
    "surveil-overseas-media.service": "surveil-research-collector.timer",
    "surveil-overseas-media.timer": "surveil-research-collector.timer",
    "surveil-china-media.service": "surveil-news-collector.timer",
    "surveil-china-media.timer": "surveil-news-collector.timer",
}

UNIT_METADATA = {
    "surveil-x-stream.service": {"group": "fetching_persistent", "type": "常驻采集", "schedule": "X 长连接"},
    "surveil-rss-monitor.service": {"group": "fetching_legacy", "type": "历史兼容", "schedule": "已切流；旧 300 秒 RSS 常驻"},
    "surveil-trendforce-page-monitor.service": {"group": "fetching_legacy", "type": "历史兼容", "schedule": "已切流；旧 900 秒 TrendForce 页面常驻"},
    "surveil-sina-flash.service": {"group": "fetching_persistent", "type": "常驻采集", "schedule": "脚本内高频轮询"},
    "surveil-overseas-media.service": {"group": "fetching_legacy", "type": "历史兼容", "schedule": "已切流；旧海外媒体批处理"},
    "surveil-china-media.service": {"group": "fetching_legacy", "type": "历史兼容", "schedule": "已切流；旧中国财经媒体批处理"},
    "surveil-sina-stock-news.service": {"group": "fetching_scheduled", "type": "定时采集", "schedule": "timer 每 30 分钟"},
    "surveil-ifind-notice.service": {"group": "fetching_scheduled", "type": "定时采集", "schedule": "timer 08:00 / 20:00"},
    "surveil-ifind-report.service": {"group": "fetching_scheduled", "type": "定时采集", "schedule": "timer 08:00 / 20:00"},
    "surveil-jygs-actions.service": {"group": "fetching_scheduled", "type": "定时采集", "schedule": "timer 12:30 / 16:00"},
    "surveil-research-collector.service": {"group": "fetching_scheduled", "type": "定时采集", "schedule": "timer 每 5 分钟；页面源内部 15 分钟"},
    "surveil-official-collector.service": {"group": "fetching_scheduled", "type": "定时采集", "schedule": "timer 每 10 分钟"},
    "surveil-news-collector.service": {"group": "fetching_scheduled", "type": "定时采集", "schedule": "timer 每 2 分钟"},
    "surveil-value-directory.service": {"group": "fetching_scheduled", "type": "定时采集", "schedule": "timer 每天 08:00；需先完成服务器浏览器登录"},
    "surveil-research-collector-shadow.service": {"group": "fetching_shadow", "type": "影子采集", "schedule": "timer 每 15 分钟"},
    "surveil-official-collector-shadow.service": {"group": "fetching_shadow", "type": "影子采集", "schedule": "timer 每 30 分钟"},
    "surveil-news-collector-shadow.service": {"group": "fetching_shadow", "type": "影子采集", "schedule": "timer 每 10 分钟"},
    "surveil-collector-shadow-digest.service": {"group": "processing_scheduled", "type": "影子报告", "schedule": "timer 21:05"},
    "surveil-article-daily.service": {"group": "processing_scheduled", "type": "定时处理", "schedule": "timer 20:50"},
    "surveil-signals-extract.service": {"group": "processing_scheduled", "type": "定时处理", "schedule": "timer 每 10 分钟"},
    "surveil-signal-outcome.service": {"group": "processing_scheduled", "type": "定时处理", "schedule": "timer 交易日 16:20"},
    "surveil-signal-review.service": {"group": "processing_scheduled", "type": "定时处理", "schedule": "timer 交易日 16:35"},
    "surveil-signal-digest.service": {"group": "processing_scheduled", "type": "定时处理", "schedule": "timer 20:35"},
    "surveil-holdings-web.service": {"group": "infrastructure", "type": "基础设施", "schedule": "Web 工作台"},
    "surveil-proxy.service": {"group": "infrastructure", "type": "基础设施", "schedule": "本地代理"},
    "surveil-sina-stock-news.timer": {"group": "fetching_scheduled", "type": "定时器", "schedule": "每 30 分钟"},
    "surveil-overseas-media.timer": {"group": "fetching_legacy", "type": "历史兼容定时器", "schedule": "已切流；旧每 5 分钟"},
    "surveil-china-media.timer": {"group": "fetching_legacy", "type": "历史兼容定时器", "schedule": "已切流；旧每 2 分钟"},
    "surveil-ifind-notice.timer": {"group": "fetching_scheduled", "type": "定时器", "schedule": "08:00 / 20:00"},
    "surveil-ifind-report.timer": {"group": "fetching_scheduled", "type": "定时器", "schedule": "08:00 / 20:00"},
    "surveil-jygs-actions.timer": {"group": "fetching_scheduled", "type": "定时器", "schedule": "12:30 / 16:00"},
    "surveil-research-collector.timer": {"group": "fetching_scheduled", "type": "定时器", "schedule": "每 5 分钟"},
    "surveil-official-collector.timer": {"group": "fetching_scheduled", "type": "定时器", "schedule": "每 10 分钟"},
    "surveil-news-collector.timer": {"group": "fetching_scheduled", "type": "定时器", "schedule": "每 2 分钟"},
    "surveil-value-directory.timer": {"group": "fetching_scheduled", "type": "定时器", "schedule": "每天 08:00；默认需人工启用"},
    "surveil-research-collector-shadow.timer": {"group": "fetching_shadow", "type": "影子定时器", "schedule": "每 15 分钟"},
    "surveil-official-collector-shadow.timer": {"group": "fetching_shadow", "type": "影子定时器", "schedule": "每 30 分钟"},
    "surveil-news-collector-shadow.timer": {"group": "fetching_shadow", "type": "影子定时器", "schedule": "每 10 分钟"},
    "surveil-collector-shadow-digest.timer": {"group": "processing_scheduled", "type": "影子报告定时器", "schedule": "21:05"},
    "surveil-article-daily.timer": {"group": "processing_scheduled", "type": "定时器", "schedule": "20:50"},
    "surveil-signals-extract.timer": {"group": "processing_scheduled", "type": "定时器", "schedule": "每 10 分钟"},
    "surveil-signal-outcome.timer": {"group": "processing_scheduled", "type": "定时器", "schedule": "交易日 16:20"},
    "surveil-signal-review.timer": {"group": "processing_scheduled", "type": "定时器", "schedule": "交易日 16:35"},
    "surveil-signal-digest.timer": {"group": "processing_scheduled", "type": "定时器", "schedule": "20:35"},
}

UNIT_GROUP_LABELS = {
    "fetching_persistent": "常驻采集服务",
    "fetching_scheduled": "定时采集任务",
    "fetching_shadow": "影子采集任务",
    "fetching_legacy": "历史兼容采集单元",
    "processing_scheduled": "非抓取处理/日报任务",
    "infrastructure": "基础设施",
    "other": "其他",
}

LOG_FILES = [
    "x-stream.err.log",
    "rss-monitor.err.log",
    "trendforce-page-monitor.err.log",
    "overseas-media.err.log",
    "china-media.err.log",
    "research-collector.err.log",
    "official-collector.err.log",
    "news-collector.err.log",
    "value-directory.err.log",
    "research-collector-shadow.err.log",
    "official-collector-shadow.err.log",
    "news-collector-shadow.err.log",
    "collector-shadow-digest.err.log",
    "sina-flash.err.log",
    "sina-stock-news.err.log",
    "ifind-notice.err.log",
    "jygs-actions.err.log",
    "holdings-web.err.log",
    "signal-review.err.log",
    "signal-digest.err.log",
    "stock-relations-import.err.log",
]

SIGNAL_FEEDBACK_VERDICTS = {"hit", "partial", "miss", "too_early", "unverifiable"}
SIGNAL_FEEDBACK_ERROR_TYPES = {
    "stale_or_price_in",
    "counter_supply_news",
    "supply_expansion_bearish",
    "wrong_relation",
    "wrong_direction",
    "timing_error",
    "low_market_attention",
    "quote_unavailable",
    "window_not_ready",
    "direction_uncertain",
    "weak_follow_through",
    "direction_or_relevance_error",
    "timing_or_duration_error",
    "none",
    "unverifiable",
    "other",
}


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "y", "on", "是"}


def workbench_environment_label() -> str:
    """Return an explicit label so private local and server portfolios are not confused."""
    configured = os.getenv("HOLDINGS_WEB_ENV_LABEL", "").strip()
    if configured:
        return configured
    return "服务器生产配置" if ROOT == Path("/opt/surveil") else "本地开发配置"


def utc_window_for_day(day: str = "") -> tuple[str, str, str]:
    if day:
        start_local = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=BJ)
    else:
        start_local = datetime.now(BJ).replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc).isoformat(),
        end_local.astimezone(timezone.utc).isoformat(),
        start_local.strftime("%Y-%m-%d"),
    )


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def normalize_time(value: str) -> str:
    return str(value or "")


def count_rows(conn: sqlite3.Connection, table: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
    if not table_exists(conn, table):
        return 0
    query = f"SELECT COUNT(*) FROM {table}"
    if where:
        query += f" WHERE {where}"
    return int(conn.execute(query, params).fetchone()[0])


def grouped_counts(conn: sqlite3.Connection, table: str, field: str, where: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    if not table_exists(conn, table):
        return []
    rows = conn.execute(
        f"""
        SELECT COALESCE({field}, '') AS key, COUNT(*) AS count
        FROM {table}
        WHERE {where}
        GROUP BY COALESCE({field}, '')
        ORDER BY count DESC, key
        """,
        params,
    ).fetchall()
    return [{"key": row["key"] or "unknown", "count": int(row["count"])} for row in rows]


def event_center_where_clause(
    *,
    time_field: str,
    start_utc: str,
    end_utc: str,
    source_lower: str,
    source_fields: tuple[str, ...],
    q_lower: str,
    q_fields: tuple[str, ...],
) -> tuple[str, list[str]]:
    clauses = [f"datetime({time_field}) >= datetime(?)", f"datetime({time_field}) < datetime(?)"]
    params = [start_utc, end_utc]
    if source_lower and source_fields:
        clauses.append("(" + " OR ".join(f"LOWER(COALESCE({field}, '')) LIKE ?" for field in source_fields) + ")")
        params.extend([f"%{source_lower}%"] * len(source_fields))
    if q_lower and q_fields:
        clauses.append("(" + " OR ".join(f"LOWER(COALESCE({field}, '')) LIKE ?" for field in q_fields) + ")")
        params.extend([f"%{q_lower}%"] * len(q_fields))
    return " AND ".join(clauses), params


def normalized_event_time_basis(value: str) -> str:
    return "published" if str(value or "").strip().lower() == "published" else "seen"


def event_time_field(*, basis: str, seen_field: str, published_field: str) -> str:
    if basis == "published":
        return f"COALESCE(NULLIF({published_field}, ''), {seen_field})"
    return seen_field


def displayed_event_time(item: dict[str, Any], basis: str) -> str:
    if basis == "published":
        return str(item.get("published_at") or item.get("seen_at") or "")
    return str(item.get("seen_at") or item.get("published_at") or "")


def fetch_events_rows(
    day: str = "",
    source: str = "",
    kind: str = "",
    q: str = "",
    time_basis: str = "seen",
    include_baseline: bool = False,
    limit: int = 100,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    start_utc, end_utc, _ = utc_window_for_day(day)
    q_lower = q.strip().lower()
    source_lower = source.strip().lower()
    kind_lower = kind.strip().lower()
    time_basis = normalized_event_time_basis(time_basis)
    rows: list[dict[str, Any]] = []
    with connect_sqlite(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if table_exists(conn, "events"):
            where, params = event_center_where_clause(
                time_field=event_time_field(
                    basis=time_basis,
                    seen_field="e.first_seen_at",
                    published_field="e.published_at",
                ),
                start_utc=start_utc,
                end_utc=end_utc,
                source_lower=source_lower,
                source_fields=("e.source",),
                q_lower=q_lower,
                q_fields=("e.title", "e.summary", "e.full_text", "e.url", "e.symbols_json", "e.themes_json"),
            )
            for row in conn.execute(
                f"""
                SELECT e.id, e.source, e.event_type, e.title, e.summary, e.url, e.published_at,
                       e.first_seen_at, e.baseline_only,
                       (
                         SELECT importance FROM event_analyses a
                         WHERE a.event_id = e.id
                         ORDER BY a.id DESC LIMIT 1
                       ) AS importance,
                       (
                         SELECT classification FROM event_analyses a
                         WHERE a.event_id = e.id
                         ORDER BY a.id DESC LIMIT 1
                       ) AS classification,
                       (
                         SELECT should_push FROM event_analyses a
                         WHERE a.event_id = e.id
                         ORDER BY a.id DESC LIMIT 1
                       ) AS should_push,
                       (
                         SELECT status FROM deliveries d
                         WHERE d.event_id = e.id
                         ORDER BY d.id DESC LIMIT 1
                       ) AS delivery_status
                FROM events e
                WHERE {where}
                  {" " if include_baseline else "AND COALESCE(e.baseline_only, 0) = 0"}
                ORDER BY e.first_seen_at DESC
                LIMIT 300
                """,
                params,
            ):
                rows.append(
                    {
                        "kind": row["event_type"] or "event",
                        "source": row["source"],
                        "source_id": row["source"],
                        "id": row["id"],
                        "title": row["title"],
                        "summary": row["summary"] or "",
                        "url": row["url"] or "",
                        "published_at": normalize_time(row["published_at"]),
                        "seen_at": normalize_time(row["first_seen_at"]),
                        "importance": row["importance"] or "",
                        "classification": row["classification"] or "",
                        "push": bool(row["should_push"]),
                        "delivery_status": row["delivery_status"] or "",
                        "baseline_only": bool(row["baseline_only"]),
                    }
                )
        if table_exists(conn, "article_reviews"):
            where, params = event_center_where_clause(
                time_field=event_time_field(
                    basis=time_basis,
                    seen_field="created_at",
                    published_field="published_at",
                ),
                start_utc=start_utc,
                end_utc=end_utc,
                source_lower=source_lower,
                source_fields=("source", "source_module"),
                q_lower=q_lower,
                q_fields=("title", "daily_summary", "reason", "affected_targets_json", "url"),
            )
            for row in conn.execute(
                f"""
                SELECT source, item_id, url, title, source_module, published_at, importance,
                       push_now, incremental_classification, daily_summary, reason, pushed_at, created_at
                FROM article_reviews
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT 300
                """,
                params,
            ):
                rows.append(
                    {
                        "kind": "article",
                        "source": row["source_module"] or row["source"],
                        "source_id": row["source"],
                        "id": row["item_id"],
                        "title": row["title"],
                        "summary": row["daily_summary"] or row["reason"] or "",
                        "url": row["url"] or "",
                        "published_at": normalize_time(row["published_at"]),
                        "seen_at": normalize_time(row["created_at"]),
                        "importance": row["importance"] or "",
                        "classification": row["incremental_classification"] or "",
                        "push": bool(row["push_now"]),
                        "delivery_status": "sent" if row["pushed_at"] else "daily",
                        "baseline_only": False,
                    }
                )
        if table_exists(conn, "official_news_reviews"):
            where, params = event_center_where_clause(
                time_field=event_time_field(
                    basis=time_basis,
                    seen_field="created_at",
                    published_field="published_at",
                ),
                start_utc=start_utc,
                end_utc=end_utc,
                source_lower=source_lower,
                source_fields=("source",),
                q_lower=q_lower,
                q_fields=("title", "daily_summary", "reason", "url"),
            )
            for row in conn.execute(
                f"""
                SELECT source, item_id, url, title, published_at, importance, daily_summary,
                       reason, pushed_at, created_at
                FROM official_news_reviews
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT 200
                """,
                params,
            ):
                rows.append(
                    {
                        "kind": "official_news",
                        "source": row["source"],
                        "source_id": row["source"],
                        "id": row["item_id"],
                        "title": row["title"],
                        "summary": row["daily_summary"] or row["reason"] or "",
                        "url": row["url"] or "",
                        "published_at": normalize_time(row["published_at"]),
                        "seen_at": normalize_time(row["created_at"]),
                        "importance": row["importance"] or "",
                        "classification": "",
                        "push": bool(row["pushed_at"]),
                        "delivery_status": "sent" if row["pushed_at"] else "daily",
                        "baseline_only": False,
                    }
                )
        if include_baseline and table_exists(conn, "seen_items"):
            where, params = event_center_where_clause(
                time_field=event_time_field(
                    basis=time_basis,
                    seen_field="s.first_seen_at",
                    published_field="s.published_at",
                ),
                start_utc=start_utc,
                end_utc=end_utc,
                source_lower=source_lower,
                source_fields=("s.source",),
                q_lower=q_lower,
                q_fields=("s.title", "s.summary", "s.url"),
            )
            reviewed_clause = ""
            if table_exists(conn, "article_reviews"):
                reviewed_clause = """
                    AND NOT EXISTS (
                        SELECT 1
                        FROM article_reviews r
                        WHERE r.source = s.source AND r.item_id = s.item_id
                    )
                """
            for row in conn.execute(
                f"""
                SELECT s.source, s.item_id, s.url, s.title, s.summary, s.published_at, s.first_seen_at
                FROM seen_items s
                WHERE {where}
                  {reviewed_clause}
                ORDER BY s.first_seen_at DESC
                LIMIT 300
                """,
                params,
            ):
                rows.append(
                    {
                        "kind": "baseline",
                        "source": row["source"],
                        "source_id": row["source"],
                        "id": row["item_id"],
                        "title": row["title"],
                        "summary": row["summary"] or "首次采集建立去重基线，未进入文章门控。",
                        "url": row["url"] or "",
                        "published_at": normalize_time(row["published_at"]),
                        "seen_at": normalize_time(row["first_seen_at"]),
                        "importance": "",
                        "classification": "仅建立去重基线",
                        "push": False,
                        "delivery_status": "baseline",
                        "baseline_only": True,
                    }
                )
        if table_exists(conn, "seen_posts"):
            seen_columns = table_columns(conn, "seen_posts")
            delivery_expr = "delivery_status" if "delivery_status" in seen_columns else "'sent'"
            where, params = event_center_where_clause(
                time_field=event_time_field(
                    basis=time_basis,
                    seen_field="first_seen_at",
                    published_field="published_at",
                ),
                start_utc=start_utc,
                end_utc=end_utc,
                source_lower=source_lower,
                source_fields=("source",),
                q_lower=q_lower,
                q_fields=("text", "url"),
            )
            for row in conn.execute(
                f"""
                SELECT source, post_id, url, text, published_at, first_seen_at,
                       {delivery_expr} AS delivery_status
                FROM seen_posts
                WHERE {where}
                ORDER BY first_seen_at DESC
                LIMIT 100
                """,
                params,
            ):
                text = row["text"] or ""
                rows.append(
                    {
                        "kind": "x_post",
                        "source": row["source"],
                        "source_id": row["source"],
                        "id": row["post_id"],
                        "title": text.splitlines()[0][:120] if text else f"X post {row['post_id']}",
                        "summary": text[:300],
                        "url": row["url"] or "",
                        "published_at": normalize_time(row["published_at"]),
                        "seen_at": normalize_time(row["first_seen_at"]),
                        "importance": "",
                        "classification": "",
                        "push": row["delivery_status"] == "sent",
                        "delivery_status": row["delivery_status"] or "",
                        "baseline_only": False,
                    }
                )
        if table_exists(conn, "jygs_events"):
            jygs_time_field = event_time_field(
                basis=time_basis,
                seen_field="first_seen_at",
                published_field="trade_date",
            )
            for row in conn.execute(
                f"""
                SELECT id, trade_date, run_slot, symbol, name, themes, reason, url, first_seen_at
                FROM jygs_events
                WHERE datetime({jygs_time_field}) >= datetime(?)
                  AND datetime({jygs_time_field}) < datetime(?)
                ORDER BY first_seen_at DESC
                LIMIT 100
                """,
                (start_utc, end_utc),
            ):
                rows.append(
                    {
                        "kind": "jygs",
                        "source": f"jygs/{row['run_slot']}",
                        "source_id": f"jygs/{row['run_slot']}",
                        "id": row["id"],
                        "title": f"{row['name']} {row['symbol'] or ''}".strip(),
                        "summary": row["reason"] or row["themes"] or "",
                        "url": row["url"] or "",
                        "published_at": row["trade_date"] or "",
                        "seen_at": normalize_time(row["first_seen_at"]),
                        "importance": "",
                        "classification": "",
                        "push": False,
                        "delivery_status": "",
                        "baseline_only": False,
                    }
                )

    def matches(item: dict[str, Any]) -> bool:
        if source_lower and source_lower not in str(item["source"]).lower() and source_lower not in str(
            item.get("source_id") or ""
        ).lower():
            return False
        if kind_lower and kind_lower != str(item["kind"]).lower():
            return False
        if q_lower:
            hay = json.dumps(item, ensure_ascii=False).lower()
            if q_lower not in hay:
                return False
        return True

    rows = [item for item in rows if matches(item)]
    rows.sort(key=lambda item: displayed_event_time(item, time_basis), reverse=True)
    return rows[: max(1, min(limit, 300))]


def fetch_signal_rows(
    *,
    q: str = "",
    source: str = "",
    symbol: str = "",
    verdict: str = "",
    importance: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    q_lower = q.strip().lower()
    source_lower = source.strip().lower()
    symbol_upper = symbol.strip().upper()
    verdict_lower = verdict.strip().lower()
    importance_lower = importance.strip().lower()
    rows: list[dict[str, Any]] = []
    with connect_sqlite(DEFAULT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if not table_exists(conn, "signals"):
            return []
        for row in conn.execute(
            """
            WITH latest_outcome AS (
                SELECT signal_id, symbol, MAX(as_of_date) AS as_of_date
                FROM signal_outcomes
                GROUP BY signal_id, symbol
            ), latest_review AS (
                SELECT signal_id, COALESCE(symbol, '') AS symbol, MAX(id) AS review_id
                FROM signal_reviews
                GROUP BY signal_id, COALESCE(symbol, '')
            )
            SELECT s.id, s.source, s.title, s.url, s.published_at, s.created_at,
                   s.importance, s.incremental_classification, s.direction,
                   s.confidence, s.thesis,
                   t.id AS target_id, t.symbol, t.name, t.target_role, t.relation_type, t.relation_reason,
                   t.expected_direction, t.confidence AS target_confidence,
                   o.as_of_date, o.return_1d, o.return_3d, o.return_5d, o.return_10d,
                   o.return_20d, o.max_drawdown, o.max_runup, o.volume_change,
                   o.outcome_status,
                   r.review_type, r.verdict, r.error_type, r.review_text, r.lessons_json, r.created_at AS reviewed_at
            FROM signals s
            LEFT JOIN signal_targets t ON t.signal_id = s.id
            LEFT JOIN latest_outcome lo ON lo.signal_id = s.id AND lo.symbol = t.symbol
            LEFT JOIN signal_outcomes o
              ON o.signal_id = lo.signal_id AND o.symbol = lo.symbol AND o.as_of_date = lo.as_of_date
            LEFT JOIN latest_review lr ON lr.signal_id = s.id AND lr.symbol = COALESCE(t.symbol, '')
            LEFT JOIN signal_reviews r ON r.id = lr.review_id
            ORDER BY s.created_at DESC, s.id DESC
            LIMIT 600
            """
        ):
            item = {
                "id": row["id"],
                "target_id": row["target_id"],
                "source": row["source"] or "",
                "title": row["title"] or "",
                "url": row["url"] or "",
                "published_at": normalize_time(row["published_at"]),
                "created_at": normalize_time(row["created_at"]),
                "importance": row["importance"] or "",
                "incremental_classification": row["incremental_classification"] or "",
                "direction": row["direction"] or "",
                "confidence": row["confidence"] or "",
                "thesis": row["thesis"] or "",
                "symbol": row["symbol"] or "",
                "name": row["name"] or "",
                "target_role": row["target_role"] or "",
                "relation_type": row["relation_type"] or "",
                "relation_reason": row["relation_reason"] or "",
                "expected_direction": row["expected_direction"] or "",
                "target_confidence": row["target_confidence"] or "",
                "as_of_date": row["as_of_date"] or "",
                "returns": {
                    "1d": row["return_1d"],
                    "3d": row["return_3d"],
                    "5d": row["return_5d"],
                    "10d": row["return_10d"],
                    "20d": row["return_20d"],
                },
                "max_drawdown": row["max_drawdown"],
                "max_runup": row["max_runup"],
                "volume_change": row["volume_change"],
                "outcome_status": row["outcome_status"] or "",
                "review_type": row["review_type"] or "",
                "verdict": row["verdict"] or "",
                "error_type": row["error_type"] or "",
                "review_text": row["review_text"] or "",
                "lessons_json": row["lessons_json"] or "",
                "reviewed_at": normalize_time(row["reviewed_at"]),
            }
            hay = json.dumps(item, ensure_ascii=False).lower()
            if q_lower and q_lower not in hay:
                continue
            if source_lower and source_lower not in str(item["source"]).lower():
                continue
            if symbol_upper and symbol_upper not in str(item["symbol"]).upper() and symbol_upper not in str(item["name"]).upper():
                continue
            if verdict_lower and verdict_lower != str(item["verdict"]).lower():
                continue
            if importance_lower and importance_lower != str(item["importance"]).lower():
                continue
            rows.append(item)
            if len(rows) >= max(1, min(limit, 300)):
                break
    return rows


def save_signal_feedback(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        signal_id = int(payload.get("signal_id") or 0)
    except (TypeError, ValueError):
        signal_id = 0
    if signal_id <= 0:
        raise HoldingsError("请求缺少有效 signal_id")

    target_id_raw = payload.get("target_id")
    target_id: int | None = None
    if target_id_raw not in (None, ""):
        try:
            target_id = int(target_id_raw)
        except (TypeError, ValueError):
            target_id = None

    verdict = str(payload.get("verdict") or "miss").strip().lower()
    if verdict not in SIGNAL_FEEDBACK_VERDICTS:
        raise HoldingsError("复盘结论无效")

    error_type = str(payload.get("error_type") or "other").strip().lower()
    if error_type not in SIGNAL_FEEDBACK_ERROR_TYPES:
        error_type = "other"

    symbol = str(payload.get("symbol") or "").strip().upper()
    review_text = str(payload.get("review_text") or "").strip()
    if not review_text:
        raise HoldingsError("请填写反馈原因")
    if len(review_text) > 3000:
        raise HoldingsError("反馈原因过长")

    lessons_raw = payload.get("lessons")
    if isinstance(lessons_raw, list):
        lessons = [str(item).strip() for item in lessons_raw if str(item).strip()]
    else:
        lessons = [
            item.strip()
            for item in str(lessons_raw or "").replace("；", "\n").replace(";", "\n").splitlines()
            if item.strip()
        ]
    if not lessons:
        lessons = [review_text]
    lessons = lessons[:8]

    tags_raw = payload.get("tags")
    tags = [str(item).strip() for item in tags_raw if str(item).strip()] if isinstance(tags_raw, list) else []
    now = datetime.now(timezone.utc).isoformat()
    lessons_json = {
        "manual": True,
        "symbol": symbol,
        "target_id": target_id,
        "lessons": lessons,
        "feedback_tags": tags,
        "user_feedback": review_text,
        "created_from": "holdings_web",
    }
    with connect_sqlite(DEFAULT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        existing = conn.execute("SELECT id FROM signals WHERE id = ?", (signal_id,)).fetchone()
        if not existing:
            raise HoldingsError("signal_id 不存在")
        if target_id is None and symbol:
            target_row = conn.execute(
                """
                SELECT id FROM signal_targets
                WHERE signal_id = ? AND UPPER(COALESCE(symbol, '')) = ?
                ORDER BY id DESC LIMIT 1
                """,
                (signal_id, symbol),
            ).fetchone()
            if target_row:
                target_id = int(target_row["id"])
                lessons_json["target_id"] = target_id
        cur = conn.execute(
            """
            INSERT INTO signal_reviews (
                signal_id, target_id, symbol, review_type, verdict, error_type, review_text,
                lessons_json, model, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                target_id,
                symbol,
                "manual",
                verdict,
                error_type,
                review_text,
                json.dumps(lessons_json, ensure_ascii=False, sort_keys=True),
                "human",
                now,
            ),
        )
        conn.commit()
        return {"id": int(cur.lastrowid), "created_at": now}


def fetch_signal_summary() -> dict[str, Any]:
    with connect_sqlite(DEFAULT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cards = [
            {"label": "信号", "value": count_rows(conn, "signals")},
            {"label": "影响标的", "value": count_rows(conn, "signal_targets")},
            {"label": "行情结果", "value": count_rows(conn, "signal_outcomes")},
            {"label": "复盘记录", "value": count_rows(conn, "signal_reviews")},
            {"label": "关系映射", "value": count_rows(conn, "stock_relations", "enabled = 1")},
        ]
        verdicts = grouped_counts(conn, "signal_reviews", "verdict", "1=1", ())
        source_scores: list[dict[str, Any]] = []
        if table_exists(conn, "source_scores"):
            for row in conn.execute(
                """
                SELECT source, window_days, signal_count, hit_rate, false_positive_rate,
                       avg_excess_return, updated_at
                FROM source_scores
                WHERE window_days = 30
                ORDER BY signal_count DESC, hit_rate DESC
                LIMIT 12
                """
            ):
                source_scores.append(
                    {
                        "source": row["source"] or "",
                        "window_days": row["window_days"],
                        "signal_count": row["signal_count"],
                        "hit_rate": row["hit_rate"],
                        "false_positive_rate": row["false_positive_rate"],
                        "avg_excess_return": row["avg_excess_return"],
                        "updated_at": row["updated_at"] or "",
                    }
                )
    return {"cards": cards, "verdicts": verdicts, "source_scores": source_scores}


def fetch_relation_rows(q: str = "", limit: int = 100, enabled: str = "all") -> list[dict[str, Any]]:
    return list_relations(db_path=DEFAULT_DB_PATH, q=q, enabled=enabled, limit=limit)


def relation_snapshot_payload() -> dict[str, Any]:
    exported = export_relations(db_path=DEFAULT_DB_PATH, config_path=STOCK_RELATIONS_CONFIG_PATH)
    return {"snapshot": exported}


def run_relation_backfill(days: int) -> dict[str, Any]:
    safe_days = max(1, min(int(days or 7), 60))
    counts = extract_signals(db_path=DEFAULT_DB_PATH, days=safe_days, dry_run=False)
    return {"days": safe_days, "counts": counts}


def overview_payload(day: str = "") -> dict[str, Any]:
    start_utc, end_utc, display_day = utc_window_for_day(day)
    with connect_sqlite(DEFAULT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        deliveries_failed = count_rows(conn, "deliveries", "sent_at >= ? AND sent_at < ? AND status = 'failed'", (start_utc, end_utc))
        article_failures = 0
        if table_exists(conn, "article_reviews"):
            article_failures = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM article_reviews
                    WHERE created_at >= ? AND created_at < ?
                      AND (reason LIKE '%失败%' OR gate_json LIKE '%error%')
                    """,
                    (start_utc, end_utc),
                ).fetchone()[0]
            )
        cards = [
            {"label": "统一事件", "value": count_rows(conn, "events", "first_seen_at >= ? AND first_seen_at < ?", (start_utc, end_utc))},
            {"label": "文章门控", "value": count_rows(conn, "article_reviews", "created_at >= ? AND created_at < ?", (start_utc, end_utc))},
            {"label": "X 新帖", "value": count_rows(conn, "seen_posts", "first_seen_at >= ? AND first_seen_at < ?", (start_utc, end_utc))},
            {"label": "韭研异动", "value": count_rows(conn, "jygs_events", "first_seen_at >= ? AND first_seen_at < ?", (start_utc, end_utc))},
            {"label": "飞书失败", "value": deliveries_failed + article_failures},
        ]
        by_source = grouped_counts(conn, "events", "source", "first_seen_at >= ? AND first_seen_at < ?", (start_utc, end_utc))
        article_importance = grouped_counts(conn, "article_reviews", "importance", "created_at >= ? AND created_at < ?", (start_utc, end_utc))
        deliveries = grouped_counts(conn, "deliveries", "status", "sent_at >= ? AND sent_at < ?", (start_utc, end_utc))
    return {
        "ok": True,
        "date": display_day,
        "cards": cards,
        "by_source": by_source[:12],
        "article_importance": article_importance,
        "deliveries": deliveries,
        "latest": fetch_events_rows(day=day, limit=10),
    }


def systemctl_show(unit: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["systemctl", "show", unit, "--no-pager"],
            check=False,
            text=True,
            capture_output=True,
            timeout=8,
        )
    except Exception as exc:  # noqa: BLE001
        return {"Id": unit, "error": str(exc)}
    values: dict[str, Any] = {"Id": unit}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in {
            "ActiveState",
            "SubState",
            "Result",
            "ExecMainStatus",
            "ExecMainPID",
            "NRestarts",
            "ExecMainStartTimestamp",
            "NextElapseUSecRealtime",
            "LastTriggerUSec",
            "LoadState",
        }:
            values[key] = value
    if result.returncode != 0:
        values["error"] = result.stderr.strip() or result.stdout.strip()
    return values


def unit_actions(unit: str) -> list[str]:
    if unit not in ALLOWED_SYSTEMD_UNITS:
        return []
    if unit == "surveil-holdings-web.service":
        return ["status"]
    if unit.endswith(".timer"):
        actions = ["restart_timer"]
        if unit in RUN_ONCE_TARGETS:
            actions.append("run_once")
        return actions
    if unit.endswith(".service"):
        return ["restart"]
    return []


def unit_display_metadata(unit: str, values: dict[str, Any]) -> dict[str, Any]:
    meta = dict(UNIT_METADATA.get(unit) or {})
    group = str(meta.get("group") or "other")
    unit_type = str(meta.get("type") or ("定时器" if unit.endswith(".timer") else "服务"))
    if unit in LEGACY_CUTOVER_UNITS:
        lifecycle = "legacy_cutover"
        lifecycle_label = "历史兼容"
        replacement = LEGACY_REPLACEMENTS.get(unit, "")
        default_visible = False
    elif unit in SHADOW_UNITS:
        lifecycle = "shadow"
        lifecycle_label = "影子验证"
        replacement = ""
        default_visible = False
    else:
        lifecycle = "production"
        lifecycle_label = "生产"
        replacement = ""
        default_visible = True
    active = str(values.get("ActiveState") or "")
    sub = str(values.get("SubState") or "")
    result = str(values.get("Result") or "")
    error = str(values.get("error") or "")
    if error:
        status_text = "状态读取异常"
    elif unit.endswith(".timer") and active == "active" and sub == "waiting":
        status_text = "等待下次触发"
    elif unit.endswith(".timer") and active == "inactive":
        status_text = "定时器未启用"
    elif active == "active" and sub == "running":
        status_text = "运行中"
    elif active == "inactive" and sub == "dead" and result == "success":
        status_text = "上次运行成功"
    elif result == "failed" or active == "failed":
        status_text = "运行失败"
    elif active:
        status_text = f"{active}/{sub}".strip("/")
    else:
        status_text = "未知"
    return {
        "group": group,
        "group_label": UNIT_GROUP_LABELS.get(group, UNIT_GROUP_LABELS["other"]),
        "unit_type": unit_type,
        "schedule": str(meta.get("schedule") or ""),
        "status_text": status_text,
        "lifecycle": lifecycle,
        "lifecycle_label": lifecycle_label,
        "replacement": replacement,
        "default_visible": default_visible,
    }


def systemctl_action_command(command: str, target: str) -> list[str]:
    mode = os.getenv("HOLDINGS_WEB_SYSTEMCTL_MODE", "auto").strip().lower()
    if mode == "direct" or (mode == "auto" and hasattr(os, "geteuid") and os.geteuid() == 0):
        return ["systemctl", "--no-block", command, target]
    return ["sudo", "-n", "systemctl", "--no-block", command, target]


def service_action_payload(unit: str, action: str) -> dict[str, Any]:
    unit = str(unit or "").strip()
    action = str(action or "").strip()
    allowed_actions = unit_actions(unit)
    if unit not in ALLOWED_SYSTEMD_UNITS or not allowed_actions:
        raise HoldingsError("不允许操作该 systemd 单元")
    if action not in allowed_actions:
        raise HoldingsError("不允许执行该操作")

    target = unit
    command = ""
    if action == "restart":
        command = "restart"
    elif action == "restart_timer":
        command = "restart"
    elif action == "run_once":
        target = RUN_ONCE_TARGETS.get(unit, "")
        if target not in ALLOWED_SYSTEMD_UNITS:
            raise HoldingsError("没有找到该 timer 对应的 service")
        command = "start"
    elif action == "status":
        return {"unit": unit, "action": action, "state": systemctl_show(unit)}
    else:
        raise HoldingsError("未知操作")

    try:
        args = systemctl_action_command(command, target)
        result = subprocess.run(
            args,
            check=False,
            text=True,
            capture_output=True,
            timeout=12,
        )
    except Exception as exc:  # noqa: BLE001
        raise HoldingsError(f"systemctl 执行失败：{exc}") from exc

    return {
        "unit": unit,
        "target": target,
        "action": action,
        "command": " ".join(args),
        "returncode": result.returncode,
        "stdout": result.stdout.strip()[-2000:],
        "stderr": result.stderr.strip()[-2000:],
        "state": systemctl_show(unit),
        "target_state": systemctl_show(target) if target != unit else {},
    }


def tail_file(path: Path, max_lines: int = 8) -> str:
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:  # noqa: BLE001
        return f"读取失败：{exc}"
    return "\n".join(lines[-max_lines:])


def health_payload() -> dict[str, Any]:
    units = [systemctl_show(unit) for unit in [*SERVICE_UNITS, *TIMER_UNITS]]
    for unit in units:
        unit_id = str(unit.get("Id", ""))
        unit.update(unit_display_metadata(unit_id, unit))
        unit["actions"] = unit_actions(unit_id)
        if unit_id in RUN_ONCE_TARGETS:
            unit["run_once_target"] = RUN_ONCE_TARGETS[unit_id]
    sources: list[dict[str, Any]] = []
    with connect_sqlite(DEFAULT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if table_exists(conn, "source_health"):
            for row in conn.execute(
                """
                SELECT monitor, source, consecutive_failures, last_success_at, last_failure_at,
                       last_error, last_alerted_at, updated_at
                FROM source_health
                ORDER BY consecutive_failures DESC, updated_at DESC
                LIMIT 200
                """
            ):
                sources.append(
                    {
                        "monitor": row["monitor"],
                        "source": row["source"],
                        "status": "failing" if int(row["consecutive_failures"] or 0) else "ok",
                        "consecutive_failures": int(row["consecutive_failures"] or 0),
                        "last_success_at": row["last_success_at"] or "",
                        "last_failure_at": row["last_failure_at"] or "",
                        "last_error": row["last_error"] or "",
                        "last_alerted_at": row["last_alerted_at"] or "",
                        "updated_at": row["updated_at"] or "",
                    }
                )
        if table_exists(conn, "x_stream_health"):
            for row in conn.execute(
                """
                SELECT issue_key, status, failure_count, first_failed_at, last_failed_at,
                       last_error, last_alerted_at, last_recovered_at
                FROM x_stream_health
                ORDER BY CASE WHEN status = 'failing' THEN 0 ELSE 1 END, failure_count DESC, last_failed_at DESC
                LIMIT 80
                """
            ):
                sources.append(
                    {
                        "monitor": "x_stream_detail",
                        "source": row["issue_key"],
                        "status": row["status"] or "",
                        "consecutive_failures": int(row["failure_count"] or 0),
                        "last_success_at": row["last_recovered_at"] or "",
                        "last_failure_at": row["last_failed_at"] or "",
                        "last_error": row["last_error"] or "",
                        "last_alerted_at": row["last_alerted_at"] or "",
                        "updated_at": row["last_failed_at"] or row["last_recovered_at"] or "",
                    }
                )
    logs_dir = ROOT / "logs"
    logs = []
    for name in LOG_FILES:
        tail = tail_file(logs_dir / name)
        if tail:
            logs.append({"name": name, "tail": tail})
    return {"ok": True, "unit_groups": UNIT_GROUP_LABELS, "units": units, "sources": sources, "logs": logs}


def html_page(token_required: bool) -> str:
    token_hint = "需要访问令牌" if token_required else "未配置访问令牌，仅限 SSH 隧道使用"
    environment_label = workbench_environment_label()
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Surveil 工作台</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1f2933;
      --muted: #6b7280;
      --line: #d8dde6;
      --accent: #176b87;
      --danger: #b42318;
      --ok: #0f766e;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }}
    header {{ height: 56px; display: flex; align-items: center; gap: 16px; padding: 0 20px; background: #102a43; color: white; }}
    header h1 {{ font-size: 18px; margin: 0; font-weight: 650; }}
    .environment-label {{ color: #d9f5ec; border-color: #6cc9b2; background: rgba(15, 118, 110, .35); }}
    nav.tabs {{ display: flex; gap: 8px; padding: 10px 20px 0; background: var(--bg); }}
    nav.tabs button {{ background: transparent; border-color: transparent; border-radius: 6px 6px 0 0; }}
    nav.tabs button.active {{ background: white; border-color: var(--line); border-bottom-color: white; color: var(--accent); }}
    main {{ padding: 18px 20px 32px; }}
    .view {{ display: none; }}
    .view.active {{ display: block; }}
    .toolbar {{ display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 14px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }}
    .section-title {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; margin: 0 0 12px; }}
    .section-title h2 {{ margin: 0; font-size: 16px; }}
    .metric-grid {{ display: grid; grid-template-columns: repeat(5, minmax(120px, 1fr)); gap: 10px; margin-bottom: 14px; }}
    .metric {{ background: white; border: 1px solid var(--line); border-radius: 8px; padding: 12px; }}
    .metric .label {{ color: var(--muted); font-size: 12px; }}
    .metric .value {{ font-size: 24px; font-weight: 700; margin-top: 6px; }}
    .split {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; }}
    .list {{ padding: 10px 12px; }}
    .list-row {{ border-bottom: 1px solid var(--line); padding: 9px 0; font-size: 13px; }}
    .list-row:last-child {{ border-bottom: 0; }}
    .badge {{ display: inline-block; border: 1px solid var(--line); border-radius: 999px; padding: 1px 7px; font-size: 12px; color: var(--muted); background: #fbfcfd; }}
    .badge.high {{ color: #9f1239; border-color: #fecdd3; background: #fff1f2; }}
    .badge.medium {{ color: #92400e; border-color: #fed7aa; background: #fff7ed; }}
    .badge.low {{ color: #166534; border-color: #bbf7d0; background: #f0fdf4; }}
    .log {{ white-space: pre-wrap; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; background: #0f172a; color: #dbeafe; padding: 10px; border-radius: 6px; overflow: auto; }}
    .summary {{ color: var(--muted); font-size: 13px; margin-left: auto; }}
    button {{ border: 1px solid var(--line); background: white; color: var(--text); height: 34px; padding: 0 12px; border-radius: 6px; cursor: pointer; font-weight: 550; }}
    button.primary {{ background: var(--accent); color: white; border-color: var(--accent); }}
    button.danger {{ color: var(--danger); border-color: #f1b7b0; }}
    button:disabled {{ opacity: .5; cursor: not-allowed; }}
    input, textarea, select {{ width: 100%; border: 1px solid var(--line); border-radius: 6px; padding: 8px; font: inherit; background: white; }}
    input[type="checkbox"] {{ width: auto; }}
    .source-control {{ height: 30px; padding: 5px 7px; font-size: 12px; }}
    .source-notes {{ min-height: 44px; max-height: 120px; resize: vertical; padding: 6px 7px; font-size: 12px; }}
    .source-checks label {{ display: block; line-height: 1.7; white-space: nowrap; }}
    .source-dirty {{ color: #92400e; font-weight: 650; }}
    table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 8px; vertical-align: top; font-size: 13px; }}
    th {{ text-align: left; background: #eef2f6; color: #334e68; position: sticky; top: 0; z-index: 1; }}
    td.symbol {{ width: 112px; }}
    td.enabled {{ width: 70px; text-align: center; }}
    td.actions {{ width: 82px; text-align: center; }}
    td.sort-cell {{ width: 54px; text-align: center; white-space: nowrap; }}
    td.sort-cell .drag-handle {{ cursor: grab; font-size: 18px; line-height: 1; color: #9fb0c3; user-select: none; }}
    td.sort-cell .drag-handle:active {{ cursor: grabbing; }}
    td.sort-cell .move-btn {{ width: 26px; height: 26px; padding: 0; line-height: 1; border-color: var(--line); background: white; color: #5a6b80; border-radius: 4px; cursor: pointer; font-size: 13px; margin: 0 1px; }}
    td.sort-cell .move-btn:hover {{ background: #eef2f6; }}
    #rows tr.dragging {{ opacity: 0.4; background: #f0f5fa !important; }}
    #rows tr.drag-over-above {{ box-shadow: inset 0 3px 0 0 var(--accent); }}
    #rows tr.drag-over-below {{ box-shadow: inset 0 -3px 0 0 var(--accent); }}
    td.name {{ width: 110px; }}
    td.full {{ width: 170px; }}
    td textarea {{ min-height: 38px; resize: vertical; }}
    .events-table td.summary-cell {{ color: var(--muted); }}
    .events-table a {{ color: var(--accent); text-decoration: none; }}
    .table-wrap {{ max-height: calc(100vh - 190px); overflow: auto; }}
    .status {{ white-space: pre-wrap; font-size: 13px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; background: white; margin-bottom: 12px; display: none; }}
    .status.ok {{ display: block; border-color: #99d6cc; color: var(--ok); }}
    .status.err {{ display: block; border-color: #f1b7b0; color: var(--danger); }}
    .modal-backdrop {{ position: fixed; inset: 0; background: rgba(15, 23, 42, .35); display: none; align-items: center; justify-content: center; padding: 20px; }}
    .modal {{ width: min(760px, 100%); background: white; border-radius: 8px; border: 1px solid var(--line); box-shadow: 0 20px 60px rgba(15, 23, 42, .25); }}
    .modal h2 {{ font-size: 16px; margin: 0; padding: 14px 16px; border-bottom: 1px solid var(--line); }}
    .modal .body {{ padding: 16px; }}
    .modal .foot {{ padding: 12px 16px; border-top: 1px solid var(--line); display: flex; justify-content: flex-end; gap: 10px; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
    .field label {{ display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }}
    .settings-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
    .settings-card {{ background: white; border: 1px solid var(--line); border-radius: 8px; padding: 12px; }}
    .settings-card h3 {{ margin: 0 0 8px; font-size: 15px; }}
    .setting-field {{ margin-top: 9px; }}
    .setting-field label {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 12px; color: var(--muted); margin-bottom: 4px; }}
    .setting-field input {{ height: 34px; }}
    .setting-mask {{ font-size: 12px; color: var(--muted); white-space: nowrap; }}
    .diff {{ max-height: 360px; overflow: auto; border: 1px solid var(--line); border-radius: 6px; padding: 10px; background: #fbfcfd; font-size: 13px; white-space: pre-wrap; }}
    .hint {{ color: var(--muted); font-size: 12px; margin-top: 6px; }}
    @media (max-width: 1000px) {{
      .metric-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .split {{ grid-template-columns: 1fr; }}
      .settings-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Surveil 工作台</h1>
    <span class="badge environment-label">环境：{html.escape(environment_label)}</span>
    <span class="hint">{html.escape(token_hint)}</span>
  </header>
  <nav class="tabs">
    <button id="tab-overview" onclick="showView('overview')">今日总览</button>
    <button id="tab-events" onclick="showView('events')">事件中心</button>
    <button id="tab-signals" onclick="showView('signals')">信号复盘</button>
    <button id="tab-relations" onclick="showView('relations')">关系映射</button>
    <button id="tab-sources" onclick="showView('sources')">信息源</button>
    <button id="tab-health" onclick="showView('health')">任务健康</button>
    <button id="tab-keywords" onclick="showView('keywords')">媒体关键词</button>
    <button id="tab-rules" onclick="showView('rules')">规则中心</button>
    <button id="tab-settings" onclick="showView('settings')">配置中心</button>
    <button id="tab-holdings" onclick="showView('holdings')">持仓管理</button>
  </nav>
  <main>
    <div id="status" class="status"></div>
    <section id="view-overview" class="view">
      <div class="section-title">
        <h2>今日总览</h2>
        <button onclick="loadOverview()">刷新</button>
      </div>
      <div id="overviewMetrics" class="metric-grid"></div>
      <div class="split">
        <section class="panel">
          <div class="list" id="overviewBreakdown"></div>
        </section>
        <section class="panel">
          <div class="list" id="overviewLatest"></div>
        </section>
      </div>
    </section>

    <section id="view-events" class="view">
      <div class="toolbar">
        <input id="eventDate" type="date" style="width:160px">
        <select id="eventTimeBasis" aria-label="日期依据" style="width:170px" onchange="loadEvents()">
          <option value="seen">按采集/处理日期</option>
          <option value="published">按原文发布时间</option>
        </select>
        <select id="eventSource" aria-label="来源过滤" style="width:320px" onchange="loadEvents()">
          <option value="">全部来源</option>
        </select>
        <label class="source-checks"><input id="eventIncludeBaseline" type="checkbox" onchange="loadEvents()"> 显示基线条目</label>
        <input id="eventQuery" placeholder="搜索标题、摘要、标的" style="width:260px">
        <button class="primary" onclick="loadEvents()">查询</button>
      </div>
      <section class="panel">
        <div class="table-wrap">
          <table class="events-table">
            <thead>
              <tr>
                <th id="eventTimeHeader" style="width:150px">采集/处理时间</th>
                <th style="width:130px">来源</th>
                <th style="width:90px">类型</th>
                <th>标题/摘要</th>
                <th style="width:110px">重要性</th>
                <th style="width:130px">状态</th>
              </tr>
            </thead>
            <tbody id="eventRows"></tbody>
          </table>
        </div>
      </section>
    </section>

    <section id="view-signals" class="view">
      <div class="section-title">
        <h2>信号复盘</h2>
        <button onclick="loadSignals()">刷新</button>
      </div>
      <div id="signalMetrics" class="metric-grid"></div>
      <div class="toolbar">
        <input id="signalSource" placeholder="来源" style="width:160px">
        <input id="signalSymbol" placeholder="代码/名称" style="width:160px">
        <select id="signalVerdict" style="width:150px">
          <option value="">全部结论</option>
          <option value="hit">hit</option>
          <option value="partial">partial</option>
          <option value="miss">miss</option>
          <option value="too_early">too_early</option>
          <option value="unverifiable">unverifiable</option>
        </select>
        <select id="signalImportance" style="width:150px">
          <option value="">全部重要性</option>
          <option value="high">high</option>
          <option value="medium">medium</option>
          <option value="low">low</option>
        </select>
        <input id="signalQuery" placeholder="搜索标题、原因、复盘" style="width:260px">
        <button class="primary" onclick="loadSignals()">查询</button>
      </div>
      <section class="panel">
        <div class="table-wrap">
          <table class="events-table">
            <thead>
              <tr>
                <th style="width:90px">结论</th>
                <th style="width:130px">标的</th>
                <th style="width:130px">收益</th>
                <th>信号/复盘</th>
                <th style="width:120px">来源</th>
                <th style="width:120px">关系</th>
                <th style="width:86px">反馈</th>
              </tr>
            </thead>
            <tbody id="signalRows"></tbody>
          </table>
        </div>
      </section>
      <div class="split" style="margin-top:12px">
        <section class="panel">
          <div class="list" id="signalSourceScores"></div>
        </section>
        <section class="panel">
          <div class="list-row" style="padding:10px 12px"><strong>产业链/关联关系</strong></div>
          <div class="toolbar" style="padding:0 12px 10px">
            <input id="relationQuery" placeholder="搜索关系、主题、股票" style="width:260px">
            <button onclick="loadRelations()">查询</button>
          </div>
          <div class="table-wrap" style="max-height:360px">
            <table>
              <thead>
                <tr>
                  <th style="width:120px">触发</th>
                  <th style="width:120px">映射</th>
                  <th style="width:120px">方向</th>
                  <th>原因</th>
                </tr>
              </thead>
              <tbody id="relationRows"></tbody>
            </table>
          </div>
        </section>
      </div>
    </section>

    <section id="view-relations" class="view">
      <div class="section-title">
        <h2>关系映射</h2>
        <div>
          <button onclick="loadRelationManager()">刷新</button>
          <button class="primary" onclick="openRelationModal()">新增关系</button>
        </div>
      </div>
      <div class="toolbar">
        <input id="relationManageQuery" placeholder="搜索起点、终点、主题、原因" style="width:260px">
        <select id="relationManageEnabled" style="width:130px">
          <option value="all">全部</option>
          <option value="enabled">启用</option>
          <option value="disabled">停用</option>
        </select>
        <button class="primary" onclick="loadRelationManager()">查询</button>
        <button onclick="exportRelationJson()">导出 JSON</button>
        <button onclick="importRelationJson()">从 JSON 导入</button>
        <button onclick="diffRelationJson()">检测差异</button>
        <input id="relationBackfillDays" type="number" min="1" max="60" value="7" style="width:86px">
        <button onclick="backfillRelations()">回填最近 N 天</button>
      </div>
      <section class="panel">
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th style="width:80px">状态</th>
                <th style="width:150px">触发</th>
                <th style="width:150px">映射</th>
                <th style="width:130px">方向/强度</th>
                <th>关系与原因</th>
                <th style="width:130px">复盘</th>
                <th style="width:150px">操作</th>
              </tr>
            </thead>
            <tbody id="relationManageRows"></tbody>
          </table>
        </div>
      </section>
      <section class="panel" style="margin-top:12px">
        <div class="list-row" style="padding:10px 12px"><strong>候选关系</strong><span class="summary">大模型或人工沉淀的候选，确认后才正式生效</span></div>
        <div class="toolbar" style="padding:0 12px 10px">
          <select id="relationSuggestionStatus" style="width:140px">
            <option value="pending">待确认</option>
            <option value="accepted">已确认</option>
            <option value="rejected">已拒绝</option>
            <option value="all">全部</option>
          </select>
          <button onclick="loadRelationSuggestions()">刷新候选</button>
        </div>
        <div class="table-wrap" style="max-height:360px">
          <table>
            <thead>
              <tr>
                <th style="width:90px">状态</th>
                <th style="width:150px">触发</th>
                <th style="width:150px">映射</th>
                <th>理由</th>
                <th style="width:130px">操作</th>
              </tr>
            </thead>
            <tbody id="relationSuggestionRows"></tbody>
          </table>
        </div>
      </section>
    </section>

    <section id="view-sources" class="view">
      <div class="section-title">
        <h2>信息源</h2>
        <div>
          <button onclick="loadSourceProfiles()">刷新</button>
          <button id="sourceProfileSaveButton" class="primary" onclick="saveSourceProfiles()" disabled>保存配置</button>
        </div>
      </div>
      <div id="sourceProfileConfigHint" class="status ok" style="display:block">
按 6 类信息抓取模型展示来源。启用、Skeptic、Tavily 覆盖已接入采集运行时；频率和代理暂仅记录。
      </div>
      <div id="sourceProfileMetrics" class="metric-grid"></div>
      <div class="toolbar">
        <select id="sourceProfileCategory" style="width:210px" onchange="renderSourceProfiles()">
          <option value="">全部来源</option>
        </select>
        <input id="sourceProfileQuery" placeholder="搜索来源、管道、服务、说明" style="width:320px" oninput="renderSourceProfiles()">
      </div>
      <section class="panel">
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th style="width:150px">类别</th>
                <th style="width:58px">启用</th>
                <th style="width:210px">来源</th>
                <th style="width:110px">状态</th>
                <th style="width:155px">频率/形态</th>
                <th style="width:170px">管道</th>
                <th style="width:150px">Skeptic/Tavily</th>
                <th>范围/筛选/代理/备注</th>
              </tr>
            </thead>
            <tbody id="sourceProfileRows"></tbody>
          </table>
        </div>
      </section>
    </section>

    <section id="view-health" class="view">
      <div class="section-title">
        <h2>任务健康</h2>
        <button onclick="loadHealth()">刷新</button>
      </div>
      <div class="status ok" style="display:block">
只允许操作 MarketPulseWire 白名单内的 systemd 单元。默认展示生产单元；历史兼容单元已完成切流，通常保持停用。
      </div>
      <div class="toolbar">
        <label><input id="showShadowUnits" type="checkbox" onchange="loadHealth()"> 显示影子任务</label>
        <label><input id="showLegacyUnits" type="checkbox" onchange="loadHealth()"> 显示历史兼容单元</label>
        <span id="healthUnitSummary" class="summary"></span>
      </div>
      <section class="panel">
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Unit</th>
                <th style="width:110px">类型</th>
                <th style="width:130px">状态</th>
                <th style="width:180px">频率/触发</th>
                <th style="width:140px">systemd</th>
                <th style="width:90px">Restarts</th>
                <th style="width:220px">最近启动/触发</th>
                <th style="width:220px">操作</th>
              </tr>
            </thead>
            <tbody id="healthRows"></tbody>
          </table>
        </div>
      </section>
      <section class="panel" style="margin-top:12px">
        <div class="list-row" style="padding:10px 12px"><strong>来源健康</strong></div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th style="width:160px">模块</th>
                <th style="width:210px">来源</th>
                <th style="width:90px">状态</th>
                <th style="width:90px">失败</th>
                <th style="width:180px">最近成功</th>
                <th style="width:180px">最近失败</th>
                <th>错误</th>
              </tr>
            </thead>
            <tbody id="sourceHealthRows"></tbody>
          </table>
        </div>
      </section>
      <div id="healthLogs" style="margin-top:12px"></div>
    </section>

    <section id="view-keywords" class="view">
      <div class="section-title">
        <h2>媒体关键词</h2>
        <div>
          <button onclick="loadKeywords()">刷新</button>
          <button onclick="resetBaseKeywords()">恢复代码默认词</button>
          <button class="primary" onclick="saveKeywords()">保存</button>
        </div>
      </div>
      <div class="split">
        <section class="panel">
          <div class="list">
            <div class="list-row"><strong>基础关键词</strong></div>
            <div class="list-row">
              <textarea id="baseKeywords" style="min-height:260px" placeholder="每行一个基础关键词"></textarea>
              <div class="hint">实际粗筛使用“基础关键词 + 额外包含关键词 - 排除关键词”。基础关键词可编辑；留空保存时会回到代码默认词。</div>
            </div>
            <div class="list-row"><strong>额外包含关键词</strong></div>
            <div class="list-row">
              <textarea id="includeKeywords" style="min-height:220px" placeholder="每行一个关键词，例如：金刚石散热"></textarea>
              <div class="hint">这些词会叠加到基础关键词上；RSS、DIGITIMES、The Elec、日经 xTECH 下一轮轮询会自动生效。</div>
            </div>
            <div class="list-row"><strong>排除关键词</strong></div>
            <div class="list-row">
              <textarea id="excludeKeywords" style="min-height:120px" placeholder="每行一个排除词"></textarea>
              <div class="hint">命中排除词的条目会在进入 LLM 门控前被过滤。</div>
            </div>
          </div>
        </section>
        <section class="panel">
          <div class="list">
            <div class="list-row"><strong>代码默认关键词</strong></div>
            <div class="hint">这是代码内置默认词，用于恢复基线；当前是否覆盖：<span id="baseOverrideStatus">-</span></div>
            <div id="defaultKeywords" class="list-row" style="max-height:560px; overflow:auto"></div>
          </div>
        </section>
      </div>
    </section>

    <section id="view-settings" class="view">
      <div class="section-title">
        <h2>配置中心</h2>
        <div>
          <button onclick="loadSettings()">刷新</button>
          <button class="primary" onclick="saveSettings()">保存</button>
        </div>
      </div>
      <div class="status ok" style="display:block">
敏感配置不会回显明文。敏感输入框留空表示保留现有值；输入新值才会覆盖服务器 .env。
保存后如需立即生效，请按页面提示重启对应服务。
      </div>
      <div id="settingsGrid" class="settings-grid"></div>
      <section class="panel" style="margin-top:12px">
        <div class="section-title">
          <h3 style="margin:0">确定性推送规则</h3>
          <button class="primary" onclick="showView('rules')">打开规则中心</button>
        </div>
        <div class="hint">国际投行、持仓硬变量、核心公司官网、宏观/Fed、SemiAnalysis 和量化产业硬变量规则已集中到规则中心。页面仅开放启停、顺序、白名单和额外词表等安全调整，并提供历史 dry-run 与修改审计。</div>
      </section>
    </section>

    <section id="view-rules" class="view">
      <div class="section-title">
        <h2>规则中心</h2>
        <div>
          <button onclick="loadRuleCenter()">刷新</button>
          <button class="primary" onclick="saveRuleCenter()">保存规则</button>
        </div>
      </div>
      <div id="ruleCenterHint" class="status ok" style="display:block">
规则由代码定义默认边界；本页仅调整安全参数。保存不会发送历史新闻，新的采集轮次会自动读取私有配置。
      </div>
      <div id="ruleCenterMetrics" class="metric-grid"></div>
      <div id="ruleCenterRows"></div>
      <section class="panel" style="margin-top:12px">
        <div class="section-title">
          <h3 style="margin:0">规则 Dry-run</h3>
          <div>
            <input id="ruleSimulationDays" type="number" min="1" max="60" value="7" style="width:80px">
            <button class="primary" onclick="runRuleSimulation()">用最近新闻模拟</button>
          </div>
        </div>
        <div class="hint">只读取数据库中最近的文章和事件，按当前页面已保存的规则运行；不会发飞书、不会改新闻状态。</div>
        <div class="table-wrap" style="margin-top:10px; max-height:420px">
          <table>
            <thead>
              <tr>
                <th style="width:120px">时间</th>
                <th style="width:150px">来源</th>
                <th>新闻与命中规则</th>
              </tr>
            </thead>
            <tbody id="ruleSimulationRows"></tbody>
          </table>
        </div>
      </section>
      <section class="panel" style="margin-top:12px">
        <div class="section-title">
          <h3 style="margin:0">规则修改审计</h3>
          <button onclick="loadRuleAudit()">刷新</button>
        </div>
        <div class="table-wrap" style="max-height:300px">
          <table>
            <thead>
              <tr>
                <th style="width:170px">时间</th>
                <th style="width:120px">操作者</th>
                <th>变化</th>
              </tr>
            </thead>
            <tbody id="ruleAuditRows"></tbody>
          </table>
        </div>
      </section>
    </section>

    <section id="view-holdings" class="view">
      <div class="toolbar">
        <button class="primary" onclick="addRow()">新增</button>
        <button onclick="openBatch()">批量导入</button>
        <button onclick="reloadData()">刷新</button>
        <button class="primary" onclick="previewSave()">保存</button>
        <input id="filter" placeholder="搜索代码、名称、关键词" style="width:260px" oninput="renderTable()">
        <span id="summary" class="summary"></span>
      </div>
      <section class="panel">
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th style="width:54px">排序</th>
                <th style="width:70px">启用</th>
                <th style="width:118px">代码</th>
                <th style="width:120px">简称</th>
                <th style="width:190px">全称</th>
                <th>别名</th>
                <th>业务简介</th>
                <th>关联新闻关键词</th>
                <th>排除关键词</th>
                <th style="width:86px">操作</th>
              </tr>
            </thead>
            <tbody id="rows"></tbody>
          </table>
        </div>
      </section>
    </section>
  </main>

  <div id="batchModal" class="modal-backdrop">
    <div class="modal">
      <h2>批量导入</h2>
      <div class="body">
        <textarea id="batchText" style="min-height:240px" placeholder="每行一只股票，例如：&#10;源杰科技,688498.SH&#10;中际旭创,300308.SZ&#10;长飞光纤"></textarea>
        <div class="hint">支持“名称,代码”或“代码,名称”；只填一个值时会自动判断是否像股票代码。</div>
      </div>
      <div class="foot">
        <button onclick="closeBatch()">取消</button>
        <button class="primary" onclick="applyBatch()">加入列表</button>
      </div>
    </div>
  </div>

  <div id="diffModal" class="modal-backdrop">
    <div class="modal">
      <h2>保存前确认</h2>
      <div class="body">
        <div id="diffText" class="diff"></div>
      </div>
      <div class="foot">
        <button onclick="closeDiff()">取消</button>
        <button class="primary" onclick="confirmSave()">确认保存</button>
      </div>
    </div>
  </div>

  <div id="relationModal" class="modal-backdrop">
    <div class="modal">
      <h2 id="relationModalTitle">编辑关系</h2>
      <div class="body">
        <div class="grid">
          <div class="field"><label>触发代码/主题</label><input id="relSymbol" placeholder="例如 NVDA、HBM、人造钻石散热"></div>
          <div class="field"><label>触发名称</label><input id="relSymbolName" placeholder="例如 NVIDIA、HBM、金刚石散热"></div>
          <div class="field"><label>映射股票代码</label><input id="relRelatedSymbol" placeholder="例如 300308.SZ"></div>
          <div class="field"><label>映射股票名称</label><input id="relRelatedName" placeholder="例如 中际旭创"></div>
          <div class="field"><label>关系类型</label><input id="relRelationType" placeholder="例如 AI optical interconnect supply chain"></div>
          <div class="field"><label>影响方向</label>
            <select id="relImpactDirection">
              <option value="positive">positive</option>
              <option value="negative">negative</option>
              <option value="neutral">neutral</option>
              <option value="uncertain">uncertain</option>
            </select>
          </div>
          <div class="field"><label>主题</label><input id="relTheme" placeholder="例如 光模块/CPO/AI 数据中心"></div>
          <div class="field"><label>置信度</label><input id="relConfidence" placeholder="高 / 中 / 低 或 0-100"></div>
          <div class="field"><label>强度</label><input id="relStrength" placeholder="1-5 或 高/中/低"></div>
          <div class="field"><label>来源</label><input id="relSource" placeholder="web / Serenity / 机构研报 / UP主蒸馏"></div>
          <div class="field"><label>生效日期</label><input id="relValidFrom" type="date"></div>
          <div class="field"><label>失效日期</label><input id="relValidTo" type="date"></div>
        </div>
        <div class="field" style="margin-top:12px"><label>映射原因 / 证据</label><textarea id="relReason" style="min-height:110px" placeholder="说明为什么这个事件会传导到该股票，最好写清一阶/二阶逻辑。"></textarea></div>
        <label style="display:flex; align-items:center; gap:8px; margin-top:10px"><input id="relEnabled" type="checkbox" checked> 启用</label>
      </div>
      <div class="foot">
        <button onclick="closeRelationModal()">取消</button>
        <button class="primary" onclick="saveRelationFromModal()">保存关系</button>
      </div>
    </div>
  </div>

  <div id="signalFeedbackModal" class="modal-backdrop">
    <div class="modal">
      <h2>修正复盘</h2>
      <div class="body">
        <div class="grid">
          <div class="field">
            <label>结论</label>
            <select id="signalFeedbackVerdict">
              <option value="miss">miss</option>
              <option value="partial">partial</option>
              <option value="hit">hit</option>
              <option value="too_early">too_early</option>
              <option value="unverifiable">unverifiable</option>
            </select>
          </div>
          <div class="field">
            <label>错误类型</label>
            <select id="signalFeedbackErrorType">
              <option value="stale_or_price_in">旧闻/已定价</option>
              <option value="counter_supply_news">后续反向消息</option>
              <option value="supply_expansion_bearish">供给扩张利空</option>
              <option value="wrong_relation">关联错误</option>
              <option value="wrong_direction">方向错误</option>
              <option value="timing_error">时点错误</option>
              <option value="low_market_attention">关注度不足</option>
              <option value="quote_unavailable">行情缺失</option>
              <option value="window_not_ready">窗口未到</option>
              <option value="direction_uncertain">方向不明</option>
              <option value="weak_follow_through">持续性不足</option>
              <option value="direction_or_relevance_error">方向或相关性错误</option>
              <option value="timing_or_duration_error">时点或持有期错误</option>
              <option value="none">无错误</option>
              <option value="unverifiable">无法验证</option>
              <option value="other">其他</option>
            </select>
          </div>
        </div>
        <div class="field" style="margin-top:12px">
          <label>反馈原因</label>
          <textarea id="signalFeedbackText" rows="5"></textarea>
        </div>
        <div class="field" style="margin-top:12px">
          <label>经验</label>
          <textarea id="signalFeedbackLessons" rows="4"></textarea>
        </div>
        <div id="signalFeedbackMeta" class="hint"></div>
      </div>
      <div class="foot">
        <button onclick="closeSignalFeedback()">取消</button>
        <button class="primary" onclick="saveSignalFeedback()">保存</button>
      </div>
    </div>
  </div>

<script>
let token = localStorage.getItem('surveil_holdings_token') || '';
let holdings = [];
let pendingPayload = null;
let loadedHoldings = false;
// 拖拽排序时记录被拖动行的原始下标，null 表示当前未拖动。
let dragIndex = null;
let codeDefaultKeywords = [];
let managedRelations = [];
let editingRelationId = null;
let signalRowsCache = [];
let editingSignalFeedback = null;
let sourceProfileCache = {{categories: [], profiles: []}};
let ruleCenterCache = {{rules: []}};
let eventSourceOptionsLoaded = false;

function headers() {{
  const h = {{'Content-Type': 'application/json'}};
  if (token) h['X-Holdings-Token'] = token;
  return h;
}}

async function api(path, options={{}}) {{
  const res = await fetch(path, {{...options, headers: {{...headers(), ...(options.headers || {{}})}}}});
  if (res.status === 401) {{
    token = prompt('请输入 HOLDINGS_WEB_TOKEN') || '';
    localStorage.setItem('surveil_holdings_token', token);
    return api(path, options);
  }}
  const data = await res.json();
  if (!res.ok || data.ok === false) throw new Error(data.error || res.statusText);
  return data;
}}

function showStatus(text, kind='ok') {{
  const el = document.getElementById('status');
  el.className = 'status ' + kind;
  el.textContent = text;
}}

function splitList(value) {{
  return String(value || '').split(/[，,;；\\n]+/).map(s => s.trim()).filter(Boolean);
}}

function joinList(value) {{
  return Array.isArray(value) ? value.join('，') : '';
}}

function escapeHtml(value) {{
  return String(value ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
}}

function badge(value) {{
  const raw = String(value || '').trim();
  if (!raw) return '<span class="badge">-</span>';
  const lower = raw.toLowerCase();
  const cls = ['high', 'medium', 'low'].includes(lower) ? lower : '';
  return `<span class="badge ${{cls}}">${{escapeHtml(raw)}}</span>`;
}}

function serviceActionLabel(action) {{
  const labels = {{
    restart: '重启服务',
    restart_timer: '重启定时器',
    run_once: '立即运行',
    status: '仅查看'
  }};
  return labels[action] || action;
}}

function serviceActionButtons(unit) {{
  const actions = (unit.actions || []).filter(action => action !== 'status');
  if (!actions.length) return '<span class="hint">只读</span>';
  return actions.map(action => `
    <button onclick="runServiceAction('${{escapeHtml(unit.Id || '')}}', '${{escapeHtml(action)}}')">${{escapeHtml(serviceActionLabel(action))}}</button>
  `).join(' ');
}}

function renderHealthUnits(units, groupLabels) {{
  const showShadow = Boolean(document.getElementById('showShadowUnits')?.checked);
  const showLegacy = Boolean(document.getElementById('showLegacyUnits')?.checked);
  const allUnits = units || [];
  const visibleUnits = allUnits.filter(unit => {{
    if (unit.lifecycle === 'shadow' && !showShadow) return false;
    if (unit.lifecycle === 'legacy_cutover' && !showLegacy) return false;
    return true;
  }});
  const hiddenShadow = allUnits.filter(unit => unit.lifecycle === 'shadow').length;
  const hiddenLegacy = allUnits.filter(unit => unit.lifecycle === 'legacy_cutover').length;
  const summary = document.getElementById('healthUnitSummary');
  if (summary) {{
    const parts = [`展示 ${{visibleUnits.length}} / ${{allUnits.length}} 个单元`];
    if (!showShadow && hiddenShadow) parts.push(`隐藏影子 ${{hiddenShadow}} 个`);
    if (!showLegacy && hiddenLegacy) parts.push(`隐藏历史兼容 ${{hiddenLegacy}} 个`);
    summary.textContent = parts.join('；');
  }}
  const order = ['fetching_persistent', 'fetching_scheduled', 'processing_scheduled', 'infrastructure', 'fetching_shadow', 'fetching_legacy', 'other'];
  const byGroup = {{}};
  visibleUnits.forEach(unit => {{
    const group = unit.group || 'other';
    if (!byGroup[group]) byGroup[group] = [];
    byGroup[group].push(unit);
  }});
  const rows = [];
  const orderedGroups = [...order, ...Object.keys(byGroup).filter(group => !order.includes(group))];
  orderedGroups.forEach(group => {{
    const groupUnits = byGroup[group] || [];
    if (!groupUnits.length) return;
    rows.push(`
      <tr>
        <td colspan="8" style="background:#f8fafc; color:#334e68; font-weight:650">
          ${{escapeHtml((groupLabels || {{}})[group] || group)}} <span class="hint">${{groupUnits.length}} 个单元</span>
        </td>
      </tr>
    `);
    groupUnits.forEach(unit => {{
      const rawStatus = [unit.ActiveState || unit.LoadState || '', unit.SubState || '', unit.Result || unit.error || '']
        .filter(Boolean).join(' / ');
      const lifecycle = unit.lifecycle_label ? `<div class="hint">${{escapeHtml(unit.lifecycle_label)}}</div>` : '';
      const replacement = unit.replacement ? `<div class="hint">替代：${{escapeHtml(unit.replacement)}}</div>` : '';
      rows.push(`
        <tr>
          <td>${{escapeHtml(unit.Id || '')}}</td>
          <td>${{escapeHtml(unit.unit_type || '')}}${{lifecycle}}${{replacement}}</td>
          <td>${{badge(unit.status_text || unit.ActiveState || unit.LoadState || '')}}</td>
          <td>${{escapeHtml(unit.schedule || '')}}</td>
          <td>${{escapeHtml(rawStatus)}}</td>
          <td>${{escapeHtml(unit.NRestarts || '')}}</td>
          <td>${{escapeHtml(unit.ExecMainStartTimestamp || unit.LastTriggerUSec || unit.NextElapseUSecRealtime || '')}}</td>
          <td>${{serviceActionButtons(unit)}}</td>
        </tr>
      `);
    }});
  }});
  return rows.join('') || '<tr><td colspan="8">暂无 systemd 单元状态。</td></tr>';
}}

function shortText(value, limit=160) {{
  const text = String(value || '').replace(/\\s+/g, ' ').trim();
  if (text.length <= limit) return text;
  return text.slice(0, limit - 3) + '...';
}}

function formatTime(value) {{
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value).slice(0, 19);
  return d.toLocaleString('zh-CN', {{hour12: false}});
}}

function todayString() {{
  const d = new Date();
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${{year}}-${{month}}-${{day}}`;
}}

function showView(name) {{
  document.querySelectorAll('.view').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('nav.tabs button').forEach(el => el.classList.remove('active'));
  document.getElementById(`view-${{name}}`).classList.add('active');
  document.getElementById(`tab-${{name}}`).classList.add('active');
  if (name === 'overview') loadOverview();
  if (name === 'events') loadEventsView();
  if (name === 'signals') loadSignals();
  if (name === 'relations') loadRelationManager();
  if (name === 'sources') loadSourceProfiles();
  if (name === 'health') loadHealth();
  if (name === 'keywords') loadKeywords();
  if (name === 'rules') loadRuleCenter();
  if (name === 'settings') {{
    loadSettings();
  }}
  if (name === 'holdings' && !loadedHoldings) reloadData();
}}

function formatPct(value) {{
  if (value === null || value === undefined || value === '') return '-';
  const num = Number(value);
  if (Number.isNaN(num)) return String(value);
  return `${{num.toFixed(2)}}%`;
}}

function formatRate(value) {{
  if (value === null || value === undefined || value === '') return '-';
  const num = Number(value);
  if (Number.isNaN(num)) return String(value);
  return `${{(num * 100).toFixed(0)}}%`;
}}

function eventSourceFilterValue(profile) {{
  if (profile.id === 'x_serenity') return 'x:serenity';
  return String(profile.id || '').trim();
}}

async function loadEventSourceOptions() {{
  if (eventSourceOptionsLoaded) return;
  const select = document.getElementById('eventSource');
  const selected = select.value;
  let data = sourceProfileCache;
  if (!Array.isArray(data.profiles) || !data.profiles.length) {{
    data = await api('/api/source-profiles');
    sourceProfileCache = data;
  }}
  const groups = new Map();
  (data.profiles || []).forEach(profile => {{
    const value = eventSourceFilterValue(profile);
    if (!value) return;
    const label = profile.category_label || '其他来源';
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push({{value, profile}});
  }});
  select.replaceChildren();
  const all = document.createElement('option');
  all.value = '';
  all.textContent = '全部来源';
  select.appendChild(all);
  groups.forEach((items, label) => {{
    const group = document.createElement('optgroup');
    group.label = label;
    items.forEach(({{value, profile}}) => {{
      const option = document.createElement('option');
      option.value = value;
      option.textContent = `${{profile.name || value}}（${{value}}）${{profile.enabled === false ? ' - 已停用' : ''}}`;
      group.appendChild(option);
    }});
    select.appendChild(group);
  }});
  if ([...select.options].some(option => option.value === selected)) {{
    select.value = selected;
  }}
  eventSourceOptionsLoaded = true;
}}

async function loadEventsView() {{
  try {{
    await loadEventSourceOptions();
  }} catch (err) {{
    showStatus(`来源下拉加载失败：${{err.message}}`, 'err');
  }}
  await loadEvents();
}}

async function loadOverview() {{
  try {{
    const data = await api('/api/overview');
    const metrics = document.getElementById('overviewMetrics');
    metrics.innerHTML = (data.cards || []).map(item => `
      <div class="metric">
        <div class="label">${{escapeHtml(item.label)}}</div>
        <div class="value">${{escapeHtml(item.value)}}</div>
      </div>
    `).join('');
    const breakdown = [];
    breakdown.push('<div class="list-row"><strong>来源分布</strong></div>');
    (data.by_source || []).forEach(item => breakdown.push(`<div class="list-row">${{escapeHtml(item.key)}} <span class="summary">${{item.count}}</span></div>`));
    breakdown.push('<div class="list-row"><strong>文章重要性</strong></div>');
    (data.article_importance || []).forEach(item => breakdown.push(`<div class="list-row">${{badge(item.key)}} <span class="summary">${{item.count}}</span></div>`));
    breakdown.push('<div class="list-row"><strong>飞书状态</strong></div>');
    (data.deliveries || []).forEach(item => breakdown.push(`<div class="list-row">${{escapeHtml(item.key)}} <span class="summary">${{item.count}}</span></div>`));
    document.getElementById('overviewBreakdown').innerHTML = breakdown.join('') || '<div class="list-row">暂无统计。</div>';
    document.getElementById('overviewLatest').innerHTML = ['<div class="list-row"><strong>最近事件</strong></div>', ...(data.latest || []).map(item => `
      <div class="list-row">
        <div>${{badge(item.importance)}} <strong>${{escapeHtml(shortText(item.title, 120))}}</strong></div>
        <div class="hint">${{escapeHtml(item.source)}} / ${{escapeHtml(item.kind)}} / ${{formatTime(item.seen_at)}}</div>
      </div>
    `)].join('');
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function loadEvents() {{
  try {{
    const params = new URLSearchParams();
    const date = document.getElementById('eventDate').value;
    const timeBasis = document.getElementById('eventTimeBasis').value;
    const source = document.getElementById('eventSource').value.trim();
    const q = document.getElementById('eventQuery').value.trim();
    if (date) params.set('date', date);
    if (timeBasis !== 'seen') params.set('time_basis', timeBasis);
    if (source) params.set('source', source);
    if (q) params.set('q', q);
    if (document.getElementById('eventIncludeBaseline').checked) params.set('include_baseline', '1');
    const data = await api('/api/events?' + params.toString());
    document.getElementById('eventTimeHeader').textContent = timeBasis === 'published' ? '原文发布时间' : '采集/处理时间';
    const rows = document.getElementById('eventRows');
    rows.innerHTML = (data.events || []).map(item => `
      <tr>
        <td>${{formatTime(timeBasis === 'published' ? (item.published_at || item.seen_at) : (item.seen_at || item.published_at))}}${{item.published_at && timeBasis !== 'published' ? `<div class="hint">原文：${{formatTime(item.published_at)}}</div>` : ''}}${{item.seen_at && timeBasis === 'published' ? `<div class="hint">采集：${{formatTime(item.seen_at)}}</div>` : ''}}</td>
        <td>${{escapeHtml(item.source || '')}}</td>
        <td>${{escapeHtml(item.kind || '')}}${{item.baseline_only ? '<div class="hint">基线</div>' : ''}}</td>
        <td class="summary-cell">
          <div><strong>${{item.url ? `<a href="${{escapeHtml(item.url)}}" target="_blank" rel="noreferrer">${{escapeHtml(item.title || '')}}</a>` : escapeHtml(item.title || '')}}</strong></div>
          <div>${{escapeHtml(shortText(item.summary || '', 220))}}</div>
        </td>
        <td>${{badge(item.importance)}}<div class="hint">${{escapeHtml(item.classification || '')}}</div></td>
        <td>${{escapeHtml(item.delivery_status || '')}}${{item.push ? '<div class="hint">push</div>' : ''}}</td>
      </tr>
    `).join('') || '<tr><td colspan="6">没有匹配事件。</td></tr>';
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function loadSignals() {{
  try {{
    const params = new URLSearchParams();
    const source = document.getElementById('signalSource').value.trim();
    const symbol = document.getElementById('signalSymbol').value.trim();
    const verdict = document.getElementById('signalVerdict').value.trim();
    const importance = document.getElementById('signalImportance').value.trim();
    const q = document.getElementById('signalQuery').value.trim();
    if (source) params.set('source', source);
    if (symbol) params.set('symbol', symbol);
    if (verdict) params.set('verdict', verdict);
    if (importance) params.set('importance', importance);
    if (q) params.set('q', q);
    const data = await api('/api/signals?' + params.toString());
    document.getElementById('signalMetrics').innerHTML = ((data.summary || {{}}).cards || []).map(item => `
      <div class="metric">
        <div class="label">${{escapeHtml(item.label)}}</div>
        <div class="value">${{escapeHtml(item.value)}}</div>
      </div>
    `).join('');
    signalRowsCache = data.signals || [];
    document.getElementById('signalRows').innerHTML = signalRowsCache.map((item, index) => {{
      const returns = item.returns || {{}};
      const returnText = [`1d ${{formatPct(returns['1d'])}}`, `3d ${{formatPct(returns['3d'])}}`, `5d ${{formatPct(returns['5d'])}}`].join('<br>');
      const title = item.url ? `<a href="${{escapeHtml(item.url)}}" target="_blank" rel="noreferrer">${{escapeHtml(item.title || '')}}</a>` : escapeHtml(item.title || '');
      return `
        <tr>
          <td>${{badge(item.verdict || item.outcome_status || '-')}}<div class="hint">${{escapeHtml(item.error_type || '')}}</div><div class="hint">${{escapeHtml(item.review_type || '')}}</div></td>
          <td><strong>${{escapeHtml(item.symbol || item.name || '-')}}</strong><div class="hint">${{escapeHtml(item.name || '')}}</div></td>
          <td>${{returnText}}<div class="hint">runup ${{formatPct(item.max_runup)}} / dd ${{formatPct(item.max_drawdown)}}</div></td>
          <td class="summary-cell">
            <div><strong>${{title}}</strong></div>
            <div>${{escapeHtml(shortText(item.thesis || '', 180))}}</div>
            <div class="hint">${{escapeHtml(shortText(item.review_text || '', 220))}}</div>
          </td>
          <td>${{escapeHtml(item.source || '')}}<div>${{badge(item.importance || '')}}</div><div class="hint">${{formatTime(item.created_at)}}</div></td>
          <td>${{escapeHtml(item.target_role || '')}}<div class="hint">${{escapeHtml(shortText(item.relation_type || item.relation_reason || '', 120))}}</div></td>
          <td><button onclick="openSignalFeedback(${{index}})">修正</button></td>
        </tr>
      `;
    }}).join('') || '<tr><td colspan="7">没有匹配信号。</td></tr>';
    const scores = ((data.summary || {{}}).source_scores || []);
    document.getElementById('signalSourceScores').innerHTML = ['<div class="list-row"><strong>来源评分（近 30 日）</strong></div>', ...scores.map(item => `
      <div class="list-row">
        <strong>${{escapeHtml(item.source || '')}}</strong>
        <span class="summary">样本 ${{item.signal_count}} / 命中 ${{formatRate(item.hit_rate)}} / 未兑现 ${{formatRate(item.false_positive_rate)}}</span>
        <div class="hint">平均方向收益：${{escapeHtml(item.avg_excess_return ?? '-')}}</div>
      </div>
    `)].join('');
    await loadRelations();
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

function openSignalFeedback(index) {{
  const item = signalRowsCache[index];
  if (!item) return;
  editingSignalFeedback = item;
  document.getElementById('signalFeedbackVerdict').value = item.verdict || 'miss';
  document.getElementById('signalFeedbackErrorType').value = item.error_type || 'stale_or_price_in';
  document.getElementById('signalFeedbackText').value = item.review_text || '';
  let lessons = '';
  try {{
    const parsed = item.lessons_json ? JSON.parse(item.lessons_json) : {{}};
    if (Array.isArray(parsed.lessons)) lessons = parsed.lessons.join('\\n');
  }} catch (err) {{}}
  document.getElementById('signalFeedbackLessons').value = lessons;
  document.getElementById('signalFeedbackMeta').textContent = `${{item.symbol || '-'}} / ${{item.title || ''}}`;
  document.getElementById('signalFeedbackModal').style.display = 'flex';
}}

function closeSignalFeedback() {{
  editingSignalFeedback = null;
  document.getElementById('signalFeedbackModal').style.display = 'none';
}}

async function saveSignalFeedback() {{
  if (!editingSignalFeedback) return;
  try {{
    const payload = {{
      signal_id: editingSignalFeedback.id,
      target_id: editingSignalFeedback.target_id || null,
      symbol: editingSignalFeedback.symbol || '',
      verdict: document.getElementById('signalFeedbackVerdict').value,
      error_type: document.getElementById('signalFeedbackErrorType').value,
      review_text: document.getElementById('signalFeedbackText').value.trim(),
      lessons: document.getElementById('signalFeedbackLessons').value.trim()
    }};
    await api('/api/signal-feedback', {{method: 'POST', body: JSON.stringify(payload)}});
    closeSignalFeedback();
    await loadSignals();
    showStatus('已保存人工复盘反馈。');
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function loadRelations() {{
  try {{
    const params = new URLSearchParams();
    const q = document.getElementById('relationQuery') ? document.getElementById('relationQuery').value.trim() : '';
    if (q) params.set('q', q);
    const data = await api('/api/signal-relations?' + params.toString());
    document.getElementById('relationRows').innerHTML = (data.relations || []).map(item => `
      <tr>
        <td><strong>${{escapeHtml(item.symbol || '')}}</strong><div class="hint">${{escapeHtml(item.symbol_name || '')}}</div></td>
        <td><strong>${{escapeHtml(item.related_symbol || '')}}</strong><div class="hint">${{escapeHtml(item.related_name || '')}}</div></td>
        <td>${{badge(item.impact_direction || '')}}<div class="hint">${{escapeHtml(item.confidence || '')}}</div></td>
        <td class="summary-cell">
          <div>${{escapeHtml(item.relation_type || '')}} / ${{escapeHtml(item.theme || '')}}</div>
          <div class="hint">${{escapeHtml(shortText(item.reason || '', 180))}}</div>
        </td>
      </tr>
    `).join('') || '<tr><td colspan="4">暂无关系配置。可复制 config/stock_relations.example.json 为私有 config/stock_relations.json 后导入。</td></tr>';
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function loadRelationManager() {{
  try {{
    const params = new URLSearchParams();
    const q = document.getElementById('relationManageQuery') ? document.getElementById('relationManageQuery').value.trim() : '';
    const enabled = document.getElementById('relationManageEnabled') ? document.getElementById('relationManageEnabled').value : 'all';
    if (q) params.set('q', q);
    if (enabled) params.set('enabled', enabled);
    const data = await api('/api/relations?' + params.toString());
    managedRelations = data.relations || [];
    document.getElementById('relationManageRows').innerHTML = managedRelations.map(item => `
      <tr>
        <td>${{badge(item.enabled ? '启用' : '停用')}}<div class="hint">${{formatTime(item.updated_at)}}</div></td>
        <td><strong>${{escapeHtml(item.symbol || '')}}</strong><div class="hint">${{escapeHtml(item.symbol_name || '')}}</div></td>
        <td><strong>${{escapeHtml(item.related_symbol || '')}}</strong><div class="hint">${{escapeHtml(item.related_name || '')}}</div></td>
        <td>${{badge(item.impact_direction || '')}}<div class="hint">强度 ${{escapeHtml(item.relation_strength || '-')}} / 置信 ${{escapeHtml(item.confidence || '-')}}</div></td>
        <td class="summary-cell">
          <div>${{escapeHtml(item.relation_type || '')}} / ${{escapeHtml(item.theme || '')}}</div>
          <div class="hint">${{escapeHtml(shortText(item.reason || '', 220))}}</div>
          <div class="hint">${{escapeHtml(item.source || '')}} ${{item.valid_to ? ' / 有效至 ' + escapeHtml(item.valid_to) : ''}}</div>
        </td>
        <td>${{escapeHtml(item.last_review_verdict || '-')}}<div class="hint">hit ${{item.hit_count || 0}} / miss ${{item.miss_count || 0}}</div></td>
        <td>
          <button onclick="editRelation(${{item.id}})">编辑</button>
          <button onclick="toggleRelation(${{item.id}}, ${{item.enabled ? 'false' : 'true'}})">${{item.enabled ? '停用' : '启用'}}</button>
          <button class="danger" onclick="deleteRelationRow(${{item.id}})">删除</button>
        </td>
      </tr>
    `).join('') || '<tr><td colspan="7">暂无关系映射。</td></tr>';
    await loadRelationSuggestions();
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

function clearRelationForm() {{
  editingRelationId = null;
  document.getElementById('relationModalTitle').textContent = '新增关系';
  ['relSymbol','relSymbolName','relRelatedSymbol','relRelatedName','relRelationType','relTheme','relConfidence','relStrength','relSource','relValidFrom','relValidTo','relReason'].forEach(id => {{
    document.getElementById(id).value = '';
  }});
  document.getElementById('relImpactDirection').value = 'positive';
  document.getElementById('relEnabled').checked = true;
}}

function openRelationModal(item=null) {{
  clearRelationForm();
  if (item) {{
    editingRelationId = item.id;
    document.getElementById('relationModalTitle').textContent = '编辑关系';
    document.getElementById('relSymbol').value = item.symbol || '';
    document.getElementById('relSymbolName').value = item.symbol_name || '';
    document.getElementById('relRelatedSymbol').value = item.related_symbol || '';
    document.getElementById('relRelatedName').value = item.related_name || '';
    document.getElementById('relRelationType').value = item.relation_type || '';
    document.getElementById('relImpactDirection').value = item.impact_direction || 'uncertain';
    document.getElementById('relTheme').value = item.theme || '';
    document.getElementById('relConfidence').value = item.confidence || '';
    document.getElementById('relStrength').value = item.relation_strength || '';
    document.getElementById('relSource').value = item.source || 'web';
    document.getElementById('relValidFrom').value = item.valid_from || '';
    document.getElementById('relValidTo').value = item.valid_to || '';
    document.getElementById('relReason').value = item.reason || '';
    document.getElementById('relEnabled').checked = item.enabled !== false;
  }} else {{
    document.getElementById('relSource').value = 'web';
  }}
  document.getElementById('relationModal').style.display = 'flex';
}}

function closeRelationModal() {{
  document.getElementById('relationModal').style.display = 'none';
}}

function editRelation(id) {{
  const item = managedRelations.find(row => Number(row.id) === Number(id));
  if (!item) {{
    showStatus('没有找到这条关系。', 'err');
    return;
  }}
  openRelationModal(item);
}}

function relationFormPayload() {{
  return {{
    symbol: document.getElementById('relSymbol').value.trim(),
    symbol_name: document.getElementById('relSymbolName').value.trim(),
    related_symbol: document.getElementById('relRelatedSymbol').value.trim(),
    related_name: document.getElementById('relRelatedName').value.trim(),
    relation_type: document.getElementById('relRelationType').value.trim() || 'related',
    impact_direction: document.getElementById('relImpactDirection').value.trim(),
    theme: document.getElementById('relTheme').value.trim(),
    confidence: document.getElementById('relConfidence').value.trim(),
    relation_strength: document.getElementById('relStrength').value.trim(),
    source: document.getElementById('relSource').value.trim() || 'web',
    valid_from: document.getElementById('relValidFrom').value.trim(),
    valid_to: document.getElementById('relValidTo').value.trim(),
    reason: document.getElementById('relReason').value.trim(),
    enabled: document.getElementById('relEnabled').checked
  }};
}}

async function saveRelationFromModal() {{
  try {{
    const payload = {{id: editingRelationId, relation: relationFormPayload()}};
    const data = await api('/api/relations/save', {{method: 'POST', body: JSON.stringify(payload)}});
    closeRelationModal();
    await loadRelationManager();
    showStatus(`关系已保存并同步 JSON 快照：${{(data.snapshot || {{}}).path || ''}}`);
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function deleteRelationRow(id) {{
  if (!confirm('确认删除这条关系映射？')) return;
  try {{
    const data = await api('/api/relations/delete', {{method: 'POST', body: JSON.stringify({{id}})}});
    await loadRelationManager();
    showStatus(`关系已删除并同步 JSON 快照：${{(data.snapshot || {{}}).path || ''}}`);
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function toggleRelation(id, enabled) {{
  try {{
    const data = await api('/api/relations/toggle', {{method: 'POST', body: JSON.stringify({{id, enabled}})}});
    await loadRelationManager();
    showStatus(`关系已${{enabled ? '启用' : '停用'}}并同步 JSON 快照：${{(data.snapshot || {{}}).path || ''}}`);
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function exportRelationJson() {{
  try {{
    const data = await api('/api/relations/export', {{method: 'POST', body: JSON.stringify({{}})}});
    showStatus(`已导出 ${{(data.snapshot || {{}}).count || 0}} 条关系到 ${{(data.snapshot || {{}}).path || ''}}`);
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function importRelationJson() {{
  if (!confirm('确认从私有 config/stock_relations.json 导入并覆盖同 key 关系？')) return;
  try {{
    const data = await api('/api/relations/import', {{method: 'POST', body: JSON.stringify({{}})}});
    await loadRelationManager();
    showStatus(`导入完成：读取 ${{data.counts.read}} 条，写入 ${{data.counts.imported}} 条，跳过 ${{data.counts.skipped}} 条。`);
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function diffRelationJson() {{
  try {{
    const data = await api('/api/relations/diff');
    const diff = data.diff || {{}};
    const text = [
      `数据库：${{diff.db_count || 0}} 条`,
      `JSON：${{diff.json_count || 0}} 条`,
      `JSON 无效行：${{diff.invalid_json_rows || 0}}`,
      '',
      `仅数据库存在：${{(diff.only_in_db || []).length}}`,
      JSON.stringify(diff.only_in_db || [], null, 2),
      '',
      `仅 JSON 存在：${{(diff.only_in_json || []).length}}`,
      JSON.stringify(diff.only_in_json || [], null, 2),
      '',
      `内容不同：${{(diff.changed || []).length}}`,
      JSON.stringify(diff.changed || [], null, 2)
    ].join('\\n');
    document.getElementById('diffText').textContent = text;
    document.getElementById('diffModal').style.display = 'flex';
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function backfillRelations() {{
  if (!confirm('确认重跑最近 N 天信号抽取？这会按当前关系映射补充 related_stock。')) return;
  try {{
    const days = Number(document.getElementById('relationBackfillDays').value || 7);
    const data = await api('/api/relations/backfill', {{method: 'POST', body: JSON.stringify({{days}})}});
    showStatus(`回填完成：最近 ${{data.days}} 天，${{JSON.stringify(data.counts)}}`);
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function loadRelationSuggestions() {{
  try {{
    const status = document.getElementById('relationSuggestionStatus') ? document.getElementById('relationSuggestionStatus').value : 'pending';
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    const data = await api('/api/relation-suggestions?' + params.toString());
    document.getElementById('relationSuggestionRows').innerHTML = (data.suggestions || []).map(item => `
      <tr>
        <td>${{badge(item.status || '')}}<div class="hint">${{formatTime(item.updated_at)}}</div></td>
        <td><strong>${{escapeHtml(item.symbol || '')}}</strong><div class="hint">${{escapeHtml(item.symbol_name || '')}}</div></td>
        <td><strong>${{escapeHtml(item.related_symbol || '')}}</strong><div class="hint">${{escapeHtml(item.related_name || '')}}</div></td>
        <td class="summary-cell">
          <div>${{escapeHtml(item.relation_type || '')}} / ${{escapeHtml(item.theme || '')}} / ${{escapeHtml(item.confidence || '')}}</div>
          <div class="hint">${{escapeHtml(shortText(item.reason || '', 220))}}</div>
        </td>
        <td>
          ${{item.status === 'pending' ? `<button onclick="acceptSuggestion(${{item.id}})">确认</button><button class="danger" onclick="rejectSuggestion(${{item.id}})">拒绝</button>` : '-'}}
        </td>
      </tr>
    `).join('') || '<tr><td colspan="5">暂无候选关系。</td></tr>';
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function acceptSuggestion(id) {{
  try {{
    const data = await api('/api/relation-suggestions/accept', {{method: 'POST', body: JSON.stringify({{id}})}});
    await loadRelationManager();
    showStatus(`候选关系已确认并同步 JSON 快照：${{(data.snapshot || {{}}).path || ''}}`);
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function rejectSuggestion(id) {{
  try {{
    await api('/api/relation-suggestions/reject', {{method: 'POST', body: JSON.stringify({{id}})}});
    await loadRelationSuggestions();
    showStatus('候选关系已拒绝。');
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function runServiceAction(unit, action) {{
  const label = serviceActionLabel(action);
  if (!confirm(`确认对 ${{unit}} 执行“${{label}}”？`)) return;
  try {{
    const data = await api('/api/service-action', {{method: 'POST', body: JSON.stringify({{unit, action}})}});
    const targetText = data.target && data.target !== unit ? `，目标 ${{data.target}}` : '';
    showStatus(`${{unit}} 已提交“${{label}}”${{targetText}}。`);
    await loadHealth();
  }} catch (err) {{
    showStatus(err.message, 'err');
    await loadHealth();
  }}
}}

function renderSourceProfileMetrics(categories) {{
  const metrics = document.getElementById('sourceProfileMetrics');
  metrics.innerHTML = (categories || []).map(item => `
    <div class="metric">
      <div class="label">${{escapeHtml(item.label || item.id || '')}}</div>
      <div class="value">${{escapeHtml(item.count || 0)}}</div>
      <div class="hint">${{Number(item.failing || 0) ? '异常 ' + escapeHtml(item.failing) : '运行记录正常/待记录'}}${{Number(item.disabled || 0) ? '；停用 ' + escapeHtml(item.disabled) : ''}}</div>
    </div>
  `).join('');
}}

function renderSourceCategoryOptions(categories) {{
  const select = document.getElementById('sourceProfileCategory');
  const current = select.value;
  select.innerHTML = '<option value="">全部来源</option>' + (categories || []).map(item => `
    <option value="${{escapeHtml(item.id || '')}}">${{escapeHtml(item.label || item.id || '')}}（${{escapeHtml(item.count || 0)}}）</option>
  `).join('');
  select.value = current;
}}

function sourceProfileSearchText(item) {{
  return [
    item.category_label, item.name, item.id, item.source_type, item.fetch_range,
    item.filter_policy, item.frequency, item.runtime_shape, item.pipeline,
    item.fetcher, item.tavily_policy, item.proxy_profile, item.text_length_policy,
    (item.service_units || []).join(' '), item.notes, item.enabled ? 'enabled' : 'disabled'
  ].join(' ').toLowerCase();
}}

function setSourceProfileDirty(isDirty) {{
  sourceProfileCache.dirty = Boolean(isDirty);
  const button = document.getElementById('sourceProfileSaveButton');
  if (button) button.disabled = !sourceProfileCache.dirty;
}}

function updateSourceProfileDraft(el) {{
  const sourceId = el.dataset.sourceId || '';
  const field = el.dataset.field || '';
  const item = (sourceProfileCache.profiles || []).find(profile => profile.id === sourceId);
  if (!item || !field) return;
  item[field] = el.type === 'checkbox' ? Boolean(el.checked) : el.value;
  item._draft_modified = true;
  setSourceProfileDirty(true);
}}

function sourceProfilesForSave() {{
  return (sourceProfileCache.profiles || []).map(item => ({{
    id: item.id,
    enabled: item.enabled !== false,
    frequency: item.frequency || '',
    skeptic_enabled: Boolean(item.skeptic_enabled),
    web_evidence_enabled: Boolean(item.web_evidence_enabled),
    proxy_profile: item.proxy_profile || '',
    notes: item.notes || ''
  }}));
}}

function renderSourceProfiles() {{
  const category = document.getElementById('sourceProfileCategory').value;
  const q = document.getElementById('sourceProfileQuery').value.trim().toLowerCase();
  const rows = (sourceProfileCache.profiles || []).filter(item => {{
    if (category && item.category !== category) return false;
    if (q && !sourceProfileSearchText(item).includes(q)) return false;
    return true;
  }});
  document.getElementById('sourceProfileRows').innerHTML = rows.map(item => {{
    const health = item.health_status === 'unknown' ? '未记录' : item.health_status;
    const healthDetail = item.last_error ? `<div class="hint">${{escapeHtml(shortText(item.last_error, 120))}}</div>` : '';
    const gates = [
      item.skeptic_enabled ? 'Skeptic' : '无 Skeptic',
      item.web_evidence_enabled ? 'Tavily 可触发' : '无 Tavily'
    ].join('<br>');
    const services = (item.service_units || []).map(unit => `<span class="badge">${{escapeHtml(unit)}}</span>`).join(' ');
    const modified = item.config_modified ? '<div class="hint source-dirty">本地覆盖</div>' : '';
    const enabledChecked = item.enabled !== false ? 'checked' : '';
    const skepticChecked = item.skeptic_enabled ? 'checked' : '';
    const evidenceChecked = item.web_evidence_enabled ? 'checked' : '';
    return `
      <tr>
        <td>${{escapeHtml(item.category_label || item.category || '')}}</td>
        <td>
          <input type="checkbox" data-source-id="${{escapeHtml(item.id || '')}}" data-field="enabled" onchange="updateSourceProfileDraft(this)" ${{enabledChecked}}>
          ${{modified}}
        </td>
        <td>
          <strong>${{item.url ? `<a href="${{escapeHtml(item.url)}}" target="_blank" rel="noreferrer">${{escapeHtml(item.name || '')}}</a>` : escapeHtml(item.name || '')}}</strong>
          <div class="hint">${{escapeHtml(item.id || '')}} / ${{escapeHtml(item.source_type || '')}}</div>
          <div class="hint">${{escapeHtml(item.runtime_note || '')}}</div>
        </td>
        <td>${{badge(health)}}<div class="hint">失败 ${{escapeHtml(item.consecutive_failures || 0)}}</div>${{healthDetail}}</td>
        <td>
          <input class="source-control" data-source-id="${{escapeHtml(item.id || '')}}" data-field="frequency" value="${{escapeHtml(item.frequency || '')}}" oninput="updateSourceProfileDraft(this)">
          <div class="hint">${{escapeHtml(item.runtime_shape || '')}}</div>
        </td>
        <td>${{escapeHtml(item.pipeline || '')}}<div class="hint">${{escapeHtml(item.text_length_policy || '')}}</div></td>
        <td>
          <div class="source-checks">
            <label><input type="checkbox" data-source-id="${{escapeHtml(item.id || '')}}" data-field="skeptic_enabled" onchange="updateSourceProfileDraft(this)" ${{skepticChecked}}> Skeptic</label>
            <label><input type="checkbox" data-source-id="${{escapeHtml(item.id || '')}}" data-field="web_evidence_enabled" onchange="updateSourceProfileDraft(this)" ${{evidenceChecked}}> Tavily</label>
          </div>
          <div class="hint">${{gates}}</div>
          <div class="hint">${{escapeHtml(item.tavily_policy || '')}}</div>
        </td>
        <td class="summary-cell">
          <div>${{escapeHtml(item.fetch_range || '')}}</div>
          <div class="hint">${{escapeHtml(item.filter_policy || '')}}</div>
          <div class="hint">${{escapeHtml(item.fetcher || '')}}</div>
          <div class="hint">${{services}}</div>
          <div style="margin-top:6px">
            <input class="source-control" data-source-id="${{escapeHtml(item.id || '')}}" data-field="proxy_profile" value="${{escapeHtml(item.proxy_profile || '')}}" oninput="updateSourceProfileDraft(this)">
          </div>
          <textarea class="source-notes" data-source-id="${{escapeHtml(item.id || '')}}" data-field="notes" oninput="updateSourceProfileDraft(this)">${{escapeHtml(item.notes || '')}}</textarea>
        </td>
      </tr>
    `;
  }}).join('') || '<tr><td colspan="8">没有匹配信息源。</td></tr>';
}}

async function loadSourceProfiles() {{
  try {{
    const data = await api('/api/source-profiles');
    sourceProfileCache = {{
      categories: data.categories || [],
      profiles: data.profiles || [],
      config_path: data.config_path || '',
      config_exists: Boolean(data.config_exists),
      runtime_note: data.runtime_note || '',
      dirty: false
    }};
    renderSourceProfileMetrics(sourceProfileCache.categories);
    renderSourceCategoryOptions(sourceProfileCache.categories);
    renderSourceProfiles();
    setSourceProfileDirty(false);
    const hint = document.getElementById('sourceProfileConfigHint');
    if (hint) {{
      const suffix = sourceProfileCache.config_exists ? '已存在本地覆盖配置' : '尚未保存本地覆盖配置';
      hint.textContent = `${{data.runtime_note || 'enabled/Skeptic/Tavily 覆盖已接入实际采集。'}} 配置文件：${{sourceProfileCache.config_path || '-'}}；${{suffix}}。`;
    }}
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function saveSourceProfiles() {{
  try {{
    const data = await api('/api/source-profiles', {{
      method: 'POST',
      body: JSON.stringify({{profiles: sourceProfilesForSave()}})
    }});
    sourceProfileCache = {{
      categories: data.categories || [],
      profiles: data.profiles || [],
      config_path: data.config_path || '',
      config_exists: Boolean(data.config_exists),
      runtime_note: data.runtime_note || '',
      dirty: false
    }};
    renderSourceProfileMetrics(sourceProfileCache.categories);
    renderSourceCategoryOptions(sourceProfileCache.categories);
    renderSourceProfiles();
    setSourceProfileDirty(false);
    const hint = document.getElementById('sourceProfileConfigHint');
    if (hint) {{
      hint.textContent = `${{data.runtime_note || 'enabled/Skeptic/Tavily 覆盖已接入实际采集。'}} 配置文件：${{sourceProfileCache.config_path || '-'}}；已存在本地覆盖配置。`;
    }}
    const saved = data.save_result || {{}};
    showStatus(`信息源配置已保存：停用 ${{saved.disabled_count || 0}} 个，覆盖 ${{saved.override_count || 0}} 个。启用/Skeptic/Tavily 将由运行时读取；频率/代理暂仅记录。`);
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function loadHealth() {{
  try {{
    const data = await api('/api/health');
    document.getElementById('healthRows').innerHTML = renderHealthUnits(data.units || [], data.unit_groups || {{}});
    document.getElementById('sourceHealthRows').innerHTML = (data.sources || []).map(source => `
      <tr>
        <td>${{escapeHtml(source.monitor || '')}}</td>
        <td>${{escapeHtml(source.source || '')}}</td>
        <td>${{badge(source.status || '')}}</td>
        <td>${{escapeHtml(String(source.consecutive_failures || 0))}}</td>
        <td>${{formatTime(source.last_success_at || '')}}</td>
        <td>${{formatTime(source.last_failure_at || '')}}</td>
        <td class="summary-cell">${{escapeHtml(shortText(source.last_error || '', 180))}}</td>
      </tr>
    `).join('') || '<tr><td colspan="7">暂无来源健康记录。</td></tr>';
    document.getElementById('healthLogs').innerHTML = (data.logs || []).map(log => `
      <section class="panel" style="margin-top:12px">
        <div class="list-row" style="padding:10px 12px"><strong>${{escapeHtml(log.name)}}</strong></div>
        <div class="log">${{escapeHtml(log.tail || '')}}</div>
      </section>
    `).join('');
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

function keywordTextToList(value) {{
  return String(value || '').split(/[，,;；\\n]+/).map(s => s.trim()).filter(Boolean);
}}

function keywordListToText(value) {{
  return Array.isArray(value) ? value.join('\\n') : '';
}}

function sameKeywordList(a, b) {{
  const left = (a || []).map(item => String(item || '').trim()).filter(Boolean);
  const right = (b || []).map(item => String(item || '').trim()).filter(Boolean);
  if (left.length !== right.length) return false;
  return left.every((item, index) => item === right[index]);
}}

async function loadKeywords() {{
  try {{
    const data = await api('/api/media-keywords');
    codeDefaultKeywords = data.code_default_keywords || data.default_keywords || [];
    document.getElementById('baseKeywords').value = keywordListToText(data.base_keywords || data.default_keywords || []);
    document.getElementById('includeKeywords').value = keywordListToText(data.include_keywords || []);
    document.getElementById('excludeKeywords').value = keywordListToText(data.exclude_keywords || []);
    document.getElementById('baseOverrideStatus').textContent = data.base_keywords_overridden ? '已自定义' : '使用代码默认';
    document.getElementById('defaultKeywords').innerHTML = codeDefaultKeywords.map(item => `<span class="badge" style="margin:2px">${{escapeHtml(item)}}</span>`).join('');
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

function resetBaseKeywords() {{
  document.getElementById('baseKeywords').value = keywordListToText(codeDefaultKeywords);
  showStatus('已把基础关键词恢复为代码默认词，点击保存后生效。');
}}

async function saveKeywords() {{
  try {{
    const baseKeywords = keywordTextToList(document.getElementById('baseKeywords').value);
    const payload = {{
      base_keywords: sameKeywordList(baseKeywords, codeDefaultKeywords) ? [] : baseKeywords,
      include_keywords: keywordTextToList(document.getElementById('includeKeywords').value),
      exclude_keywords: keywordTextToList(document.getElementById('excludeKeywords').value)
    }};
    const data = await api('/api/media-keywords', {{method: 'POST', body: JSON.stringify(payload)}});
    codeDefaultKeywords = data.code_default_keywords || data.default_keywords || codeDefaultKeywords;
    document.getElementById('baseKeywords').value = keywordListToText(data.base_keywords || data.default_keywords || []);
    document.getElementById('includeKeywords').value = keywordListToText(data.include_keywords || []);
    document.getElementById('excludeKeywords').value = keywordListToText(data.exclude_keywords || []);
    document.getElementById('baseOverrideStatus').textContent = data.base_keywords_overridden ? '已自定义' : '使用代码默认';
    showStatus(`媒体关键词已保存。基础 ${{(data.base_keywords || data.default_keywords || []).length}} 个，额外包含 ${{(data.include_keywords || []).length}} 个，排除 ${{(data.exclude_keywords || []).length}} 个。`);
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

function ruleFieldId(ruleId, fieldKey) {{
  return `rule-${{ruleId}}-${{fieldKey}}`;
}}

function renderRuleField(rule, field) {{
  const id = ruleFieldId(rule.id, field.key);
  const help = field.help ? `<div class="hint">${{escapeHtml(field.help)}}</div>` : '';
  if (field.type === 'bool') {{
    return `
      <div class="setting-field">
        <label><span>${{escapeHtml(field.label || field.key)}}</span></label>
        <label><input id="${{escapeHtml(id)}}" data-rule-id="${{escapeHtml(rule.id)}}" data-rule-key="${{escapeHtml(field.key)}}" data-rule-type="bool" type="checkbox" ${{field.value ? 'checked' : ''}}> 启用</label>
        ${{help}}
      </div>
    `;
  }}
  if (field.type === 'list') {{
    return `
      <div class="setting-field">
        <label><span>${{escapeHtml(field.label || field.key)}}</span></label>
        <textarea id="${{escapeHtml(id)}}" data-rule-id="${{escapeHtml(rule.id)}}" data-rule-key="${{escapeHtml(field.key)}}" data-rule-type="list" style="min-height:72px" placeholder="每行一个">${{escapeHtml(keywordListToText(field.value || []))}}</textarea>
        ${{help}}
      </div>
    `;
  }}
  return `
    <div class="setting-field">
      <label><span>${{escapeHtml(field.label || field.key)}}</span></label>
      <input id="${{escapeHtml(id)}}" data-rule-id="${{escapeHtml(rule.id)}}" data-rule-key="${{escapeHtml(field.key)}}" data-rule-type="int" type="number" value="${{escapeHtml(field.value ?? '')}}" min="${{escapeHtml(field.min ?? '')}}" max="${{escapeHtml(field.max ?? '')}}">
      ${{help}}
    </div>
  `;
}}

function renderRuleCenter() {{
  const rules = ruleCenterCache.rules || [];
  const total = rules.length;
  const enabled = rules.filter(rule => (rule.fields || []).find(field => field.key === 'enabled')?.value !== false).length;
  const recent = rules.reduce((sum, rule) => sum + Number((rule.stats || {{}}).matches_30d || 0), 0);
  document.getElementById('ruleCenterMetrics').innerHTML = [
    {{label: '硬规则', value: total, hint: '代码定义的确定性规则'}},
    {{label: '当前启用', value: enabled, hint: '可在本页启停'}},
    {{label: '近 30 天命中', value: recent, hint: '按规则命中 JSON 汇总'}}
  ].map(item => `<section class="metric"><div class="label">${{escapeHtml(item.label)}}</div><div class="value">${{escapeHtml(item.value)}}</div><div class="hint">${{escapeHtml(item.hint)}}</div></section>`).join('');
  document.getElementById('ruleCenterRows').innerHTML = rules.map(rule => {{
    const stats = rule.stats || {{}};
    const last = stats.last_match || {{}};
    const fields = rule.fields || [];
    const left = fields.slice(0, Math.ceil(fields.length / 2));
    const right = fields.slice(Math.ceil(fields.length / 2));
    return `
      <section class="panel" style="margin-top:12px">
        <div class="section-title">
          <div>
            <h3 style="margin:0">${{escapeHtml(rule.name || rule.id || '')}}</h3>
            <div class="hint">${{escapeHtml(rule.group || '')}} / ${{escapeHtml(rule.runtime || '')}}</div>
          </div>
          <div>${{badge('近30天 ' + String(stats.matches_30d || 0) + ' 次')}}</div>
        </div>
        <div class="summary-cell">${{escapeHtml(rule.description || '')}}</div>
        <div class="settings-grid" style="margin-top:10px">
          <section class="settings-card">${{left.map(field => renderRuleField(rule, field)).join('')}}</section>
          <section class="settings-card">
            ${{right.map(field => renderRuleField(rule, field)).join('')}}
            <div class="hint" style="margin-top:12px">最近命中：${{last.title ? escapeHtml(shortText(last.title, 160)) : '暂无'}}${{last.published_at ? '；' + escapeHtml(formatTime(last.published_at)) : ''}}</div>
          </section>
        </div>
      </section>
    `;
  }}).join('') || '<section class="panel">暂无规则定义。</section>';
}}

async function loadRuleCenter() {{
  try {{
    const data = await api('/api/rule-center');
    ruleCenterCache = data;
    renderRuleCenter();
    document.getElementById('ruleCenterHint').textContent =
      `${{data.runtime_note || ''}} 私有覆盖：${{data.config_path || '-'}}；${{data.has_local_override ? '已存在覆盖' : '当前使用代码默认'}}。`;
    await loadRuleAudit();
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

function ruleCenterPayloadFromDom() {{
  const rules = {{}};
  (ruleCenterCache.rules || []).forEach(rule => {{ rules[rule.id] = {{}}; }});
  document.querySelectorAll('[data-rule-id][data-rule-key]').forEach(input => {{
    const ruleId = input.dataset.ruleId;
    const key = input.dataset.ruleKey;
    const type = input.dataset.ruleType;
    if (!rules[ruleId]) rules[ruleId] = {{}};
    if (type === 'bool') rules[ruleId][key] = Boolean(input.checked);
    else if (type === 'list') rules[ruleId][key] = keywordTextToList(input.value);
    else rules[ruleId][key] = Number(input.value || 0);
  }});
  return {{rules}};
}}

async function saveRuleCenter() {{
  try {{
    const data = await api('/api/rule-center', {{method: 'POST', body: JSON.stringify(ruleCenterPayloadFromDom())}});
    ruleCenterCache = data;
    renderRuleCenter();
    await loadRuleAudit();
    showStatus('规则中心配置已保存并写入审计记录。新资讯会动态读取，无需重启服务。');
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function loadRuleAudit() {{
  try {{
    const data = await api('/api/rule-center/audit');
    document.getElementById('ruleAuditRows').innerHTML = (data.items || []).map(item => {{
      const changes = (item.changes || []).map(change => {{
        const rule = (ruleCenterCache.rules || []).find(row => row.id === change.rule_id);
        return `<div><strong>${{escapeHtml((rule || {{}}).name || change.rule_id || '')}}</strong>：${{escapeHtml(shortText(JSON.stringify(change.after || {{}}), 220))}}</div>`;
      }}).join('');
      return `<tr><td>${{escapeHtml(formatTime(item.changed_at || ''))}}</td><td>${{escapeHtml(item.actor || '')}}</td><td class="summary-cell">${{changes || '-'}}</td></tr>`;
    }}).join('') || '<tr><td colspan="3">暂无规则修改记录。</td></tr>';
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function runRuleSimulation() {{
  try {{
    const days = Number(document.getElementById('ruleSimulationDays').value || 7);
    const data = await api('/api/rule-center/simulate', {{method: 'POST', body: JSON.stringify({{days}})}});
    document.getElementById('ruleSimulationRows').innerHTML = (data.results || []).map(item => {{
      const matches = (item.matches || []).map(match => `<div><strong>${{escapeHtml(match.name || match.rule_id || '')}}</strong><div class="hint">${{escapeHtml(shortText(match.reason || '', 180))}}</div></div>`).join('');
      return `<tr><td>${{escapeHtml(formatTime(item.published_at || ''))}}</td><td>${{escapeHtml(item.source || '')}}</td><td class="summary-cell"><strong>${{escapeHtml(item.title || '')}}</strong><div style="margin-top:6px">${{matches}}</div></td></tr>`;
    }}).join('') || `<tr><td colspan="3">最近 ${{data.days || days}} 天扫描 ${{data.scanned || 0}} 条，没有命中当前硬规则。</td></tr>`;
    showStatus(`Dry-run 完成：扫描 ${{data.scanned || 0}} 条，命中 ${{data.matched || 0}} 条；未发送飞书。`);
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function loadInvestmentBankThemeRules() {{
  try {{
    const data = await api('/api/investment-bank-theme-rules');
    document.getElementById('investmentBankThemeEnabled').checked = Boolean(data.enabled);
    document.getElementById('investmentBankThemeMinScore').value = data.min_evidence_score || 2;
    document.getElementById('investmentBankThemeDedupDays').value = data.dedup_lookback_days || 14;
    document.getElementById('investmentBankThemeSecondary').checked = Boolean(data.allow_secondary_sources);
    document.getElementById('investmentBankThemeBanks').value = keywordListToText(data.allowed_banks || []);
    document.getElementById('investmentBankThemeKeywords').value = keywordListToText(data.extra_theme_keywords || []);
    document.getElementById('investmentBankThemeActions').value = keywordListToText(data.extra_action_keywords || []);
    document.getElementById('investmentBankThemeRuleHint').textContent =
      `本地配置：${{data.path || '-'}}；${{data.has_local_override ? '已存在本地覆盖' : '当前使用代码默认配置'}}。`;
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function saveInvestmentBankThemeRules() {{
  try {{
    const payload = {{
      enabled: document.getElementById('investmentBankThemeEnabled').checked,
      min_evidence_score: Number(document.getElementById('investmentBankThemeMinScore').value || 2),
      dedup_lookback_days: Number(document.getElementById('investmentBankThemeDedupDays').value || 14),
      allow_secondary_sources: document.getElementById('investmentBankThemeSecondary').checked,
      allowed_banks: keywordTextToList(document.getElementById('investmentBankThemeBanks').value),
      extra_theme_keywords: keywordTextToList(document.getElementById('investmentBankThemeKeywords').value),
      extra_action_keywords: keywordTextToList(document.getElementById('investmentBankThemeActions').value)
    }};
    const data = await api('/api/investment-bank-theme-rules', {{method: 'POST', body: JSON.stringify(payload)}});
    await loadInvestmentBankThemeRules();
    showStatus(`国际投行重大主题策略已保存：最低证据分 ${{data.min_evidence_score}}，去重 ${{data.dedup_lookback_days}} 天。下一条新资讯会自动读取。`);
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function loadSettings() {{
  try {{
    const data = await api('/api/settings');
    const grid = document.getElementById('settingsGrid');
    grid.innerHTML = (data.groups || []).map(group => `
      <section class="settings-card">
        <h3>${{escapeHtml(group.title || group.id || '')}}</h3>
        <div class="hint">${{escapeHtml(group.restart_hint || '')}}</div>
        ${{(group.fields || []).map(field => `
          <div class="setting-field">
            <label>
              <span>${{escapeHtml(field.label || field.key || '')}}</span>
              <span class="setting-mask">${{field.sensitive ? (field.configured ? '已配置 ' + escapeHtml(field.masked || '') : '未配置') : ''}}</span>
            </label>
            <input
              data-setting-key="${{escapeHtml(field.key || '')}}"
              data-sensitive="${{field.sensitive ? '1' : '0'}}"
              value="${{field.sensitive ? '' : escapeHtml(field.value || '')}}"
              placeholder="${{escapeHtml(field.sensitive ? '留空保留现有值；输入新值覆盖' : (field.placeholder || ''))}}"
              autocomplete="off"
            >
            ${{field.help ? `<div class="hint">${{escapeHtml(field.help)}}</div>` : ''}}
          </div>
        `).join('')}}
      </section>
    `).join('');
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

function settingsRestartAdvice(changedItems) {{
  const keys = (changedItems || []).map(item => item.key || '');
  const hasPrefix = prefix => keys.some(key => key.startsWith(prefix));
  const hasAny = names => keys.some(key => names.includes(key));
  const lines = [];
  if (hasPrefix('WEB_EVIDENCE_') || hasAny(['TAVILY_API_KEY', 'BRAVE_SEARCH_API_KEY'])) {{
    lines.push('Tavily/Web Evidence：新 collector 为 timer one-shot，下一轮会读取配置；如需马上验证，在任务健康页立即运行 surveil-research-collector.timer、surveil-official-collector.timer、surveil-news-collector.timer。');
  }}
  if (hasPrefix('LLM_') || hasPrefix('OPENAI_')) {{
    lines.push('大模型配置：重启常驻的 surveil-x-stream.service、surveil-sina-flash.service；研究机构/官网/新闻媒体 collector 下一轮自动读取，也可立即运行对应 timer。');
  }}
  if (hasPrefix('VALUE_DIRECTORY_')) {{
    lines.push('价值目录：下一次每天 08:00 timer 会读取新配置；如需马上验证，在任务健康页立即运行 surveil-value-directory.timer。');
  }}
  if (hasPrefix('X_')) {{
    lines.push('X 配置：重启 surveil-x-stream.service。');
  }}
  if (hasPrefix('SINA_')) {{
    lines.push('新浪配置：重启 surveil-sina-flash.service；可选立即运行 surveil-sina-stock-news.timer。');
  }}
  if (hasPrefix('IFIND_')) {{
    lines.push('iFinD 配置：公告/研报 timer 下一轮自动读取；如需马上验证，立即运行对应 timer 或 smoke service。');
  }}
  if (hasAny(['SURVEIL_HTTP_PROXY', 'HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY'])) {{
    lines.push('代理环境：重启使用代理的常驻服务；collector timer 下一轮自动读取。若修改 mihomo 配置，重启 surveil-proxy.service。');
  }}
  return lines;
}}

async function saveSettings() {{
  try {{
    const values = {{}};
    document.querySelectorAll('[data-setting-key]').forEach(input => {{
      const key = input.dataset.settingKey;
      const sensitive = input.dataset.sensitive === '1';
      const value = input.value.trim();
      if (!key) return;
      if (sensitive && !value) return;
      values[key] = value;
    }});
    const data = await api('/api/settings', {{method: 'POST', body: JSON.stringify({{values}})}});
    const changedItems = data.changed || [];
    const changed = changedItems.map(item => `${{item.key}}: ${{item.old || '<空>'}} -> ${{item.new || '<空>'}}`).join('\\n');
    const advice = settingsRestartAdvice(changedItems);
    await loadSettings();
    showStatus(changed ? `配置已保存：\\n${{changed}}${{advice.length ? '\\n\\n生效建议：\\n- ' + advice.join('\\n- ') : '\\n\\n如需立即生效，请重启对应服务。'}}` : '没有配置变化。');
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

function readRow(row, item={{}}) {{
  return {{
    ...item,
    enabled: row.querySelector('[data-field="enabled"]').checked,
    symbol: row.querySelector('[data-field="symbol"]').value.trim(),
    name: row.querySelector('[data-field="name"]').value.trim(),
    full_name: row.querySelector('[data-field="full_name"]').value.trim(),
    aliases: splitList(row.querySelector('[data-field="aliases"]').value),
    business_summary: row.querySelector('[data-field="business_summary"]').value.trim(),
    news_keywords: splitList(row.querySelector('[data-field="news_keywords"]').value),
    news_exclude_keywords: splitList(row.querySelector('[data-field="news_exclude_keywords"]').value)
  }};
}}

function syncRowsFromDom() {{
  document.querySelectorAll('#rows tr[data-index]').forEach(row => {{
    const index = Number(row.dataset.index);
    if (Number.isInteger(index) && index >= 0 && index < holdings.length) {{
      holdings[index] = readRow(row, holdings[index] || {{}});
    }}
  }});
}}

function currentRows() {{
  syncRowsFromDom();
  return holdings.map(item => ({{
    enabled: item.enabled !== false,
    symbol: String(item.symbol || '').trim(),
    name: String(item.name || '').trim(),
    full_name: String(item.full_name || '').trim(),
    aliases: splitList(Array.isArray(item.aliases) ? item.aliases.join('，') : item.aliases),
    business_summary: String(item.business_summary || '').trim(),
    news_keywords: splitList(Array.isArray(item.news_keywords) ? item.news_keywords.join('，') : item.news_keywords),
    news_exclude_keywords: splitList(Array.isArray(item.news_exclude_keywords) ? item.news_exclude_keywords.join('，') : item.news_exclude_keywords)
  }}));
}}

function renderTable(sync=true) {{
  if (sync) syncRowsFromDom();
  const q = document.getElementById('filter').value.trim().toLowerCase();
  const body = document.getElementById('rows');
  body.innerHTML = '';
  let visible = 0;
  const hasFilter = !!q;
  holdings.forEach((item, index) => {{
    const hay = JSON.stringify(item).toLowerCase();
    if (q && !hay.includes(q)) return;
    visible += 1;
    const tr = document.createElement('tr');
    tr.dataset.index = index;
    // 仅在未过滤时允许拖拽排序，避免过滤状态下拖拽打乱隐藏行的语义。
    tr.draggable = !hasFilter;
    tr.innerHTML = `
      <td class="sort-cell">
        <span class="drag-handle" title="拖动调整顺序"${{hasFilter ? ' style="opacity:0.3"' : ''}}>⠿</span>
        <button class="move-btn" onclick="moveRow(${{index}}, -1)" title="上移">↑</button>
        <button class="move-btn" onclick="moveRow(${{index}}, 1)" title="下移">↓</button>
      </td>
      <td class="enabled"><input data-field="enabled" type="checkbox" ${{item.enabled !== false ? 'checked' : ''}}></td>
      <td class="symbol"><input data-field="symbol" value="${{escapeHtml(item.symbol || '')}}"></td>
      <td class="name"><input data-field="name" value="${{escapeHtml(item.name || '')}}"></td>
      <td class="full"><textarea data-field="full_name">${{escapeHtml(item.full_name || '')}}</textarea></td>
      <td><textarea data-field="aliases">${{escapeHtml(joinList(item.aliases))}}</textarea></td>
      <td><textarea data-field="business_summary">${{escapeHtml(item.business_summary || '')}}</textarea></td>
      <td><textarea data-field="news_keywords">${{escapeHtml(joinList(item.news_keywords))}}</textarea></td>
      <td><textarea data-field="news_exclude_keywords">${{escapeHtml(joinList(item.news_exclude_keywords))}}</textarea></td>
      <td class="actions"><button class="danger" onclick="removeRow(${{index}})">删除</button></td>
    `;
    if (!hasFilter) {{
      tr.addEventListener('dragstart', (ev) => {{
        dragIndex = index;
        tr.classList.add('dragging');
        ev.dataTransfer.effectAllowed = 'move';
      }});
      tr.addEventListener('dragend', () => {{
        tr.classList.remove('dragging');
        clearDragMarkers();
      }});
      tr.addEventListener('dragover', (ev) => {{
        ev.preventDefault();
        ev.dataTransfer.dropEffect = 'move';
        if (dragIndex === null || dragIndex === index) return;
        const rect = tr.getBoundingClientRect();
        const after = (ev.clientY - rect.top) > rect.height / 2;
        clearDragMarkers();
        tr.classList.add(after ? 'drag-over-below' : 'drag-over-above');
      }});
      tr.addEventListener('dragleave', () => {{
        tr.classList.remove('drag-over-above', 'drag-over-below');
      }});
      tr.addEventListener('drop', (ev) => {{
        ev.preventDefault();
        if (dragIndex === null || dragIndex === index) return;
        const rect = tr.getBoundingClientRect();
        const after = (ev.clientY - rect.top) > rect.height / 2;
        reorderHoldings(dragIndex, after ? index + 1 : index);
        clearDragMarkers();
      }});
    }}
    tr.addEventListener('input', () => {{
      holdings[index] = readRow(tr, holdings[index] || {{}});
    }});
    tr.addEventListener('change', () => {{
      holdings[index] = readRow(tr, holdings[index] || {{}});
    }});
    body.appendChild(tr);
  }});
  document.getElementById('summary').textContent = `共 ${{holdings.length}} 只，显示 ${{visible}} 只`;
}}

function clearDragMarkers() {{
  document.querySelectorAll('#rows tr').forEach(tr => {{
    tr.classList.remove('drag-over-above', 'drag-over-below');
  }});
}}

// 把 from 位置的持仓移动到 to 位置（to 是目标插入点的数组下标）。
function reorderHoldings(from, to) {{
  if (from < 0 || from >= holdings.length) return;
  if (to < 0) to = 0;
  if (to > holdings.length) to = holdings.length;
  if (from === to || from + 1 === to) return;
  const moved = holdings.splice(from, 1)[0];
  const insertAt = to > from ? to - 1 : to;
  holdings.splice(insertAt, 0, moved);
  renderTable(false);
}}

function moveRow(index, delta) {{
  syncRowsFromDom();
  const target = index + delta;
  if (target < 0 || target >= holdings.length) return;
  const tmp = holdings[index];
  holdings[index] = holdings[target];
  holdings[target] = tmp;
  renderTable(false);
}}

async function reloadData() {{
  try {{
    const data = await api('/api/holdings');
    holdings = data.holdings || [];
    loadedHoldings = true;
    renderTable(false);
    showStatus('已加载持仓。');
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

function addRow() {{
  syncRowsFromDom();
  holdings.push({{enabled: true, symbol: '', name: '', aliases: [], news_keywords: [], news_exclude_keywords: []}});
  renderTable(false);
}}

function removeRow(index) {{
  if (!confirm('确认删除这只持仓？')) return;
  syncRowsFromDom();
  holdings.splice(index, 1);
  renderTable(false);
}}

function openBatch() {{ document.getElementById('batchModal').style.display = 'flex'; }}
function closeBatch() {{ document.getElementById('batchModal').style.display = 'none'; }}
function closeDiff() {{ document.getElementById('diffModal').style.display = 'none'; }}

function parseBatchLine(line) {{
  const parts = line.split(/[，,\\t]+/).map(s => s.trim()).filter(Boolean);
  if (!parts.length) return null;
  const codeLike = value => /^(\\d{{6}}(\\.(SH|SZ|BJ))?|HK\\d{{1,5}}|0?\\d{{4,5}}\\.HK)$/i.test(value);
  if (parts.length === 1) {{
    const only = parts[0];
    if (codeLike(only)) return {{symbol: only, name: only, enabled: true}};
    return {{symbol: '', name: only, enabled: true}};
  }}
  const [a, b] = parts;
  if (codeLike(a)) return {{symbol: a, name: b, enabled: true}};
  return {{symbol: b, name: a, enabled: true}};
}}

function applyBatch() {{
  syncRowsFromDom();
  const lines = document.getElementById('batchText').value.split(/\\n+/);
  const parsed = lines.map(parseBatchLine).filter(Boolean);
  holdings.push(...parsed);
  document.getElementById('batchText').value = '';
  closeBatch();
  renderTable(false);
}}

async function previewSave() {{
  try {{
    pendingPayload = currentRows();
    const data = await api('/api/preview', {{method: 'POST', body: JSON.stringify({{holdings: pendingPayload}})}});
    // 后端 normalize_holdings_for_save 会通过新浪接口补全缺失的股票代码，
    // 这里用补全后的 holdings 回写数据和表格，让用户在预览阶段就能看到补全结果。
    if (Array.isArray(data.holdings) && data.holdings.length) {{
      holdings = data.holdings;
      pendingPayload = data.holdings;
      renderTable(false);
    }}
    const warnings = (data.warnings || []).map(item => `! ${{item.message || item}}`).join('\\n');
    document.getElementById('diffText').textContent = [warnings ? `校验提醒：\\n${{warnings}}` : '', data.diff_text || '没有变化。'].filter(Boolean).join('\\n\\n');
    document.getElementById('diffModal').style.display = 'flex';
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

async function confirmSave() {{
  try {{
    const data = await api('/api/save', {{method: 'POST', body: JSON.stringify({{holdings: pendingPayload || currentRows()}})}});
    closeDiff();
    showStatus(`保存成功。\\n备份：${{data.backup_path || '无'}}\\n已同步 SQLite：${{data.imported_count}} 只持仓。`);
    holdings = data.holdings || holdings;
    renderTable();
  }} catch (err) {{
    showStatus(err.message, 'err');
  }}
}}

document.getElementById('eventDate').value = todayString();
showView('overview');
</script>
</body>
</html>"""


def diff_text(diff: dict[str, Any]) -> str:
    lines: list[str] = []
    added = diff.get("added") or []
    removed = diff.get("removed") or []
    changed = diff.get("changed") or []
    if added:
        lines.append("新增：")
        for item in added:
            lines.append(f"+ {item.get('symbol', '')} {item.get('name', '')}")
    if removed:
        lines.append("删除：")
        for item in removed:
            lines.append(f"- {item.get('symbol', '')} {item.get('name', '')}")
    if changed:
        lines.append("修改：")
        for item in changed:
            before = item.get("before", {})
            after = item.get("after", {})
            lines.append(f"* {after.get('symbol') or before.get('symbol')} {before.get('name', '')} -> {after.get('name', '')}")
    return "\n".join(lines) or "没有变化。"


class HoldingsHandler(BaseHTTPRequestHandler):
    server_version = "SurveilHoldingsWeb/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    @property
    def token(self) -> str:
        return str(getattr(self.server, "token", ""))

    @property
    def restart_sina_flash(self) -> bool:
        return bool(getattr(self.server, "restart_sina_flash", False))

    def authorized(self) -> bool:
        if not self.token:
            return True
        supplied = self.headers.get("X-Holdings-Token", "")
        if supplied == self.token:
            return True
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        return (qs.get("token") or [""])[0] == self.token

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, content: str) -> None:
        body = content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def require_auth(self) -> bool:
        if self.authorized():
            return True
        self.send_json({"ok": False, "error": "未授权，请输入 HOLDINGS_WEB_TOKEN"}, HTTPStatus.UNAUTHORIZED)
        return False

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_html(html_page(bool(self.token)))
            return
        if parsed.path == "/api/holdings":
            if not self.require_auth():
                return
            try:
                self.send_json({"ok": True, "holdings": normalized_holdings()})
            except Exception as exc:  # noqa: BLE001
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/overview":
            if not self.require_auth():
                return
            try:
                qs = parse_qs(parsed.query)
                self.send_json(overview_payload((qs.get("date") or [""])[0]))
            except Exception as exc:  # noqa: BLE001
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/events":
            if not self.require_auth():
                return
            try:
                qs = parse_qs(parsed.query)
                limit_raw = (qs.get("limit") or ["100"])[0]
                try:
                    limit = int(limit_raw)
                except ValueError:
                    limit = 100
                events = fetch_events_rows(
                    day=(qs.get("date") or [""])[0],
                    source=(qs.get("source") or [""])[0],
                    kind=(qs.get("kind") or [""])[0],
                    q=(qs.get("q") or [""])[0],
                    time_basis=(qs.get("time_basis") or ["seen"])[0],
                    include_baseline=(qs.get("include_baseline") or [""])[0].strip().lower()
                    in {"1", "true", "yes", "on"},
                    limit=limit,
                )
                self.send_json({"ok": True, "events": events})
            except Exception as exc:  # noqa: BLE001
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/signals":
            if not self.require_auth():
                return
            try:
                qs = parse_qs(parsed.query)
                limit_raw = (qs.get("limit") or ["100"])[0]
                try:
                    limit = int(limit_raw)
                except ValueError:
                    limit = 100
                self.send_json(
                    {
                        "ok": True,
                        "summary": fetch_signal_summary(),
                        "signals": fetch_signal_rows(
                            q=(qs.get("q") or [""])[0],
                            source=(qs.get("source") or [""])[0],
                            symbol=(qs.get("symbol") or [""])[0],
                            verdict=(qs.get("verdict") or [""])[0],
                            importance=(qs.get("importance") or [""])[0],
                            limit=limit,
                        ),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/signal-relations":
            if not self.require_auth():
                return
            try:
                qs = parse_qs(parsed.query)
                limit_raw = (qs.get("limit") or ["100"])[0]
                try:
                    limit = int(limit_raw)
                except ValueError:
                    limit = 100
                self.send_json(
                    {
                        "ok": True,
                        "relations": fetch_relation_rows(q=(qs.get("q") or [""])[0], limit=limit),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/relations":
            if not self.require_auth():
                return
            try:
                qs = parse_qs(parsed.query)
                limit_raw = (qs.get("limit") or ["300"])[0]
                try:
                    limit = int(limit_raw)
                except ValueError:
                    limit = 300
                self.send_json(
                    {
                        "ok": True,
                        "relations": fetch_relation_rows(
                            q=(qs.get("q") or [""])[0],
                            enabled=(qs.get("enabled") or ["all"])[0],
                            limit=limit,
                        ),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/relations/diff":
            if not self.require_auth():
                return
            try:
                self.send_json(
                    {
                        "ok": True,
                        "diff": diff_relations(db_path=DEFAULT_DB_PATH, config_path=STOCK_RELATIONS_CONFIG_PATH),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/relation-suggestions":
            if not self.require_auth():
                return
            try:
                qs = parse_qs(parsed.query)
                self.send_json(
                    {
                        "ok": True,
                        "suggestions": list_relation_suggestions(
                            db_path=DEFAULT_DB_PATH,
                            status=(qs.get("status") or ["pending"])[0],
                            limit=100,
                        ),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/health":
            if not self.require_auth():
                return
            try:
                self.send_json(health_payload())
            except Exception as exc:  # noqa: BLE001
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/source-profiles":
            if not self.require_auth():
                return
            try:
                self.send_json(source_profiles_payload(DEFAULT_DB_PATH))
            except Exception as exc:  # noqa: BLE001
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/media-keywords":
            if not self.require_auth():
                return
            try:
                payload = media_keyword_payload()
                payload["ok"] = True
                self.send_json(payload)
            except Exception as exc:  # noqa: BLE001
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/rule-center":
            if not self.require_auth():
                return
            try:
                payload = rule_center_payload(DEFAULT_DB_PATH)
                payload["ok"] = True
                self.send_json(payload)
            except Exception as exc:  # noqa: BLE001
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/rule-center/audit":
            if not self.require_auth():
                return
            try:
                self.send_json({"ok": True, "items": list_rule_audit(db_path=DEFAULT_DB_PATH)})
            except Exception as exc:  # noqa: BLE001
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/investment-bank-theme-rules":
            if not self.require_auth():
                return
            try:
                payload = investment_bank_theme_config_payload()
                payload["ok"] = True
                self.send_json(payload)
            except Exception as exc:  # noqa: BLE001
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/settings":
            if not self.require_auth():
                return
            try:
                payload = settings_payload()
                payload["ok"] = True
                self.send_json(payload)
            except Exception as exc:  # noqa: BLE001
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not self.require_auth():
            return
        parsed = urlparse(self.path)
        try:
            payload = self.read_json()
            if parsed.path == "/api/media-keywords":
                base_keywords = payload.get("base_keywords")
                include_keywords = payload.get("include_keywords")
                exclude_keywords = payload.get("exclude_keywords")
                if not isinstance(include_keywords, list) or not isinstance(exclude_keywords, list):
                    raise HoldingsError("请求缺少 include_keywords / exclude_keywords 数组")
                if base_keywords is not None and not isinstance(base_keywords, list):
                    raise HoldingsError("base_keywords 必须是数组")
                saved = save_media_keyword_config(base_keywords, include_keywords, exclude_keywords)
                saved.update(
                    {
                        "code_default_keywords": media_keyword_payload()["code_default_keywords"],
                        "default_keywords": saved["base_keywords"] or media_keyword_payload()["code_default_keywords"],
                        "base_keywords_overridden": bool(saved["base_keywords"]),
                    }
                )
                saved["ok"] = True
                self.send_json(saved)
                return
            if parsed.path == "/api/rule-center":
                response = save_rule_center_config(payload, db_path=DEFAULT_DB_PATH)
                response["ok"] = True
                self.send_json(response)
                return
            if parsed.path == "/api/rule-center/simulate":
                response = simulate_rules(
                    db_path=DEFAULT_DB_PATH,
                    days=int(payload.get("days") or 7),
                    limit=int(payload.get("limit") or 120),
                )
                response["ok"] = True
                self.send_json(response)
                return
            if parsed.path == "/api/investment-bank-theme-rules":
                saved = save_investment_bank_theme_config(payload)
                saved.update(
                    {
                        "path": str(investment_bank_theme_config_payload()["path"]),
                        "has_local_override": True,
                        "ok": True,
                    }
                )
                self.send_json(saved)
                return
            if parsed.path == "/api/settings":
                values = payload.get("values")
                if not isinstance(values, dict):
                    raise HoldingsError("请求缺少 values 对象")
                saved = save_settings(values)
                saved["ok"] = True
                self.send_json(saved)
                return
            if parsed.path == "/api/source-profiles":
                saved = save_source_profile_config(payload)
                response = source_profiles_payload(DEFAULT_DB_PATH)
                response["save_result"] = saved
                response["ok"] = True
                self.send_json(response)
                return
            if parsed.path == "/api/service-action":
                unit = str(payload.get("unit") or "").strip()
                action = str(payload.get("action") or "").strip()
                response = service_action_payload(unit, action)
                response["ok"] = True
                if int(response.get("returncode") or 0) != 0:
                    error_text = response.get("stderr") or response.get("stdout") or "systemctl 返回非 0"
                    response["ok"] = False
                    response["error"] = str(error_text)
                    self.send_json(response, HTTPStatus.BAD_REQUEST)
                else:
                    self.send_json(response)
                return
            if parsed.path == "/api/signal-feedback":
                saved = save_signal_feedback(payload)
                saved["ok"] = True
                self.send_json(saved)
                return
            if parsed.path == "/api/relations/save":
                relation = payload.get("relation")
                if not isinstance(relation, dict):
                    raise HoldingsError("请求缺少 relation 对象")
                relation_id = payload.get("id")
                saved_relation = save_relation(
                    relation,
                    db_path=DEFAULT_DB_PATH,
                    relation_id=int(relation_id) if relation_id else None,
                )
                response = {"ok": True, "relation": saved_relation}
                response.update(relation_snapshot_payload())
                self.send_json(response)
                return
            if parsed.path == "/api/relations/delete":
                relation_id = int(payload.get("id") or 0)
                if relation_id <= 0:
                    raise HoldingsError("请求缺少有效 id")
                deleted = delete_relation(relation_id=relation_id, db_path=DEFAULT_DB_PATH)
                response = {"ok": True, "deleted": deleted}
                response.update(relation_snapshot_payload())
                self.send_json(response)
                return
            if parsed.path == "/api/relations/toggle":
                relation_id = int(payload.get("id") or 0)
                if relation_id <= 0:
                    raise HoldingsError("请求缺少有效 id")
                enabled = bool(payload.get("enabled"))
                relation = set_relation_enabled(relation_id=relation_id, enabled=enabled, db_path=DEFAULT_DB_PATH)
                response = {"ok": True, "relation": relation}
                response.update(relation_snapshot_payload())
                self.send_json(response)
                return
            if parsed.path == "/api/relations/export":
                response = {"ok": True}
                response.update(relation_snapshot_payload())
                self.send_json(response)
                return
            if parsed.path == "/api/relations/import":
                counts = import_relations(db_path=DEFAULT_DB_PATH, config_path=STOCK_RELATIONS_CONFIG_PATH)
                response = {"ok": True, "counts": counts}
                response.update(relation_snapshot_payload())
                self.send_json(response)
                return
            if parsed.path == "/api/relations/backfill":
                response = {"ok": True}
                response.update(run_relation_backfill(int(payload.get("days") or 7)))
                self.send_json(response)
                return
            if parsed.path == "/api/relation-suggestions/accept":
                suggestion_id = int(payload.get("id") or 0)
                if suggestion_id <= 0:
                    raise HoldingsError("请求缺少有效 id")
                relation = accept_relation_suggestion(suggestion_id=suggestion_id, db_path=DEFAULT_DB_PATH)
                response = {"ok": True, "relation": relation}
                response.update(relation_snapshot_payload())
                self.send_json(response)
                return
            if parsed.path == "/api/relation-suggestions/reject":
                suggestion_id = int(payload.get("id") or 0)
                if suggestion_id <= 0:
                    raise HoldingsError("请求缺少有效 id")
                rejected = reject_relation_suggestion(suggestion_id=suggestion_id, db_path=DEFAULT_DB_PATH)
                self.send_json({"ok": True, "rejected": rejected})
                return
            items = payload.get("holdings")
            if not isinstance(items, list):
                raise HoldingsError("请求缺少 holdings 数组")
            current = normalized_holdings()
            normalized = normalize_holdings_for_save(items, current)
            if parsed.path == "/api/preview":
                warnings = validate_holdings(normalized, verify_remote=True)
                diff = holdings_diff(current, normalized)
                self.send_json(
                    {
                        "ok": True,
                        "diff": diff,
                        "diff_text": diff_text(diff),
                        "holdings": normalized,
                        "warnings": warnings,
                    }
                )
                return
            if parsed.path == "/api/save":
                result = save_holdings(normalized, db_path=DEFAULT_DB_PATH)
                if self.restart_sina_flash:
                    subprocess.run(["systemctl", "restart", "surveil-sina-flash.service"], check=False)
                self.send_json(
                    {
                        "ok": True,
                        "backup_path": str(result.backup_path) if result.backup_path else "",
                        "imported_count": result.imported_count,
                        "changed_count": result.changed_count,
                        "holdings": normalized_holdings(),
                    }
                )
                return
        except Exception as exc:  # noqa: BLE001
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self.send_error(HTTPStatus.NOT_FOUND)


def main() -> int:
    parser = argparse.ArgumentParser(description="Surveil 持仓管理 Web UI")
    parser.add_argument("--host", default=os.getenv("HOLDINGS_WEB_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.getenv("HOLDINGS_WEB_PORT", str(DEFAULT_PORT))))
    args = parser.parse_args()

    load_env(ROOT / ".env")
    host = args.host
    port = args.port
    server = ThreadingHTTPServer((host, port), HoldingsHandler)
    server.token = os.getenv("HOLDINGS_WEB_TOKEN", "").strip()
    server.restart_sina_flash = env_flag("HOLDINGS_WEB_RESTART_SINA_FLASH", False)
    print(f"Surveil holdings web listening on http://{host}:{port}", flush=True)
    if not server.token:
        print("WARNING: HOLDINGS_WEB_TOKEN 未配置。请仅通过 SSH 隧道访问。", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
