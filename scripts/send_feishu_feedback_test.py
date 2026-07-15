#!/usr/bin/env python3
"""Send one explicit feedback test card without enabling natural delivery switching."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from env_utils import load_env
from feishu_app import configured, feedback_listener_enabled, send_interactive_card
from market_feedback import FeedbackIdentity, feedback_test_card


ROOT = Path(__file__).resolve().parents[1]


def build_test_card(item_id: str) -> dict:
    return feedback_test_card(FeedbackIdentity("test", "feishu_feedback", item_id))


def main() -> int:
    parser = argparse.ArgumentParser(description="发送一次明确确认的飞书反馈测试卡")
    parser.add_argument("--confirm", action="store_true", help="确认向已配置群发送测试卡")
    args = parser.parse_args()
    if not args.confirm:
        raise SystemExit("需要 --confirm 才会发送测试卡")
    load_env(ROOT / ".env")
    if not feedback_listener_enabled():
        raise SystemExit("需要先启用 FEISHU_FEEDBACK_LISTENER_ENABLED")
    if not configured():
        raise SystemExit("飞书反馈应用配置不完整")
    item_id = f"test-{int(time.time() * 1_000_000)}"
    response = send_interactive_card(build_test_card(item_id))
    if not response.ok:
        raise SystemExit(f"测试卡发送失败：{response.code or ''} {response.message}")
    print(f"测试卡已发送：item_id={item_id} message_id={response.message_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
