#!/usr/bin/env python3
"""Monitor public AlphaAbstract research-summary pages."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sqlite3
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from collector_runtime import load_source_state, save_source_state
from db_utils import (
    connect_sqlite,
    ensure_seen_tables,
    ensure_source_state_table,
    retry_on_locked,
    update_seen_item_lifecycle,
)
from http_utils import http_get
from market_flow import normalize_market_item, process_market_item
from production_admission import admission_lifecycle_values, production_admission_context
from rss_monitor import DB_PATH, save_new_items, strip_tags
from source_health import record_source_failure, record_source_success
from source_profiles import source_profile_enabled
from time_utils import parse_datetime_to_utc_iso
from x_check import load_env


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"

SOURCE_ID = "alphabstract_summaries"
MONITOR = "alphabstract"
HOME_URL = "https://alphabstract.com/"
SITEMAP_URL = "https://alphabstract.com/sitemap.xml"
SOURCE_MODULE = "AlphaAbstract / Summaries"
ACCESS_NOTE = (
    "AlphaAbstract robots.txt 允许公开抓取；当前通过 sitemap 和公开 summary 页面读取摘要正文、"
    "Article JSON-LD 与原始来源链接，不绕过登录、付费墙或访问控制。"
)
SCOPE_STATE_PREFIX = "alphabstract_scope"
EXPANDED_SCOPE_BASELINE_STATE_KEY = "expanded_scope_baseline_at"


@dataclass(frozen=True)
class AlphaAbstractSource:
    name: str
    module: str
    sitemap_url: str
    home_url: str
    access_note: str


DEFAULT_SOURCE = AlphaAbstractSource(
    name=SOURCE_ID,
    module=SOURCE_MODULE,
    sitemap_url=SITEMAP_URL,
    home_url=HOME_URL,
    access_note=ACCESS_NOTE,
)

ALPHAABSTRACT_SOURCES: tuple[AlphaAbstractSource, ...] = (DEFAULT_SOURCE,)


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


def load_scope_state(conn: sqlite3.Connection, source: str) -> dict[str, Any]:
    return load_source_state(conn, source, prefix=SCOPE_STATE_PREFIX)


def save_scope_state(conn: sqlite3.Connection, source: str, state: dict[str, Any]) -> None:
    save_source_state(conn, source, state, prefix=SCOPE_STATE_PREFIX)
    conn.commit()


def normalize_alpha_date(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return f"{raw}T00:00:00+00:00"
    return parse_datetime_to_utc_iso(raw)


def canonical_article_id(url: str) -> str:
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    path = parsed.path.rstrip("/")
    return path.strip("/") or str(url or "").strip()


def is_summary_url(url: str) -> bool:
    parsed = urllib.parse.urlsplit(str(url or ""))
    hostname = str(parsed.hostname or "").lower()
    return (
        parsed.scheme in {"http", "https"}
        and (hostname == "alphabstract.com" or hostname.endswith(".alphabstract.com"))
        and parsed.path.startswith("/summaries/")
    )


def fetch_text(url: str) -> str:
    response = http_get(
        url,
        headers={"Accept": "text/html,application/xhtml+xml,application/xml,text/xml;q=0.9,*/*;q=0.8"},
        timeout=int(os.getenv("ALPHAABSTRACT_TIMEOUT_SECONDS", "25")),
        retries=int(os.getenv("ALPHAABSTRACT_RETRY_COUNT", os.getenv("SURVEIL_HTTP_RETRY_COUNT", "1"))),
    )
    return response.content.decode("utf-8", errors="replace")


def parse_sitemap_entries(xml_text: str) -> list[dict[str, str]]:
    root = ET.fromstring(xml_text)
    entries: list[dict[str, str]] = []
    for url_node in root.findall(".//{*}url"):
        loc = (url_node.findtext("{*}loc") or "").strip()
        if not is_summary_url(loc):
            continue
        lastmod = (url_node.findtext("{*}lastmod") or "").strip()
        entries.append({"url": loc, "lastmod": normalize_alpha_date(lastmod)})
    entries.sort(key=lambda item: item.get("lastmod") or "", reverse=True)
    return entries


class MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.links: dict[str, str] = {}
        self.jsonld_scripts: list[str] = []
        self.title_parts: list[str] = []
        self._capture_title = False
        self._capture_jsonld = False
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta":
            key = data.get("property") or data.get("name")
            content = data.get("content", "")
            if key and content:
                self.meta[key] = html.unescape(content).strip()
        elif tag.lower() == "link":
            rel = data.get("rel", "").lower()
            href = data.get("href", "")
            if rel and href:
                self.links[rel] = href.strip()
        elif tag.lower() == "title":
            self._capture_title = True
        elif tag.lower() == "script" and "application/ld+json" in data.get("type", "").lower():
            self._capture_jsonld = True
            self._script_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._capture_title = False
        elif tag.lower() == "script" and self._capture_jsonld:
            script = "".join(self._script_parts).strip()
            if script:
                self.jsonld_scripts.append(script)
            self._script_parts = []
            self._capture_jsonld = False

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self.title_parts.append(data)
        if self._capture_jsonld:
            self._script_parts.append(data)

    @property
    def title(self) -> str:
        return " ".join("".join(self.title_parts).split()).strip()


def iter_jsonld_nodes(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        graph = value.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from iter_jsonld_nodes(item)
        for key in ("mainEntity", "mainEntityOfPage"):
            nested = value.get(key)
            if isinstance(nested, (dict, list)):
                yield from iter_jsonld_nodes(nested)
    elif isinstance(value, list):
        for item in value:
            yield from iter_jsonld_nodes(item)


def is_article_jsonld(node: dict[str, Any]) -> bool:
    node_type = node.get("@type")
    values = node_type if isinstance(node_type, list) else [node_type]
    return any(str(value).lower() in {"article", "newsarticle", "blogposting"} for value in values)


def article_jsonld(scripts: list[str]) -> dict[str, Any]:
    for script in scripts:
        try:
            parsed = json.loads(script)
        except json.JSONDecodeError:
            continue
        for node in iter_jsonld_nodes(parsed):
            if is_article_jsonld(node):
                return node
    return {}


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def jsonld_name(value: Any) -> str:
    if isinstance(value, dict):
        return compact_text(value.get("name"))
    return compact_text(value)


def jsonld_url(value: Any) -> str:
    if isinstance(value, dict):
        return compact_text(value.get("url") or value.get("@id"))
    return ""


def extract_body_text(html_text: str, *, url: str) -> str:
    try:
        import trafilatura

        extracted = trafilatura.extract(
            html_text,
            url=url,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
        if extracted and len(extracted.strip()) > 120:
            return extracted.strip()
    except Exception:  # noqa: BLE001 - fallback keeps collection useful
        pass

    match = re.search(
        r'<div[^>]*class="[^"]*\bsummary-prose\b[^"]*"[^>]*>(.*?)</div>\s*<div[^>]*class="[^"]*\bmt-12\b',
        html_text,
        flags=re.I | re.S,
    )
    if match:
        return strip_tags(match.group(1))

    article_match = re.search(r"<article\b[^>]*>(.*?)</article>", html_text, flags=re.I | re.S)
    if article_match:
        return strip_tags(article_match.group(1))
    return ""


def first_non_empty(*values: Any) -> str:
    for value in values:
        text = compact_text(value)
        if text:
            return text
    return ""


def normalize_entry_from_article(
    url: str,
    html_text: str,
    *,
    source: AlphaAbstractSource = DEFAULT_SOURCE,
    sitemap_lastmod: str = "",
) -> dict[str, Any] | None:
    parser = MetadataParser()
    parser.feed(html_text)
    article = article_jsonld(parser.jsonld_scripts)
    canonical = first_non_empty(article.get("url"), parser.links.get("canonical"), url)
    if not is_summary_url(canonical):
        return None

    based_on = article.get("isBasedOn") if isinstance(article.get("isBasedOn"), dict) else {}
    author = article.get("author") if isinstance(article.get("author"), dict) else {}
    published_at = normalize_alpha_date(
        article.get("datePublished")
        or parser.meta.get("article:published_time")
        or sitemap_lastmod
    )
    modified_at = normalize_alpha_date(
        article.get("dateModified")
        or parser.meta.get("article:modified_time")
        or sitemap_lastmod
    )
    title = first_non_empty(
        article.get("headline"),
        parser.meta.get("og:title"),
        parser.title.removesuffix(" · AlphaAbstract"),
    )
    description = first_non_empty(article.get("description"), parser.meta.get("description"), parser.meta.get("og:description"))
    full_text = extract_body_text(html_text, url=canonical)
    if not title or not full_text:
        return None

    item_id = canonical_article_id(canonical)
    original_source_name = jsonld_name(based_on)
    original_source_url = jsonld_url(based_on)
    summary = description or compact_text(full_text[:360])
    return {
        "id": item_id,
        "url": canonical,
        "title": title,
        "summary": summary,
        "content": summary,
        "full_text": full_text,
        "published_at": published_at,
        "source_module": source.module,
        "source_display": source.module,
        "body_source": "AlphaAbstract public summary page",
        "access_note": source.access_note,
        "categories": [jsonld_name(author)] if jsonld_name(author) else [],
        "raw": {
            "source": source.name,
            "sitemap_url": source.sitemap_url,
            "sitemap_lastmod": sitemap_lastmod,
            "canonical_url": canonical,
            "modified_at": modified_at,
            "author": jsonld_name(author),
            "original_source_name": original_source_name,
            "original_source_url": original_source_url,
            "publisher": jsonld_name(article.get("publisher")),
            "publisher_role": "third_party_research_summary",
        },
    }


def fetch_sitemap_entries(source: AlphaAbstractSource = DEFAULT_SOURCE) -> list[dict[str, str]]:
    return parse_sitemap_entries(fetch_text(source.sitemap_url))


def max_pages_per_run() -> int:
    raw = os.getenv("ALPHAABSTRACT_MAX_PAGES_PER_RUN", "").strip()
    try:
        return max(0, int(raw)) if raw else 40
    except ValueError:
        return 40


def discover_items(source: AlphaAbstractSource = DEFAULT_SOURCE) -> list[dict[str, Any]]:
    entries = fetch_sitemap_entries(source)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        url = entry["url"]
        item_id = canonical_article_id(url)
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        lastmod = entry.get("lastmod", "")
        items.append(
            {
                "id": item_id,
                "url": url,
                "title": "",
                "summary": "",
                "content": "",
                "published_at": lastmod,
                "source_module": source.module,
                "source_display": source.module,
                "access_note": source.access_note,
                "raw": {
                    "source": source.name,
                    "sitemap_url": source.sitemap_url,
                    "sitemap_lastmod": lastmod,
                    "publisher_role": "third_party_research_summary",
                },
            }
        )
    return items


def enrich_item(
    item: dict[str, Any],
    source: AlphaAbstractSource = DEFAULT_SOURCE,
) -> dict[str, Any]:
    url = str(item.get("url") or "").strip()
    raw = dict(item.get("raw") or {})
    enriched = normalize_entry_from_article(
        url,
        fetch_text(url),
        source=source,
        sitemap_lastmod=str(raw.get("sitemap_lastmod") or item.get("published_at") or ""),
    )
    if not enriched:
        raise ValueError("public summary page did not contain a valid title and body")
    if str(enriched.get("id") or "") != str(item.get("id") or canonical_article_id(url)):
        raise ValueError("public summary canonical identity differs from sitemap identity")
    return enriched


def extract_items(source: AlphaAbstractSource = DEFAULT_SOURCE) -> list[dict[str, Any]]:
    """Return fully enriched items for the read-only collector report."""
    discoveries = discover_items(source)
    limit = max_pages_per_run()
    if limit:
        discoveries = discoveries[:limit]
    return [enrich_item(item, source) for item in discoveries]


def save_new_alphabstract_items_with_retry(
    source: AlphaAbstractSource,
    items: Iterable[dict[str, Any]],
    *,
    notify_baseline: bool = False,
    expanded_scope_baseline: bool = False,
) -> list[dict[str, Any]]:
    def operation() -> list[dict[str, Any]]:
        with connect_db() as conn:
            return save_new_items(
                conn,
                source.name,
                items,
                notify_baseline=notify_baseline,
                source_label=source.module,
                expanded_scope_baseline=expanded_scope_baseline,
            )

    return retry_on_locked(operation)


def normalized_alphabstract_item(
    item: dict[str, Any],
    source: AlphaAbstractSource = DEFAULT_SOURCE,
):
    prepared = dict(item)
    raw = dict(prepared.get("raw") or {})
    raw.setdefault("publisher_role", "third_party_research_summary")
    prepared["raw"] = raw
    prepared["source_category"] = "research_industry_media"
    prepared["publisher_role"] = "third_party_research_summary"
    prepared["collector"] = "alphabstract_monitor"
    prepared["content_type"] = "research_summary"
    return normalize_market_item(
        source.name,
        prepared,
        store_kind="article",
        source_profile_id=source.name,
    )


def set_seen_item_lifecycle(source: str, item_id: str, **values: str | None) -> None:
    with connect_db() as conn:
        update_seen_item_lifecycle(conn, source, item_id, **values)
        conn.commit()


def set_seen_item_enriched(source: str, item_id: str, item: dict[str, Any]) -> None:
    """Backfill bounded discovery metadata; the article body stays out of seen_items."""
    with connect_db() as conn:
        cursor = conn.execute(
            """
            UPDATE seen_items
            SET url = ?, title = ?, summary = ?, published_at = ?,
                processability_status = 'succeeded', processability_reason = '',
                admission_status = 'pending', admission_reason = '',
                processing_status = 'not_applicable', processing_error = '',
                lifecycle_updated_at = ?
            WHERE source = ? AND item_id = ?
            """,
            (
                item.get("url", ""),
                item.get("title", ""),
                item.get("summary", ""),
                item.get("published_at", ""),
                datetime.now(timezone.utc).isoformat(),
                source,
                item_id,
            ),
        )
        if cursor.rowcount != 1:
            raise LookupError(f"seen item not found: {source}/{item_id}")
        conn.commit()


def notify_item(item: dict[str, Any], *, source: AlphaAbstractSource = DEFAULT_SOURCE) -> None:
    discovery = dict(item)
    item_id = str(discovery.get("id", "") or "")
    discovery.pop("_seen_item_retry", None)
    discovery.pop("_seen_item_retry_first_seen_at", None)
    try:
        enriched = enrich_item(discovery, source)
    except Exception as exc:
        set_seen_item_lifecycle(
            source.name,
            item_id,
            processability_status="failed_retryable",
            processability_reason=f"detail_fetch_or_parse_failed:{type(exc).__name__}",
            admission_status="pending",
            admission_reason="",
            processing_status="not_applicable",
            processing_error="",
            processed_at=None,
            lifecycle_updated_at=datetime.now(timezone.utc).isoformat(),
        )
        raise

    set_seen_item_enriched(source.name, item_id, enriched)
    try:
        normalized = normalized_alphabstract_item(enriched, source)
        admission_context = production_admission_context(normalized, db_path=DB_PATH)
        admission = admission_context.result
        if admission.status != "admitted":
            set_seen_item_lifecycle(
                source.name,
                item_id,
                **admission_lifecycle_values(admission, processing_status="not_applicable"),
                processed_at=datetime.now(timezone.utc).isoformat(),
            )
            return
        set_seen_item_lifecycle(
            source.name,
            item_id,
            **admission_lifecycle_values(admission, processing_status="pending"),
        )
        outcome = process_market_item(
            normalized,
            enriched,
            store_kind="article",
            source_profile_id=source.name,
            db_path=DB_PATH,
            use_rule_dedup=True,
            production_admission=admission,
            production_portfolio=admission_context.portfolio,
        )
        set_seen_item_lifecycle(
            source.name,
            item_id,
            **admission_lifecycle_values(admission, processing_status="succeeded"),
            processed_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as exc:
        set_seen_item_lifecycle(
            source.name,
            item_id,
            processing_status="failed_retryable",
            processing_error=f"{type(exc).__name__}: {str(exc)[:400]}",
            processed_at=None,
            lifecycle_updated_at=datetime.now(timezone.utc).isoformat(),
        )
        raise
    decision = outcome.flow_result.decision
    print(
        f"{source.name} 统一决策：importance={decision.importance} "
        f"action={decision.action} delivery={outcome.delivery_status} title={enriched.get('title', '')}",
        flush=True,
    )


def run_once(sources: list[AlphaAbstractSource] | None = None, notify_baseline: bool = False) -> int:
    sources = sources or list(ALPHAABSTRACT_SOURCES)
    total_new = 0
    for source in sources:
        if not source_profile_enabled(source.name):
            print(f"source profile: {source.name} 已停用，跳过本轮。", flush=True)
            continue
        try:
            items = discover_items(source)
            with connect_db() as conn:
                record_source_success(conn, MONITOR, source.name)
                scope_state = load_scope_state(conn, source.name)
            expanded_scope_baseline = not bool(scope_state.get(EXPANDED_SCOPE_BASELINE_STATE_KEY))
            new_items = (
                save_new_alphabstract_items_with_retry(
                    source,
                    items,
                    notify_baseline=notify_baseline,
                    expanded_scope_baseline=expanded_scope_baseline,
                )
                if items
                else []
            )
            if items and expanded_scope_baseline:
                scope_state[EXPANDED_SCOPE_BASELINE_STATE_KEY] = datetime.now(timezone.utc).isoformat()
                with connect_db() as conn:
                    save_scope_state(conn, source.name, scope_state)
        except Exception as exc:  # noqa: BLE001 - one source failure should be recorded
            with connect_db() as conn:
                record_source_failure(conn, MONITOR, source.name, exc)
            print(f"{source.name} AlphaAbstract 监控失败：{exc}", flush=True)
            continue
        if not new_items:
            print(f"{source.name}: 没有发现需通知的新条目。", flush=True)
            continue
        total_new += len(new_items)
        print(f"{source.name}: 发现 {len(new_items)} 条新条目。", flush=True)
        limit = max_pages_per_run()
        selected_items = new_items[:limit] if limit else new_items
        if len(selected_items) < len(new_items):
            print(
                f"{source.name}: 本轮处理 {len(selected_items)} 条，其余 pending 条目留待下一自然周期。",
                flush=True,
            )
        for item in selected_items:
            print("=" * 80)
            print(item.get("title", ""))
            print(item.get("url", ""))
            print(item.get("published_at", ""))
            try:
                notify_item(item, source=source)
            except Exception as exc:  # noqa: BLE001 - retry state is already persisted
                print(f"{source.name} 条目处理失败，留待重试：{exc}", flush=True)
    return total_new


def selected_sources(names: list[str]) -> list[AlphaAbstractSource]:
    if not names:
        return list(ALPHAABSTRACT_SOURCES)
    by_name = {source.name: source for source in ALPHAABSTRACT_SOURCES}
    missing = [name for name in names if name not in by_name]
    if missing:
        raise SystemExit(f"未知 AlphaAbstract source：{', '.join(missing)}")
    return [by_name[name] for name in names]


def main() -> int:
    load_env(ENV_PATH)
    parser = argparse.ArgumentParser(description="Monitor AlphaAbstract public research summaries.")
    parser.add_argument("--source", action="append", default=[], help="只监控指定 AlphaAbstract source id，可重复。")
    parser.add_argument("--interval", type=int, default=int(os.getenv("ALPHAABSTRACT_INTERVAL", "0")))
    parser.add_argument("--notify-baseline", action="store_true", help="首次建立基线时也发送通知。默认不发送旧条目。")
    args = parser.parse_args()
    sources = selected_sources(args.source)
    notify_baseline = args.notify_baseline or os.getenv("SURVEIL_NOTIFY_BASELINE", "") == "1"
    if args.interval <= 0:
        run_once(sources, notify_baseline=notify_baseline)
        return 0
    print(f"开始监控 {len(sources)} 个 AlphaAbstract source，轮询间隔 {args.interval} 秒。", flush=True)
    while True:
        run_once(sources, notify_baseline=notify_baseline)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
