"""Public WallstreetCN article and livenews discovery/detail enrichment."""

from __future__ import annotations

import html as html_lib
import json
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import trafilatura
from lxml import html

from http_utils import http_get


SOURCE = "wallstreetcn_news"
BASE_URL = "https://wallstreetcn.com"
DEFAULT_CATEGORIES = ("global",)
LAST_SURFACE_RESULTS: dict[str, dict[str, Any]] = {}
PENDING_STATE: dict[str, Any] = {}


def _timeout() -> int:
    try:
        return max(5, int(os.getenv("WALLSTREETCN_FETCH_TIMEOUT_SECONDS", "20")))
    except ValueError:
        return 20


def _categories() -> list[str]:
    raw = os.getenv("WALLSTREETCN_NEWS_CATEGORIES", "").strip()
    values = raw.split(",") if raw else list(DEFAULT_CATEGORIES)
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def discovery_surface_results(surface: str) -> list[dict[str, Any]]:
    """Return near-real-time list results without sitemap reconciliation rows."""
    return [
        row
        for name, row in LAST_SURFACE_RESULTS.items()
        if row.get("surface") == surface and not name.startswith("sitemap:")
    ]


def canonical_url(value: str) -> str:
    parsed = urllib.parse.urlparse(str(value or "").strip())
    if not parsed.netloc:
        parsed = urllib.parse.urlparse(urllib.parse.urljoin(BASE_URL, str(value or "")))
    if parsed.netloc not in {"wallstreetcn.com", "www.wallstreetcn.com"}:
        return ""
    path = re.sub(r"/+", "/", parsed.path)
    return urllib.parse.urlunparse(("https", "wallstreetcn.com", path, "", "", ""))


def _surface_and_id(url: str) -> tuple[str, str]:
    match = re.search(r"/(articles|livenews)/(\d+)$", url)
    if not match:
        return "", ""
    return ("article" if match.group(1) == "articles" else "livenews", match.group(2))


def parse_list_page(html_text: str, *, surface: str, discovery_url: str) -> list[dict[str, Any]]:
    try:
        document = html.fromstring(html_text)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"WallstreetCN {surface} list HTML invalid") from exc
    path = "articles" if surface == "article" else "livenews"
    links = document.xpath(f'//a[starts-with(@href, "/{path}/") or starts-with(@href, "/member/{path}/")]')
    by_id: dict[str, dict[str, Any]] = {}
    for link in links:
        href = str(link.get("href") or "")
        access_tier = "member" if href.startswith("/member/") else "public"
        public_href = href.replace("/member/articles/", "/articles/") if access_tier == "member" else href
        url = canonical_url(public_href)
        resolved_surface, item_id = _surface_and_id(url)
        if resolved_surface != surface or not item_id:
            continue
        title_nodes = link.xpath('.//h1//text() | .//h2//text() | .//h3//text() | .//span//text()')
        title = re.sub(r"\s+", " ", " ".join(str(value) for value in title_nodes)).strip()
        if not title:
            title = re.sub(r"\s+", " ", link.text_content()).strip()
        time_nodes = link.xpath('.//time/@datetime')
        published_at = str(time_nodes[0]).strip() if time_nodes else ""
        by_id.setdefault(f"{surface}:{item_id}", {
            "id": f"{surface}:{item_id}",
            "url": url,
            "title": title,
            "summary": title,
            "content": "",
            "published_at": published_at,
            "source_module": "华尔街见闻",
            "body_source": f"公开{surface}列表页",
            "access_note": "华尔街见闻公开页面；不访问会员内容或绕过访问控制。",
            "raw": {
                "wallstreetcn_id": item_id,
                "wallstreetcn_surface": surface,
                "wallstreetcn_discovery_url": discovery_url,
                "wallstreetcn_access_tier": access_tier,
            },
        })
    if not by_id:
        raise ValueError(f"WallstreetCN {surface} list contains no stable item links")
    return list(by_id.values())


def _fetch_list(url: str, surface: str) -> list[dict[str, Any]]:
    response = http_get(
        url,
        headers={"Accept": "text/html,application/xhtml+xml", "User-Agent": "MarketPulseWire/1.0"},
        timeout=_timeout(),
    )
    return parse_list_page(response.content.decode("utf-8", errors="replace"), surface=surface, discovery_url=url)


def parse_sitemap(xml_text: str, *, surface: str, discovery_url: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"WallstreetCN {surface} sitemap XML invalid") from exc
    items: list[dict[str, Any]] = []
    for node in root:
        values: dict[str, str] = {}
        for child in node.iter():
            key = child.tag.rsplit("}", 1)[-1]
            value = str(child.text or "").strip()
            if value and key not in values:
                values[key] = value
        url = canonical_url(values.get("loc", ""))
        resolved_surface, item_id = _surface_and_id(url)
        if resolved_surface != surface or not item_id:
            continue
        items.append(
            {
                "id": f"{surface}:{item_id}",
                "url": url,
                "title": values.get("title", ""),
                "summary": values.get("title", ""),
                "content": "",
                "published_at": values.get("publication_date") or values.get("lastmod", ""),
                "source_module": "华尔街见闻",
                "body_source": f"官方{surface} sitemap",
                "access_note": "华尔街见闻官方公开 sitemap；不访问会员内容或绕过访问控制。",
                "raw": {
                    "wallstreetcn_id": item_id,
                    "wallstreetcn_surface": surface,
                    "wallstreetcn_discovery_url": discovery_url,
                    "wallstreetcn_access_tier": "public",
                },
            }
        )
    if not items:
        raise ValueError(f"WallstreetCN {surface} sitemap contains no item URLs")
    return items


def _sitemap_items(surface: str, month: str) -> list[dict[str, Any]]:
    path = "articles" if surface == "article" else "livenews"
    url = f"{BASE_URL}/sitemap-{path}-{month}.xml"
    response = http_get(url, headers={"Accept": "application/xml,text/xml"}, timeout=_timeout())
    return parse_sitemap(response.content.decode("utf-8", errors="replace"), surface=surface, discovery_url=url)


def collect_items(*, state: dict[str, Any] | None = None, force_reconcile: bool = False) -> list[dict[str, Any]]:
    PENDING_STATE.clear()
    state = dict(state or {})
    targets = [(f"articles:{category}", f"{BASE_URL}/news/{category}", "article") for category in _categories()]
    targets.append(("livenews", f"{BASE_URL}/live", "livenews"))
    by_id: dict[str, dict[str, Any]] = {}
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(4, len(targets))) as executor:
        futures = {executor.submit(_fetch_list, url, surface): (name, url, surface) for name, url, surface in targets}
        for future in as_completed(futures):
            name, url, surface = futures[future]
            try:
                rows = future.result()
                for row in rows:
                    by_id[row["id"]] = row
                results[name] = {"ok": True, "count": len(rows), "url": url, "surface": surface, "error": ""}
            except Exception as exc:  # noqa: BLE001 - partial surface failure stays visible
                results[name] = {
                    "ok": False,
                    "count": 0,
                    "url": url,
                    "surface": surface,
                    "error": f"{type(exc).__name__}: {exc}",
                }

    now = time.time()
    try:
        interval = max(300, int(os.getenv("WALLSTREETCN_SITEMAP_RECONCILE_SECONDS", "3600")))
    except ValueError:
        interval = 3600
    reconcile = force_reconcile or now - float(state.get("last_sitemap_reconcile_epoch") or 0) >= interval
    if reconcile:
        sitemap_results: list[bool] = []
        current_month = datetime.now(timezone.utc).strftime("%Y%m")
        previous_state_month = str(state.get("last_sitemap_month") or "")
        months = [current_month]
        if previous_state_month and previous_state_month != current_month:
            months.append(previous_state_month)
        for month in months:
            for surface in ("article", "livenews"):
                name = f"sitemap:{surface}:{month}"
                try:
                    rows = _sitemap_items(surface, month)
                    for row in rows:
                        by_id.setdefault(row["id"], row)
                    results[name] = {"ok": True, "count": len(rows), "surface": surface, "error": ""}
                    sitemap_results.append(True)
                except Exception as exc:  # noqa: BLE001
                    results[name] = {
                        "ok": False,
                        "count": 0,
                        "surface": surface,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    sitemap_results.append(False)
        if sitemap_results and all(sitemap_results):
            PENDING_STATE.update(
                {
                    "last_sitemap_reconcile_epoch": now,
                    "last_sitemap_reconcile_at": datetime.now(timezone.utc).isoformat(),
                    "last_sitemap_month": current_month,
                }
            )
    LAST_SURFACE_RESULTS.clear()
    LAST_SURFACE_RESULTS.update(results)
    if not any(result.get("ok") for result in results.values()):
        raise RuntimeError(f"WallstreetCN all discovery surfaces failed: {results}")
    return sorted(by_id.values(), key=lambda item: (str(item.get("published_at") or ""), item["id"]), reverse=True)


def _meta(document: Any, *keys: str) -> str:
    for key in keys:
        values = document.xpath(
            f'//meta[translate(@property,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz")="{key.casefold()}" or '
            f'translate(@name,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz")="{key.casefold()}"]/@content'
        )
        if values and str(values[0]).strip():
            return html_lib.unescape(str(values[0]).strip())
    return ""


def enrich_item(item: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(item)
    raw = dict(enriched.get("raw") or {})
    if raw.get("wallstreetcn_access_tier") == "member":
        raw["wallstreetcn_detail_status"] = "member_unavailable"
        enriched["raw"] = raw
        enriched["_skip_decision"] = True
        return enriched
    response = http_get(
        str(enriched.get("url") or ""),
        headers={"Accept": "text/html,application/xhtml+xml", "User-Agent": "MarketPulseWire/1.0"},
        timeout=_timeout(),
    )
    html_text = response.content.decode("utf-8", errors="replace")
    try:
        document = html.fromstring(html_text)
    except (ValueError, TypeError) as exc:
        raise ValueError("WallstreetCN detail HTML invalid") from exc
    title = _meta(document, "og:title", "twitter:title")
    if not title:
        titles = document.xpath("//h1//text() | //title/text()")
        title = re.sub(r"\s+", " ", " ".join(str(value) for value in titles)).strip()
    title = re.sub(r"\s*[-_|]\s*华尔街见闻\s*$", "", title).strip()
    if raw.get("wallstreetcn_surface") == "livenews" and title in {"快讯", "华尔街见闻"}:
        title = str(enriched.get("title") or "").strip()
    published_at = _meta(document, "article:published_time", "og:published_time") or str(enriched.get("published_at") or "")
    if not published_at:
        time_values = document.xpath("//time/@datetime")
        published_at = str(time_values[0]).strip() if time_values else ""
    if not published_at:
        timestamp = re.search(r'"display_time"\s*:\s*(\d{10})', html_text)
        if timestamp:
            published_at = datetime.fromtimestamp(int(timestamp.group(1)), tz=timezone.utc).isoformat()
    author = _meta(document, "author", "article:author")
    body = trafilatura.extract(
        html_text,
        url=str(response.url),
        include_comments=False,
        include_tables=False,
        favor_recall=True,
    ) or ""
    body = body.strip()
    body = re.split(r"\n\s*风险提示及免责条款", body, maxsplit=1)[0].strip()
    if len(body) < 20:
        body = _meta(document, "description", "og:description").strip()
    if not title or not body:
        raise ValueError("WallstreetCN detail lacks non-empty title/body")
    enriched.update(
        {
            "title": title,
            "summary": body[:600],
            "content": body,
            "full_text": body,
            "published_at": published_at,
            "source_module": "华尔街见闻",
            "body_source": "华尔街见闻公开详情页",
        }
    )
    raw.update(
        {
            "wallstreetcn_detail_status": "ok",
            "wallstreetcn_author": author,
            "wallstreetcn_canonical_url": canonical_url(str(response.url)),
        }
    )
    enriched["raw"] = raw
    enriched["_wallstreetcn_enriched"] = True
    return enriched
