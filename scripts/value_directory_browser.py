"""Controlled browser access for ValueList investment-bank research pages.

This module intentionally does not expose cookie import/export helpers. The
server keeps a dedicated browser profile and the user logs in there manually.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from time_utils import parse_datetime_to_utc_iso


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "value_directory_ib_stocks"
LIST_URL = "https://www.valuelist.cn/ib-research/global-investment-banks-stocks"
SOURCE_MODULE = "价值目录 / 国际投行-个股"
INDUSTRY_MACRO_SOURCE_ID = "value_directory_ib_industry_macro"
INDUSTRY_MACRO_LIST_URL = "https://www.valuelist.cn/ib-research/global-investment-banks"
INDUSTRY_MACRO_SOURCE_MODULE = "价值目录 / 国际投行-行业宏观"
CN_TZ = timezone(timedelta(hours=8))
LIST_EMPTY_WAIT_MS = 15_000
BROWSER_CLOSE_WAIT_SECONDS = 5.0
BROWSER_DIAGNOSTIC_LIMIT = 800

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


class BrowserLaunchFailed(ValueDirectoryError):
    """Raised when an installed browser cannot acquire the private profile."""


class BrowserShutdownTimeout(ValueDirectoryError):
    """Raised when a browser still owns the private profile after close."""


class AccessBlocked(ValueDirectoryError):
    """Raised when the page is blocked by WAF, login, or an empty DOM."""


@dataclass(frozen=True)
class BrowserConfig:
    profile_dir: Path
    executable_path: str | None
    headless: bool
    timeout_ms: int


@dataclass(frozen=True)
class ValueDirectorySource:
    source_id: str
    module: str
    list_url: str
    categories: tuple[str, ...]


VALUE_DIRECTORY_SOURCES: dict[str, ValueDirectorySource] = {
    SOURCE_ID: ValueDirectorySource(
        source_id=SOURCE_ID,
        module=SOURCE_MODULE,
        list_url=LIST_URL,
        categories=("国际投行-个股",),
    ),
    INDUSTRY_MACRO_SOURCE_ID: ValueDirectorySource(
        source_id=INDUSTRY_MACRO_SOURCE_ID,
        module=INDUSTRY_MACRO_SOURCE_MODULE,
        list_url=INDUSTRY_MACRO_LIST_URL,
        categories=("国际投行-行业宏观",),
    ),
}


def source_config(source_id: str = SOURCE_ID) -> ValueDirectorySource:
    try:
        return VALUE_DIRECTORY_SOURCES[source_id]
    except KeyError as exc:
        allowed = ", ".join(sorted(VALUE_DIRECTORY_SOURCES))
        raise ValueDirectoryError(f"未知价值目录来源：{source_id}；允许值：{allowed}") from exc


def default_source_ids() -> list[str]:
    raw = os.getenv("VALUE_DIRECTORY_SOURCES", "").strip()
    if not raw:
        return list(VALUE_DIRECTORY_SOURCES)
    requested = [part.strip() for part in re.split(r"[,;\s]+", raw) if part.strip()]
    return [source_config(source_id).source_id for source_id in requested]


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
        if path.parts[:2] == ("/", "snap"):
            continue
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


def _symlink_target(path: Path) -> str:
    try:
        return os.readlink(path)
    except OSError:
        return ""


def _unix_socket_inode(socket_path: str, proc_root: Path) -> str:
    if not socket_path:
        return ""
    try:
        lines = (proc_root / "net" / "unix").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in lines[1:]:
        parts = line.split(maxsplit=7)
        if len(parts) == 8 and parts[7] == socket_path:
            return parts[6]
    return ""


def _socket_holder_pids(inode: str, proc_root: Path) -> list[int]:
    if not inode:
        return []
    expected = f"socket:[{inode}]"
    holders: list[int] = []
    try:
        pid_dirs = [path for path in proc_root.iterdir() if path.name.isdigit()]
    except OSError:
        return holders
    for pid_dir in pid_dirs:
        try:
            links = list((pid_dir / "fd").iterdir())
        except OSError:
            continue
        for link in links:
            try:
                if os.readlink(link) == expected:
                    holders.append(int(pid_dir.name))
                    break
            except OSError:
                continue
        if len(holders) >= 8:
            break
    return sorted(holders)


def profile_lock_state(
    profile_dir: Path,
    *,
    proc_root: Path = Path("/proc"),
    hostname: str | None = None,
) -> dict[str, Any]:
    hostname = hostname or socket.gethostname()
    lock_path = profile_dir / "SingletonLock"
    socket_link = profile_dir / "SingletonSocket"
    lock_exists = lock_path.exists() or lock_path.is_symlink()
    lock_target = _symlink_target(lock_path)
    lock_host = ""
    lock_pid: int | None = None
    if lock_target and "-" in lock_target:
        candidate_host, candidate_pid = lock_target.rsplit("-", 1)
        if candidate_pid.isdigit():
            lock_host = candidate_host
            lock_pid = int(candidate_pid)
    same_host = bool(lock_host) and lock_host == hostname
    owner_pid_alive: bool | None = None
    if same_host and lock_pid is not None:
        owner_pid_alive = (proc_root / str(lock_pid)).exists()

    socket_target = _symlink_target(socket_link)
    if socket_target and not os.path.isabs(socket_target):
        socket_target = str((socket_link.parent / socket_target).resolve(strict=False))
    socket_exists = bool(socket_target) and Path(socket_target).exists()
    socket_inode = _unix_socket_inode(socket_target, proc_root)
    holders = _socket_holder_pids(socket_inode, proc_root)
    return {
        "lock_exists": lock_exists,
        "lock_target": lock_target[:160],
        "lock_same_host": same_host,
        "lock_pid": lock_pid,
        "lock_pid_alive": owner_pid_alive,
        "socket_exists": socket_exists,
        "socket_registered": bool(socket_inode),
        "socket_holder_pids": holders,
    }


def profile_lock_active(state: dict[str, Any]) -> bool:
    return bool(
        state.get("lock_pid_alive") is True
        or state.get("socket_registered")
        or state.get("socket_holder_pids")
    )


def wait_for_profile_release(
    profile_dir: Path,
    *,
    timeout_seconds: float = BROWSER_CLOSE_WAIT_SECONDS,
    poll_seconds: float = 0.1,
    state_reader: Callable[[Path], dict[str, Any]] = profile_lock_state,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[bool, dict[str, Any]]:
    deadline = monotonic() + max(0.0, timeout_seconds)
    state = state_reader(profile_dir)
    while profile_lock_active(state) and monotonic() < deadline:
        sleeper(poll_seconds)
        state = state_reader(profile_dir)
    return not profile_lock_active(state), state


def compact_browser_error(exc: Exception) -> str:
    return " ".join(str(exc).split())[:BROWSER_DIAGNOSTIC_LIMIT]


def browser_diagnostic(config: BrowserConfig) -> dict[str, Any]:
    executable = config.executable_path or "playwright-managed"
    try:
        lock_state = profile_lock_state(config.profile_dir)
    except Exception as exc:  # noqa: BLE001 - diagnostics must not hide the browser error.
        lock_state = {"diagnostic_error": f"{type(exc).__name__}: {compact_browser_error(exc)}"}
    return {
        "exception_context": "persistent_context",
        "executable": executable[:240],
        "executable_exists": Path(executable).exists() if config.executable_path else None,
        "profile_lock": lock_state,
    }


def launch_browser_context(playwright: Any, config: BrowserConfig) -> Any:
    try:
        return playwright.chromium.launch_persistent_context(**launch_kwargs(config))
    except Exception as exc:  # noqa: BLE001 - preserve bounded native browser diagnostics.
        detail = compact_browser_error(exc)
        diagnostic = json.dumps(browser_diagnostic(config), ensure_ascii=False, separators=(",", ":"))
        raise BrowserLaunchFailed(
            f"浏览器启动失败：{type(exc).__name__}: {detail}; diagnostic={diagnostic}"
        ) from exc


def close_browser_context(context: Any, config: BrowserConfig) -> None:
    try:
        context.close()
    except Exception as exc:  # noqa: BLE001 - retain bounded close diagnostics.
        diagnostic = json.dumps(browser_diagnostic(config), ensure_ascii=False, separators=(",", ":"))
        raise BrowserShutdownTimeout(
            f"浏览器关闭失败：{type(exc).__name__}: {compact_browser_error(exc)}; diagnostic={diagnostic}"
        ) from exc
    released, state = wait_for_profile_release(config.profile_dir)
    if released:
        if state.get("lock_exists"):
            diagnostic = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
            print(f"ValueList browser closed with stale recoverable lock: {diagnostic}", flush=True)
        return
    diagnostic = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    raise BrowserShutdownTimeout(f"浏览器关闭后仍持有 profile 超过 {BROWSER_CLOSE_WAIT_SECONDS:.1f}s：{diagnostic}")


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


def normalize_entry(raw: dict[str, Any], source: ValueDirectorySource | None = None) -> dict[str, Any] | None:
    source = source or source_config()
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
        "source_module": source.module,
        "source_display": source.module,
        "body_source": "价值目录列表页",
        "categories": list(source.categories),
        "raw": {
            "source": source.source_id,
            "source_page": source.list_url,
            "raw_published": str(raw.get("published") or raw.get("published_at") or ""),
        },
    }


def dedupe_entries(entries: list[dict[str, Any]], source: ValueDirectorySource | None = None) -> list[dict[str, Any]]:
    source = source or source_config()
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for raw in entries:
        item = normalize_entry(raw, source)
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


def evaluate_list_payload_with_empty_wait(
    page: Any,
    safe_limit: int,
    timeout_ms: int,
    *,
    timeout_error: type[Exception],
) -> dict[str, Any]:
    payload = evaluate_list_payload(page, safe_limit, timeout_ms)
    state = classify_page_state(
        f"{payload.get('title', '')}\n{payload.get('bodySample', '')}",
        article_count=int(payload.get("articleCount") or 0),
        url=str(payload.get("url") or ""),
    )
    if state != "empty":
        return payload
    try:
        page.wait_for_selector("article", timeout=min(LIST_EMPTY_WAIT_MS, timeout_ms))
    except timeout_error:
        pass
    return evaluate_list_payload(page, safe_limit, timeout_ms)


def collect_entries(limit: int = 30, url: str | None = None, source_id: str = SOURCE_ID) -> list[dict[str, Any]]:
    """Read visible list-page cards with a persistent server browser profile."""
    source = source_config(source_id)
    target_url = url or source.list_url
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001 - provide actionable setup message
        raise BrowserNotConfigured("缺少 Python Playwright 依赖。请先安装 requirements.txt。") from exc

    config = browser_config()
    safe_limit = max(1, min(int(limit), 100))
    with sync_playwright() as playwright:
        context = launch_browser_context(playwright, config)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(target_url, wait_until="domcontentloaded", timeout=config.timeout_ms)
            page.wait_for_timeout(int(os.getenv("VALUE_DIRECTORY_WAF_SETTLE_MS", "6000") or "6000"))
            payload = evaluate_list_payload_with_empty_wait(
                page,
                safe_limit,
                config.timeout_ms,
                timeout_error=PlaywrightTimeoutError,
            )
        finally:
            close_browser_context(context, config)

    state = classify_page_state(
        f"{payload.get('title', '')}\n{payload.get('bodySample', '')}",
        article_count=int(payload.get("articleCount") or 0),
        url=str(payload.get("url") or ""),
    )
    if state != "ok":
        raise AccessBlocked(f"价值目录列表页不可用：state={state}")
    entries = dedupe_entries(list(payload.get("entries") or []), source)
    if not entries:
        raise AccessBlocked("价值目录列表页未解析到研报条目。")
    return entries


def collect_entries_for_source(source_id: str, limit: int = 30) -> list[dict[str, Any]]:
    return collect_entries(limit=limit, source_id=source_id)


def classify_detail_page_state(text: str, *, preview_count: int, url: str = "") -> str:
    haystack = " ".join(str(text or "").split()).lower()
    if any(pattern.lower() in haystack for pattern in WAF_PATTERNS):
        return "waf"
    if "/login" in str(url).lower() or any(pattern.lower() in haystack for pattern in LOGIN_PATTERNS) and preview_count == 0:
        return "login"
    if preview_count <= 0:
        return "empty"
    return "ok"


def collect_preview(url: str) -> dict[str, Any]:
    """Read visible first-page preview assets from a ValueList detail page.

    This reads only the page and image preview already visible in the user's
    browser session. It does not click purchase/download controls and does not
    retrieve report PDFs.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        raise BrowserNotConfigured("缺少 Python Playwright 依赖。请先安装 requirements.txt。") from exc

    config = browser_config()
    with sync_playwright() as playwright:
        context = launch_browser_context(playwright, config)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=config.timeout_ms)
            page.wait_for_timeout(int(os.getenv("VALUE_DIRECTORY_WAF_SETTLE_MS", "6000") or "6000"))
            payload = evaluate_detail_payload(page)
        finally:
            close_browser_context(context, config)

    state = classify_detail_page_state(
        f"{payload.get('title', '')}\n{payload.get('articleText', '')}",
        preview_count=len(payload.get("previewImages") or []) + (1 if payload.get("articleText") else 0),
        url=str(payload.get("url") or ""),
    )
    payload["state"] = state
    if state in {"waf", "login"}:
        raise AccessBlocked(f"价值目录详情页不可用：state={state}")
    return payload


def evaluate_detail_payload(page: Any) -> dict[str, Any]:
    script = """() => {
        const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            return rect.width > 20 && rect.height > 20 && style.display !== "none" && style.visibility !== "hidden";
        };
        const rectOf = (el) => {
            const r = el.getBoundingClientRect();
            return {x: r.x, y: r.y, width: r.width, height: r.height};
        };
        const article = document.querySelector("main article") || document.querySelector("article") || document.body;
        const h1 = article?.querySelector("h1")?.innerText || document.querySelector("h1")?.innerText || "";
        const articleText = (article?.innerText || "").trim();
        const images = Array.from(article?.querySelectorAll("img") || [])
            .filter(visible)
            .map((img) => ({
                src: img.currentSrc || img.src || "",
                alt: img.alt || "",
                naturalWidth: img.naturalWidth || 0,
                naturalHeight: img.naturalHeight || 0,
                rect: rectOf(img)
            }))
            .filter((img) => {
                const src = String(img.src || "");
                if (!src) return false;
                if (src.includes("avatar") || src.includes("logo") || src.includes("thumb-ing")) return false;
                if (img.naturalWidth < 500 || img.naturalHeight < 350) return false;
                if (img.rect.width < 350 || img.rect.height < 250) return false;
                return true;
            });
        return {
            url: location.href,
            title: document.title || h1,
            heading: h1,
            bodySample: (document.body?.innerText || "").slice(0, 1500),
            articleText: articleText.slice(0, 3000),
            previewImages: images.slice(0, 3),
            hasPurchaseButton: /立即购买|购买|积分/.test(document.body?.innerText || ""),
        };
    }"""
    return dict(page.evaluate(script))


def evaluate_list_payload(page: Any, safe_limit: int, timeout_ms: int) -> dict[str, Any]:
    script = """(limit) => {
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
    }"""
    last_exc: Exception | None = None
    for _ in range(3):
        try:
            return dict(page.evaluate(script, safe_limit))
        except Exception as exc:  # noqa: BLE001 - page may still be navigating after WAF/SPA redirects
            last_exc = exc
            if "Execution context was destroyed" not in str(exc):
                raise
            page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(1500)
    assert last_exc is not None
    raise last_exc
