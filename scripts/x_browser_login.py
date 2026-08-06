#!/usr/bin/env python3
"""Open a headed X browser session for a one-time server login."""

from __future__ import annotations

import argparse
import time

from env_utils import load_env
from x_browser_monitor import HOME_URL, browser_config
from value_directory_browser import close_browser_context, launch_browser_context


def open_login_session(url: str = HOME_URL) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        raise SystemExit("缺少 Python Playwright 依赖。请先部署并安装 requirements.txt。") from exc

    config = browser_config()
    runtime_config = config.browser_config()
    with sync_playwright() as playwright:
        context = launch_browser_context(playwright, runtime_config)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=config.timeout_ms)
            print("X 登录浏览器已打开。请在 VNC 窗口中手动登录；完成后关闭浏览器或按 Ctrl-C。", flush=True)
            while context.pages:
                time.sleep(2)
        except KeyboardInterrupt:
            pass
        finally:
            close_browser_context(context, runtime_config)


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=HOME_URL)
    args = parser.parse_args()
    open_login_session(args.url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
