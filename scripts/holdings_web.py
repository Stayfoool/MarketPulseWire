#!/usr/bin/env python3
"""Local-only web UI for portfolio holdings management."""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import hmac
import json
import os
import secrets
import sqlite3
import subprocess
import time
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
    HoldingsConflictError,
    HoldingsError,
    holdings_diff,
    holdings_revision,
    normalized_holdings,
    normalize_holdings_for_save,
    remote_validation_symbols,
    save_holdings,
    validate_holdings,
)
from market_db import DEFAULT_DB_PATH
from market_feedback import FEEDBACK_LABELS, feedback_projection_by_item, feedback_quality_payload
from media_keyword_config import media_keyword_payload, save_media_keyword_config
from investment_bank_theme_config import config_payload as investment_bank_theme_config_payload
from investment_bank_theme_config import save_config as save_investment_bank_theme_config
from market_view import article_view_from_row, event_view_from_row, official_view_from_row
from rule_center import list_rule_audit, rule_center_payload, save_rule_center_config, simulate_rules
from rule_shadow_report_store import list_daily_reports, load_daily_report
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
WEB_ROOT = ROOT / "web"
RULE_SHADOW_REPORT_DIR = ROOT / "reports"
WEB_INDEX_PATH = WEB_ROOT / "index.html"
WEB_STATIC_ASSETS = {
    "/static/styles.css": (WEB_ROOT / "styles.css", "text/css; charset=utf-8"),
    "/static/app.js": (WEB_ROOT / "app.js", "text/javascript; charset=utf-8"),
}
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
BJ = ZoneInfo("Asia/Shanghai")
HOLDINGS_PREVIEW_TTL_SECONDS = 15 * 60
_HOLDINGS_PREVIEW_SIGNING_KEY = secrets.token_bytes(32)


def rule_shadow_reports_payload(
    report_date: str = "",
    *,
    report_dir: Path = RULE_SHADOW_REPORT_DIR,
) -> dict[str, Any]:
    reports = list_daily_reports(report_dir)
    selected_date = report_date or (str(reports[0].get("date") or "") if reports else "")
    report = load_daily_report(report_dir, selected_date) if selected_date else None
    if report is not None:
        report = dict(report)
        report.pop("report_dir", None)
        cleaned_items: list[dict[str, Any]] = []
        for item in report.get("items") if isinstance(report.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            cleaned = dict(item)
            cleaned.pop("report_path", None)
            cleaned_items.append(cleaned)
        report["items"] = cleaned_items
    return {
        "ok": True,
        "reports": reports,
        "selected_date": selected_date,
        "report": report,
    }
SYSTEMCTL_SHOW_FIELDS = {
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
}

SERVICE_UNITS = [
    "surveil-x-stream.service",
    "surveil-feishu-feedback.service",
    "surveil-rss-monitor.service",
    "surveil-trendforce-page-monitor.service",
    "surveil-sina-flash.service",
    "surveil-overseas-media.service",
    "surveil-china-media.service",
    "surveil-sina-stock-news.service",
    "surveil-company-disclosures.service",
    "surveil-jygs-actions.service",
    "surveil-article-daily.service",
    "surveil-rule-shadow-daily.service",
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
    "surveil-rule-shadow-daily.timer",
    "surveil-signals-extract.timer",
    "surveil-signal-outcome.timer",
    "surveil-signal-review.timer",
    "surveil-signal-digest.timer",
    "surveil-company-disclosures.timer",
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
    "surveil-rule-shadow-daily.timer": "surveil-rule-shadow-daily.service",
    "surveil-signals-extract.timer": "surveil-signals-extract.service",
    "surveil-signal-outcome.timer": "surveil-signal-outcome.service",
    "surveil-signal-review.timer": "surveil-signal-review.service",
    "surveil-signal-digest.timer": "surveil-signal-digest.service",
    "surveil-company-disclosures.timer": "surveil-company-disclosures.service",
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
    "surveil-company-disclosures.service": {"group": "fetching_scheduled", "type": "定时采集", "schedule": "timer 08:00 / 20:00"},
    "surveil-jygs-actions.service": {
        "group": "fetching_scheduled",
        "type": "定时采集",
        "schedule": "默认停用；legacy product path",
        "health_alert": False,
    },
    "surveil-research-collector.service": {"group": "fetching_scheduled", "type": "定时采集", "schedule": "timer 每 5 分钟；页面源内部 15 分钟"},
    "surveil-official-collector.service": {"group": "fetching_scheduled", "type": "定时采集", "schedule": "timer 每 10 分钟"},
    "surveil-news-collector.service": {"group": "fetching_scheduled", "type": "定时采集", "schedule": "timer 每 2 分钟"},
    "surveil-value-directory.service": {"group": "fetching_scheduled", "type": "定时采集", "schedule": "timer 每天 08:00；需先完成服务器浏览器登录"},
    "surveil-research-collector-shadow.service": {"group": "fetching_shadow", "type": "影子采集", "schedule": "timer 每 15 分钟"},
    "surveil-official-collector-shadow.service": {"group": "fetching_shadow", "type": "影子采集", "schedule": "timer 每 30 分钟"},
    "surveil-news-collector-shadow.service": {"group": "fetching_shadow", "type": "影子采集", "schedule": "timer 每 10 分钟"},
    "surveil-collector-shadow-digest.service": {"group": "processing_scheduled", "type": "影子报告", "schedule": "timer 21:05"},
    "surveil-article-daily.service": {"group": "processing_scheduled", "type": "定时处理", "schedule": "timer 20:50"},
    "surveil-rule-shadow-daily.service": {"group": "processing_scheduled", "type": "规则对比报告", "schedule": "每天 15:30 北京时间"},
    "surveil-signals-extract.service": {"group": "processing_scheduled", "type": "定时处理", "schedule": "timer 每 10 分钟"},
    "surveil-signal-outcome.service": {"group": "processing_scheduled", "type": "定时处理", "schedule": "timer 交易日 16:20"},
    "surveil-signal-review.service": {"group": "processing_scheduled", "type": "定时处理", "schedule": "timer 交易日 16:35"},
    "surveil-signal-digest.service": {"group": "processing_scheduled", "type": "定时处理", "schedule": "timer 20:35"},
    "surveil-holdings-web.service": {"group": "infrastructure", "type": "基础设施", "schedule": "Web 工作台"},
    "surveil-feishu-feedback.service": {"group": "infrastructure", "type": "基础设施", "schedule": "飞书长连接"},
    "surveil-proxy.service": {"group": "infrastructure", "type": "基础设施", "schedule": "本地代理"},
    "surveil-sina-stock-news.timer": {"group": "fetching_scheduled", "type": "定时器", "schedule": "每 30 分钟"},
    "surveil-overseas-media.timer": {"group": "fetching_legacy", "type": "历史兼容定时器", "schedule": "已切流；旧每 5 分钟"},
    "surveil-china-media.timer": {"group": "fetching_legacy", "type": "历史兼容定时器", "schedule": "已切流；旧每 2 分钟"},
    "surveil-company-disclosures.timer": {"group": "fetching_scheduled", "type": "定时器", "schedule": "08:00 / 20:00"},
    "surveil-jygs-actions.timer": {
        "group": "fetching_scheduled",
        "type": "定时器",
        "schedule": "默认停用；legacy product path",
        "health_alert": False,
    },
    "surveil-research-collector.timer": {"group": "fetching_scheduled", "type": "定时器", "schedule": "每 5 分钟"},
    "surveil-official-collector.timer": {"group": "fetching_scheduled", "type": "定时器", "schedule": "每 10 分钟"},
    "surveil-news-collector.timer": {"group": "fetching_scheduled", "type": "定时器", "schedule": "每 2 分钟"},
    "surveil-value-directory.timer": {"group": "fetching_scheduled", "type": "定时器", "schedule": "每天 08:00；默认需人工启用"},
    "surveil-research-collector-shadow.timer": {"group": "fetching_shadow", "type": "影子定时器", "schedule": "每 15 分钟"},
    "surveil-official-collector-shadow.timer": {"group": "fetching_shadow", "type": "影子定时器", "schedule": "每 30 分钟"},
    "surveil-news-collector-shadow.timer": {"group": "fetching_shadow", "type": "影子定时器", "schedule": "每 10 分钟"},
    "surveil-collector-shadow-digest.timer": {"group": "processing_scheduled", "type": "影子报告定时器", "schedule": "21:05"},
    "surveil-article-daily.timer": {"group": "processing_scheduled", "type": "定时器", "schedule": "20:50"},
    "surveil-rule-shadow-daily.timer": {"group": "processing_scheduled", "type": "定时器", "schedule": "每天 15:30 北京时间"},
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

UNIT_TASK_LABELS = {
    "surveil-x-stream": "X / Serenity",
    "surveil-feishu-feedback": "飞书反馈",
    "surveil-sina-flash": "新浪财经快讯",
    "surveil-sina-stock-news": "新浪持仓个股新闻",
    "surveil-company-disclosures": "公司公告 / 巨潮资讯",
    "surveil-jygs-actions": "韭研公社异动",
    "surveil-research-collector": "研究机构 / 行业媒体采集",
    "surveil-official-collector": "公司官网采集",
    "surveil-news-collector": "新闻媒体采集",
    "surveil-value-directory": "价值目录",
    "surveil-article-daily": "文章日报",
    "surveil-rule-shadow-daily": "新旧规则对比日报",
    "surveil-signals-extract": "信号提取",
    "surveil-signal-outcome": "信号结果更新",
    "surveil-signal-review": "信号复盘",
    "surveil-signal-digest": "信号摘要",
    "surveil-holdings-web": "Surveil 工作台",
    "surveil-proxy": "网络代理",
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
    "rule-shadow-daily.err.log",
    "sina-flash.err.log",
    "sina-stock-news.err.log",
    "company-disclosures.err.log",
    "jygs-actions.err.log",
    "holdings-web.err.log",
    "feishu-feedback.err.log",
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


def utc_window_for_range(start_day: str = "", end_day: str = "") -> tuple[str, str, str, str]:
    start_value = str(start_day or "").strip()
    end_value = str(end_day or "").strip()
    if not start_value and not end_value:
        today = datetime.now(BJ).strftime("%Y-%m-%d")
        start_value = end_value = today
    elif not start_value or not end_value:
        raise ValueError("开始日期和结束日期必须同时填写")
    try:
        start_local = datetime.strptime(start_value, "%Y-%m-%d").replace(tzinfo=BJ)
        end_local = datetime.strptime(end_value, "%Y-%m-%d").replace(tzinfo=BJ)
    except ValueError as exc:
        raise ValueError("日期必须是 YYYY-MM-DD 格式") from exc
    if start_local > end_local:
        raise ValueError("开始日期不能晚于结束日期")
    end_local += timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc).isoformat(),
        end_local.astimezone(timezone.utc).isoformat(),
        start_value,
        end_value,
    )


def utc_window_for_day(day: str = "") -> tuple[str, str, str]:
    start_utc, end_utc, display_start, _ = utc_window_for_range(day, day)
    return start_utc, end_utc, display_start


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


def normalized_event_feedback_filter(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {*FEEDBACK_LABELS, "unlabelled"} else ""


def event_feedback_filter_clause(
    conn: sqlite3.Connection,
    *,
    feedback_filter: str,
    item_kind: str,
    source_expr: str,
    item_id_expr: str,
    delivered_expr: str,
) -> tuple[str, list[str]]:
    if not feedback_filter:
        return "", []
    if not table_exists(conn, "market_feedback"):
        return (f"AND ({delivered_expr})", []) if feedback_filter == "unlabelled" else ("AND 0", [])
    current_feedback = f"""
        SELECT 1 FROM market_feedback f
        WHERE f.item_kind = ?
          AND f.source = {source_expr}
          AND CAST(f.item_id AS TEXT) = CAST({item_id_expr} AS TEXT)
          AND f.label IN ('high_value', 'duplicate', 'invalid')
          AND NOT EXISTS (
            SELECT 1 FROM market_feedback newer
            WHERE newer.item_kind = f.item_kind
              AND newer.source = f.source
              AND newer.item_id = f.item_id
              AND newer.operator_id = f.operator_id
              AND (
                newer.clicked_at_us > f.clicked_at_us
                OR (newer.clicked_at_us = f.clicked_at_us AND newer.id > f.id)
              )
          )
    """
    if feedback_filter == "unlabelled":
        return f"AND ({delivered_expr}) AND NOT EXISTS ({current_feedback})", [item_kind]
    return f"AND ({delivered_expr}) AND EXISTS ({current_feedback} AND f.label = ?)", [item_kind, feedback_filter]


def apply_event_feedback(
    item: dict[str, Any],
    *,
    item_kind: str = "",
    source: str = "",
    item_id: str = "",
    delivered: bool = False,
    projection: dict[tuple[str, str, str], dict[str, Any]] | None = None,
) -> None:
    capable = item_kind in {"article", "official", "event"}
    item["feedback_capable"] = capable
    item["feedback_delivered"] = bool(capable and delivered)
    item["feedback_labels"] = []
    item["feedback_reason_labels"] = []
    item["feedback_operator_count"] = 0
    item["feedback_received_at"] = ""
    if not capable:
        item["feedback_state"] = "not_applicable"
        item["feedback_display"] = "不适用"
        return
    if not delivered:
        item["feedback_state"] = "not_delivered"
        item["feedback_display"] = "—"
        return
    current = (projection or {}).get((item_kind, source, str(item_id)))
    if not current:
        item["feedback_state"] = "unlabelled"
        item["feedback_display"] = "未反馈"
        return
    labels = [str(label) for label in current.get("labels") or [] if str(label) in FEEDBACK_LABELS]
    item["feedback_state"] = labels[0] if len(labels) == 1 else "mixed"
    item["feedback_labels"] = labels
    item["feedback_display"] = str(current.get("display") or "已反馈")
    item["feedback_reason_labels"] = list(current.get("reason_labels") or [])
    item["feedback_operator_count"] = int(current.get("operator_count") or 0)
    item["feedback_received_at"] = normalize_time(current.get("received_at"))


def event_feedback_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    delivered = [item for item in rows if item.get("feedback_delivered")]
    labelled = [item for item in delivered if item.get("feedback_labels")]
    return {
        "delivered": len(delivered),
        "labelled": len(labelled),
        **{
            label: sum(1 for item in labelled if label in (item.get("feedback_labels") or []))
            for label in FEEDBACK_LABELS
        },
    }


def fetch_events_rows(
    day: str = "",
    start_day: str = "",
    end_day: str = "",
    source: str = "",
    kind: str = "",
    q: str = "",
    time_basis: str = "seen",
    include_baseline: bool = False,
    feedback: str = "",
    limit: int = 100,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    if day and (start_day or end_day):
        raise ValueError("date 不能与 from/to 同时使用")
    if day:
        start_day = end_day = day
    start_utc, end_utc, _, _ = utc_window_for_range(start_day, end_day)
    q_lower = q.strip().lower()
    source_lower = source.strip().lower()
    kind_lower = kind.strip().lower()
    feedback_filter = normalized_event_feedback_filter(feedback)
    time_basis = normalized_event_time_basis(time_basis)
    rows: list[dict[str, Any]] = []
    with connect_sqlite(db_path) as conn:
        conn.row_factory = sqlite3.Row
        feedback_projection = feedback_projection_by_item(conn)
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
            feedback_where, feedback_params = event_feedback_filter_clause(
                conn,
                feedback_filter=feedback_filter,
                item_kind="event",
                source_expr="e.source",
                item_id_expr="e.id",
                delivered_expr="EXISTS (SELECT 1 FROM deliveries fd WHERE fd.event_id = e.id AND fd.channel = 'feishu' AND fd.status = 'sent')",
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
                         SELECT analysis_json FROM event_analyses a
                         WHERE a.event_id = e.id
                         ORDER BY a.id DESC LIMIT 1
                       ) AS analysis_json,
                       (
                         SELECT status FROM deliveries d
                         WHERE d.event_id = e.id
                         ORDER BY d.id DESC LIMIT 1
                       ) AS delivery_status,
                       (
                         SELECT MAX(sent_at) FROM deliveries d
                         WHERE d.event_id = e.id AND d.channel = 'feishu' AND d.status = 'sent'
                       ) AS pushed_at,
                       e.symbols_json,
                       e.themes_json
                FROM events e
                WHERE {where}
                  {feedback_where}
                  {" " if include_baseline else "AND COALESCE(e.baseline_only, 0) = 0"}
                ORDER BY e.first_seen_at DESC
                LIMIT 300
                """,
                [*params, *feedback_params],
            ):
                view = event_view_from_row(row).to_web_row()
                view["published_at"] = normalize_time(row["published_at"])
                view["seen_at"] = normalize_time(row["first_seen_at"])
                apply_event_feedback(
                    view,
                    item_kind="event",
                    source=str(row["source"] or ""),
                    item_id=str(row["id"]),
                    delivered=bool(row["pushed_at"]),
                    projection=feedback_projection,
                )
                rows.append(view)
        if table_exists(conn, "article_reviews"):
            article_columns = table_columns(conn, "article_reviews")
            gate_json_expr = "gate_json" if "gate_json" in article_columns else "'{}' AS gate_json"
            affected_targets_expr = (
                "affected_targets_json" if "affected_targets_json" in article_columns else "'[]' AS affected_targets_json"
            )
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
            feedback_where, feedback_params = event_feedback_filter_clause(
                conn,
                feedback_filter=feedback_filter,
                item_kind="article",
                source_expr="article_reviews.source",
                item_id_expr="article_reviews.item_id",
                delivered_expr="COALESCE(pushed_at, '') <> ''",
            )
            for row in conn.execute(
                f"""
                SELECT source, item_id, url, title, source_module, published_at, importance,
                       push_now, incremental_classification, daily_summary, reason, pushed_at, created_at,
                       {affected_targets_expr}, {gate_json_expr}
                FROM article_reviews
                WHERE {where}
                  {feedback_where}
                ORDER BY created_at DESC
                LIMIT 300
                """,
                [*params, *feedback_params],
            ):
                view = article_view_from_row(row).to_web_row()
                view["published_at"] = normalize_time(row["published_at"])
                view["seen_at"] = normalize_time(row["created_at"])
                apply_event_feedback(
                    view,
                    item_kind="article",
                    source=str(row["source"] or ""),
                    item_id=str(row["item_id"]),
                    delivered=bool(row["pushed_at"]),
                    projection=feedback_projection,
                )
                rows.append(view)
        if table_exists(conn, "official_news_reviews"):
            official_columns = table_columns(conn, "official_news_reviews")
            should_push_expr = "should_push_now" if "should_push_now" in official_columns else "0 AS should_push_now"
            analysis_json_expr = "analysis_json" if "analysis_json" in official_columns else "'{}' AS analysis_json"
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
            feedback_where, feedback_params = event_feedback_filter_clause(
                conn,
                feedback_filter=feedback_filter,
                item_kind="official",
                source_expr="official_news_reviews.source",
                item_id_expr="official_news_reviews.item_id",
                delivered_expr="COALESCE(pushed_at, '') <> ''",
            )
            for row in conn.execute(
                f"""
                SELECT source, item_id, url, title, published_at, importance, daily_summary,
                       reason, pushed_at, created_at, {should_push_expr}, {analysis_json_expr}
                FROM official_news_reviews
                WHERE {where}
                  {feedback_where}
                ORDER BY created_at DESC
                LIMIT 200
                """,
                [*params, *feedback_params],
            ):
                view = official_view_from_row(row).to_web_row()
                view["published_at"] = normalize_time(row["published_at"])
                view["seen_at"] = normalize_time(row["created_at"])
                apply_event_feedback(
                    view,
                    item_kind="official",
                    source=str(row["source"] or ""),
                    item_id=str(row["item_id"]),
                    delivered=bool(row["pushed_at"]),
                    projection=feedback_projection,
                )
                rows.append(view)
        if include_baseline and not feedback_filter and table_exists(conn, "seen_items"):
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
                        "summary": row["summary"] or "首次采集建立去重基线，未进入决策层。",
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
        if not feedback_filter and table_exists(conn, "seen_posts"):
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
        if not feedback_filter and table_exists(conn, "jygs_events"):
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

    for item in rows:
        if "feedback_state" not in item:
            apply_event_feedback(item)

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
        if feedback_filter == "unlabelled" and item.get("feedback_state") != "unlabelled":
            return False
        if feedback_filter in FEEDBACK_LABELS and feedback_filter not in (item.get("feedback_labels") or []):
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
            {"label": "来源决策", "value": count_rows(conn, "article_reviews", "created_at >= ? AND created_at < ?", (start_utc, end_utc))},
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


def parse_systemctl_show_output(output: str) -> dict[str, dict[str, Any]]:
    parsed: dict[str, dict[str, Any]] = {}
    for block in output.split("\n\n"):
        values: dict[str, Any] = {}
        for line in block.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key == "Id" or key in SYSTEMCTL_SHOW_FIELDS:
                values[key] = value
        unit_id = str(values.get("Id") or "")
        if unit_id:
            parsed[unit_id] = values
    return parsed


def systemctl_show_many(units: list[str]) -> list[dict[str, Any]]:
    if not units:
        return []
    try:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                *units,
                "--no-pager",
                f"--property=Id,{','.join(sorted(SYSTEMCTL_SHOW_FIELDS))}",
            ],
            check=False,
            text=True,
            capture_output=True,
            timeout=8,
        )
    except Exception as exc:  # noqa: BLE001
        return [{"Id": unit, "error": str(exc)} for unit in units]
    parsed = parse_systemctl_show_output(result.stdout)
    command_error = result.stderr.strip() if result.returncode != 0 else ""
    rows: list[dict[str, Any]] = []
    for unit in units:
        values = parsed.get(unit, {"Id": unit})
        if command_error or unit not in parsed:
            values["error"] = command_error or "systemctl 未返回该单元状态"
        rows.append(values)
    return rows


def systemctl_show(unit: str) -> dict[str, Any]:
    return systemctl_show_many([unit])[0]


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
        "health_alert": bool(meta.get("health_alert", lifecycle == "production")),
    }


def logical_task_id(unit: str) -> str:
    for suffix in (".timer", ".service"):
        if unit.endswith(suffix):
            return unit[: -len(suffix)]
    return unit


def raw_systemd_state(unit: dict[str, Any] | None) -> str:
    if not unit:
        return ""
    state = "/".join(
        str(unit.get(key) or "")
        for key in ("ActiveState", "SubState", "Result")
        if unit.get(key)
    )
    exit_status = str(unit.get("ExecMainStatus") or "").strip()
    if exit_status and exit_status != "0":
        state = f"{state}; exit={exit_status}" if state else f"exit={exit_status}"
    return state


def task_execution_status(service: dict[str, Any] | None) -> str:
    if not service:
        return "执行服务状态缺失"
    active = str(service.get("ActiveState") or "")
    sub = str(service.get("SubState") or "")
    result = str(service.get("Result") or "")
    exit_status = str(service.get("ExecMainStatus") or "").strip()
    if service.get("error"):
        return "执行状态读取异常"
    if active == "active" and sub == "running":
        return "正在执行"
    if result == "failed" or active == "failed" or (exit_status and exit_status != "0"):
        return f"最近运行失败（exit {exit_status}）" if exit_status else "最近运行失败"
    if active == "inactive" and sub == "dead" and result == "success":
        return "上次运行成功"
    return str(service.get("status_text") or f"{active}/{sub}".strip("/") or "未知")


def task_health_issue(task: dict[str, Any]) -> dict[str, Any] | None:
    if task.get("lifecycle") != "production" or not task.get("health_alert", True):
        return None
    timer = task.get("timer") or None
    service = task.get("service") or None
    for unit in (timer, service):
        if unit and unit.get("error"):
            return {
                "kind": "task",
                "id": str(task.get("Id") or ""),
                "label": str(task.get("label") or task.get("Id") or ""),
                "reason": "状态读取异常",
            }
    if timer and str(timer.get("ActiveState") or "") != "active":
        return {
            "kind": "task",
            "id": str(task.get("Id") or ""),
            "label": str(task.get("label") or task.get("Id") or ""),
            "reason": "生产定时器未启用",
        }
    if not service:
        return {
            "kind": "task",
            "id": str(task.get("Id") or ""),
            "label": str(task.get("label") or task.get("Id") or ""),
            "reason": "执行服务状态缺失",
        }
    active = str(service.get("ActiveState") or "")
    result = str(service.get("Result") or "")
    exit_status = str(service.get("ExecMainStatus") or "").strip()
    if result == "failed" or active == "failed" or (exit_status and exit_status != "0"):
        reason = f"最近运行失败（exit {exit_status}）" if exit_status else "最近运行失败"
        return {
            "kind": "task",
            "id": str(task.get("Id") or ""),
            "label": str(task.get("label") or task.get("Id") or ""),
            "reason": reason,
        }
    if not timer and active != "active":
        return {
            "kind": "task",
            "id": str(task.get("Id") or ""),
            "label": str(task.get("label") or task.get("Id") or ""),
            "reason": "常驻生产服务未运行",
        }
    return None


def build_health_tasks(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(unit.get("Id") or ""): unit for unit in units}
    paired_services = set(RUN_ONCE_TARGETS.values())
    tasks: list[dict[str, Any]] = []

    for timer_id in TIMER_UNITS:
        timer = by_id.get(timer_id)
        if not timer:
            continue
        service = by_id.get(RUN_ONCE_TARGETS.get(timer_id, ""))
        task_id = logical_task_id(timer_id)
        tasks.append(
            {
                "Id": task_id,
                "label": UNIT_TASK_LABELS.get(task_id, task_id.removeprefix("surveil-").replace("-", " ")),
                "unit_type": "定时任务",
                "group": timer.get("group") or "other",
                "group_label": timer.get("group_label") or UNIT_GROUP_LABELS["other"],
                "lifecycle": timer.get("lifecycle") or "production",
                "lifecycle_label": timer.get("lifecycle_label") or "",
                "replacement": timer.get("replacement") or "",
                "health_alert": bool(timer.get("health_alert", True)),
                "schedule": timer.get("schedule") or "",
                "schedule_status": timer.get("status_text") or "未知",
                "execution_status": task_execution_status(service),
                "next_trigger": timer.get("NextElapseUSecRealtime") or "",
                "last_execution": (service or {}).get("ExecMainStartTimestamp") or timer.get("LastTriggerUSec") or "",
                "NRestarts": (service or {}).get("NRestarts") or "",
                "timer": timer,
                "service": service,
                "action_unit": timer,
                "raw_timer_state": raw_systemd_state(timer),
                "raw_service_state": raw_systemd_state(service),
            }
        )

    for service_id in SERVICE_UNITS:
        if service_id in paired_services:
            continue
        service = by_id.get(service_id)
        if not service:
            continue
        task_id = logical_task_id(service_id)
        tasks.append(
            {
                "Id": task_id,
                "label": UNIT_TASK_LABELS.get(task_id, task_id.removeprefix("surveil-").replace("-", " ")),
                "unit_type": service.get("unit_type") or "服务",
                "group": service.get("group") or "other",
                "group_label": service.get("group_label") or UNIT_GROUP_LABELS["other"],
                "lifecycle": service.get("lifecycle") or "production",
                "lifecycle_label": service.get("lifecycle_label") or "",
                "replacement": service.get("replacement") or "",
                "health_alert": bool(service.get("health_alert", True)),
                "schedule": service.get("schedule") or "",
                "schedule_status": "常驻" if service.get("ActiveState") == "active" else "未运行",
                "execution_status": task_execution_status(service),
                "next_trigger": "",
                "last_execution": service.get("ExecMainStartTimestamp") or "",
                "NRestarts": service.get("NRestarts") or "",
                "timer": None,
                "service": service,
                "action_unit": service,
                "raw_timer_state": "",
                "raw_service_state": raw_systemd_state(service),
            }
        )
    for task in tasks:
        task["health_issue"] = task_health_issue(task)
    return tasks


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


def health_units() -> list[dict[str, Any]]:
    units = systemctl_show_many([*SERVICE_UNITS, *TIMER_UNITS])
    for unit in units:
        unit_id = str(unit.get("Id", ""))
        unit.update(unit_display_metadata(unit_id, unit))
        unit["actions"] = unit_actions(unit_id)
        if unit_id in RUN_ONCE_TARGETS:
            unit["run_once_target"] = RUN_ONCE_TARGETS[unit_id]
    return units


def health_sources(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    with connect_sqlite(db_path) as conn:
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
    return sources


def source_profile_health_issue(profile: dict[str, Any]) -> dict[str, Any] | None:
    if not profile.get("enabled", True) or profile.get("health_status") != "failing":
        return None
    failures = int(profile.get("consecutive_failures") or 0)
    return {
        "kind": "source",
        "id": str(profile.get("id") or ""),
        "label": str(profile.get("name") or profile.get("id") or ""),
        "reason": f"来源连续失败 {failures} 次",
    }


def build_health_summary(
    tasks: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    task_issues = [issue for task in tasks if (issue := task.get("health_issue"))]
    source_issues = [issue for profile in profiles if (issue := source_profile_health_issue(profile))]
    issues = [*task_issues, *source_issues]
    return {
        "ok": True,
        "total_failures": len(issues),
        "task_failures": len(task_issues),
        "source_failures": len(source_issues),
        "issues": issues[:50],
        "issues_truncated": len(issues) > 50,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def active_source_health_keys(profiles: list[dict[str, Any]], sources: list[dict[str, Any]]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    enabled_profile_ids: set[str] = set()
    for profile in profiles:
        if not profile.get("enabled", True):
            continue
        enabled_profile_ids.add(str(profile.get("id") or ""))
        for record in profile.get("health_records") or []:
            if record.get("status") == "failing":
                keys.add((str(record.get("monitor") or ""), str(record.get("source") or "")))
    if "x_serenity" in enabled_profile_ids:
        keys.update(
            ("x_stream_detail", str(source.get("source") or ""))
            for source in sources
            if source.get("monitor") == "x_stream_detail" and source.get("status") == "failing"
        )
    return keys


def health_summary_payload(db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    tasks = build_health_tasks(health_units())
    profiles = source_profiles_payload(db_path=db_path).get("profiles") or []
    return build_health_summary(tasks, profiles)


def health_payload(db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    units = health_units()
    tasks = build_health_tasks(units)
    sources = health_sources(db_path)
    profiles = source_profiles_payload(db_path=db_path).get("profiles") or []
    active_source_keys = active_source_health_keys(profiles, sources)
    for source in sources:
        source["health_issue"] = (
            str(source.get("monitor") or ""),
            str(source.get("source") or ""),
        ) in active_source_keys
    summary = build_health_summary(tasks, profiles)
    logs_dir = ROOT / "logs"
    logs = []
    for name in LOG_FILES:
        tail = tail_file(logs_dir / name)
        if tail:
            logs.append({"name": name, "tail": tail})
    return {
        "ok": True,
        "unit_groups": UNIT_GROUP_LABELS,
        "units": units,
        "tasks": tasks,
        "sources": sources,
        "summary": summary,
        "logs": logs,
    }


def html_page(token_required: bool) -> str:
    token_hint = "需要访问令牌" if token_required else "未配置访问令牌，仅限 SSH 隧道使用"
    environment_label = workbench_environment_label()
    template = WEB_INDEX_PATH.read_text(encoding="utf-8").rstrip("\n")
    replacements = {
        "__WORKBENCH_ENVIRONMENT_LABEL__": html.escape(environment_label),
        "__WORKBENCH_TOKEN_HINT__": html.escape(token_hint),
    }
    for placeholder, value in replacements.items():
        if template.count(placeholder) != 1:
            raise HoldingsError(f"Web 模板占位符异常：{placeholder}")
        template = template.replace(placeholder, value)
    return template


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


def _urlsafe_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _urlsafe_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def issue_holdings_preview_token(
    base_revision: str,
    payload_revision: str,
    *,
    now: int | None = None,
) -> str:
    issued_at = int(time.time()) if now is None else int(now)
    body = json.dumps(
        {"base": base_revision, "payload": payload_revision, "issued_at": issued_at},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = _urlsafe_encode(body)
    signature = hmac.new(_HOLDINGS_PREVIEW_SIGNING_KEY, encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_urlsafe_encode(signature)}"


def verify_holdings_preview_token(token: str, *, now: int | None = None) -> tuple[str, str]:
    try:
        encoded, encoded_signature = token.split(".", 1)
        signature = _urlsafe_decode(encoded_signature)
        expected_signature = hmac.new(
            _HOLDINGS_PREVIEW_SIGNING_KEY,
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("signature mismatch")
        payload = json.loads(_urlsafe_decode(encoded).decode("utf-8"))
        base_revision = str(payload["base"])
        payload_revision = str(payload["payload"])
        issued_at = int(payload["issued_at"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HoldingsError("保存预览无效，请重新点击保存生成预览。") from exc
    current_time = int(time.time()) if now is None else int(now)
    if issued_at > current_time + 30 or current_time - issued_at > HOLDINGS_PREVIEW_TTL_SECONDS:
        raise HoldingsError("保存预览已过期，请重新点击保存生成预览。")
    return base_revision, payload_revision


def prepare_holdings_preview(
    items: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = normalize_holdings_for_save(items, current)
    symbols = remote_validation_symbols(current, normalized)
    warnings = validate_holdings(normalized, remote_symbols=symbols)
    base_revision = holdings_revision(current)
    payload_revision = holdings_revision(normalized)
    return {
        "normalized": normalized,
        "warnings": warnings,
        "remote_checked_count": len(symbols),
        "base_revision": base_revision,
        "payload_revision": payload_revision,
        "preview_token": issue_holdings_preview_token(base_revision, payload_revision),
    }


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
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_static_asset(self, path: str) -> None:
        asset = WEB_STATIC_ASSETS.get(path)
        if asset is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        asset_path, content_type = asset
        try:
            body = asset_path.read_bytes()
        except OSError as exc:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, explain=str(exc))
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
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
        if parsed.path in WEB_STATIC_ASSETS:
            self.send_static_asset(parsed.path)
            return
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
                    start_day=(qs.get("from") or [""])[0],
                    end_day=(qs.get("to") or [""])[0],
                    source=(qs.get("source") or [""])[0],
                    kind=(qs.get("kind") or [""])[0],
                    q=(qs.get("q") or [""])[0],
                    time_basis=(qs.get("time_basis") or ["seen"])[0],
                    include_baseline=(qs.get("include_baseline") or [""])[0].strip().lower()
                    in {"1", "true", "yes", "on"},
                    feedback=(qs.get("feedback") or [""])[0],
                    limit=limit,
                )
                self.send_json({"ok": True, "events": events, "feedback_summary": event_feedback_summary(events)})
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
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
        if parsed.path == "/api/health/summary":
            if not self.require_auth():
                return
            try:
                self.send_json(health_summary_payload())
            except Exception as exc:  # noqa: BLE001
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/feedback-quality":
            if not self.require_auth():
                return
            try:
                qs = parse_qs(parsed.query)
                days = int((qs.get("days") or ["30"])[0])
                payload = feedback_quality_payload(db_path=DEFAULT_DB_PATH, days=days)
                payload["ok"] = True
                self.send_json(payload)
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
        if parsed.path == "/api/rule-shadow-reports":
            if not self.require_auth():
                return
            try:
                qs = parse_qs(parsed.query)
                report_date = (qs.get("date") or [""])[0]
                self.send_json(rule_shadow_reports_payload(report_date))
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
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
        request_id = secrets.token_hex(4)
        request_started = time.monotonic()
        try:
            payload = self.read_json()
            if parsed.path == "/api/media-keywords":
                semiconductor_ai_keywords = payload.get("semiconductor_ai_keywords")
                exclude_keywords = payload.get("exclude_keywords")
                if not isinstance(semiconductor_ai_keywords, list) or not isinstance(exclude_keywords, list):
                    raise HoldingsError(
                        "请求缺少 semiconductor_ai_keywords / exclude_keywords 数组"
                    )
                saved = save_media_keyword_config(
                    semiconductor_ai_keywords, exclude_keywords
                )
                saved.pop("backup_path", None)
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
            if parsed.path == "/api/preview":
                preview = prepare_holdings_preview(items, current)
                normalized = preview["normalized"]
                diff = holdings_diff(current, normalized)
                duration_ms = round((time.monotonic() - request_started) * 1000)
                print(
                    "holdings_preview "
                    f"request_id={request_id} duration_ms={duration_ms} "
                    f"payload={preview['payload_revision'][:12]} "
                    f"remote_checked={preview['remote_checked_count']} outcome=ok",
                    flush=True,
                )
                self.send_json(
                    {
                        "ok": True,
                        "diff": diff,
                        "diff_text": diff_text(diff),
                        "holdings": normalized,
                        "warnings": preview["warnings"],
                        "remote_checked_count": preview["remote_checked_count"],
                        "preview_token": preview["preview_token"],
                        "preview_expires_in_seconds": HOLDINGS_PREVIEW_TTL_SECONDS,
                    }
                )
                return
            if parsed.path == "/api/save":
                normalized = normalize_holdings_for_save(items, current, enrich_symbols=False)
                base_revision, payload_revision = verify_holdings_preview_token(
                    str(payload.get("preview_token") or "")
                )
                if holdings_revision(normalized) != payload_revision:
                    raise HoldingsConflictError("待保存内容与预览不一致，请重新预览。")
                result = save_holdings(
                    normalized,
                    db_path=DEFAULT_DB_PATH,
                    expected_current_revision=base_revision,
                    expected_payload_revision=payload_revision,
                )
                if self.restart_sina_flash and result.backup_path is not None:
                    subprocess.run(["systemctl", "restart", "surveil-sina-flash.service"], check=False)
                duration_ms = round((time.monotonic() - request_started) * 1000)
                print(
                    "holdings_save "
                    f"request_id={request_id} duration_ms={duration_ms} "
                    f"payload={result.revision[:12]} no_change={int(result.no_change)} "
                    f"sync_repaired={int(result.sync_repaired)} outcome=ok",
                    flush=True,
                )
                self.send_json(
                    {
                        "ok": True,
                        "backup_path": str(result.backup_path) if result.backup_path else "",
                        "imported_count": result.imported_count,
                        "changed_count": result.changed_count,
                        "no_change": result.no_change,
                        "sync_repaired": result.sync_repaired,
                        "revision": result.revision,
                        "holdings": normalized_holdings(),
                    }
                )
                return
        except Exception as exc:  # noqa: BLE001
            if parsed.path in {"/api/preview", "/api/save"}:
                duration_ms = round((time.monotonic() - request_started) * 1000)
                print(
                    "holdings_request "
                    f"request_id={request_id} path={parsed.path} duration_ms={duration_ms} "
                    f"error={type(exc).__name__} outcome=failed",
                    flush=True,
                )
            status = HTTPStatus.CONFLICT if isinstance(exc, HoldingsConflictError) else HTTPStatus.BAD_REQUEST
            self.send_json({"ok": False, "error": str(exc)}, status)
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
