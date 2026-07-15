#!/usr/bin/env python3
"""Receive Feishu card actions through the official long connection."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from env_utils import load_env
from feishu_app import feedback_listener_enabled
from market_feedback import FeedbackError, feedback_card_for_callback, handle_feedback_callback
from market_db import DEFAULT_DB_PATH, init_db


ROOT = Path(__file__).resolve().parents[1]


def callback_response(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        result = handle_feedback_callback(payload)
        response: dict[str, Any] = {"toast": result["toast"]}
        state = result.get("card_state") if isinstance(result.get("card_state"), dict) else {}
        identity = state.get("identity")
        try:
            card = feedback_card_for_callback(
                identity,
                str(state.get("label") or ""),
                state.get("reason_tags") or [],
            ) if identity is not None else None
        except Exception as exc:  # noqa: BLE001 - feedback is already durable; state projection is best effort
            print(f"飞书反馈卡片状态更新失败：{exc}", flush=True)
            card = None
        if card is not None:
            response["card"] = {"type": "raw", "data": card}
        return response
    except FeedbackError as exc:
        return {"toast": {"type": "warning", "content": str(exc)}}
    except Exception as exc:  # noqa: BLE001 - acknowledge safely within Feishu's three-second limit
        print(f"飞书反馈处理失败：{exc}", flush=True)
        return {"toast": {"type": "error", "content": "反馈记录失败，请稍后重试"}}


def main() -> int:
    load_env(ROOT / ".env")
    if not feedback_listener_enabled():
        raise SystemExit("FEISHU_FEEDBACK_LISTENER_ENABLED / FEISHU_FEEDBACK_ENABLED 未启用")
    app_id = os.getenv("FEISHU_APP_ID", "").strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        raise SystemExit("FEISHU_APP_ID / FEISHU_APP_SECRET 未配置")
    if not os.getenv("FEISHU_FEEDBACK_CHAT_ID", "").strip():
        raise SystemExit("FEISHU_FEEDBACK_CHAT_ID 未配置")
    if not os.getenv("FEISHU_FEEDBACK_TOKEN_SECRET", "").strip():
        raise SystemExit("FEISHU_FEEDBACK_TOKEN_SECRET 未配置")
    if not os.getenv("FEISHU_FEEDBACK_ALLOWED_OPEN_IDS", "").strip():
        raise SystemExit("FEISHU_FEEDBACK_ALLOWED_OPEN_IDS 未配置")
    init_db(DEFAULT_DB_PATH).close()

    try:
        import lark_oapi as lark
        from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse
    except ImportError as exc:
        raise SystemExit("缺少官方 lark-oapi 依赖") from exc

    def on_card_action(data: Any) -> Any:
        payload = json.loads(lark.JSON.marshal(data))
        return P2CardActionTriggerResponse(callback_response(payload))

    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_card_action_trigger(on_card_action)
        .build()
    )
    client = lark.ws.Client(
        app_id,
        app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )
    print("启动飞书反馈长连接", flush=True)
    client.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
