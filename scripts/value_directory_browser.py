"""Controlled browser access for ValueList investment-bank research pages.

This module intentionally does not expose cookie import/export helpers. The
server keeps a dedicated browser profile and the user logs in there manually.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from time_utils import parse_datetime_to_utc_iso


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "value_directory_ib_stocks"
LIST_URL = "https://www.valuelist.cn/ib-research/global-investment-banks-stocks"
SOURCE_MODULE = "价值目录 / 国际投行-个股"
CN_TZ = timezone(timedelta(hours=8))

WAF_PATTERNS = (
    "人机验证",
    "人机识别",
    "宝塔防火墙",
    "防御系统",
    "checking your access",
)
LOGIN_PATTERNS = (
    "登录",
    "注册",
    "login",
    "sign in",
)


class ValueDirectoryError(RuntimeError):
    """Base exception for ValueList collection failures."""


class BrowserNotConfigured(ValueDirectoryError):
    """Raised when Chromium/Playwright is unavailable."""


class AccessBlocked(ValueDirectoryError):
    """Raised when the page is blocked by WAF, login, or an empty DOM."""


@dataclass(frozen=True)
class BrowserConfig:
    profile_dir: Path
    executable_path: str | None
    headless: bool
    timeout_ms: int


def private_profile_dir() -> Path:
    raw = os.getenv("VALUE_DIRECTORY_PROFILE_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return ROOT / "data" / "browser-profiles" / "valuelist"


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def chromium_candidates() -> list[str]:
    configured = os.getenv("VALUE_DIRECTORY_CHROMIUM_PATH", "").strip()
    candidates = [
        configured,
        shutil.which("google-chrome-stable") or "",
        shutil.which("google-chrome") or "",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    return [candidate for candidate in candidates if candidate]


def chromium_executable() -> str | None:
    for candidate in chromium_candidates():
        path = Path(candidate)
        if path.exists() and os.access(path, os.X_OK):
            return str(path)
    return None


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def browser_config() -> BrowserConfig:
    profile = private_profile_dir()
    ensure_private_dir(profile)
    raw_timeout = os.getenv("VALUE_DIRECTORY_BROWSER_TIMEOUT_MS", "").strip()
    try:
        timeout_ms = max(5_000, min(int(raw_timeout), 120_000)) if raw_timeout else 45_000
    except ValueError:
        timeout_ms = 45_000
    return BrowserConfig(
        profile_dir=profile,
        executable_path=chromium_executable(),
        headless=env_bool("VALUE_DIRECTORY_HEADLESS", False),
        timeout_ms=timeout_ms,
    )


def browser_args() -> list[str]:
    return [
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        "--disable-background-networking",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--window-size=1280,900",
    ]


def launch_kwargs(config: BrowserConfig, *, headless: bool | None = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "user_data_dir": str(config.profile_dir),
        "headless": config.headless if headless is None else headless,
        "args": browser_args(),
        "viewport": {"width": 1280, "height": 900},
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
    }
    if config.executable_path:
        kwargs["executable_path"] = config.executable_path
    return kwargs


def page_id_from_url(url: str) -> str:
    match = re.search(r"/(\d+)\.html(?:$|[?#])", str(url or ""))
    return match.group(1) if match else str(url or "")


def normalize_date(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        dt = datetime.fromisoformat(raw).replace(tzinfo=CN_TZ)
        return dt.astimezone(timezone.utc).isoformat()
    return parse_datetime_to_utc_iso(raw)


def normalize_entry(raw: dict[str, Any]) -> dict[str, Any] | None:
    title = " ".join(str(raw.get("title") or "").split())
    url = str(raw.get("url") or "").strip()
    if not title or not url or not url.startswith("http"):
        return None
    item_id = page_id_from_url(url) or title
    published_at = normalize_date(raw.get("published") or raw.get("published_at"))
    summary = title
    return {
        "id": item_id,
        "url": url,
        "title": title,
        "summary": summary,
        "content": summary,
        "full_text": summary,
        "published_at": published_at,
        "source_module": SOURCE_MODULE,
        "source_display": SOURCE_MODULE,
        "body_source": "价值目录列表页",
        "categories": ["国际投行-个股"],
        "raw": {
            "source": SOURCE_ID,
            "source_page": LIST_URL,
            "raw_published": str(raw.get("published") or raw.get("published_at") or ""),
        },
    }


def dedupe_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for raw in entries:
        item = normalize_entry(raw)
        if not item:
            continue
        key = str(item["id"])
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def classify_page_state(text: str, *, article_count: int, url: str = "") -> str:
    haystack = " ".join(str(text or "").split()).lower()
    if any(pattern.lower() in haystack for pattern in WAF_PATTERNS):
        return "waf"
    if "/login" in str(url).lower() or any(pattern.lower() in haystack for pattern in LOGIN_PATTERNS) and article_count == 0:
        return "login"
    if article_count <= 0:
        return "empty"
    return "ok"


def collect_entries(limit: int = 30, url: str = LIST_URL) -> list[dict[str, Any]]:
    """Read visible list-page cards with a persistent server browser profile."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001 - provide actionable setup message
        raise BrowserNotConfigured("缺少 Python Playwright 依赖。请先安装 requirements.txt。") from exc

    config = browser_config()
    safe_limit = max(1, min(int(limit), 100))
    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(**launch_kwargs(config))
        except Exception as exc:  # noqa: BLE001 - setup failures need a clear operator message
            raise BrowserNotConfigured(
                "浏览器启动失败。请安装系统 Chrome/Chromium，或运行 `python -m playwright install chromium`。"
            ) from exc
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=config.timeout_ms)
            page.wait_for_timeout(int(os.getenv("VALUE_DIRECTORY_WAF_SETTLE_MS", "6000") or "6000"))
            payload = page.evaluate(
                """(limit) => {
                    const text = document.body?.innerText || "";
                    const articles = Array.from(document.querySelectorAll("article")).slice(0, limit);
                    const entries = articles.map(article => {
                        const link = article.querySelector("h2 a") || article.querySelector("a[href]");
                        const time = article.querySelector("time");
                        return {
                            title: (link?.textContent || link?.querySelector("img")?.alt || "").trim(),
                            url: link?.href || "",
                            published: (time?.textContent || "").trim()
                        };
                    });
                    return {
                        url: location.href,
                        title: document.title || "",
                        bodySample: text.slice(0, 1200),
                        articleCount: articles.length,
                        entries
                    };
                }""",
                safe_limit,
            )
        finally:
            context.close()

    state = classify_page_state(
        f"{payload.get('title', '')}\n{payload.get('bodySample', '')}",
        article_count=int(payload.get("articleCount") or 0),
        url=str(payload.get("url") or ""),
    )
    if state != "ok":
        raise AccessBlocked(f"价值目录列表页不可用：state={state}")
    entries = dedupe_entries(list(payload.get("entries") or []))
    if not entries:
        raise AccessBlocked("价值目录列表页未解析到研报条目。")
    return entries
