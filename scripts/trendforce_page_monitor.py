#!/usr/bin/env python3
"""Low-frequency monitors for TrendForce official list pages without usable RSS."""

from __future__ import annotations

import argparse
import html
import os
import re
import sqlite3
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from collector_runtime import (
    filter_enabled_named_for_run,
    load_source_state as runtime_load_source_state,
    save_source_state as runtime_save_source_state,
)
from db_utils import ensure_trendforce_page_seen_table, retry_on_locked, update_seen_item_lifecycle
from http_utils import http_get
from llm_analysis import llm_config
from market_flow import normalize_market_item, process_market_item
from production_admission import admission_lifecycle_values, persist_production_admission_context, production_admission_context
from rss_monitor import DB_PATH, connect_db, fetch_article_body, parse_date, strip_tags
from source_health import record_source_failure, record_source_success
from trendforce_sources import PageSource, TREND_FORCE_PAGE_SOURCES
from x_check import load_env


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
PAGE_SOURCE_KEY = "trendforce_page"
SCOPE_STATE_PREFIX = "trendforce_page_scope"
EXPANDED_SCOPE_BASELINE_STATE_KEY = "expanded_scope_baseline_at"


def fetch_html(url: str) -> str:
    response = http_get(
        url,
        headers={"Accept": "text/html,application/xhtml+xml"},
        timeout=int(os.getenv("TRENDFORCE_PAGE_TIMEOUT_SECONDS", "35")),
        retries=int(os.getenv("TRENDFORCE_PAGE_RETRY_COUNT", os.getenv("SURVEIL_HTTP_RETRY_COUNT", "2"))),
    )
    return response.content.decode("utf-8", errors="replace")


def clean_text(value: str) -> str:
    value = strip_tags(value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def first_match(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            cleaned = clean_text(match.group(1))
            if cleaned:
                return cleaned
    return ""


def parse_page_date(value: str) -> str:
    if not value:
        return ""
    normalized = value.strip().replace("/", "-")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
        return f"{normalized}T00:00:00+08:00"
    parsed = parse_date(value)
    return parsed if parsed != value else normalized


def article_id(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def item_from_anchor(
    source: PageSource,
    href: str,
    anchor_html: str,
    context_html: str,
    title_patterns: list[str],
    summary_patterns: list[str],
) -> dict | None:
    url = urllib.parse.urljoin(source.url, href)
    title = first_match(title_patterns, anchor_html) or first_match(title_patterns, context_html)
    if not title:
        title = clean_text(anchor_html)
    if not title or len(title) < 8:
        return None

    date_match = re.search(r"\b20\d{2}[/-]\d{2}[/-]\d{2}\b", anchor_html) or re.search(
        r"\b20\d{2}[/-]\d{2}[/-]\d{2}\b", context_html
    )
    summary = first_match(summary_patterns, anchor_html) or first_match(summary_patterns, context_html)
    published_at = parse_page_date(date_match.group(0) if date_match else "")
    return {
        "id": article_id(url),
        "url": url,
        "title": title,
        "summary": summary,
        "published_at": published_at,
        "source_module": source.module,
        "source_display": source.module,
        "access_note": source.access_note,
        "body_source": "TrendForce 官方列表页摘要",
        "page_source": source.name,
    }


def extract_prnewswire_semi_items(source: PageSource, html_text: str) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    pattern = re.compile(
        r"<a\b(?=[^>]*class=[\"'][^\"']*newsreleaseconsolidatelink[^\"']*[\"'])(?=[^>]*href=[\"']([^\"']+)[\"'])[^>]*>(.*?)</a>",
        re.I | re.S,
    )
    for match in pattern.finditer(html_text):
        href = match.group(1)
        anchor_html = match.group(2)
        start = max(0, match.start() - 500)
        end = min(len(html_text), match.end() + 2600)
        context_html = html_text[start:end]
        context_text = clean_text(context_html)
        url = urllib.parse.urljoin(source.url, href)
        title = first_match(
            [
                r"<h3[^>]*>.*?<span[^>]*>(.*?)</span>.*?</h3>",
                r"<h3[^>]*>(.*?)</h3>",
            ],
            anchor_html,
        ) or clean_text(anchor_html)
        title = re.sub(r"^\d{2}:\d{2}\s+ET\s*", "", title).strip()
        summary = first_match(
            [
                r"<p[^>]*class=[\"'][^\"']*remove-outline[^\"']*[\"'][^>]*>(.*?)</p>",
                r"<p[^>]*>(.*?)</p>",
            ],
            anchor_html,
        )
        if not title or len(title) < 8:
            continue
        if not re.search(r"\bSEMI\b|/semi[-/]", f"{url} {title}", re.I):
            continue
        date_match = re.search(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+\d{1,2},\s+20\d{2}\b", context_text, re.I)
        item_id = article_id(url)
        if item_id in seen:
            continue
        seen.add(item_id)
        items.append(
            {
                "id": item_id,
                "url": url,
                "title": title,
                "summary": summary,
                "published_at": parse_page_date(date_match.group(0) if date_match else ""),
                "source_module": source.module,
                "source_display": source.module,
                "access_note": source.access_note,
                "body_source": "PR Newswire 半导体分类页摘要",
                "page_source": source.name,
            }
        )
    return items


def extract_research_items(source: PageSource, html_text: str) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    pattern = re.compile(
        r"<a\b(?=[^>]*href=[\"']([^\"']*/research/download/RP[^\"']+)[\"'])[^>]*>(.*?)</a>",
        re.I | re.S,
    )
    for match in pattern.finditer(html_text):
        start = max(0, match.start() - 1800)
        end = min(len(html_text), match.end() + 4200)
        item = item_from_anchor(
            source,
            match.group(1),
            match.group(2),
            html_text[start:end],
            [
                r"<strong[^>]*>(.*?)</strong>",
                r"<h2[^>]*class=[\"'][^\"']*card-title[^\"']*[\"'][^>]*>(.*?)</h2>",
                r"<h3[^>]*>(.*?)</h3>",
            ],
            [
                r"<p[^>]*class=[\"'][^\"']*card-desc[^\"']*[\"'][^>]*>(.*?)</p>",
                r"<p[^>]*class=[\"'][^\"']*text-ellipsis-2[^\"']*[\"'][^>]*>(.*?)</p>",
            ],
        )
        if item and item["id"] not in seen:
            seen.add(item["id"])
            items.append(item)
    return items


def extract_news_items(source: PageSource, html_text: str) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    pattern = re.compile(
        r"<a\b(?=[^>]*href=[\"']([^\"']*/news/\d{4}/\d{2}/\d{2}/[^\"']+)[\"'])[^>]*>(.*?)</a>",
        re.I | re.S,
    )
    for match in pattern.finditer(html_text):
        start = max(0, match.start() - 1300)
        end = min(len(html_text), match.end() + 2600)
        item = item_from_anchor(
            source,
            match.group(1),
            match.group(2),
            html_text[start:end],
            [
                r"<strong[^>]*>(.*?)</strong>",
                r"<h2[^>]*>(.*?)</h2>",
            ],
            [
                r"<p[^>]*>(.*?)</p>",
            ],
        )
        if item and item["id"] not in seen:
            seen.add(item["id"])
            if not item.get("published_at"):
                date_match = re.search(r"/news/(\d{4})/(\d{2})/(\d{2})/", item["url"])
                if date_match:
                    item["published_at"] = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}T00:00:00+08:00"
            item["body_source"] = "TrendForce News 页面正文"
            items.append(item)
    return items


def extract_press_analysis_items(source: PageSource, html_text: str) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    pattern = re.compile(
        r"<a\b(?=[^>]*class=[\"'][^\"']*title-link[^\"']*[\"'])(?=[^>]*href=[\"']([^\"']+)[\"'])[^>]*>(.*?)</a>",
        re.I | re.S,
    )
    for match in pattern.finditer(html_text):
        href = match.group(1)
        if "presscenter/analysis?page=" in href or href.startswith("#"):
            continue
        start = max(0, match.start() - 1300)
        end = min(len(html_text), match.end() + 2600)
        item = item_from_anchor(
            source,
            href,
            match.group(2),
            html_text[start:end],
            [
                r"<strong[^>]*>(.*?)</strong>",
                r"<h3[^>]*>(.*?)</h3>",
            ],
            [
                r"<p[^>]*>(.*?)</p>",
            ],
        )
        if item and item["id"] not in seen:
            seen.add(item["id"])
            item["body_source"] = "TrendForce Press Centre 列表页摘要"
            items.append(item)
    return items


def extract_items(source: PageSource) -> list[dict]:
    html_text = fetch_html(source.url)
    if source.kind == "prnewswire_semi":
        return extract_prnewswire_semi_items(source, html_text)
    if source.kind in {"research", "selected_topics"}:
        return extract_research_items(source, html_text)
    if source.kind == "news":
        return extract_news_items(source, html_text)
    if source.kind == "press_analysis":
        return extract_press_analysis_items(source, html_text)
    raise ValueError(f"未知官方页面类型：{source.kind}")


def ensure_page_seen_table(conn: sqlite3.Connection) -> None:
    ensure_trendforce_page_seen_table(conn)


def source_initialized(conn: sqlite3.Connection, source_name: str) -> bool:
    row = conn.execute("SELECT 1 FROM seen_sources WHERE source = ? LIMIT 1", (source_name,)).fetchone()
    return row is not None


def load_scope_state(conn: sqlite3.Connection, source_name: str) -> dict:
    return runtime_load_source_state(conn, source_name, prefix=SCOPE_STATE_PREFIX)


def save_scope_state(conn: sqlite3.Connection, source_name: str, state: dict) -> None:
    runtime_save_source_state(conn, source_name, state, prefix=SCOPE_STATE_PREFIX)


def save_new_page_items(
    conn: sqlite3.Connection,
    source: PageSource,
    items: list[dict],
    notify_baseline: bool = False,
    expanded_scope_baseline: bool = False,
) -> list[dict]:
    ensure_page_seen_table(conn)
    source_name = source.name
    is_baseline = not source_initialized(conn, source_name)
    now = datetime.now(timezone.utc).isoformat()
    if is_baseline:
        conn.execute(
            "INSERT OR IGNORE INTO seen_sources (source, first_seen_at) VALUES (?, ?)",
            (source_name, now),
        )

    baseline_only = bool((is_baseline and not notify_baseline) or expanded_scope_baseline)
    new_items: list[dict] = []
    for item in sorted(items, key=lambda entry: entry.get("published_at") or ""):
        item_id = str(item["id"])
        existing = conn.execute(
            """
            SELECT collection_class, processability_status, admission_status,
                   processing_status, first_seen_at
            FROM seen_items WHERE source = ? AND item_id = ?
            """,
            (source_name, item_id),
        ).fetchone()
        if existing:
            retryable = (
                str(existing[0]) == "live"
                and (
                    str(existing[1]) in {"pending", "failed_retryable"}
                    or str(existing[2]) == "pending"
                    or str(existing[3]) in {"pending", "failed_retryable"}
                )
            )
            if retryable:
                update_seen_item_lifecycle(
                    conn,
                    source_name,
                    item_id,
                    processability_status="pending",
                    processability_reason="",
                    admission_status="pending",
                    admission_reason="",
                    processing_status="not_applicable",
                    processing_error="",
                    processed_at=None,
                    lifecycle_updated_at=datetime.now(timezone.utc).isoformat(),
                )
                item = dict(item)
                item["_seen_item_retry"] = True
                item["_seen_item_retry_first_seen_at"] = str(existing[4] or "")
                new_items.append(item)
            continue
        try:
            conn.execute(
                """
                INSERT INTO seen_items (
                    source, item_id, url, title, summary, published_at, first_seen_at,
                    collection_class, processability_status, processability_reason,
                    admission_status, admission_reason, processing_status,
                    processing_error, processed_at, lifecycle_updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_name,
                    item_id,
                    item.get("url", ""),
                    item.get("title", ""),
                    item.get("summary", ""),
                    item.get("published_at", ""),
                    now,
                    "baseline" if baseline_only else "live",
                    "not_required" if baseline_only else "pending",
                    "expanded_scope_baseline" if expanded_scope_baseline else "",
                    "not_applicable" if baseline_only else "pending",
                    "expanded_scope_baseline" if expanded_scope_baseline else "",
                    "not_applicable",
                    "",
                    now if baseline_only else None,
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            continue

        globally_new = True
        try:
            conn.execute(
                """
                INSERT INTO trendforce_page_seen_items (
                    item_id, url, title, first_source, first_seen_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    item.get("url", ""),
                    item.get("title", ""),
                    source_name,
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            globally_new = False

        if baseline_only:
            continue
        if globally_new:
            new_items.append(item)

    conn.commit()
    if baseline_only:
        print(f"{source.name}: 首次建立基线 {len(items)} 条，默认不发送旧内容。")
    return new_items


def save_new_page_items_with_retry(
    source: PageSource,
    items: list[dict],
    notify_baseline: bool = False,
    expanded_scope_baseline: bool = False,
) -> list[dict]:
    def operation() -> list[dict]:
        with connect_db() as conn:
            return save_new_page_items(
                conn,
                source,
                items,
                notify_baseline=notify_baseline,
                expanded_scope_baseline=expanded_scope_baseline,
            )

    return retry_on_locked(operation)


def enrich_item(item: dict) -> dict:
    enriched = dict(item)
    summary = clean_text(enriched.get("summary", ""))
    full_text = summary
    body_source = enriched.get("body_source", "TrendForce 官方列表页摘要")
    detail_fetch_status = "not_required"
    detail_fetch_error = ""

    if enriched.get("page_source", "").startswith("trendforce_news_"):
        detail_fetch_status = "pending"
        try:
            body, fetched_source = fetch_article_body(enriched.get("url", ""))
            if body:
                full_text = body
                body_source = fetched_source
                detail_fetch_status = "succeeded"
            else:
                detail_fetch_status = "empty"
        except Exception as exc:
            detail_fetch_status = "failed"
            detail_fetch_error = f"{type(exc).__name__}: {str(exc)[:400]}"
            print(f"{enriched.get('url')} 正文抓取失败，回退列表摘要：{exc}")

    enriched["summary"] = summary
    enriched["full_text"] = full_text or summary
    enriched["body_source"] = body_source
    enriched["detail_fetch_status"] = detail_fetch_status
    if detail_fetch_error:
        enriched["detail_fetch_error"] = detail_fetch_error
    enriched.setdefault("source_display", enriched.get("source_module") or enriched.get("page_source") or "TrendForce 官方页面")
    return enriched


def set_seen_item_lifecycle(source: str, item_id: str, **values: str | None) -> None:
    with connect_db() as conn:
        update_seen_item_lifecycle(conn, source, item_id, **values)
        conn.commit()


def notify_item(item: dict) -> None:
    prepared = dict(item)
    item_id = str(prepared.pop("id", "") or "")
    prepared.pop("_seen_item_retry", None)
    prepared.pop("_seen_item_retry_first_seen_at", None)
    try:
        enriched = enrich_item(prepared)
        detail_status = str(enriched.get("detail_fetch_status") or "not_required")
        fallback = detail_status in {"failed", "empty"}
        set_seen_item_lifecycle(
            str(enriched.get("page_source") or PAGE_SOURCE_KEY),
            item_id,
            processability_status="fallback" if fallback else "succeeded",
            processability_reason="detail_fallback" if fallback else "",
            admission_status="pending",
            admission_reason="",
            processing_status="not_applicable",
            processing_error="",
        )
        profile_id = str(enriched.get("page_source") or PAGE_SOURCE_KEY)
        normalized = normalize_market_item(
            PAGE_SOURCE_KEY,
            enriched,
            store_kind="article",
            source_profile_id=profile_id,
        )
        admission_context = persist_production_admission_context(normalized, production_admission_context(normalized, db_path=DB_PATH), db_path=DB_PATH)
        admission = admission_context.result
        if admission.status != "admitted":
            set_seen_item_lifecycle(
                profile_id,
                item_id,
                **admission_lifecycle_values(admission, processing_status="not_applicable"),
                processed_at=datetime.now(timezone.utc).isoformat(),
            )
            return
        set_seen_item_lifecycle(
            profile_id,
            item_id,
            **admission_lifecycle_values(admission, processing_status="pending"),
        )
        outcome = process_market_item(
            normalized,
            enriched,
            store_kind="article",
            source_profile_id=profile_id,
            db_path=DB_PATH,
            use_rule_dedup=False,
            production_admission=admission,
            production_portfolio=admission_context.portfolio,
            market_item_id=admission_context.market_item_id,
            market_review_id=admission_context.market_review_id,
        )
        set_seen_item_lifecycle(
            profile_id,
            item_id,
            **admission_lifecycle_values(admission, processing_status="succeeded"),
            processed_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as exc:
        profile_id = str(prepared.get("page_source") or PAGE_SOURCE_KEY)
        set_seen_item_lifecycle(
            profile_id,
            item_id,
            processing_status="failed_retryable",
            processing_error=f"{type(exc).__name__}: {str(exc)[:400]}",
            processed_at=None,
            lifecycle_updated_at=datetime.now(timezone.utc).isoformat(),
        )
        raise
    review = outcome.payload
    print(
        f"{profile_id} 决策层：importance={review.get('importance')} "
        f"push={review.get('push_now')} title={enriched.get('title', '')}",
        flush=True,
    )


def run_once(sources: list[PageSource], notify_baseline: bool = False) -> int:
    enabled_sources = filter_enabled_named_for_run(sources, label="TrendForce 页面")
    if not enabled_sources:
        return 0
    sources = enabled_sources
    total_new = 0
    for source in sources:
        try:
            items = extract_items(source)
            with connect_db() as conn:
                record_source_success(conn, "trendforce_page", source.name)
                scope_state = load_scope_state(conn, source.name)
            expanded_scope_baseline = not bool(scope_state.get(EXPANDED_SCOPE_BASELINE_STATE_KEY))
            new_items = save_new_page_items_with_retry(
                source,
                items,
                notify_baseline=notify_baseline,
                expanded_scope_baseline=expanded_scope_baseline,
            )
            if items and expanded_scope_baseline:
                scope_state[EXPANDED_SCOPE_BASELINE_STATE_KEY] = datetime.now(timezone.utc).isoformat()
                with connect_db() as conn:
                    save_scope_state(conn, source.name, scope_state)
        except Exception as exc:
            with connect_db() as conn:
                record_source_failure(conn, "trendforce_page", source.name, exc)
            print(f"{source.name} 页面监控失败：{exc}")
            continue

        if not new_items:
            print(f"{source.name}: 没有发现需通知的新条目。")
            continue
        total_new += len(new_items)
        print(f"{source.name}: 发现 {len(new_items)} 条新条目。")
        for item in new_items:
            print("=" * 80)
            print(item.get("title", ""))
            print(item.get("url", ""))
            print(item.get("published_at", ""))
            notify_item(item)
    return total_new


def selected_sources(names: list[str]) -> list[PageSource]:
    if not names:
        return list(TREND_FORCE_PAGE_SOURCES)
    by_name = {source.name: source for source in TREND_FORCE_PAGE_SOURCES}
    missing = [name for name in names if name not in by_name]
    if missing:
        raise SystemExit(f"未知 TrendForce 页面源：{', '.join(missing)}")
    return [by_name[name] for name in names]


def main() -> int:
    load_env(ENV_PATH)
    config = llm_config()
    if config:
        _, base_url, model = config
        print(f"TrendForce page monitor LLM config: {base_url} / {model}", flush=True)
    else:
        print("TrendForce page monitor LLM config: 未配置", flush=True)
    parser = argparse.ArgumentParser(description="Monitor TrendForce official list pages.")
    parser.add_argument("--source", action="append", default=[], help="只监控指定 PageSource name，可重复。")
    parser.add_argument("--interval", type=int, default=int(os.getenv("TRENDFORCE_PAGE_INTERVAL", "0")))
    parser.add_argument("--notify-baseline", action="store_true", help="首次建立基线时也发送通知。默认不发送旧条目。")
    args = parser.parse_args()
    sources = selected_sources(args.source)
    notify_baseline = args.notify_baseline or os.getenv("SURVEIL_NOTIFY_BASELINE", "") == "1"

    if args.interval <= 0:
        run_once(sources, notify_baseline=notify_baseline)
        return 0

    print(f"开始监控 {len(sources)} 个 TrendForce 官方页面，轮询间隔 {args.interval} 秒。")
    while True:
        run_once(sources, notify_baseline=notify_baseline)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
