#!/usr/bin/env python3
"""Monitor public official trade-policy sources for early-warning content."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from db_utils import retry_on_locked, update_seen_item_lifecycle
from http_utils import http_get
from decision_engine import decide_market_item
from market_flow import normalize_market_item, process_market_item
from rss_monitor import DB_PATH, connect_db, save_new_items, strip_tags
from source_health import record_source_failure, record_source_success
from source_profiles import source_profile_enabled
from time_utils import parse_datetime_to_utc_iso
from trade_policy_sources import TRADE_POLICY_SOURCES, TradePolicySource
from x_check import load_env


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
MONITOR = "trade_policy"

def compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def normalize_date(value: Any) -> str:
    raw = compact_text(value)
    if not raw:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return f"{raw}T00:00:00+00:00"
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return parse_datetime_to_utc_iso(raw)


def canonical_item_id(url: str, fallback: str = "") -> str:
    if fallback:
        return compact_text(fallback)
    parsed = urllib.parse.urlsplit(str(url or ""))
    path = parsed.path.rstrip("/")
    return path.rsplit("/", 1)[-1] or str(url or "")


def fetch_bytes(url: str, *, accept: str) -> bytes:
    response = http_get(
        url,
        headers={"Accept": accept},
        timeout=float(os.getenv("TRADE_POLICY_TIMEOUT_SECONDS", "25") or "25"),
        retries=int(os.getenv("TRADE_POLICY_RETRY_COUNT", os.getenv("SURVEIL_HTTP_RETRY_COUNT", "1")) or "1"),
    )
    return response.content


def fetch_text(url: str) -> str:
    return fetch_bytes(
        url,
        accept="text/html,application/xhtml+xml,application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
    ).decode("utf-8", errors="replace")


def fetch_json(url: str) -> dict[str, Any]:
    payload = json.loads(fetch_bytes(url, accept="application/json").decode("utf-8", errors="replace"))
    if not isinstance(payload, dict):
        raise ValueError("official JSON response is not an object")
    return payload


def parse_federal_register_payload(payload: dict[str, Any], source: TradePolicySource) -> list[dict[str, Any]]:
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("Federal Register response lacks results")
    items: list[dict[str, Any]] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        title = compact_text(row.get("title"))
        abstract = compact_text(row.get("abstract"))
        primary = f"{title} {abstract}".lower()
        if not any(term in primary for term in ("china", "chinese", "people's republic of china")):
            continue
        url = str(row.get("html_url") or "").strip()
        document_number = compact_text(row.get("document_number"))
        if not title or not url or not document_number:
            continue
        agencies = [
            compact_text(agency.get("name") or agency.get("raw_name"))
            for agency in row.get("agencies") or []
            if isinstance(agency, dict)
        ]
        agency_context = (
            f"United States {', '.join(agency for agency in agencies if agency)} document concerning China"
        ).strip()
        summary_text = abstract or compact_text(strip_tags(str(row.get("excerpts") or "")))
        summary = compact_text(f"{agency_context}: {summary_text}")
        items.append(
            {
                "id": document_number,
                "url": url,
                "title": title,
                "summary": summary,
                "content": summary,
                "full_text": summary,
                "published_at": normalize_date(row.get("publication_date")),
                "source_module": source.module,
                "source_display": source.module,
                "body_source": "Federal Register official API",
                "access_note": source.access_note,
                "raw": {
                    "document_number": document_number,
                    "document_type": compact_text(row.get("type")),
                    "agencies": [agency for agency in agencies if agency],
                    "pdf_url": str(row.get("pdf_url") or ""),
                    "public_inspection_pdf_url": str(row.get("public_inspection_pdf_url") or ""),
                    "discovery_url": source.url,
                },
            }
        )
    return items


class ListItemParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, Any]] = []
        self._depth = 0
        self._current: dict[str, Any] | None = None
        self._link: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "li":
            if self._depth == 0:
                self._current = {"text": "", "links": []}
            self._depth += 1
        elif tag.lower() == "a" and self._current is not None:
            self._link = {"href": data.get("href", ""), "title": data.get("title", ""), "text": ""}

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current["text"] += data
        if self._link is not None:
            self._link["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._link is not None:
            if self._current is not None:
                self._current["links"].append(self._link)
            self._link = None
        elif tag.lower() == "li" and self._depth:
            self._depth -= 1
            if self._depth == 0 and self._current is not None:
                self.items.append(self._current)
                self._current = None


def parse_list_items(html_text: str) -> list[dict[str, Any]]:
    parser = ListItemParser()
    parser.feed(html_text)
    return parser.items


def list_item_date(text: str) -> str:
    match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    return normalize_date(match.group(1)) if match else ""


def parse_ustr_html(html_text: str, source: TradePolicySource) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in parse_list_items(html_text):
        published_at = list_item_date(str(row.get("text") or ""))
        for link in row.get("links") or []:
            href = urllib.parse.urljoin(source.url, str(link.get("href") or ""))
            path = urllib.parse.urlsplit(href).path
            if "/press-office/press-releases/20" not in path or not published_at:
                continue
            title = compact_text(link.get("title") or link.get("text"))
            item_id = canonical_item_id(href)
            if not title or item_id in seen:
                continue
            seen.add(item_id)
            items.append(
                {
                    "id": item_id,
                    "url": href,
                    "title": title,
                    "summary": "",
                    "content": "",
                    "published_at": published_at,
                    "source_module": source.module,
                    "source_display": source.module,
                    "body_source": "USTR official press release list",
                    "access_note": source.access_note,
                    "raw": {"discovery_url": source.url},
                }
            )
    items.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    return items[:60]


def parse_eu_rss(xml_text: str, source: TradePolicySource) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    items: list[dict[str, Any]] = []
    for node in root.findall(".//item"):
        title = compact_text(node.findtext("title"))
        url = compact_text(node.findtext("link"))
        summary = compact_text(strip_tags(node.findtext("description") or ""))
        guid = compact_text(node.findtext("guid"))
        if not title or not url:
            continue
        items.append(
            {
                "id": canonical_item_id(url, guid),
                "url": url,
                "title": title,
                "summary": summary,
                "content": summary,
                "full_text": summary,
                "published_at": normalize_date(node.findtext("pubDate")),
                "source_module": source.module,
                "source_display": source.module,
                "body_source": "European Commission Press Corner RSS",
                "access_note": source.access_note,
                "raw": {
                    "categories": [compact_text(category.text) for category in node.findall("category") if category.text],
                    "discovery_url": source.url,
                },
            }
        )
    return items


def parse_mofcom_links(
    html_text: str,
    source: TradePolicySource,
    *,
    path_fragment: str,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in parse_list_items(html_text):
        published_at = list_item_date(str(row.get("text") or ""))
        for link in row.get("links") or []:
            href = urllib.parse.urljoin(source.url, str(link.get("href") or ""))
            if path_fragment not in urllib.parse.urlsplit(href).path:
                continue
            title = compact_text(link.get("title") or link.get("text"))
            item_id = canonical_item_id(href)
            if not title or item_id in seen:
                continue
            seen.add(item_id)
            items.append(
                {
                    "id": item_id,
                    "url": href,
                    "title": title,
                    "summary": "",
                    "content": "",
                    "published_at": published_at,
                    "source_module": source.module,
                    "source_display": source.module,
                    "body_source": "MOFCOM official list",
                    "access_note": source.access_note,
                    "raw": {"discovery_url": source.url},
                }
            )
    items.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    return items[:40]


def parse_mofcom_policy_html(html_text: str, source: TradePolicySource) -> list[dict[str, Any]]:
    return parse_mofcom_links(html_text, source, path_fragment="/zcfb/")


def parse_mofcom_spokesperson_html(html_text: str, source: TradePolicySource) -> list[dict[str, Any]]:
    return parse_mofcom_links(html_text, source, path_fragment="/xwfb/xwfyrth/art/")


PARSERS = {
    "federal_register_json": lambda payload, source: parse_federal_register_payload(payload, source),
    "ustr_html": parse_ustr_html,
    "eu_rss": parse_eu_rss,
    "mofcom_policy_html": parse_mofcom_policy_html,
    "mofcom_spokesperson_html": parse_mofcom_spokesperson_html,
}


def discover_items(source: TradePolicySource) -> list[dict[str, Any]]:
    if source.parser == "federal_register_json":
        items = parse_federal_register_payload(fetch_json(source.url), source)
    else:
        items = PARSERS[source.parser](fetch_text(source.url), source)
    if not items:
        raise ValueError(f"{source.name} official source parsed zero items")
    return items


class PageMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title_parts: list[str] = []
        self._capture_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta":
            key = data.get("name") or data.get("property")
            if key and data.get("content"):
                self.meta[key.lower()] = compact_text(data["content"])
        elif tag.lower() == "title":
            self._capture_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._capture_title = False

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self.title_parts.append(data)

    @property
    def title(self) -> str:
        return compact_text("".join(self.title_parts))


def extract_page_text(html_text: str, *, url: str) -> tuple[str, str]:
    try:
        import trafilatura

        extracted = trafilatura.extract(
            html_text,
            url=url,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
        if extracted and len(extracted.strip()) >= 80:
            return extracted.strip()[:30000], "public detail page / trafilatura"
    except Exception:  # noqa: BLE001 - deterministic fallback remains available
        pass

    paragraphs = [compact_text(strip_tags(part)) for part in re.findall(r"(?is)<p\b[^>]*>(.*?)</p>", html_text)]
    paragraphs = [part for part in paragraphs if len(part) >= 30]
    if paragraphs:
        return "\n\n".join(paragraphs)[:30000], "public detail page / paragraphs"
    return "", "public detail page / metadata"


def enrich_item(item: dict[str, Any]) -> dict[str, Any]:
    url = str(item.get("url") or "").strip()
    if not url:
        return item
    html_text = fetch_text(url)
    parser = PageMetadataParser()
    parser.feed(html_text)
    meta = parser.meta
    body, body_source = extract_page_text(html_text, url=url)
    title = compact_text(
        meta.get("articletitle")
        or meta.get("og:title")
        or meta.get("twitter:title")
        or item.get("title")
        or parser.title
    )
    summary = compact_text(
        meta.get("description")
        or meta.get("og:description")
        or meta.get("twitter:description")
        or item.get("summary")
    )
    published_at = normalize_date(
        meta.get("pubdate")
        or meta.get("article:published_time")
        or meta.get("date")
        or item.get("published_at")
    )
    enriched = dict(item)
    enriched.update(
        {
            "title": title or str(item.get("title") or ""),
            "summary": summary or compact_text(body[:500]),
            "content": summary or body,
            "full_text": body or summary or str(item.get("full_text") or ""),
            "published_at": published_at,
            "body_source": body_source,
        }
    )
    raw = dict(enriched.get("raw") or {})
    raw.update(
        {
            "detail_page_metadata": {
                key: value
                for key, value in meta.items()
                if key in {"articletitle", "pubdate", "contentsource", "description", "og:title", "og:description"}
            },
            "detail_extraction_source": body_source,
        }
    )
    enriched["raw"] = raw
    return enriched


def seen_item_ids(source_name: str) -> set[str]:
    with connect_db() as conn:
        return {
            str(row[0] or "")
            for row in conn.execute("SELECT item_id FROM seen_items WHERE source = ?", (source_name,))
        }


def source_has_baseline(source_name: str) -> bool:
    with connect_db() as conn:
        row = conn.execute("SELECT 1 FROM seen_sources WHERE source = ? LIMIT 1", (source_name,)).fetchone()
    return row is not None


def enrich_unseen_items(source: TradePolicySource, items: list[dict[str, Any]], *, enrich_all: bool = False) -> list[dict[str, Any]]:
    seen = set() if enrich_all else seen_item_ids(source.name)
    enriched: list[dict[str, Any]] = []
    for item in items:
        if str(item.get("id") or "") in seen or source.parser == "federal_register_json":
            enriched.append(item)
            continue
        try:
            enriched.append(enrich_item(item))
        except Exception as exc:  # noqa: BLE001 - title/list evidence remains processable
            fallback = dict(item)
            raw = dict(fallback.get("raw") or {})
            raw["detail_enrichment_error"] = f"{type(exc).__name__}: {exc}"
            fallback["raw"] = raw
            enriched.append(fallback)
    return enriched


def save_new_trade_policy_items_with_retry(
    source: TradePolicySource,
    items: Iterable[dict[str, Any]],
    *,
    notify_baseline: bool = False,
) -> list[dict[str, Any]]:
    def operation() -> list[dict[str, Any]]:
        with connect_db() as conn:
            return save_new_items(
                conn,
                source.name,
                items,
                notify_baseline=notify_baseline,
                source_label=source.module,
            )

    return retry_on_locked(operation)


def set_seen_item_lifecycle(source: str, item_id: str, **values: Any) -> None:
    with connect_db() as conn:
        update_seen_item_lifecycle(conn, source, item_id, **values)
        conn.commit()


def normalized_trade_policy_item(item: dict[str, Any], source: TradePolicySource):
    prepared = dict(item)
    raw = dict(prepared.get("raw") or {})
    raw.setdefault("publisher_role", "government_official")
    raw.setdefault("official_source", source.module)
    prepared["raw"] = raw
    prepared["source_category"] = "official_policy"
    prepared["publisher_role"] = "government_official"
    prepared["collector"] = "trade_policy_monitor"
    prepared["content_type"] = "official_policy"
    return normalize_market_item(
        source.name,
        prepared,
        store_kind="article",
        source_profile_id=source.name,
    )


def notify_item(item: dict[str, Any], *, source: TradePolicySource) -> None:
    item_id = str(item.get("id") or "")
    enriched = dict(item)
    detail_status = "not_required"
    detail_error = ""
    if source.parser != "federal_register_json":
        try:
            enriched = enrich_item(item)
            detail_status = "succeeded"
        except Exception as exc:  # noqa: BLE001 - official list evidence remains usable.
            detail_status = "fallback"
            detail_error = f"{type(exc).__name__}: {str(exc)[:400]}"
            raw = dict(enriched.get("raw") or {})
            raw["detail_enrichment_error"] = detail_error
            enriched["raw"] = raw
    set_seen_item_lifecycle(
        source.name,
        item_id,
        processability_status=detail_status,
        processability_reason="detail_fallback" if detail_status == "fallback" else "",
        admission_status="admitted",
        admission_reason="current_official_trade_source",
        admission_matched_families_json=json.dumps(["trade_policy"]),
        admission_evidence_json="[]",
        admission_config_version="current-production",
        admission_rule_contract_version="current-flow-v1",
        admission_evaluated_at=datetime.now(timezone.utc).isoformat(),
        processing_status="pending",
        processing_error="",
    )
    try:
        normalized = normalized_trade_policy_item(enriched, source)
        outcome = process_market_item(
            normalized,
            enriched,
            store_kind="article",
            source_profile_id=source.name,
            db_path=DB_PATH,
            use_rule_dedup=True,
            current_admission_status="admitted",
            current_admission_reason="current_official_trade_source",
            current_matched_families=("trade_policy",),
        )
    except Exception as exc:
        set_seen_item_lifecycle(
            source.name,
            item_id,
            processing_status="failed_retryable",
            processing_error=f"{type(exc).__name__}: {str(exc)[:400]}",
            processed_at=None,
        )
        raise
    set_seen_item_lifecycle(
        source.name,
        item_id,
        processing_status="succeeded",
        processing_error="",
        processed_at=datetime.now(timezone.utc).isoformat(),
        lifecycle_updated_at=datetime.now(timezone.utc).isoformat(),
    )
    decision = outcome.flow_result.decision
    print(
        f"{source.name} 统一决策：importance={decision.importance} "
        f"action={decision.action} delivery={outcome.delivery_status} title={item.get('title', '')}",
        flush=True,
    )


def run_once(sources: list[TradePolicySource] | None = None, notify_baseline: bool = False) -> int:
    sources = sources or list(TRADE_POLICY_SOURCES)
    total_new = 0
    for source in sources:
        if not source_profile_enabled(source.name):
            print(f"source profile: {source.name} 已停用，跳过本轮。", flush=True)
            continue
        try:
            items = discover_items(source)
            new_items = save_new_trade_policy_items_with_retry(source, items, notify_baseline=notify_baseline)
            with connect_db() as conn:
                record_source_success(conn, MONITOR, source.name)
        except Exception as exc:  # noqa: BLE001 - isolate source failures and preserve health audit
            with connect_db() as conn:
                record_source_failure(conn, MONITOR, source.name, exc)
            print(f"{source.name} 官方贸易政策监控失败：{exc}", flush=True)
            continue
        if not new_items:
            print(f"{source.name}: 没有发现需通知的新条目。", flush=True)
            continue
        total_new += len(new_items)
        print(f"{source.name}: 发现 {len(new_items)} 条新条目。", flush=True)
        for item in new_items:
            notify_item(item, source=source)
    return total_new


def shadow_collect(
    sources: list[TradePolicySource] | None = None,
    *,
    limit: int = 10,
) -> dict[str, Any]:
    sources = sources or list(TRADE_POLICY_SOURCES)
    rows: list[dict[str, Any]] = []
    for source in sources:
        if not source_profile_enabled(source.name):
            continue
        try:
            discovered = discover_items(source)
            selected = discovered if limit <= 0 else discovered[:limit]
            items = enrich_unseen_items(source, selected, enrich_all=True)
            candidates = []
            for item in items:
                decision = decide_market_item(normalized_trade_policy_item(item, source), holdings=[])
                candidates.append(
                    {
                        "source": source.name,
                        "id": item.get("id", ""),
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "published_at": item.get("published_at", ""),
                        "decision": decision.to_dict(),
                    }
                )
            rows.append(
                {
                    "source": source.name,
                    "label": source.module,
                    "ok": True,
                    "raw_count": len(discovered),
                    "candidates": candidates,
                    "error": "",
                }
            )
        except Exception as exc:  # noqa: BLE001 - shadow report should include every source
            rows.append(
                {
                    "source": source.name,
                    "label": source.module,
                    "ok": False,
                    "raw_count": 0,
                    "candidates": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return {
        "mode": "shadow",
        "wrote_production_state": False,
        "rows": rows,
        "errors": [row for row in rows if not row.get("ok")],
    }


def selected_sources(names: Iterable[str]) -> list[TradePolicySource]:
    requested = [compact_text(name) for name in names if compact_text(name)]
    if not requested:
        return list(TRADE_POLICY_SOURCES)
    by_name = {source.name: source for source in TRADE_POLICY_SOURCES}
    missing = [name for name in requested if name not in by_name]
    if missing:
        raise SystemExit(f"未知官方贸易政策 source：{', '.join(missing)}")
    return [by_name[name] for name in requested]


def main() -> int:
    load_env(ENV_PATH)
    parser = argparse.ArgumentParser(description="Monitor official China-US / China-EU trade-policy sources.")
    parser.add_argument("--source", action="append", default=[], help="只跑指定 source id，可重复。")
    parser.add_argument("--shadow", action="store_true", help="只抓取、解析和运行直接决策，不写生产库或投递。")
    parser.add_argument("--limit", type=int, default=10, help="shadow 每个来源最多输出条数；0 表示不限制。")
    parser.add_argument("--notify-baseline", action="store_true", help="首次建立基线时也处理旧条目；默认关闭。")
    args = parser.parse_args()
    sources = selected_sources(args.source)
    if args.shadow:
        print(json.dumps(shadow_collect(sources, limit=max(0, args.limit)), ensure_ascii=False, indent=2))
        return 0
    run_once(sources, notify_baseline=args.notify_baseline or os.getenv("SURVEIL_NOTIFY_BASELINE", "") == "1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
