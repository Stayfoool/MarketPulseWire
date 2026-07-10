#!/usr/bin/env python3
"""Open a headed ValueList browser session for first-time server login."""

from __future__ import annotations

import argparse
import time

from env_utils import load_env
from value_directory_browser import LIST_URL, browser_config, launch_kwargs


def open_login_session(url: str = LIST_URL) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        raise SystemExit("缺少 Python Playwright 依赖。请先部署并安装 requirements.txt。") from exc

    config = browser_config()
    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(**launch_kwargs(config, headless=False))
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(
                "浏览器启动失败。请安装系统 Chrome/Chromium，或运行 `python -m playwright install chromium`。"
            ) from exc
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=config.timeout_ms)
        print("价值目录登录浏览器已打开。请在 VNC 窗口中手动登录；完成后关闭浏览器或按 Ctrl-C。", flush=True)
        try:
            while True:
                time.sleep(2)
                if not context.pages:
                    break
        except KeyboardInterrupt:
            pass
        finally:
            context.close()


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="Open ValueList login browser on the server.")
    parser.add_argument("--url", default=LIST_URL)
    args = parser.parse_args()
    open_login_session(args.url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
