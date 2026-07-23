#!/usr/bin/env python3
"""Poll RSS feeds and deduplicate new articles."""

from __future__ import annotations

import argparse
import html
import os
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable

import feedparser
import trafilatura

from collector_runtime import (
    filter_enabled_mapping_for_run,
    load_source_state as runtime_load_source_state,
    load_source_states,
    save_source_state as runtime_save_source_state,
    source_state_key,
    split_sources_by_backoff,
)
from db_utils import (
    connect_sqlite,
    ensure_seen_tables,
    ensure_source_state_table,
    retry_on_locked,
    update_seen_item_lifecycle,
)
from http_utils import http_get
from llm_analysis import llm_config
from market_flow import is_official_news_source, normalize_market_item, process_market_item
from media_sources import is_overseas_media_source, overseas_media_access_note, overseas_media_module
from media_keyword_config import is_media_focus_item
from production_admission import admission_lifecycle_values, persist_production_admission_context, production_admission_context
from trendforce_sources import DEFAULT_RSS_FEEDS
from x_check import load_env
from source_backoff import backoff_state_after_failure, clear_backoff_state
from source_health import record_source_failure, record_source_success
from source_profiles import SOURCE_PROFILE_CONFIG_PATH, runtime_profile_map


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "surveil.sqlite3"

DEFAULT_FEEDS = DEFAULT_RSS_FEEDS

CORE_COMPANY_FEEDS = {
    "openai_news",
    "nvidia_blog",
    "nvidia_developer_blog",
    "samsung_semiconductor_news",
    "samsung_global_semiconductor",
    "skhynix_newsroom",
    "micron_news_releases",
}

# Moving business filtering behind seen_items exposes older feed rows.  This
# marker records the first successful widened-scope response per source so
# those rows are retained as baseline and cannot be delivered retroactively.
EXPANDED_SCOPE_BASELINE_STATE_KEY = "expanded_scope_baseline_at"


def connect_db() -> sqlite3.Connection:
    conn = connect_sqlite(DB_PATH)
    ensure_seen_tables(conn)
    ensure_source_state_table(conn)
    conn.execute(
        """
        INSERT OR IGNORE INTO seen_sources (source, first_seen_at)
        SELECT source, MIN(first_seen_at)
        FROM seen_items
        GROUP BY source
        """
    )
    conn.commit()
    return conn


def parse_atom_date(value: str) -> str:
    if not value:
        return ""
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).astimezone(timezone.utc).isoformat()
    except ValueError:
        return parse_date(value)


def parse_date(value: str) -> str:
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, IndexError, AttributeError):
        return value


def feed_state_key(source: str) -> str:
    return source_state_key(source, prefix="rss_feed")


def load_source_state(conn: sqlite3.Connection, source: str) -> dict:
    return runtime_load_source_state(conn, source, prefix="rss_feed")


def save_source_state(conn: sqlite3.Connection, source: str, state: dict) -> None:
    runtime_save_source_state(conn, source, state, prefix="rss_feed")


def feed_entry_value(entry: dict, key: str) -> str:
    value = entry.get(key)
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def feed_entry_content(entry: dict) -> str:
    contents = entry.get("content")
    if isinstance(contents, list):
        parts = []
        for item in contents:
            if isinstance(item, dict):
                parts.append(str(item.get("value") or ""))
        return "\n\n".join(part for part in parts if part).strip()
    return ""


def feed_entry_categories(entry: dict) -> list[str]:
    tags = entry.get("tags")
    if not isinstance(tags, list):
        return []
    categories = []
    for tag in tags:
        if isinstance(tag, dict):
            term = str(tag.get("term") or "").strip()
            if term:
                categories.append(term)
    return categories


def parsed_feed_items(parsed: feedparser.FeedParserDict) -> list[dict]:
    items = []
    for entry in parsed.entries:
        title = feed_entry_value(entry, "title")
        link = feed_entry_value(entry, "link")
        guid = feed_entry_value(entry, "id") or feed_entry_value(entry, "guid") or link or title
        summary = feed_entry_value(entry, "summary") or feed_entry_value(entry, "description")
        content = feed_entry_content(entry)
        published_at = parse_atom_date(
            feed_entry_value(entry, "published")
            or feed_entry_value(entry, "updated")
            or feed_entry_value(entry, "created")
        )
        items.append(
            {
                "id": guid,
                "url": link,
                "title": strip_tags(title),
                "summary": summary,
                "content": content,
                "categories": feed_entry_categories(entry),
                "published_at": published_at,
            }
        )
    return items


def fetch_feed(source: str, url: str, state: dict | None = None) -> tuple[list[dict], dict, bool]:
    timeout = int(os.getenv("RSS_FETCH_TIMEOUT_SECONDS", "15"))
    retries = int(os.getenv("RSS_FETCH_RETRY_COUNT", os.getenv("SURVEIL_HTTP_RETRY_COUNT", "1")))
    headers = {
        "Accept": "application/rss+xml, application/atom+xml, application/rdf+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
    }
    state = state or {}
    if state.get("etag"):
        headers["If-None-Match"] = str(state["etag"])
    if state.get("modified"):
        headers["If-Modified-Since"] = str(state["modified"])
    response = http_get(url, headers=headers, timeout=timeout, retries=retries)
    next_state = dict(state)
    if response.status_code == 304:
        next_state["last_checked_at"] = datetime.now(timezone.utc).isoformat()
        return [], next_state, True

    parsed = feedparser.parse(response.content)
    if parsed.get("bozo") and parsed.get("bozo_exception"):
        print(f"{source} feedparser warning: {parsed.get('bozo_exception')}", flush=True)
    etag = response.headers.get("etag") or parsed.get("etag")
    modified = response.headers.get("last-modified")
    parsed_modified = parsed.get("modified")
    if not modified and parsed_modified:
        modified = parsed_modified if isinstance(parsed_modified, str) else ""
    if etag:
        next_state["etag"] = str(etag)
    if modified:
        next_state["modified"] = str(modified)
    next_state["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    next_state["last_status_code"] = response.status_code
    return parsed_feed_items(parsed), next_state, False


def strip_tags(value: str) -> str:
    value = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", "", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</p>", "\n\n", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    return html.unescape(value).strip()


def fetch_article_body(url: str) -> tuple[str, str]:
    if not url:
        return "", "RSS"
    response = http_get(
        url,
        headers={"Accept": "text/html,application/xhtml+xml"},
        timeout=int(os.getenv("RSS_ARTICLE_FETCH_TIMEOUT_SECONDS", "30")),
    )
    html_text = response.content.decode("utf-8", errors="replace")

    extracted = trafilatura.extract(
        html_text,
        url=response.url,
        include_comments=False,
        include_tables=False,
        favor_recall=True,
    )
    if extracted and len(extracted.strip()) > 80:
        return extracted.strip(), "页面正文"

    paragraphs = re.findall(r"(?is)<p[^>]*>(.*?)</p>", html_text)
    cleaned = [strip_tags(p) for p in paragraphs]
    cleaned = [
        p
        for p in cleaned
        if len(p) > 40
        and not p.lower().startswith(("copyright", "related", "for more information"))
        and "cookie" not in p.lower()
    ]
    if cleaned:
        return "\n\n".join(cleaned), "页面正文"

    meta = re.search(r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']', html_text, re.I | re.S)
    if meta:
        return html.unescape(meta.group(1)).strip(), "页面 meta description"
    return "", "RSS"


def source_has_seen(conn: sqlite3.Connection, source: str) -> bool:
    row = conn.execute("SELECT 1 FROM seen_sources WHERE source = ? LIMIT 1", (source,)).fetchone()
    return row is not None


def save_new_items(
    conn: sqlite3.Connection,
    source: str,
    items: Iterable[dict],
    notify_baseline: bool = False,
    source_label: str | None = None,
    expanded_scope_baseline: bool = False,
) -> list[dict]:
    items_list = list(items)
    new_items: list[dict] = []
    is_baseline = not source_has_seen(conn, source)
    if is_baseline:
        conn.execute(
            "INSERT OR IGNORE INTO seen_sources (source, first_seen_at) VALUES (?, ?)",
            (source, datetime.now(timezone.utc).isoformat()),
        )
    now = datetime.now(timezone.utc).isoformat()
    baseline_only = bool((is_baseline and not notify_baseline) or expanded_scope_baseline)
    for item in sorted(items_list, key=lambda entry: entry.get("published_at") or ""):
        item_id = str(item["id"])
        existing = conn.execute(
            """
            SELECT collection_class, processability_status, admission_status,
                   processing_status, first_seen_at
            FROM seen_items WHERE source = ? AND item_id = ?
            """,
            (source, item_id),
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
                    source,
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
                    source,
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
        if not baseline_only:
            new_items.append(item)
    conn.commit()
    if is_baseline and not notify_baseline:
        label = source_label or source
        print(f"{label}: 首次建立基线 {len(items_list)} 条，默认不发送旧内容。")
        return []
    return new_items


def save_new_items_with_retry(
    source: str,
    items: Iterable[dict],
    notify_baseline: bool = False,
    source_label: str | None = None,
    expanded_scope_baseline: bool = False,
) -> list[dict]:
    def operation() -> list[dict]:
        with connect_db() as conn:
            return save_new_items(
                conn,
                source,
                items,
                notify_baseline=notify_baseline,
                source_label=source_label,
                expanded_scope_baseline=expanded_scope_baseline,
            )

    return retry_on_locked(operation)


def enrich_item(source: str, item: dict) -> dict:
    body = ""
    body_source = "RSS"
    detail_fetch_status = "not_required"
    detail_fetch_error = ""
    should_fetch_body = True
    if source.startswith("digitimes_") and os.getenv("DIGITIMES_FETCH_BODY", "").strip() != "1":
        should_fetch_body = False
        body_source = "RSS description"
    if source == "value_directory_ib_stocks":
        should_fetch_body = False
        body_source = "价值目录列表页"
    if should_fetch_body:
        detail_fetch_status = "pending"
        try:
            body, body_source = fetch_article_body(item.get("url", ""))
            detail_fetch_status = "succeeded" if body else "empty"
        except Exception as exc:
            detail_fetch_status = "failed"
            detail_fetch_error = f"{type(exc).__name__}: {str(exc)[:400]}"
            print(f"{source} 正文抓取失败，回退 RSS：{exc}")
    item = dict(item)
    item["full_text"] = body or strip_tags(item.get("content") or item.get("summary", ""))
    item["body_source"] = body_source if body else "RSS description"
    item["detail_fetch_status"] = detail_fetch_status
    if detail_fetch_error:
        item["detail_fetch_error"] = detail_fetch_error
    if is_overseas_media_source(source):
        item.setdefault("source_module", overseas_media_module(source))
        item.setdefault("access_note", overseas_media_access_note(source, item["body_source"]))
    return item


def set_seen_item_lifecycle(source: str, item_id: str, **values: str | None) -> None:
    """Persist the retry/process state without making it a second article store."""
    with connect_db() as conn:
        update_seen_item_lifecycle(conn, source, item_id, **values)
        conn.commit()


def _prepare_retry_item(item: dict) -> tuple[dict, str]:
    prepared = dict(item)
    item_id = str(prepared.pop("id", "") or "")
    prepared.pop("_seen_item_retry", None)
    prepared.pop("_seen_item_retry_first_seen_at", None)
    return prepared, item_id


def _finish_seen_item(source: str, item_id: str, enriched: dict) -> None:
    detail_status = str(enriched.get("detail_fetch_status") or "not_required")
    processability = "fallback" if detail_status in {"failed", "empty"} else "succeeded"
    reason = "detail_fallback" if processability == "fallback" else ""
    set_seen_item_lifecycle(
        source,
        item_id,
        processability_status=processability,
        processability_reason=reason,
        admission_status="pending",
        admission_reason="",
        processing_status="not_applicable",
        processing_error="",
    )


def _complete_seen_item(source: str, item_id: str, admission) -> None:
    evaluated_at = datetime.now(timezone.utc).isoformat()
    set_seen_item_lifecycle(
        source,
        item_id,
        **admission_lifecycle_values(admission, processing_status="succeeded"),
        processed_at=evaluated_at,
    )


def _fail_seen_item(source: str, item_id: str, exc: Exception) -> None:
    set_seen_item_lifecycle(
        source,
        item_id,
        processing_status="failed_retryable",
        processing_error=f"{type(exc).__name__}: {str(exc)[:400]}",
        processed_at=None,
        lifecycle_updated_at=datetime.now(timezone.utc).isoformat(),
    )


def notify_item(source: str, item: dict) -> None:
    item, item_id = _prepare_retry_item(item)
    try:
        enriched = enrich_item(source, item)
        _finish_seen_item(source, item_id, enriched)
        normalized = normalize_market_item(source, enriched, store_kind="article", source_profile_id=source)
        admission_context = persist_production_admission_context(normalized, production_admission_context(normalized, db_path=DB_PATH), db_path=DB_PATH)
        admission = admission_context.result
        if admission.status != "admitted":
            set_seen_item_lifecycle(
                source,
                item_id,
                **admission_lifecycle_values(admission, processing_status="not_applicable"),
                processed_at=datetime.now(timezone.utc).isoformat(),
            )
            print(f"{source} 五类范围准入排除：title={enriched.get('title', '')}", flush=True)
            return
        set_seen_item_lifecycle(
            source,
            item_id,
            **admission_lifecycle_values(admission, processing_status="pending"),
        )
        outcome = process_market_item(
            normalized,
            enriched,
            store_kind="article",
            source_profile_id=source,
            db_path=DB_PATH,
            production_admission=admission,
            production_portfolio=admission_context.portfolio,
            market_item_id=admission_context.market_item_id,
            market_review_id=admission_context.market_review_id,
        )
        _complete_seen_item(source, item_id, admission)
    except Exception as exc:
        _fail_seen_item(source, item_id, exc)
        raise
    item = enriched
    review = outcome.payload
    print(
        f"{source} 决策层：importance={review.get('importance')} "
        f"push={review.get('push_now')} title={item.get('title', '')}",
        flush=True,
    )
    if outcome.delivery_status == "duplicate":
        print(f"{source} 国际投行主题策略去重：title={item.get('title', '')}", flush=True)


def handle_official_news_item(source: str, item: dict) -> None:
    item, item_id = _prepare_retry_item(item)
    try:
        enriched = enrich_item(source, item)
        _finish_seen_item(source, item_id, enriched)
        normalized = normalize_market_item(source, enriched, store_kind="official", source_profile_id=source)
        admission_context = persist_production_admission_context(normalized, production_admission_context(normalized, db_path=DB_PATH), db_path=DB_PATH)
        admission = admission_context.result
        if admission.status != "admitted":
            set_seen_item_lifecycle(
                source,
                item_id,
                **admission_lifecycle_values(admission, processing_status="not_applicable"),
                processed_at=datetime.now(timezone.utc).isoformat(),
            )
            print(f"{source} 五类范围准入排除：title={enriched.get('title', '')}", flush=True)
            return
        set_seen_item_lifecycle(
            source,
            item_id,
            **admission_lifecycle_values(admission, processing_status="pending"),
        )
        outcome = process_market_item(
            normalized,
            enriched,
            store_kind="official",
            source_profile_id=source,
            db_path=DB_PATH,
            production_admission=admission,
            production_portfolio=admission_context.portfolio,
            market_item_id=admission_context.market_item_id,
            market_review_id=admission_context.market_review_id,
        )
        _complete_seen_item(source, item_id, admission)
    except Exception as exc:
        _fail_seen_item(source, item_id, exc)
        raise
    review = outcome.payload
    print(
        f"{source} 官网新闻分流：importance={review.get('importance')} "
        f"push={review.get('should_push_now')} title={enriched.get('title', '')}",
        flush=True,
    )


def filter_items(source: str, items: list[dict]) -> list[dict]:
    if not source.startswith("trendforce_") and source not in CORE_COMPANY_FEEDS and not is_overseas_media_source(source):
        return items
    filtered = []
    for item in items:
        if is_media_focus_item(
            item.get("title", ""),
            item.get("summary", ""),
            " ".join(item.get("categories", [])),
            item.get("url", ""),
        ):
            filtered.append(item)
    return filtered


def run_once(feeds: dict[str, str], notify_baseline: bool = False) -> int:
    feeds = filter_enabled_mapping_for_run(feeds, label="RSS")
    if not feeds:
        return 0
    total_new = 0
    with connect_db() as conn:
        feed_states = load_source_states(conn, feeds, prefix="rss_feed")
    max_workers = max(1, int(os.getenv("RSS_FETCH_MAX_WORKERS", "8") or "8"))
    fetched: dict[str, tuple[list[dict], dict, bool]] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(feeds)))) as executor:
        runnable_sources, skipped_sources = split_sources_by_backoff(feeds, feed_states)
        futures = {
            executor.submit(fetch_feed, source, url, feed_states.get(source, {})): source
            for source, url in feeds.items() if source in runnable_sources
        }
        for future in as_completed(futures):
            source = futures[future]
            try:
                items, next_state, not_modified = future.result()
                fetched[source] = (items, clear_backoff_state(next_state), not_modified)
                with connect_db() as conn:
                    record_source_success(conn, "rss_monitor", source)
            except Exception as exc:
                with connect_db() as conn:
                    save_source_state(conn, source, backoff_state_after_failure(source, feed_states.get(source, {})))
                    record_source_failure(conn, "rss_monitor", source, exc)
                print(f"{source} 抓取失败：{exc}", flush=True)

    for source, _url in feeds.items():
        if source in skipped_sources:
            continue
        if source not in fetched:
            continue
        try:
            items, next_state, not_modified = fetched[source]
            with connect_db() as conn:
                save_source_state(conn, source, next_state)
            if not_modified:
                print(f"{source}: feed 未变化。", flush=True)
                continue
            # The first successful widened-scope response establishes a new
            # no-notify boundary.  Previously invisible rows are retained as
            # baseline; only later discoveries enter the live path.
            expanded_scope_baseline = not bool(next_state.get(EXPANDED_SCOPE_BASELINE_STATE_KEY))
            new_items = save_new_items_with_retry(
                source,
                items,
                notify_baseline=notify_baseline,
                expanded_scope_baseline=expanded_scope_baseline,
            )
            if items and expanded_scope_baseline:
                next_state[EXPANDED_SCOPE_BASELINE_STATE_KEY] = next_state.get(
                    "last_checked_at", datetime.now(timezone.utc).isoformat()
                )
                with connect_db() as conn:
                    save_source_state(conn, source, next_state)
        except Exception as exc:
            with connect_db() as conn:
                record_source_failure(conn, "rss_monitor", source, exc)
            print(f"{source} 处理失败：{exc}", flush=True)
            continue
        if not new_items:
            print(f"{source}: 没有发现新文章。", flush=True)
            continue
        total_new += len(new_items)
        print(f"{source}: 发现 {len(new_items)} 篇新文章。", flush=True)
        for item in new_items:
            print("=" * 80)
            print(item.get("title", ""))
            print(item.get("url", ""))
            print(item.get("published_at", ""))
            try:
                if is_official_news_source(source):
                    handle_official_news_item(source, item)
                else:
                    notify_item(source, item)
            except Exception as exc:  # noqa: BLE001 - keep other feeds alive
                print(f"{source} 通知失败：{exc}")
    return total_new


def parse_feed_args(feed_args: list[str]) -> dict[str, str]:
    if not feed_args:
        return dict(DEFAULT_FEEDS)
    feeds: dict[str, str] = {}
    for raw in feed_args:
        if "=" not in raw:
            raise SystemExit("--feed 格式必须是 name=url")
        name, url = raw.split("=", 1)
        feeds[name.strip()] = url.strip()
    return feeds


def split_filter_values(*values: str | list[str] | tuple[str, ...] | None) -> set[str]:
    parsed: set[str] = set()
    for value in values:
        if not value:
            continue
        if isinstance(value, str):
            raw_values = [value]
        else:
            raw_values = list(value)
        for raw in raw_values:
            parsed.update(part.strip() for part in str(raw or "").split(",") if part.strip())
    return parsed


def filter_feeds_by_profile_categories(
    feeds: dict[str, str],
    *,
    include_categories: set[str] | None = None,
    exclude_categories: set[str] | None = None,
) -> dict[str, str]:
    include_categories = include_categories or set()
    exclude_categories = exclude_categories or set()
    if not include_categories and not exclude_categories:
        return feeds

    profiles = runtime_profile_map(config_path=SOURCE_PROFILE_CONFIG_PATH)
    filtered: dict[str, str] = {}
    skipped: list[str] = []
    for source, url in feeds.items():
        category = str(profiles.get(source, {}).get("category") or "")
        if include_categories and category not in include_categories:
            skipped.append(source)
            continue
        if exclude_categories and category in exclude_categories:
            skipped.append(source)
            continue
        filtered[source] = url
    if skipped:
        print(
            "RSS monitor source category filter: "
            f"跳过 {len(skipped)} 个 source：{', '.join(skipped[:12])}",
            flush=True,
        )
    return filtered


def main() -> int:
    load_env(ENV_PATH)
    config = llm_config()
    if config:
        _, base_url, model = config
        print(f"RSS monitor LLM config: {base_url} / {model}", flush=True)
    else:
        print("RSS monitor LLM config: 未配置", flush=True)
    parser = argparse.ArgumentParser(description="Monitor RSS feeds.")
    parser.add_argument("--feed", action="append", default=[], help="RSS feed as name=url. Repeatable.")
    parser.add_argument("--interval", type=int, default=0, help="Polling interval in seconds. 0 means run once.")
    parser.add_argument("--notify-baseline", action="store_true", help="首次建立基线时也发送通知。默认不发送旧条目。")
    parser.add_argument(
        "--include-profile-category",
        action="append",
        default=[],
        help="只运行指定 source profile category，可重复或用逗号分隔。",
    )
    parser.add_argument(
        "--exclude-profile-category",
        action="append",
        default=[],
        help="排除指定 source profile category，可重复或用逗号分隔。",
    )
    args = parser.parse_args()
    feeds = filter_feeds_by_profile_categories(
        parse_feed_args(args.feed),
        include_categories=split_filter_values(
            args.include_profile_category,
            os.getenv("RSS_MONITOR_INCLUDE_PROFILE_CATEGORIES"),
        ),
        exclude_categories=split_filter_values(
            args.exclude_profile_category,
            os.getenv("RSS_MONITOR_EXCLUDE_PROFILE_CATEGORIES"),
        ),
    )
    notify_baseline = args.notify_baseline or os.getenv("SURVEIL_NOTIFY_BASELINE", "") == "1"

    if args.interval <= 0:
        run_once(feeds, notify_baseline=notify_baseline)
        return 0

    print(f"开始监控 {len(feeds)} 个 RSS feed，轮询间隔 {args.interval} 秒。")
    while True:
        run_once(feeds, notify_baseline=notify_baseline)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
