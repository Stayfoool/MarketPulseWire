#!/usr/bin/env python3
"""Monitor domestic finance media sources with a shared decision/delivery flow."""

from __future__ import annotations

import argparse
import html
import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import feedparser
import wallstreetcn_monitor as wallstreetcn

from china_media_sources import (
    CHINA_MEDIA_ACCESS_NOTES,
    CHINA_MEDIA_FEEDS,
    CHINA_MEDIA_LABELS,
    china_media_access_note,
    china_media_module,
    is_china_media_source,
)
from collector_runtime import (
    filter_enabled_mapping_for_run,
    load_source_state as runtime_load_source_state,
    save_source_state as runtime_save_source_state,
    split_sources_by_backoff,
)
from db_utils import connect_sqlite, ensure_seen_tables, retry_on_locked, update_seen_item_lifecycle
from env_utils import load_env
from http_utils import http_get
from investment_universe import investment_universe_match, relevant_digest_for_mixed_item
from international_bank_fed import fed_path_candidate
from llm_analysis import llm_config
from macro_policy import is_macro_event
from market_flow import normalize_market_item, process_market_item, record_rule_comparison
from market_item import NormalizedMarketItem
from media_keyword_config import is_media_focus_item
from rss_monitor import DB_PATH, fetch_article_body, parse_date, strip_tags
from source_backoff import backoff_state_after_failure, clear_backoff_state
from source_health import record_source_failure, record_source_success
from time_utils import parse_datetime_to_utc_iso, timestamp_to_utc_iso


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"

DOMESTIC_FEED_SOURCES = {
    "yicai_brief": CHINA_MEDIA_FEEDS["yicai_brief"],
    "cls_telegraph_api": CHINA_MEDIA_FEEDS["cls_telegraph_api"],
    "star_market_daily_subject": CHINA_MEDIA_FEEDS["star_market_daily_subject"],
    "jin10_rsshub_important": CHINA_MEDIA_FEEDS["jin10_rsshub_important"],
}

YICAI_RSSHUB_FALLBACK = CHINA_MEDIA_FEEDS["yicai_brief_rsshub"]
SINA_FINANCE_SOURCE = "sina_finance_articles"
SINA_FINANCE_ROLL_URL = CHINA_MEDIA_FEEDS[SINA_FINANCE_SOURCE]
SINA_FINANCE_REFERER = "https://finance.sina.com.cn/roll/"
SINA_FINANCE_DEFAULT_LIDS = ("2517",)
SINA_FINANCE_PENDING_ROLL_STATE: dict[str, Any] = {}
WALLSTREETCN_SOURCE = wallstreetcn.SOURCE
WALLSTREETCN_EMPTY_DETAIL_ERROR = "WallstreetCN detail lacks non-empty title/body"
WALLSTREETCN_RETRY_DELIVERY_MAX_AGE = timedelta(hours=24)
SEEN_ITEM_RETRY_KEY = "_seen_item_retry"
SEEN_ITEM_RETRY_FIRST_SEEN_KEY = "_seen_item_retry_first_seen_at"


def connect_db() -> sqlite3.Connection:
    return connect_sqlite(DB_PATH)


def ensure_seen_table(conn: sqlite3.Connection) -> None:
    ensure_seen_tables(conn)
    conn.commit()


def canonical_url(url: str) -> str:
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(key, value) for key, value in query if key not in {"utm_source", "utm_medium", "utm_campaign", "from"}]
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


def canonical_sina_url(url: str) -> str:
    normalized = canonical_url(str(url or "").strip().replace("\\/", "/"))
    if normalized.startswith("http://finance.sina.com.cn/"):
        normalized = "https://" + normalized[len("http://") :]
    return normalized


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").replace("Ａ", "A")).casefold()


def title_similarity(a: str, b: str) -> bool:
    if not a or not b:
        return False
    na = normalize_text(a)
    nb = normalize_text(b)
    return na == nb or na in nb or nb in na


def is_star_market_daily_text(*values: Any) -> bool:
    text = " ".join(str(value or "") for value in values)
    return "科创板日报" in text or "科创板最新动态" in text


def balanced_json_prefix(raw: str) -> str | None:
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(raw)):
        char = raw[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return raw[start : index + 1]
    return None


def fetch_json(url: str) -> list[dict[str, Any]]:
    response = http_get(
        url,
        headers={
            "Accept": "application/json,text/plain,*/*",
        },
        timeout=int(os.getenv("CHINA_MEDIA_FETCH_TIMEOUT_SECONDS", "20")),
    )
    body = response.content.decode("utf-8", errors="replace")
    data = json.loads(body)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "list", "items", "result"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def cls_sign(params: dict[str, str]) -> str:
    """Sign CLS public frontend API params.

    The production web/mobile frontend signs the sorted query string with
    sha1 first, then md5 over the sha1 hex digest. The sign field itself is
    intentionally excluded from params.
    """
    qs = "&".join(f"{key}={value}" for key, value in sorted(params.items()))
    return hashlib.md5(hashlib.sha1(qs.encode("utf-8")).hexdigest().encode("utf-8")).hexdigest()


def parse_cls_time(value: Any) -> str:
    if value in (None, ""):
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    if raw.isdigit():
        return timestamp_to_utc_iso(raw)
    return parse_datetime_to_utc_iso(raw)


def cls_product_label(value: Any) -> str:
    match = re.match(r"^【([^】]+)】", str(value or "").strip())
    return match.group(1).strip() if match else ""


def cls_author_targets(value: Any) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for part in str(value or "").split("##"):
        raw_code, separator, raw_name = part.strip().partition("@@")
        if not raw_code and not raw_name:
            continue
        code_text = raw_code.strip()
        name = raw_name.strip() if separator else ""
        match = re.fullmatch(r"(?i)(sh|sz|bj|hk)(\d{5,6})", code_text)
        if match:
            exchange = match.group(1).upper()
            code = f"{match.group(2)}.{exchange}"
        else:
            code = code_text
        identity = (name.casefold(), code.casefold())
        if identity in seen:
            continue
        seen.add(identity)
        targets.append({"name": name, "code": code, "raw_code": code_text})
    return targets


def cls_product_metadata(row: dict[str, Any]) -> dict[str, Any]:
    official_title = str(row.get("title") or "").strip()
    share_img = str(row.get("share_img") or "").strip()
    author_extends = str(row.get("author_extends") or "").strip()
    return {
        "type": str(row.get("type") if row.get("type") is not None else ""),
        "product_label": cls_product_label(official_title),
        "official_title": official_title,
        "share_img": share_img,
        "share_img_name": share_img.rsplit("/", 1)[-1] if share_img else "",
        "is_vip": share_img.rsplit("/", 1)[-1].casefold() == "vip.png" if share_img else False,
        "author_extends": author_extends,
        "author_targets": cls_author_targets(author_extends),
    }


def sina_finance_roll_lids() -> list[str]:
    raw = os.getenv("SINA_FINANCE_ROLL_LIDS", "").strip()
    parts = raw.split(",") if raw else list(SINA_FINANCE_DEFAULT_LIDS)
    lids: list[str] = []
    for part in parts:
        lid = str(part or "").strip()
        if lid and lid not in lids:
            lids.append(lid)
    return lids or list(SINA_FINANCE_DEFAULT_LIDS)


def normalize_sina_docid(value: Any) -> str:
    raw = str(value or "").strip()
    if raw.startswith("comos:"):
        raw = raw.split(":", 1)[1]
    match = re.search(r"doc-i([a-z0-9]+)\.shtml", raw)
    if match:
        return match.group(1)
    match = re.search(r"detail-i([a-z0-9]+)\.d\.html", raw)
    if match:
        return match.group(1)
    return raw


def compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def sina_meta_content(html_text: str, keys: Iterable[str]) -> str:
    ordered_keys = [str(key).lower() for key in keys]
    for tag in re.findall(r"(?is)<meta\b[^>]*>", html_text):
        attrs = {
            str(name).lower(): html.unescape(value).strip()
            for name, _quote, value in re.findall(r"""([:\w-]+)\s*=\s*(['"])(.*?)\2""", tag, flags=re.S)
        }
        marker = str(attrs.get("name") or attrs.get("property") or "").lower()
        if marker in ordered_keys and attrs.get("content"):
            return attrs["content"]
    return ""


def first_sina_meta_content(html_text: str, keys: Iterable[str]) -> str:
    for key in keys:
        value = sina_meta_content(html_text, [key])
        if value:
            return value
    return ""


def sina_page_title(html_text: str) -> str:
    title = sina_meta_content(html_text, {"og:title", "twitter:title"})
    if not title:
        match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html_text)
        title = strip_tags(match.group(1)) if match else ""
    return re.sub(r"[_-]新浪财经[_-]新浪网\s*$", "", compact_text(title))


def sina_artibody_html(html_text: str) -> str:
    match = re.search(r"""(?is)<div\b[^>]*\bid\s*=\s*(['"])artibody\1[^>]*>""", html_text)
    if not match:
        return ""
    start = match.end()
    markers = [
        "<!-- 原始正文end -->",
        "<!-- 正文 end -->",
        '<div class="article-bottom',
        "<div class='article-bottom",
        '<div id="article-bottom',
        "<div id='article-bottom",
    ]
    end = len(html_text)
    for marker in markers:
        index = html_text.find(marker, start)
        if index >= 0:
            end = min(end, index)
    return html_text[start:end]


def parse_sina_artibody(html_text: str) -> str:
    body_html = sina_artibody_html(html_text)
    if not body_html:
        return ""
    body_html = re.split(r"(?is)<div\b[^>]*\bclass\s*=\s*(['\"])[^'\"]*appendQr_wrap", body_html, maxsplit=1)[0]
    body_html = re.sub(r"(?is)<script\b.*?</script>|<style\b.*?</style>|<!--.*?-->", "", body_html)
    paragraphs = re.findall(r"(?is)<p\b[^>]*>(.*?)</p>", body_html)
    cleaned = [compact_text(strip_tags(paragraph)) for paragraph in paragraphs]
    cleaned = [
        paragraph
        for paragraph in cleaned
        if paragraph
        and not paragraph.startswith(("责任编辑：", "新浪声明", "海量资讯、精准解读"))
        and "新浪财经APP" not in paragraph
    ]
    if cleaned:
        return "\n\n".join(cleaned)[:40000]
    return compact_text(strip_tags(body_html))[:40000]


def parse_sina_detail_html(html_text: str, *, fallback_url: str = "") -> dict[str, str]:
    title = sina_page_title(html_text)
    published_at = parse_datetime_to_utc_iso(
        first_sina_meta_content(
            html_text,
            [
                "bytedance:published_time",
                "weibo: article:create_at",
                "article:published_time",
                "og:published_time",
            ],
        )
    )
    author = sina_meta_content(html_text, {"article:author", "weibo: article:author"})
    description = sina_meta_content(html_text, {"description", "og:description"})
    canonical = canonical_sina_url(sina_meta_content(html_text, {"og:url"}) or fallback_url)
    body = parse_sina_artibody(html_text)
    return {
        "title": title,
        "published_at": published_at,
        "author": author,
        "description": compact_text(description),
        "url": canonical,
        "full_text": body,
        "docid": normalize_sina_docid(canonical),
    }


def fetch_sina_roll_page(lid: str, page: int, num: int) -> list[dict[str, Any]]:
    params = {
        "pageid": os.getenv("SINA_FINANCE_ROLL_PAGEID", "153"),
        "lid": lid,
        "k": "",
        "num": str(num),
        "page": str(page),
        "r": str(time.time()),
    }
    response = http_get(
        f"{SINA_FINANCE_ROLL_URL}?{urllib.parse.urlencode(params)}",
        headers={
            "Accept": "application/json,text/plain,*/*",
            "Referer": SINA_FINANCE_REFERER,
        },
        timeout=env_int("SINA_FINANCE_ROLL_TIMEOUT_SECONDS", 15, minimum=1),
    )
    payload = json.loads(response.content.decode("utf-8", errors="replace"))
    if not isinstance(payload, dict):
        raise RuntimeError("新浪财经滚动 API 响应格式异常：root 不是 JSON object")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("新浪财经滚动 API 响应格式异常：缺少 result")
    status = result.get("status") if isinstance(result.get("status"), dict) else {}
    if str(status.get("code", "0")) not in {"0", ""}:
        raise RuntimeError(f"新浪财经滚动 API 返回错误：{status}")
    rows = result.get("data")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise RuntimeError("新浪财经滚动 API 响应格式异常：data 不是列表")
    return [row for row in rows if isinstance(row, dict)]


def sina_roll_row_to_item(row: dict[str, Any], *, lid: str, page: int) -> dict[str, Any] | None:
    title = strip_tags(str(row.get("title") or "")).strip()
    url = canonical_sina_url(str(row.get("url") or row.get("wapurl") or ""))
    docid = normalize_sina_docid(row.get("docid") or url)
    if not title and not url:
        return None
    published_at = parse_datetime_to_utc_iso(row.get("ctime") or row.get("mtime") or row.get("intime") or "")
    summary = strip_tags(str(row.get("intro") or row.get("summary") or row.get("wapsummary") or "")).strip()
    media_name = str(row.get("media_name") or row.get("media") or row.get("source") or "").strip()
    item_id = docid or url or title
    return {
        "id": item_id,
        "url": url,
        "title": title,
        "summary": summary,
        "content": "",
        "published_at": published_at,
        "source_module": CHINA_MEDIA_LABELS[SINA_FINANCE_SOURCE],
        "access_note": CHINA_MEDIA_ACCESS_NOTES[SINA_FINANCE_SOURCE],
        "body_source": "新浪财经滚动 API",
        "raw": {
            "docid": docid,
            "roll_docid": row.get("docid"),
            "roll_lid": lid,
            "roll_page": page,
            "roll_ctime": row.get("ctime"),
            "roll_media_name": media_name,
            "roll_channelid": row.get("channelid"),
            "roll_categoryid": row.get("categoryid"),
            "wapurl": row.get("wapurl"),
        },
    }


def merge_sina_roll_item(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    raw = dict(merged.get("raw") or {})
    incoming_raw = dict(incoming.get("raw") or {})
    channels = list(raw.get("roll_channels") or [])
    existing_channel = {
        "lid": raw.get("roll_lid"),
        "page": raw.get("roll_page"),
        "ctime": raw.get("roll_ctime"),
    }
    if existing_channel["lid"] and existing_channel not in channels:
        channels.append(existing_channel)
    channel = {
        "lid": incoming_raw.get("roll_lid"),
        "page": incoming_raw.get("roll_page"),
        "ctime": incoming_raw.get("roll_ctime"),
    }
    if channel not in channels:
        channels.append(channel)
    raw.update({key: value for key, value in incoming_raw.items() if key not in raw or raw.get(key) in (None, "")})
    raw["roll_channels"] = channels
    merged["raw"] = raw
    if not merged.get("summary") and incoming.get("summary"):
        merged["summary"] = incoming["summary"]
    return merged


def seen_item_ids_for_source(source: str) -> set[str]:
    try:
        with connect_db() as conn:
            ensure_seen_table(conn)
            return {
                str(row[0] or "")
                for row in conn.execute("SELECT item_id FROM seen_items WHERE source = ?", (source,))
            }
    except sqlite3.Error:
        return set()


def fetch_sina_detail(url: str) -> dict[str, str]:
    response = http_get(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": SINA_FINANCE_REFERER,
        },
        timeout=env_int("SINA_FINANCE_DETAIL_TIMEOUT_SECONDS", 20, minimum=1),
    )
    return parse_sina_detail_html(response.content.decode("utf-8", errors="replace"), fallback_url=str(response.url))


def enrich_sina_finance_item(item: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(item)
    raw = dict(enriched.get("raw") or {})
    try:
        detail = fetch_sina_detail(str(enriched.get("url") or ""))
        if detail.get("title"):
            enriched["title"] = detail["title"]
        if detail.get("published_at"):
            enriched["published_at"] = detail["published_at"]
        if detail.get("url"):
            enriched["url"] = detail["url"]
        if detail.get("description") and not enriched.get("summary"):
            enriched["summary"] = detail["description"]
        if detail.get("full_text"):
            enriched["full_text"] = detail["full_text"]
            enriched["body_source"] = "新浪财经详情页 #artibody"
        else:
            enriched["full_text"] = str(enriched.get("summary") or "")
            enriched["body_source"] = "新浪财经滚动 API 摘要（详情页正文为空）"
        raw.update(
            {
                "detail_fetch_status": "ok",
                "detail_author": detail.get("author", ""),
                "detail_docid": detail.get("docid", ""),
                "detail_body_chars": len(detail.get("full_text") or ""),
            }
        )
    except Exception as exc:  # noqa: BLE001 - retain the API row as an auditable fallback
        enriched["full_text"] = str(enriched.get("summary") or "")
        enriched["body_source"] = "新浪财经滚动 API 摘要（详情页抓取失败）"
        raw.update({"detail_fetch_status": "failed", "detail_fetch_error": f"{type(exc).__name__}: {exc}"})
        if not enriched["full_text"]:
            raise RuntimeError(f"新浪财经详情页解析失败且滚动摘要为空：{enriched.get('url')} ({exc})") from exc
    enriched["raw"] = raw
    return enriched


def parse_sina_finance_article_items(
    *,
    persist_state: bool = True,
    enrich_details: bool = True,
) -> list[dict[str, Any]]:
    source = SINA_FINANCE_SOURCE
    if persist_state:
        SINA_FINANCE_PENDING_ROLL_STATE.clear()
    state = load_source_state(source) if persist_state else {}
    watermarks = state.get("roll_watermarks") if isinstance(state.get("roll_watermarks"), dict) else {}
    page_size = env_int("SINA_FINANCE_ROLL_PAGE_SIZE", 30, minimum=1)
    max_pages = env_int("SINA_FINANCE_ROLL_MAX_PAGES", 3, minimum=1)
    page_size = min(page_size, 100)
    seen_ids = seen_item_ids_for_source(source) if persist_state else set()
    by_key: dict[str, dict[str, Any]] = {}
    channel_errors: dict[str, str] = {}
    max_ctime_by_lid: dict[str, int] = {}
    for lid in sina_finance_roll_lids():
        previous = int(watermarks.get(lid) or 0)
        try:
            for page in range(1, max_pages + 1):
                rows = fetch_sina_roll_page(lid, page, page_size)
                if not rows:
                    break
                reached_previous = False
                for row in rows:
                    try:
                        ctime = int(str(row.get("ctime") or row.get("mtime") or row.get("intime") or "0"))
                    except ValueError:
                        ctime = 0
                    if ctime:
                        max_ctime_by_lid[lid] = max(max_ctime_by_lid.get(lid, 0), ctime)
                    if previous and ctime and ctime <= previous:
                        reached_previous = True
                        continue
                    item = sina_roll_row_to_item(row, lid=lid, page=page)
                    if not item:
                        continue
                    key = str(item.get("id") or item.get("url") or "")
                    if not key or key in seen_ids:
                        continue
                    by_key[key] = merge_sina_roll_item(by_key[key], item) if key in by_key else item
                if reached_previous:
                    break
        except Exception as exc:  # noqa: BLE001 - isolate a single roll channel
            channel_errors[lid] = f"{type(exc).__name__}: {exc}"
    if channel_errors and not max_ctime_by_lid:
        raise RuntimeError(f"新浪财经滚动 API 全部频道失败：{channel_errors}")
    if channel_errors:
        print(f"新浪财经滚动 API 部分频道失败：{channel_errors}", flush=True)
    items = sorted(by_key.values(), key=lambda item: str(item.get("published_at") or ""), reverse=True)
    discovered = [enrich_sina_finance_item(item) for item in items] if enrich_details else items
    if persist_state:
        next_watermarks = dict(watermarks)
        for lid, ctime in max_ctime_by_lid.items():
            if ctime:
                next_watermarks[lid] = max(int(next_watermarks.get(lid) or 0), ctime)
        SINA_FINANCE_PENDING_ROLL_STATE.clear()
        SINA_FINANCE_PENDING_ROLL_STATE.update(
            {
                "roll_watermarks": next_watermarks,
                "last_roll_checked_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        if channel_errors:
            SINA_FINANCE_PENDING_ROLL_STATE["last_partial_errors"] = channel_errors
    return discovered


def commit_sina_finance_roll_state() -> None:
    if not SINA_FINANCE_PENDING_ROLL_STATE:
        return
    next_state = dict(load_source_state(SINA_FINANCE_SOURCE))
    next_state.update(SINA_FINANCE_PENDING_ROLL_STATE)
    if "last_partial_errors" not in SINA_FINANCE_PENDING_ROLL_STATE:
        next_state.pop("last_partial_errors", None)
    save_source_state(SINA_FINANCE_SOURCE, next_state)
    SINA_FINANCE_PENDING_ROLL_STATE.clear()


def env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def load_source_state(source: str) -> dict[str, Any]:
    with connect_db() as conn:
        return runtime_load_source_state(conn, source)


def save_source_state(source: str, state: dict[str, Any]) -> None:
    with connect_db() as conn:
        runtime_save_source_state(conn, source, state)
        conn.commit()


def should_skip_cls_poll(source: str, *, force: bool = False) -> bool:
    if force or os.getenv("CLS_FORCE_FETCH", "").strip() == "1":
        return False
    min_seconds = env_int("CLS_MIN_POLL_SECONDS", 60, minimum=0)
    if min_seconds <= 0:
        return False
    state = load_source_state(source)
    last_fetch = str(state.get("last_fetch_at") or "")
    if not last_fetch:
        return False
    try:
        elapsed = datetime.now(timezone.utc).timestamp() - datetime.fromisoformat(last_fetch).timestamp()
    except ValueError:
        return False
    if elapsed < min_seconds:
        print(f"财联社公开前端 API 距上次抓取 {elapsed:.1f}s，小于 {min_seconds}s，跳过本轮。", flush=True)
        return True
    return False


def parse_first_finance_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    try:
        rows = fetch_json(DOMESTIC_FEED_SOURCES["yicai_brief"])
    except Exception as exc:
        print(f"第一财经公开 JSON 读取失败，尝试 RSSHub：{exc}", flush=True)
        rows = fetch_json(YICAI_RSSHUB_FALLBACK)

    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("newcontent") or row.get("title") or row.get("LiveTitle") or "").strip()
        url = str(row.get("ShareUrl") or row.get("url") or row.get("link") or "").strip()
        if not title and not url:
            continue
        summary = str(row.get("LiveContent") or row.get("summary") or row.get("description") or row.get("newcontent") or "").strip()
        published_at = str(row.get("CreateDate") or row.get("published_at") or row.get("pubDate") or "").strip()
        item_id = str(row.get("LiveID") or url or title)
        items.append(
            {
                "id": item_id,
                "url": canonical_url(url),
                "title": title,
                "summary": strip_tags(summary),
                "content": "",
                "published_at": parse_date(published_at),
                "source_module": CHINA_MEDIA_LABELS["yicai_brief"],
                "access_note": CHINA_MEDIA_ACCESS_NOTES["yicai_brief"],
                "body_source": "公开 JSON",
            }
        )
    return items


def parse_cls_items(*, persist_state: bool = True, force: bool = False) -> list[dict[str, Any]]:
    source = "cls_telegraph_api"
    if should_skip_cls_poll(source, force=force):
        return []
    params = {
        "app": "CailianpressWeb",
        "category": os.getenv("CLS_ROLL_CATEGORY", ""),
        "lastTime": os.getenv("CLS_ROLL_LAST_TIME", ""),
        "os": "web",
        "refresh_type": os.getenv("CLS_ROLL_REFRESH_TYPE", "1"),
        "rn": os.getenv("CLS_ROLL_RN", "20"),
        "sv": os.getenv("CLS_ROLL_SV", "7.7.5"),
    }
    signed_params = dict(params)
    signed_params["sign"] = cls_sign(params)
    url = f"{DOMESTIC_FEED_SOURCES['cls_telegraph_api']}?{urllib.parse.urlencode(signed_params)}"
    try:
        response = http_get(
            url,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://m.cls.cn/telegraph",
            },
            timeout=int(os.getenv("CLS_FETCH_TIMEOUT_SECONDS", os.getenv("CHINA_MEDIA_FETCH_TIMEOUT_SECONDS", "20"))),
        )
        data = json.loads(response.content.decode("utf-8", errors="replace"))
    except Exception as exc:
        print(f"财联社公开前端 API 读取失败：{exc}", flush=True)
        raise
    if persist_state:
        save_source_state(source, {"last_fetch_at": datetime.now(timezone.utc).isoformat()})

    if not isinstance(data, dict):
        raise RuntimeError("财联社公开前端 API 响应格式异常：root 不是 JSON object")
    errno = data.get("errno", data.get("errNo", data.get("code", 0)))
    if errno not in (0, "0", None):
        message = data.get("msg") or data.get("message") or data.get("error") or ""
        raise RuntimeError(f"财联社公开前端 API 返回错误：errno={errno} message={message}")

    payload = data.get("data")
    rows = payload.get("roll_data") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        rows = []
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("content") or row.get("title") or "").strip()
        title = strip_tags(title)
        author = str(row.get("author") or row.get("source") or row.get("media") or "").strip()
        url = str(row.get("shareurl") or row.get("shareUrl") or row.get("url") or "").strip()
        if not title:
            continue
        ctime = row.get("ctime") or row.get("time") or row.get("published_at") or ""
        item_id = str(row.get("id") or row.get("telegraphId") or row.get("ctime") or url or title)
        source_module = CHINA_MEDIA_LABELS["cls_telegraph_api"]
        if is_star_market_daily_text(title, author):
            source_module = "科创板日报 / 财联社电报"
        cls_metadata = cls_product_metadata(row)
        items.append(
            {
                "id": item_id,
                "url": canonical_url(url),
                "title": title,
                "summary": title,
                "content": "",
                "published_at": parse_cls_time(ctime),
                "source_module": source_module,
                "access_note": CHINA_MEDIA_ACCESS_NOTES["cls_telegraph_api"],
                "body_source": "公开前端 API",
                "cls_metadata": cls_metadata,
                "raw": {"cls_metadata": cls_metadata},
            }
        )
    return items


def next_data_from_html(html_text: str) -> dict[str, Any]:
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html_text, flags=re.S)
    if not match:
        raise RuntimeError("科创板日报专题页未找到 __NEXT_DATA__")
    raw = html.unescape(match.group(1))
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("科创板日报专题页 __NEXT_DATA__ 不是 JSON object")
    return parsed


def article_url_from_row(row: dict[str, Any]) -> str:
    for key in ("share_url", "shareUrl", "jump_url", "externalLink", "url", "link"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    article_id = str(row.get("article_id") or row.get("id") or "").strip()
    if article_id:
        return f"https://api3.cls.cn/share/article/{article_id}?os=web&sv=7.7.5&app=CailianpressWeb"
    return ""


def star_market_context(row: dict[str, Any]) -> str:
    stocks = row.get("stock_list") or []
    stock_names: list[str] = []
    if isinstance(stocks, list):
        for stock in stocks:
            if isinstance(stock, dict) and stock.get("name"):
                stock_names.append(str(stock.get("name")))
    subjects = row.get("subjects") or []
    subject_names: list[str] = []
    if isinstance(subjects, list):
        for subject in subjects:
            if isinstance(subject, dict) and subject.get("subject_name"):
                subject_names.append(str(subject.get("subject_name")))
    tags = row.get("article_tags") or []
    tag_names: list[str] = []
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, dict) and tag.get("name"):
                tag_names.append(str(tag.get("name")))
    parts = []
    if stock_names:
        parts.append("涉及标的：" + "、".join(stock_names[:8]))
    if subject_names:
        parts.append("专题：" + "、".join(subject_names[:8]))
    if tag_names:
        parts.append("标签：" + "、".join(tag_names[:8]))
    return "；".join(parts)


def parse_star_market_daily_subject_items() -> list[dict[str, Any]]:
    source = "star_market_daily_subject"
    response = http_get(
        DOMESTIC_FEED_SOURCES[source],
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        timeout=int(os.getenv("CHINA_MEDIA_FETCH_TIMEOUT_SECONDS", "20")),
    )
    data = next_data_from_html(response.content.decode("utf-8", errors="replace"))
    props = data.get("props") if isinstance(data, dict) else {}
    page_props = props.get("pageProps", {}) if isinstance(props, dict) else {}
    payload = page_props.get("data", {}) if isinstance(page_props, dict) else {}
    rows = payload.get("articles") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        raise RuntimeError("科创板日报专题页 articles 格式异常")

    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = strip_tags(str(row.get("article_title") or row.get("title") or "").strip())
        if not title:
            continue
        brief = strip_tags(str(row.get("article_brief") or row.get("article_guide_text") or "").strip())
        author = str(row.get("article_author") or "").strip()
        context = star_market_context(row)
        summary = "\n".join(part for part in (brief, context, author) if part)
        article_id = str(row.get("article_id") or row.get("id") or title).strip()
        url = canonical_url(article_url_from_row(row))
        published_at = parse_cls_time(row.get("article_time") or row.get("ctime") or "")
        items.append(
            {
                "id": article_id,
                "url": url,
                "title": title,
                "summary": summary or title,
                "content": "",
                "published_at": published_at,
                "source_module": CHINA_MEDIA_LABELS[source],
                "access_note": CHINA_MEDIA_ACCESS_NOTES[source],
                "body_source": "公开专题页 JSON",
            }
        )
    return items


def parse_jin10_items() -> list[dict[str, Any]]:
    feed = CHINA_MEDIA_FEEDS["jin10_rsshub_important"]
    try:
        response = http_get(
            feed,
            headers={"Accept": "application/rss+xml, application/xml, text/xml"},
            timeout=int(os.getenv("CHINA_MEDIA_FETCH_TIMEOUT_SECONDS", "20")),
        )
        parsed = feedparser.parse(response.content)
    except Exception as exc:
        print(f"金十 RSSHub 读取失败：{exc}", flush=True)
        raise

    items: list[dict[str, Any]] = []
    for item in parsed.entries:
        title = str(item.get("title") or "").strip()
        url = str(item.get("link") or "").strip()
        summary = str(item.get("summary") or item.get("description") or "").strip()
        guid = str(item.get("id") or item.get("guid") or url or title).strip()
        published_at = parse_date(str(item.get("published") or item.get("updated") or "").strip())
        items.append(
            {
                "id": guid,
                "url": canonical_url(url),
                "title": title,
                "summary": strip_tags(summary),
                "content": "",
                "published_at": published_at,
                "source_module": CHINA_MEDIA_LABELS["jin10_rsshub_important"],
                "access_note": CHINA_MEDIA_ACCESS_NOTES["jin10_rsshub_important"],
                "body_source": "RSSHub",
            }
        )
    return items


def source_items(
    source: str,
    *,
    persist_state: bool = True,
    force: bool = False,
    defer_enrichment: bool = False,
) -> list[dict[str, Any]]:
    if source == "yicai_brief":
        return parse_first_finance_items()
    if source == "cls_telegraph_api":
        return parse_cls_items(persist_state=persist_state, force=force)
    if source == "star_market_daily_subject":
        return parse_star_market_daily_subject_items()
    if source == "jin10_rsshub_important":
        return parse_jin10_items()
    if source == SINA_FINANCE_SOURCE:
        return parse_sina_finance_article_items(
            persist_state=persist_state,
            enrich_details=not defer_enrichment,
        )
    if source == WALLSTREETCN_SOURCE:
        return wallstreetcn.collect_items(state=load_source_state(source))
    return []


def enrich_item(source: str, item: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(item)
    body = ""
    body_source = str(enriched.get("body_source") or "公开页面")
    if source == SINA_FINANCE_SOURCE:
        if not enriched.get("full_text") and not (enriched.get("raw") or {}).get("detail_fetch_status"):
            enriched = enrich_sina_finance_item(enriched)
        enriched["full_text"] = str(enriched.get("full_text") or enriched.get("content") or enriched.get("summary") or "")
        enriched.setdefault("source_module", china_media_module(source))
        enriched.setdefault("access_note", china_media_access_note(source, enriched["body_source"]))
        return enriched
    if source == WALLSTREETCN_SOURCE:
        if enriched.get("_wallstreetcn_enriched") or enriched.get("_skip_decision"):
            return enriched
        return wallstreetcn.enrich_item(enriched)
    if source == "yicai_brief" and enriched.get("url"):
        try:
            body, body_source = fetch_article_body(enriched["url"])
        except Exception as exc:
            print(f"第一财经正文抓取失败，回退摘要：{exc}", flush=True)
    if source in {"cls_telegraph_api", "star_market_daily_subject", "jin10_rsshub_important"} and enriched.get("url"):
        try:
            body, body_source = fetch_article_body(enriched["url"])
        except Exception:
            pass
    enriched["full_text"] = body or str(enriched.get("content") or enriched.get("summary") or "")
    enriched["body_source"] = body_source if body else body_source
    enriched.setdefault("source_module", china_media_module(source))
    enriched.setdefault("access_note", china_media_access_note(source, enriched["body_source"]))
    if source == "jin10_rsshub_important":
        digest = relevant_digest_for_mixed_item(source, enriched)
        if digest:
            first_line = digest.splitlines()[0].strip()
            enriched["_original_full_text"] = enriched.get("full_text") or ""
            enriched["full_text"] = digest
            enriched["summary"] = digest
            enriched["title"] = f"金十重要事件：{first_line[:80]}"
            enriched["body_source"] = f"{enriched['body_source']}（相关条目摘取）"
    return enriched


def seen_source(conn: sqlite3.Connection, source: str) -> bool:
    row = conn.execute("SELECT 1 FROM seen_sources WHERE source = ? LIMIT 1", (source,)).fetchone()
    return row is not None


def seen_item_id(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "").strip()
    url = canonical_url(str(item.get("url") or "").strip())
    return str(item.get("id") or url or title)


def should_deliver_wallstreetcn_retry(
    source: str,
    *,
    is_retry: bool,
    first_seen_at: str,
    published_at: str,
    now: datetime | None = None,
) -> bool:
    if source != WALLSTREETCN_SOURCE or not is_retry:
        return True
    reference_raw = first_seen_at or published_at
    normalized = parse_datetime_to_utc_iso(reference_raw)
    try:
        reference = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return False
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return (
        current.astimezone(timezone.utc) - reference.astimezone(timezone.utc)
        <= WALLSTREETCN_RETRY_DELIVERY_MAX_AGE
    )


def set_seen_item_lifecycle(source: str, item_id: str, **values: str | None) -> None:
    values["lifecycle_updated_at"] = datetime.now(timezone.utc).isoformat()

    def operation() -> None:
        with connect_db() as conn:
            ensure_seen_table(conn)
            update_seen_item_lifecycle(conn, source, item_id, **values)
            conn.commit()

    retry_on_locked(operation)


def restore_existing_wallstreetcn_empty_details(source: str) -> int:
    if source != WALLSTREETCN_SOURCE:
        return 0
    now = datetime.now(timezone.utc).isoformat()

    def operation() -> int:
        with connect_db() as conn:
            ensure_seen_table(conn)
            cursor = conn.execute(
                """
                UPDATE seen_items
                SET processability_status = 'failed_retryable',
                    processability_reason = ?,
                    admission_status = 'pending',
                    admission_reason = '',
                    processing_status = 'not_applicable',
                    processing_error = '',
                    processed_at = NULL,
                    lifecycle_updated_at = ?
                WHERE source = ? AND collection_class = 'live'
                  AND processability_status = 'failed_terminal'
                  AND processability_reason = 'wallstreetcn_detail_empty'
                  AND admission_status = 'not_applicable'
                  AND processing_status = 'not_applicable'
                """,
                (
                    f"ValueError: {WALLSTREETCN_EMPTY_DETAIL_ERROR}",
                    now,
                    source,
                ),
            )
            conn.commit()
            return int(cursor.rowcount)

    return retry_on_locked(operation)


def retryable_seen_items(source: str) -> list[dict[str, Any]]:
    restored = restore_existing_wallstreetcn_empty_details(source)
    if restored:
        print(f"华尔街见闻：{restored} 条空快讯详情恢复为可重试，等待来源再次发现。", flush=True)
    with connect_db() as conn:
        ensure_seen_table(conn)
        rows = conn.execute(
            """
            SELECT item_id, url, title, summary, published_at
            FROM seen_items
            WHERE source = ? AND collection_class = 'live'
              AND (
                (
                  ? != ?
                  AND processability_status IN ('pending', 'failed_retryable')
                )
                OR (
                  processability_status IN ('not_required', 'succeeded', 'fallback')
                  AND admission_status = 'pending'
                )
                OR processing_status IN ('pending', 'failed_retryable')
              )
            ORDER BY first_seen_at ASC
            """,
            (source, source, WALLSTREETCN_SOURCE),
        ).fetchall()
    return [
        {
            "id": str(row[0] or ""),
            "url": str(row[1] or ""),
            "title": str(row[2] or ""),
            "summary": str(row[3] or ""),
            "published_at": str(row[4] or ""),
        }
        for row in rows
    ]


def save_new_items(
    conn: sqlite3.Connection,
    source: str,
    items: Iterable[dict[str, Any]],
    notify_baseline: bool = False,
) -> list[dict[str, Any]]:
    ensure_seen_table(conn)
    items_list = list(items)
    is_baseline = not seen_source(conn, source)
    if is_baseline:
        conn.execute(
            "INSERT OR IGNORE INTO seen_sources (source, first_seen_at) VALUES (?, ?)",
            (source, datetime.now(timezone.utc).isoformat()),
        )
    now = datetime.now(timezone.utc).isoformat()
    new_items: list[dict[str, Any]] = []
    seen_titles: list[str] = []
    selected_ids: set[str] = set()
    star_market_sources = {"cls_telegraph_api", "star_market_daily_subject"}
    for item in sorted(items_list, key=lambda row: row.get("published_at") or "", reverse=False):
        title = str(item.get("title") or "").strip()
        url = canonical_url(str(item.get("url") or "").strip())
        item_id = seen_item_id(item)
        if not title and not url:
            continue
        existing = conn.execute(
            """
            SELECT collection_class, processability_status, admission_status, processing_status, first_seen_at
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
            if retryable and item_id not in selected_ids:
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
                    lifecycle_updated_at=now,
                )
                item[SEEN_ITEM_RETRY_KEY] = True
                item[SEEN_ITEM_RETRY_FIRST_SEEN_KEY] = str(existing[4] or "")
                selected_ids.add(item_id)
                new_items.append(item)
            continue
        if any(title_similarity(title, prior) for prior in seen_titles):
            continue
        if source in star_market_sources and is_star_market_daily_text(
            title,
            item.get("summary"),
            item.get("source_module"),
        ):
            duplicate = conn.execute(
                """
                SELECT source, title FROM seen_items
                WHERE source IN ('cls_telegraph_api', 'star_market_daily_subject')
                  AND ((? != '' AND url = ?) OR title = ?)
                LIMIT 1
                """,
                (url, url, title),
            ).fetchone()
            if duplicate:
                continue
            recent_rows = conn.execute(
                """
                SELECT title FROM seen_items
                WHERE source IN ('cls_telegraph_api', 'star_market_daily_subject')
                ORDER BY first_seen_at DESC
                LIMIT 80
                """
            ).fetchall()
            if any(title_similarity(title, str(row[0] or "")) for row in recent_rows):
                continue
        seen_titles.append(title)
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
                    url,
                    title,
                    str(item.get("summary") or ""),
                    str(item.get("published_at") or ""),
                    now,
                    "baseline" if is_baseline and not notify_baseline else "live",
                    "not_required" if is_baseline and not notify_baseline else "pending",
                    "",
                    "not_applicable" if is_baseline and not notify_baseline else "pending",
                    "",
                    "not_applicable",
                    "",
                    now if is_baseline and not notify_baseline else None,
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            continue
        selected_ids.add(item_id)
        new_items.append(item)
    conn.commit()
    if is_baseline and not notify_baseline:
        print(f"{china_media_module(source)}: 首次建立基线 {len(items_list)} 条，默认不发送旧内容。", flush=True)
        return []
    return new_items


def save_new_items_with_retry(
    source: str,
    items: Iterable[dict[str, Any]],
    notify_baseline: bool = False,
) -> list[dict[str, Any]]:
    def operation() -> list[dict[str, Any]]:
        with connect_db() as conn:
            return save_new_items(conn, source, items, notify_baseline=notify_baseline)

    return retry_on_locked(operation)


def current_admission_result(
    item: dict[str, Any] | NormalizedMarketItem,
    source: str = "",
    *,
    source_module: str = "",
) -> dict[str, Any]:
    if isinstance(item, NormalizedMarketItem):
        admission_item: dict[str, Any] = {
            "title": item.title,
            "summary": item.summary,
            "full_text": item.full_text,
            "url": item.url,
            "published_at": item.published_at,
            "source_module": source_module,
        }
        effective_source = source or item.source
    else:
        admission_item = item
        effective_source = source or str(item.get("source") or "")
    if fed_path_candidate(admission_item):
        return {"admitted": True, "reason": "fed_path_candidate", "matched_families": ("fed_policy",)}
    match = investment_universe_match(effective_source, admission_item)
    if isinstance(item, dict):
        item["_investment_universe_match"] = match
    if not match.get("matched"):
        return {
            "admitted": False,
            "reason": "investment_universe_no_match",
            "matched_families": (),
            "universe_match": match,
        }
    if is_macro_event(admission_item):
        return {
            "admitted": True,
            "reason": "macro_event",
            "matched_families": ("macro_data",),
            "universe_match": match,
        }
    tags = set(match.get("tags") or [])
    if {"holding_match", "user_include_keyword"} & tags:
        return {
            "admitted": True,
            "reason": "holding_or_include_keyword",
            "matched_families": ("holding",),
            "universe_match": match,
        }
    if is_media_focus_item(
        str(admission_item.get("title") or ""),
        str(admission_item.get("summary") or ""),
        str(admission_item.get("full_text") or ""),
        str(admission_item.get("source_module") or ""),
    ):
        return {
            "admitted": True,
            "reason": "media_focus",
            "matched_families": ("semiconductor_ai",),
            "universe_match": match,
        }
    return {
        "admitted": False,
        "reason": "media_focus_no_match",
        "matched_families": (),
        "universe_match": match,
    }


def should_focus_item(item: dict[str, Any], source: str = "") -> bool:
    return bool(current_admission_result(item, source)["admitted"])


def is_mandatory_yicai_morning_brief(source: str, item: dict[str, Any]) -> bool:
    if source != "yicai_brief":
        return False
    text = f"{item.get('title', '')} {item.get('summary', '')} {item.get('full_text', '')}"
    return "券商晨会观点速递" in strip_tags(str(text))


def notify_item(source: str, item: dict[str, Any]) -> None:
    item_id = seen_item_id(item)
    is_seen_item_retry = bool(item.pop(SEEN_ITEM_RETRY_KEY, False))
    retry_first_seen_at = str(item.pop(SEEN_ITEM_RETRY_FIRST_SEEN_KEY, "") or "")
    try:
        enriched = enrich_item(source, item)
    except Exception as exc:
        set_seen_item_lifecycle(
            source,
            item_id,
            processability_status="failed_retryable",
            processability_reason=f"{type(exc).__name__}: {str(exc)[:400]}",
            admission_status="pending",
            processing_status="not_applicable",
            processed_at=None,
        )
        raise
    if enriched.get("_skip_decision"):
        set_seen_item_lifecycle(
            source,
            item_id,
            processability_status="failed_terminal",
            processability_reason="access_unavailable",
            admission_status="not_applicable",
            processing_status="not_applicable",
            processed_at=datetime.now(timezone.utc).isoformat(),
        )
        return
    raw = enriched.get("raw") if isinstance(enriched.get("raw"), dict) else {}
    fallback = raw.get("detail_fetch_status") == "failed" or "抓取失败" in str(enriched.get("body_source") or "")
    set_seen_item_lifecycle(
        source,
        item_id,
        processability_status="fallback" if fallback else "succeeded",
        processability_reason="detail_fallback" if fallback else "",
        admission_status="pending",
        processing_status="not_applicable",
    )
    try:
        normalized = normalize_market_item(source, enriched, store_kind="article", source_profile_id=source)
        mandatory_morning = is_mandatory_yicai_morning_brief(source, enriched)
        admission = (
            {"admitted": True, "reason": "yicai_morning_brief", "matched_families": ("semiconductor_ai",)}
            if mandatory_morning
            else current_admission_result(
                normalized,
                source,
                source_module=str(enriched.get("source_module") or ""),
            )
        )
    except Exception as exc:
        set_seen_item_lifecycle(
            source,
            item_id,
            admission_status="pending",
            admission_reason=f"evaluation_failed:{type(exc).__name__}",
            processing_status="not_applicable",
        )
        raise
    if not admission["admitted"]:
        set_seen_item_lifecycle(
            source,
            item_id,
            admission_status="excluded",
            admission_reason=str(admission["reason"]),
            processing_status="not_applicable",
            processed_at=datetime.now(timezone.utc).isoformat(),
        )
        record_rule_comparison(
            normalized,
            None,
            {"store_kind": "seen_items", "source": source, "item_id": item_id},
            current_admission_status="excluded",
            current_admission_reason=str(admission["reason"]),
            current_matched_families=tuple(admission["matched_families"]),
        )
        return
    universe_match = admission.get("universe_match") or investment_universe_match(source, enriched)
    enriched["push_reason"] = (
        "强制推送规则：第一财经券商晨会观点速递为每日固定栏目。"
        if mandatory_morning
        else str(universe_match.get("reason") or "")
    )
    set_seen_item_lifecycle(
        source,
        item_id,
        admission_status="admitted",
        admission_reason=str(admission["reason"]),
        processing_status="pending",
    )
    deliver = should_deliver_wallstreetcn_retry(
        source,
        is_retry=is_seen_item_retry,
        first_seen_at=retry_first_seen_at,
        published_at=str(enriched.get("published_at") or ""),
    )
    try:
        outcome = process_market_item(
            normalized,
            enriched,
            store_kind="article",
            source_profile_id=source,
            db_path=DB_PATH,
            current_admission_status="admitted",
            current_admission_reason=str(admission["reason"]),
            current_matched_families=tuple(admission["matched_families"]),
            deliver=deliver,
        )
    except Exception as exc:
        set_seen_item_lifecycle(
            source,
            item_id,
            processing_status="failed_retryable",
            processing_error=f"{type(exc).__name__}: {str(exc)[:800]}",
        )
        raise
    set_seen_item_lifecycle(
        source,
        item_id,
        processing_status="succeeded",
        processing_error="",
        processed_at=datetime.now(timezone.utc).isoformat(),
    )
    review = outcome.payload
    if not deliver:
        print(
            f"{source} 历史可重试条目已完成准入、决策和保存，但不再即时推送：title={enriched.get('title', '')}",
            flush=True,
        )
    print(
        f"{source} 决策层：importance={review.get('importance')} push={review.get('push_now')} title={enriched.get('title', '')}",
        flush=True,
    )
    if outcome.delivery_status == "duplicate":
        print(f"{source} 国际投行主题策略去重：title={enriched.get('title', '')}", flush=True)


def run_once(sources: list[str], notify_baseline: bool = False) -> int:
    sources = list(filter_enabled_mapping_for_run({source: source for source in sources}, label="中国财经媒体").keys())
    if not sources:
        return 0
    total_new = 0
    fetched: dict[str, list[dict[str, Any]]] = {}
    max_workers = min(len(sources), env_int("CHINA_MEDIA_FETCH_MAX_WORKERS", 3, minimum=1))
    source_states = {source: load_source_state(source) for source in sources}
    runnable_sources, _skipped_sources = split_sources_by_backoff(
        sources,
        source_states,
        label_for_source=china_media_module,
    )
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(source_items, source, defer_enrichment=True): source
            for source in runnable_sources
        }
        for future in as_completed(futures):
            source = futures[future]
            try:
                fetched[source] = future.result()
                with connect_db() as conn:
                    record_source_success(conn, "china_finance_media", source)
                    if source == WALLSTREETCN_SOURCE:
                        article_results = wallstreetcn.discovery_surface_results("article")
                        live_results = wallstreetcn.discovery_surface_results("livenews")
                        for health_source, rows in (("articles", article_results), ("livenews", live_results)):
                            failures = [row for row in rows if not row.get("ok")]
                            if failures:
                                record_source_failure(conn, "wallstreetcn", health_source, RuntimeError(str(failures)))
                            elif rows:
                                record_source_success(conn, "wallstreetcn", health_source)
                save_source_state(source, clear_backoff_state(load_source_state(source)))
            except Exception as exc:
                save_source_state(source, backoff_state_after_failure(source, load_source_state(source)))
                with connect_db() as conn:
                    record_source_failure(conn, "china_finance_media", source, exc)
                print(f"{china_media_module(source)} 抓取失败：{exc}", flush=True)
    for source in sources:
        if source not in fetched:
            continue
        items = fetched[source]
        by_id = {seen_item_id(item): item for item in items}
        for retry_item in retryable_seen_items(source):
            by_id.setdefault(seen_item_id(retry_item), retry_item)
        items = list(by_id.values())
        try:
            new_items = save_new_items_with_retry(source, items, notify_baseline=notify_baseline)
            if source == SINA_FINANCE_SOURCE:
                commit_sina_finance_roll_state()
            if source == WALLSTREETCN_SOURCE and wallstreetcn.PENDING_STATE:
                next_state = dict(load_source_state(source))
                next_state.update(wallstreetcn.PENDING_STATE)
                save_source_state(source, next_state)
                wallstreetcn.PENDING_STATE.clear()
        except Exception as exc:
            with connect_db() as conn:
                record_source_failure(conn, "china_finance_media", source, exc)
            print(f"{china_media_module(source)} 处理失败：{exc}", flush=True)
            continue
        if not new_items:
            print(f"{china_media_module(source)}：没有发现新条目。", flush=True)
            continue
        total_new += len(new_items)
        print(f"{china_media_module(source)}：发现 {len(new_items)} 条新条目。", flush=True)
        processing_failed = False
        for item in new_items:
            try:
                notify_item(source, item)
            except Exception as exc:  # noqa: BLE001 - retain retry state and continue the batch
                processing_failed = True
                with connect_db() as conn:
                    record_source_failure(conn, "china_finance_media", source, exc)
                    if source == WALLSTREETCN_SOURCE:
                        record_source_failure(conn, "wallstreetcn", "detail", exc)
                print(f"{china_media_module(source)} 条目处理失败，已保留为可重试：{type(exc).__name__}: {exc}", flush=True)
        if source == WALLSTREETCN_SOURCE and not processing_failed:
            with connect_db() as conn:
                record_source_success(conn, "wallstreetcn", "detail")
    return total_new


def parse_sources_arg(raw: list[str]) -> list[str]:
    if not raw:
        return [
            "yicai_brief",
            "cls_telegraph_api",
            "star_market_daily_subject",
            "jin10_rsshub_important",
            SINA_FINANCE_SOURCE,
        ]
    sources = []
    for part in raw:
        for name in part.split(","):
            name = name.strip()
            if name:
                sources.append(name)
    invalid = [name for name in sources if not is_china_media_source(name)]
    if invalid:
        raise SystemExit(f"未知中国财经媒体源：{', '.join(invalid)}")
    return sources


def main() -> int:
    load_env(ENV_PATH)
    config = llm_config()
    if config:
        _, base_url, model = config
        print(f"China finance media monitor LLM config: {base_url} / {model}", flush=True)
    else:
        print("China finance media monitor LLM config: 未配置", flush=True)

    parser = argparse.ArgumentParser(description="Monitor domestic finance media sources.")
    parser.add_argument("--source", action="append", default=[], help="Source name, repeatable or comma separated.")
    parser.add_argument("--interval", type=int, default=0, help="Polling interval in seconds. 0 means run once.")
    parser.add_argument("--notify-baseline", action="store_true", help="首次建立基线时也发送通知。默认不发送旧条目。")
    args = parser.parse_args()
    sources = parse_sources_arg(args.source)
    notify_baseline = args.notify_baseline or os.getenv("SURVEIL_NOTIFY_BASELINE", "") == "1"

    if args.interval <= 0:
        run_once(sources, notify_baseline=notify_baseline)
        return 0

    print(f"开始监控 {len(sources)} 个中国财经媒体源，轮询间隔 {args.interval} 秒。", flush=True)
    while True:
        run_once(sources, notify_baseline=notify_baseline)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
