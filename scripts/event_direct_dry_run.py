#!/usr/bin/env python3
"""Read-only direct decision self-check for event-family sources."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from collector_direct_shadow import safe_direct_event_decision_payload, safe_load_shadow_holdings
from market_db import DEFAULT_DB_PATH


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports"
EVENT_SOURCES = ("sina_flash", "sina_stock_news", "ifind_notice")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_self_check_events() -> list[dict[str, Any]]:
    """Provide source-shape coverage without pretending fixtures are live data."""
    published_at = "2026-07-12T00:00:00+00:00"
    return [
        {
            "source": "sina_flash",
            "source_event_id": "self-check:sina-flash",
            "event_type": "flash_news",
            "title": "美国 CPI 大幅低于市场预期，2年期美债收益率下跌",
            "summary": "市场重新定价美联储降息路径。",
            "published_at": published_at,
            "symbols": [],
            "themes": ["宏观流动性/美联储政策"],
            "raw": {"self_check": True},
        },
        {
            "source": "sina_stock_news",
            "source_event_id": "self-check:sina-stock-news",
            "event_type": "stock_news",
            "title": "持仓公司个股资讯链路自检",
            "summary": "只验证标准化和决策入口，不代表真实新闻。",
            "published_at": published_at,
            "symbols": ["SELF_CHECK"],
            "themes": ["新浪财经个股资讯"],
            "raw": {"self_check": True},
        },
        {
            "source": "ifind_notice",
            "source_event_id": "self-check:ifind-notice",
            "event_type": "announcement",
            "title": "iFinD 公司公告链路自检",
            "summary": "只验证公告标准化和决策入口。",
            "published_at": published_at,
            "symbols": ["SELF_CHECK"],
            "themes": [],
            "raw": {"self_check": True},
        },
    ]


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def load_recent_events_read_only(
    db_path: Path,
    *,
    sources: list[str],
    limit: int,
) -> tuple[list[dict[str, Any]], str]:
    if not db_path.exists():
        return [], ""
    placeholders = ", ".join("?" for _ in sources)
    query = f"""
        SELECT source, source_event_id, event_type, title, summary, full_text, url,
               published_at, first_seen_at, symbols_json, themes_json, raw_json
        FROM events
        WHERE source IN ({placeholders})
        ORDER BY id DESC
        LIMIT ?
    """
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            rows = conn.execute(query, (*sources, max(0, limit))).fetchall()
    except sqlite3.Error as exc:
        return [], f"{type(exc).__name__}: {exc}"
    return [
        {
            "source": row[0],
            "source_event_id": row[1],
            "event_type": row[2],
            "title": row[3],
            "summary": row[4],
            "full_text": row[5],
            "url": row[6],
            "published_at": row[7],
            "first_seen_at": row[8],
            "symbols": _json_list(row[9]),
            "themes": _json_list(row[10]),
            "raw": _json_object(row[11]),
        }
        for row in rows
    ], ""


def build_report(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    sources: list[str] | None = None,
    include_self_check: bool = True,
    include_recent: bool = True,
    recent_limit: int = 40,
) -> dict[str, Any]:
    selected_sources = list(dict.fromkeys(sources or EVENT_SOURCES))
    holdings, holdings_error = safe_load_shadow_holdings(db_path)
    events: list[tuple[str, dict[str, Any]]] = []
    if include_self_check:
        events.extend(
            ("canonical_self_check", event)
            for event in canonical_self_check_events()
            if event["source"] in selected_sources
        )
    recent_error = ""
    if include_recent:
        recent, recent_error = load_recent_events_read_only(
            db_path,
            sources=selected_sources,
            limit=recent_limit,
        )
        events.extend(("recent_database_event", event) for event in recent)

    rows: list[dict[str, Any]] = []
    for input_kind, event in events:
        result = safe_direct_event_decision_payload(event, holdings=holdings)
        rows.append(
            {
                "input_kind": input_kind,
                "source": str(event.get("source") or ""),
                "source_event_id": str(event.get("source_event_id") or ""),
                "result": result,
            }
        )
    coverage = {
        source: {
            "rows": sum(1 for row in rows if row["source"] == source),
            "ok": any(row["source"] == source and row["result"].get("ok") for row in rows),
            "errors": sum(1 for row in rows if row["source"] == source and not row["result"].get("ok")),
        }
        for source in selected_sources
    }
    return {
        "ok": all(item["ok"] for item in coverage.values()) and not holdings_error and not recent_error,
        "mode": "event_direct_dry_run",
        "generated_at": utc_now(),
        "db_path": str(db_path),
        "sources": selected_sources,
        "coverage": coverage,
        "holdings_count": len(holdings),
        "holdings_error": holdings_error,
        "recent_read_error": recent_error,
        "side_effects": {
            "llm_called": False,
            "feishu_called": False,
            "review_store_written": False,
            "delivery_record_written": False,
            "dedup_reservation_attempted": False,
            "production_state_written": False,
        },
        "rows": rows,
    }


def write_report(payload: dict[str, Any], report_dir: Path = REPORT_DIR) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = report_dir / f"event-direct-dry-run-{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run read-only direct decision checks for event sources.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--source", action="append", choices=EVENT_SOURCES, dest="sources")
    parser.add_argument("--self-check", action="store_true", help="Include canonical source-shape checks.")
    parser.add_argument("--recent", action="store_true", help="Include recent events from SQLite read-only mode.")
    parser.add_argument("--recent-limit", type=int, default=40)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    default_modes = not args.self_check and not args.recent
    payload = build_report(
        db_path=args.db_path,
        sources=args.sources,
        include_self_check=args.self_check or default_modes,
        include_recent=args.recent or default_modes,
        recent_limit=max(0, args.recent_limit),
    )
    if args.write_report:
        payload["report_path"] = str(write_report(payload, args.report_dir))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"event direct dry-run: ok={payload['ok']} rows={len(payload['rows'])} "
            f"coverage={payload['coverage']}"
        )
        if payload.get("report_path"):
            print(f"report: {payload['report_path']}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
