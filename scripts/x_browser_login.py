#!/usr/bin/env python3
"""Open Chromium directly for a one-time X login on the server display."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
from typing import Any, Callable

from env_utils import load_env
from x_browser_monitor import HOME_URL, browser_config
from value_directory_browser import profile_lock_active, profile_lock_state, wait_for_profile_release


def resolve_chromium_executable(
    configured: str | None,
    *,
    playwright_factory: Callable[[], Any] | None = None,
) -> Path:
    if configured:
        executable = Path(configured)
    else:
        try:
            if playwright_factory is None:
                from playwright.sync_api import sync_playwright

                playwright_factory = sync_playwright
            with playwright_factory() as playwright:
                executable = Path(playwright.chromium.executable_path)
        except Exception as exc:  # noqa: BLE001 - operator helper needs a concise failure.
            raise SystemExit("无法定位 Chromium。请先安装项目的 Playwright 浏览器依赖。") from exc
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise SystemExit(f"Chromium 不存在或不可执行：{executable}")
    return executable


def direct_chromium_command(executable: Path, profile_dir: Path, url: str) -> list[str]:
    return [
        str(executable),
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        "--window-size=1280,900",
        "--lang=zh-CN",
        "--ozone-platform=x11",
        url,
    ]


def _stop_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def _interrupt_login(_signum: int, _frame: Any) -> None:
    raise KeyboardInterrupt


def open_login_session(url: str = HOME_URL) -> None:
    config = browser_config()
    lock_state = profile_lock_state(config.profile_dir)
    if profile_lock_active(lock_state):
        raise SystemExit("X 浏览器 profile 正在使用中。请先关闭旧的登录或采集浏览器。")

    executable = resolve_chromium_executable(config.executable_path)
    command = direct_chromium_command(executable, config.profile_dir, url)
    process = subprocess.Popen(command, start_new_session=True)

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _interrupt_login)
    return_code: int | None = None
    try:
        print(
            "X 登录 Chromium 已直接启动。请在 VNC 窗口中手动登录；完成后关闭浏览器或按 Ctrl-C。",
            flush=True,
        )
        return_code = process.wait()
    except KeyboardInterrupt:
        _stop_process(process)
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)

    released, state = wait_for_profile_release(config.profile_dir)
    if not released:
        raise SystemExit(f"Chromium 退出后仍持有 X 浏览器 profile：{state}")
    if return_code not in {None, 0}:
        raise SystemExit(f"Chromium 异常退出，状态码：{return_code}")


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=HOME_URL)
    args = parser.parse_args()
    open_login_session(args.url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
