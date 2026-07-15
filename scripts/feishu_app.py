"""Feishu application-bot sender for feedback-enabled market cards."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from feishu_image import tenant_access_token


MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages"


@dataclass(frozen=True)
class FeishuAppResponse:
    ok: bool
    code: int | None
    message: str
    message_id: str
    body: str


def feedback_enabled() -> bool:
    return os.getenv("FEISHU_FEEDBACK_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def feedback_chat_id() -> str:
    return os.getenv("FEISHU_FEEDBACK_CHAT_ID", "").strip()


def configured() -> bool:
    return bool(
        feedback_enabled()
        and os.getenv("FEISHU_APP_ID", "").strip()
        and os.getenv("FEISHU_APP_SECRET", "").strip()
        and feedback_chat_id()
        and os.getenv("FEISHU_FEEDBACK_TOKEN_SECRET", "").strip()
        and os.getenv("FEISHU_FEEDBACK_ALLOWED_OPEN_IDS", "").strip()
    )


def send_interactive_card(card: dict[str, Any], *, chat_id: str | None = None) -> FeishuAppResponse:
    target_chat = (chat_id if chat_id is not None else feedback_chat_id()).strip()
    if not target_chat:
        return FeishuAppResponse(False, None, "FEISHU_FEEDBACK_CHAT_ID 未配置", "", "")
    token = tenant_access_token()
    if not token:
        return FeishuAppResponse(False, None, "无法获取 tenant_access_token", "", "")
    query = urllib.parse.urlencode({"receive_id_type": "chat_id"})
    payload = {
        "receive_id": target_chat,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False, separators=(",", ":")),
    }
    request = urllib.request.Request(
        f"{MESSAGE_URL}?{query}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "surveil-feishu-feedback/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return FeishuAppResponse(False, exc.code, f"HTTP {exc.code}", "", body)
    except urllib.error.URLError as exc:
        return FeishuAppResponse(False, None, f"网络请求失败：{exc}", "", "")
    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        return FeishuAppResponse(False, None, "飞书返回非 JSON 响应", "", body)
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    return FeishuAppResponse(
        ok=result.get("code") == 0,
        code=result.get("code"),
        message=str(result.get("msg") or result.get("message") or ""),
        message_id=str(data.get("message_id") or ""),
        body=body,
    )
