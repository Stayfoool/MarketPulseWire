#!/usr/bin/env python3
"""Poll X's visible Following timeline through a private Chromium profile."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from collector_runtime import load_source_state, save_source_state
from db_utils import connect_sqlite
from env_utils import load_env
from market_flow import normalize_market_item, process_market_item
from market_store import processing_failure_status, source_item_id, source_item_review_snapshot
from production_admission import persist_production_admission_context, production_admission_context
from source_health import record_source_failure, record_source_success
from source_profiles import source_profile_enabled
from value_directory_browser import (
    BrowserConfig,
    BrowserLaunchFailed,
    BrowserNotConfigured,
    BrowserShutdownTimeout,
    close_browser_context,
    ensure_private_dir,
    launch_browser_context,
)


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
DB_PATH = ROOT / "data" / "surveil.sqlite3"
SOURCE_PROFILE_ID = "x_serenity"
SOURCE = "x_serenity"
HEALTH_MONITOR = "x_browser"
STATE_PREFIX = "x_browser"
HOME_URL = "https://x.com/home"
FOLLOWING_LABELS = ("Following", "正在关注", "关注中")
LOGIN_MARKERS = ("Log in", "登录", "Sign in", "注册")
CHALLENGE_MARKERS = ("人机验证", "验证你是人类", "checking your browser", "unusual activity")
DEFAULT_MAX_SCROLLS = 5
DEFAULT_MAX_POSTS = 100
DEFAULT_TIMEOUT_MS = 45_000
DEFAULT_RUN_TIMEOUT_SECONDS = 90
REPORT_PATH = ROOT / "reports" / "x-browser-latest.json"


class XBrowserError(RuntimeError):
    """Base error for the browser collection boundary."""


class XBrowserAccessBlocked(XBrowserError):
    """Raised when X shows login, challenge, or an unusable page."""


class XBrowserParseError(XBrowserError):
    """Raised when the visible page has no usable tweet identity."""


@dataclass(frozen=True)
class XBrowserConfig:
    profile_dir: Path
    executable_path: str | None
    headless: bool
    timeout_ms: int
    run_timeout_seconds: int
    max_scrolls: int
    max_posts: int

    def browser_config(self) -> BrowserConfig:
        return BrowserConfig(
            profile_dir=self.profile_dir,
            executable_path=self.executable_path,
            headless=self.headless,
            timeout_ms=self.timeout_ms,
        )


@dataclass
class XBrowserReport:
    run_started_at: str
    run_finished_at: str = ""
    status: str = "ok"
    raw_items: int = 0
    baseline_items: int = 0
    new_items: int = 0
    reviewed_items: int = 0
    pushed_items: int = 0
    excluded_items: int = 0
    processing_errors: int = 0
    empty_items: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_started_at": self.run_started_at,
            "run_finished_at": self.run_finished_at,
            "status": self.status,
            "raw_items": self.raw_items,
            "baseline_items": self.baseline_items,
            "new_items": self.new_items,
            "reviewed_items": self.reviewed_items,
            "pushed_items": self.pushed_items,
            "excluded_items": self.excluded_items,
            "processing_errors": self.processing_errors,
            "empty_items": self.empty_items,
            "errors": list(self.errors)[:20],
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def chromium_executable() -> str | None:
    configured = os.getenv("X_BROWSER_CHROMIUM_PATH", "").strip()
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
    for candidate in candidates:
        if candidate and Path(candidate).exists() and os.access(candidate, os.X_OK):
            return candidate
    return None


def browser_config() -> XBrowserConfig:
    profile = Path(
        os.getenv("X_BROWSER_PROFILE_DIR", str(ROOT / "data" / "browser-profiles" / "x"))
    ).expanduser()
    ensure_private_dir(profile)
    return XBrowserConfig(
        profile_dir=profile,
        executable_path=chromium_executable(),
        headless=_env_bool("X_BROWSER_HEADLESS", True),
        timeout_ms=_env_int(
            "X_BROWSER_TIMEOUT_MS",
            DEFAULT_TIMEOUT_MS,
            minimum=5_000,
            maximum=120_000,
        ),
        run_timeout_seconds=_env_int(
            "X_BROWSER_RUN_TIMEOUT_SECONDS",
            DEFAULT_RUN_TIMEOUT_SECONDS,
            minimum=15,
            maximum=300,
        ),
        max_scrolls=_env_int(
            "X_BROWSER_MAX_SCROLLS",
            DEFAULT_MAX_SCROLLS,
            minimum=0,
            maximum=20,
        ),
        max_posts=_env_int(
            "X_BROWSER_MAX_POSTS",
            DEFAULT_MAX_POSTS,
            minimum=1,
            maximum=300,
        ),
    )


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\u200b", "").split()).strip()


def _status_id(url: str) -> str:
    match = re.search(r"/status/(\d+)", str(url or ""))
    return match.group(1) if match else ""


def _iso_datetime(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError:
        return raw


def normalize_tweet_record(record: dict[str, Any]) -> dict[str, Any] | None:
    url = str(record.get("url") or "").strip()
    tweet_id = str(record.get("id") or _status_id(url)).strip()
    text = _clean_text(record.get("text"))
    if not tweet_id or not url or not text:
        return None
    username = _clean_text(record.get("author_username")).lstrip("@")
    author_name = _clean_text(record.get("author_name"))
    published_at = _iso_datetime(record.get("published_at"))
    media = [
        {"type": str(item.get("type") or "image"), "url": str(item.get("url") or "")}
        for item in record.get("media", [])
        if isinstance(item, dict) and str(item.get("url") or "").strip()
    ]
    quoted = [_clean_text(value) for value in record.get("quoted_texts", []) if _clean_text(value)]
    full_text = text
    if quoted:
        full_text = f"{text}\n\n引用推文：{'；'.join(dict.fromkeys(quoted))}"
    display_author = f"@{username}" if username else (author_name or "X")
    return {
        "id": tweet_id,
        "url": url,
        "title": f"{display_author} 的 X 推文",
        "summary": full_text[:280],
        "content": full_text,
        "full_text": full_text,
        "published_at": published_at,
        "source_category": "x_serenity",
        "publisher_role": "x_author",
        "content_type": "x_tweet",
        "source_module": "X / 正在关注",
        "body_source": "X 可见页面",
        "raw": {
            "id": tweet_id,
            "author_username": username,
            "author_name": author_name,
            "media": media,
            "quoted_texts": quoted,
            "timeline": "following",
        },
    }


def _page_snapshot(page: Any) -> list[dict[str, Any]]:
    script = r"""
    () => Array.from(document.querySelectorAll('article[data-testid="tweet"]')).map(article => {
      const status = Array.from(article.querySelectorAll('a[href*="/status/"]'))
        .find(link => /\/status\/\d+/.test(link.getAttribute('href') || '') && link.querySelector('time'));
      const href = status ? new URL(status.getAttribute('href'), location.origin).href : '';
      const texts = Array.from(article.querySelectorAll('[data-testid="tweetText"]'))
        .map(node => (node.innerText || '').trim()).filter(Boolean);
      const user = article.querySelector('[data-testid="User-Name"]');
      const userText = user ? (user.innerText || '').trim() : '';
      const username = Array.from((user || article).querySelectorAll('a[href^="/"]'))
        .map(link => (link.getAttribute('href') || '').replace(/^\//, '').split('/')[0])
        .find(value => value && !value.includes('status')) || '';
      const social = article.querySelector('[data-testid="socialContext"]');
      const media = Array.from(article.querySelectorAll('img[src*="pbs.twimg.com/media"], video[poster]'))
        .map(node => node.getAttribute('src') || node.getAttribute('poster') || '')
        .filter(Boolean);
      return {
        id: href.match(/\/status\/(\d+)/)?.[1] || '',
        url: href,
        text: texts[0] || '',
        quoted_texts: texts.slice(1),
        author_username: username,
        author_name: userText.split('\n')[0] || '',
        published_at: status?.querySelector('time')?.getAttribute('datetime') || '',
        media: media.map(url => ({type: 'image', url})),
        social_context: social ? (social.innerText || '') : '',
      };
    });
    """
    rows = page.evaluate(script)
    return rows if isinstance(rows, list) else []


def _body_text(page: Any) -> str:
    try:
        return _clean_text(page.locator("body").inner_text())
    except Exception:
        return ""


def ensure_page_access(page: Any) -> None:
    url = str(getattr(page, "url", "") or "")
    body = _body_text(page)
    lowered = f"{url} {body}".casefold()
    if "/i/flow/login" in lowered or any(marker.casefold() in lowered for marker in LOGIN_MARKERS):
        raise XBrowserAccessBlocked("X 登录状态失效")
    if any(marker.casefold() in lowered for marker in CHALLENGE_MARKERS):
        raise XBrowserAccessBlocked("X 页面出现验证或访问挑战")


def select_following_tab(page: Any) -> None:
    for selector in ('[role="tab"]', 'a[role="tab"]'):
        tabs = page.locator(selector)
        try:
            count = tabs.count()
        except Exception:
            continue
        for index in range(count):
            tab = tabs.nth(index)
            try:
                label = _clean_text(tab.inner_text())
            except Exception:
                continue
            if any(candidate.casefold() in label.casefold() for candidate in FOLLOWING_LABELS):
                try:
                    tab.click()
                    page.wait_for_timeout(500)
                except Exception as exc:
                    raise XBrowserError(f"切换 X 正在关注时间线失败：{exc}") from exc
                return
    raise XBrowserParseError("X 页面未找到“正在关注”时间线标签")


def collect_visible_posts(
    page: Any,
    *,
    max_scrolls: int,
    max_posts: int,
    sleep_ms: int = 700,
    deadline: float | None = None,
) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for scroll_index in range(max_scrolls + 1):
        if deadline is not None and time.monotonic() >= deadline:
            raise XBrowserError("X 浏览器采集超过单轮总超时")
        for raw in _page_snapshot(page):
            item = normalize_tweet_record(raw)
            if item is None:
                continue
            social = _clean_text(raw.get("social_context")).casefold()
            if "promoted" in social or "推广" in social:
                continue
            if "reposted" in social or "转发" in social or "转帖" in social:
                continue
            seen.setdefault(str(item["id"]), item)
            if len(seen) >= max_posts:
                return list(seen.values())[:max_posts]
        if scroll_index >= max_scrolls:
            break
        try:
            page.mouse.wheel(0, 1400)
            page.wait_for_timeout(sleep_ms)
        except Exception:
            break
    return list(seen.values())[:max_posts]


def collect_following_posts(
    *,
    config: XBrowserConfig | None = None,
    playwright_factory: Callable[[], Any] | None = None,
) -> list[dict[str, Any]]:
    config = config or browser_config()
    deadline = time.monotonic() + config.run_timeout_seconds
    try:
        if playwright_factory is None:
            from playwright.sync_api import sync_playwright

            playwright_factory = sync_playwright
        with playwright_factory() as playwright:
            context = launch_browser_context(playwright, config.browser_config())
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(HOME_URL, wait_until="domcontentloaded", timeout=config.timeout_ms)
                ensure_page_access(page)
                select_following_tab(page)
                page.wait_for_timeout(800)
                posts = collect_visible_posts(
                    page,
                    max_scrolls=config.max_scrolls,
                    max_posts=config.max_posts,
                    deadline=deadline,
                )
                if not posts:
                    raise XBrowserParseError("X 正在关注时间线没有可解析的推文")
                return posts
            finally:
                close_browser_context(context, config.browser_config())
    except (BrowserNotConfigured, BrowserLaunchFailed, BrowserShutdownTimeout, XBrowserError):
        raise
    except Exception as exc:  # noqa: BLE001 - collector boundary reports bounded errors
        raise XBrowserError(f"X 浏览器采集失败：{type(exc).__name__}: {exc}") from exc


def _item_collection_class(db_path: Path, item: dict[str, Any]) -> str:
    normalized = normalize_market_item(SOURCE, item, source_profile_id=SOURCE_PROFILE_ID)
    with connect_sqlite(db_path) as conn:
        row = conn.execute(
            "SELECT collection_class FROM market_items WHERE source=? AND source_item_id=?",
            (normalized.source, source_item_id(normalized)),
        ).fetchone()
    return str(row[0] or "") if row else ""


def _baseline_done(conn: sqlite3.Connection) -> bool:
    state = load_source_state(conn, SOURCE, prefix=STATE_PREFIX)
    return bool(state.get("baseline_at"))


def _save_run_state(conn: sqlite3.Connection, *, baseline_at: str | None = None, last_seen_id: str = "") -> None:
    state = load_source_state(conn, SOURCE, prefix=STATE_PREFIX)
    state["last_run_at"] = utc_now()
    if baseline_at:
        state["baseline_at"] = baseline_at
    if last_seen_id:
        state["last_seen_id"] = last_seen_id
    save_source_state(conn, SOURCE, state, prefix=STATE_PREFIX)


def _process_new_item(item: dict[str, Any], *, db_path: Path, report: XBrowserReport) -> None:
    normalized = normalize_market_item(SOURCE, item, source_profile_id=SOURCE_PROFILE_ID)
    context = production_admission_context(normalized, db_path=db_path)
    context = persist_production_admission_context(normalized, context, db_path=db_path)
    if context.result.status != "admitted":
        report.excluded_items += 1
        return
    outcome = process_market_item(
        normalized,
        item,
        db_path=db_path,
        production_admission=context.result,
        production_portfolio=context.portfolio,
        market_item_id=context.market_item_id,
        market_review_id=context.market_review_id,
    )
    report.reviewed_items += 1
    if outcome.delivery_status == "sent":
        report.pushed_items += 1


def run_once(
    *,
    db_path: Path = DB_PATH,
    dry_run: bool = False,
    posts: list[dict[str, Any]] | None = None,
) -> XBrowserReport:
    started = utc_now()
    report = XBrowserReport(started)
    if not source_profile_enabled(SOURCE_PROFILE_ID):
        report.status = "disabled"
        report.run_finished_at = utc_now()
        return report
    if posts is None:
        posts = collect_following_posts()
    report.raw_items = len(posts)
    if not posts:
        raise XBrowserParseError("X 正在关注时间线返回空内容")
    if dry_run:
        report.new_items = len(posts)
        report.run_finished_at = utc_now()
        return report

    with connect_sqlite(db_path) as conn:
        baseline_done = _baseline_done(conn)
    if not baseline_done:
        for item in posts:
            normalized = normalize_market_item(SOURCE, item, source_profile_id=SOURCE_PROFILE_ID)
            process_market_item(
                normalized,
                item,
                db_path=db_path,
                baseline_only=True,
            )
            report.baseline_items += 1
        with connect_sqlite(db_path) as conn:
            _save_run_state(
                conn,
                baseline_at=utc_now(),
                last_seen_id=str(posts[0].get("id") or ""),
            )
        report.run_finished_at = utc_now()
        return report

    for item in posts:
        if _item_collection_class(db_path, item) == "baseline":
            continue
        normalized = normalize_market_item(SOURCE, item, source_profile_id=SOURCE_PROFILE_ID)
        snapshot = source_item_review_snapshot(
            normalized.source,
            source_item_id(normalized),
            db_path=db_path,
        )
        if snapshot and snapshot["review_status"] in {"succeeded", "excluded", "not_applicable", "insufficient_evidence"}:
            continue
        report.new_items += 1
        try:
            _process_new_item(item, db_path=db_path, report=report)
        except Exception as exc:  # noqa: BLE001 - keep later posts retryable
            report.processing_errors += 1
            status = processing_failure_status(exc)
            report.errors.append(
                f"{source_item_id(normalized)}: {status}: {type(exc).__name__}: {exc}"
            )
    with connect_sqlite(db_path) as conn:
        _save_run_state(conn, last_seen_id=str(posts[0].get("id") or ""))
    report.run_finished_at = utc_now()
    return report


def write_report(report: XBrowserReport, path: Path = REPORT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    load_env(ENV_PATH)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--strict-exit", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = run_once(dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 - one source boundary
        report = XBrowserReport(utc_now(), run_finished_at=utc_now(), errors=[f"{type(exc).__name__}: {exc}"])
        with connect_sqlite(DB_PATH) as conn:
            record_source_failure(conn, HEALTH_MONITOR, SOURCE, exc)
        print(f"X 浏览器采集失败：{exc}", file=sys.stderr, flush=True)
        if args.write_report:
            write_report(report)
        return 1
    if report.status != "disabled":
        with connect_sqlite(DB_PATH) as conn:
            record_source_success(conn, HEALTH_MONITOR, SOURCE)
    print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True), flush=True)
    if args.write_report:
        write_report(report)
    return 1 if args.strict_exit and (report.errors or report.processing_errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
