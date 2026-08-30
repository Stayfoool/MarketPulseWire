"""Safe Web settings access for the Surveil runtime .env file."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llm_provider_config import (
    DEEPSEEK_PROVIDER,
    ZHIPU_GLM_BASE_URL,
    ZHIPU_GLM_MODEL,
    ZHIPU_GLM_PROVIDER,
    canonical_llm_provider,
    selected_llm_provider,
)


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


@dataclass(frozen=True)
class SettingField:
    key: str
    label: str
    group: str
    sensitive: bool = False
    help: str = ""
    placeholder: str = ""


SETTING_GROUPS: list[dict[str, Any]] = [
    {
        "id": "llm",
        "title": "大模型",
        "restart_hint": "点击当前模型即可切换；新浪财经快讯常驻服务会立即重启，其他定时采集任务下一轮读取新模型。",
        "fields": [
            SettingField("LLM_BASE_URL", "DeepSeek / 兼容模型 Base URL", "llm", placeholder="https://api.deepseek.com"),
            SettingField("LLM_MODEL", "DeepSeek / 兼容模型名称", "llm", placeholder="deepseek-chat"),
            SettingField("LLM_API_KEY", "DeepSeek / 兼容模型 API Key", "llm", sensitive=True, help="留空表示保留现有密钥。"),
            SettingField("LLM_GLM_API_KEY", "智谱 GLM 5.3 Flash API Key", "llm", sensitive=True, help="单独保存；留空表示保留现有密钥。"),
            SettingField("LLM_TIMEOUT_SECONDS", "超时秒数", "llm", placeholder="90"),
            SettingField("LLM_RETRY_COUNT", "重试次数", "llm", placeholder="2"),
            SettingField("LLM_THINKING_TYPE", "默认 thinking", "llm", placeholder="disabled"),
            SettingField("LLM_GATE_THINKING_TYPE", "门控 thinking", "llm", placeholder="enabled"),
            SettingField("ATTRIBUTED_RESEARCH_LLM_ENABLED", "媒体机构归因语义抽取", "llm", placeholder="1"),
            SettingField("ATTRIBUTED_RESEARCH_LLM_THINKING_TYPE", "媒体机构归因 thinking", "llm", placeholder="disabled"),
            SettingField("ATTRIBUTED_RESEARCH_LLM_MAX_OUTPUT_TOKENS", "媒体机构归因输出 tokens", "llm", placeholder="900"),
        ],
    },
    {
        "id": "value_directory",
        "title": "价值目录",
        "restart_hint": "保存后价值目录 timer 下一次运行会读取新配置；如需马上验证，可在任务健康页立即运行 surveil-value-directory.timer。",
        "fields": [
            SettingField("VALUE_DIRECTORY_SOURCES", "启用来源", "value_directory", placeholder="value_directory_ib_stocks,value_directory_ib_industry_macro"),
            SettingField("VALUE_DIRECTORY_PREVIEW_ENABLED", "启用第一页预览", "value_directory", placeholder="1"),
            SettingField("VALUE_DIRECTORY_PREVIEW_OCR_ENABLED", "启用第一页 OCR", "value_directory", placeholder="1"),
            SettingField("VALUE_DIRECTORY_PREVIEW_OCR_LANG", "OCR 语言", "value_directory", placeholder="ch"),
            SettingField("VALUE_DIRECTORY_PREVIEW_OCR_MIN_CHARS", "OCR 最少字数", "value_directory", placeholder="40"),
            SettingField("VALUE_DIRECTORY_PREVIEW_LLM_ENABLED", "启用 OCR 文本提取", "value_directory", placeholder="1"),
            SettingField("VALUE_DIRECTORY_PREVIEW_LLM_TIMEOUT_SECONDS", "第一页提取超时秒数", "value_directory", placeholder="45"),
            SettingField("VALUE_DIRECTORY_PREVIEW_LLM_RETRY_COUNT", "第一页提取重试次数", "value_directory", placeholder="1"),
            SettingField("VALUE_DIRECTORY_PREVIEW_VISION_FALLBACK_ENABLED", "启用视觉模型兜底", "value_directory", placeholder="0"),
            SettingField("VALUE_DIRECTORY_PUSH_ON_PREVIEW_FAILURE", "提取失败仍推送硬规则", "value_directory", placeholder="1"),
            SettingField("VALUE_DIRECTORY_RECHECK_UNPUSHED", "复核未推送旧条目", "value_directory", placeholder="0", help="默认关闭；pending/failed_retryable 条目仍会自动重试。"),
            SettingField("VALUE_DIRECTORY_RECHECK_UNPUSHED_LIMIT", "单次复核旧条数", "value_directory", placeholder="30"),
            SettingField("VALUE_DIRECTORY_BROWSER_TIMEOUT_MS", "浏览器超时毫秒", "value_directory", placeholder="45000"),
        ],
    },
    {
        "id": "x",
        "title": "X / Serenity",
        "restart_hint": "保存后建议重启 surveil-x-browser-collector.timer；首次登录需使用服务器操作员登录脚本。",
        "fields": [
            SettingField("X_BROWSER_HEADLESS", "无界面采集", "x", placeholder="1"),
            SettingField("X_BROWSER_TIMEOUT_MS", "页面超时毫秒", "x", placeholder="45000"),
            SettingField("X_BROWSER_RUN_TIMEOUT_SECONDS", "单轮总超时秒数", "x", placeholder="90"),
            SettingField("X_BROWSER_MAX_SCROLLS", "单轮滚动次数", "x", placeholder="5"),
            SettingField("X_BROWSER_MAX_POSTS", "单轮最多推文", "x", placeholder="100"),
        ],
    },
    {
        "id": "feishu",
        "title": "飞书",
        "restart_hint": "普通 webhook 配置下一条推送生效；先用仅监听模式验证回调，再开启卡片反馈切换自然市场推送。",
        "fields": [
            SettingField("FEISHU_WEBHOOK", "机器人 Webhook", "feishu", sensitive=True),
            SettingField("FEISHU_SECRET", "签名 Secret", "feishu", sensitive=True, help="机器人未开启签名校验时可留空。"),
            SettingField("FEISHU_APP_ID", "App ID", "feishu"),
            SettingField("FEISHU_APP_SECRET", "App Secret", "feishu", sensitive=True),
            SettingField("FEISHU_FEEDBACK_ENABLED", "启用卡片反馈", "feishu", placeholder="0"),
            SettingField("FEISHU_FEEDBACK_LISTENER_ENABLED", "仅监听反馈回调", "feishu", placeholder="0"),
            SettingField("FEISHU_FEEDBACK_CHAT_ID", "反馈测试/生产群 Chat ID", "feishu"),
            SettingField(
                "FEISHU_FEEDBACK_ALLOWED_OPEN_IDS",
                "允许反馈的 Open ID",
                "feishu",
                help="多个 Open ID 使用英文逗号分隔；空值时拒绝所有反馈。测试群首次识别身份时可短暂使用 *，随后立即改为实际 Open ID。",
            ),
            SettingField("FEISHU_FEEDBACK_TOKEN_SECRET", "反馈标识签名 Secret", "feishu", sensitive=True),
            SettingField("FEISHU_RETRY_COUNT", "重试次数", "feishu", placeholder="2"),
        ],
    },
    {
        "id": "network",
        "title": "网络 / RSS 抓取",
        "restart_hint": "保存后建议重启统一 collector，使代理、超时、并发和健康告警配置立即生效。",
        "fields": [
            SettingField("SURVEIL_HTTP_PROXY", "HTTP 代理", "network", placeholder="http://127.0.0.1:7890"),
            SettingField("SURVEIL_USER_AGENT", "User-Agent", "network", placeholder="Mozilla/5.0 ..."),
            SettingField("SURVEIL_HTTP_TIMEOUT_SECONDS", "默认超时秒数", "network", placeholder="20"),
            SettingField("SURVEIL_HTTP_RETRY_COUNT", "默认重试次数", "network", placeholder="2"),
            SettingField("SURVEIL_HTTP_RETRY_BACKOFF_SECONDS", "重试退避秒数", "network", placeholder="2"),
            SettingField("RSS_FETCH_MAX_WORKERS", "RSS 并发数", "network", placeholder="8"),
            SettingField("RSS_FETCH_TIMEOUT_SECONDS", "RSS 超时秒数", "network", placeholder="15"),
            SettingField("RSS_FETCH_RETRY_COUNT", "RSS 重试次数", "network", placeholder="1"),
            SettingField("SOURCE_HEALTH_ALERT_FAILURES", "连续失败告警阈值", "network", placeholder="3"),
            SettingField("SOURCE_HEALTH_ALERT_COOLDOWN_MINUTES", "告警冷却分钟", "network", placeholder="60"),
            SettingField("SOURCE_HEALTH_ALERT_RECOVERY", "恢复告警", "network", placeholder="1"),
        ],
    },
    {
        "id": "sina",
        "title": "新浪智研 / 新浪新闻",
        "restart_hint": "保存后建议重启新浪快讯常驻服务；个股资讯 timer 下一次运行会读取新配置。",
        "fields": [
            SettingField("SINA_NEWS_PROVIDER", "新闻源", "sina", placeholder="legacy"),
            SettingField("SINA_ZY_API_BASE_URL", "智研 API Base URL", "sina"),
            SettingField("SINA_ZY_API_KEY", "智研 API Key", "sina", sensitive=True),
            SettingField("SINA_ZY_TIMEOUT_SECONDS", "智研超时秒数", "sina", placeholder="20"),
            SettingField("SINA_FLASH_POLL_SECONDS", "快讯轮询秒数", "sina", placeholder="10"),
            SettingField("SINA_FLASH_TAGS", "快讯 tags", "sina", placeholder="10"),
        ],
    },
    {
        "id": "web",
        "title": "Web 工作台",
        "restart_hint": "HOLDINGS_WEB_TOKEN 变更后需要重启 surveil-holdings-web.service 才会用于鉴权。",
        "fields": [
            SettingField("HOLDINGS_WEB_TOKEN", "访问 Token", "web", sensitive=True, help="留空表示保留现有 token。"),
        ],
    },
]

FIELDS_BY_KEY = {field.key: field for group in SETTING_GROUPS for field in group["fields"]}


def llm_model_selector(values: dict[str, str]) -> dict[str, Any]:
    current = selected_llm_provider(values)
    return {
        "current": current,
        "options": [
            {
                "id": DEEPSEEK_PROVIDER,
                "label": "DeepSeek",
                "base_url": values.get("LLM_BASE_URL") or "https://api.deepseek.com",
                "model": values.get("LLM_MODEL") or "deepseek-chat",
                "configured": bool(
                    values.get("LLM_API_KEY") and values.get("LLM_BASE_URL") and values.get("LLM_MODEL")
                ),
            },
            {
                "id": ZHIPU_GLM_PROVIDER,
                "label": "智谱 GLM 5.3 Flash",
                "base_url": ZHIPU_GLM_BASE_URL,
                "model": ZHIPU_GLM_MODEL,
                "configured": bool(values.get("LLM_GLM_API_KEY")),
            },
        ],
    }

def parse_env_file(path: Path = ENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def settings_payload(path: Path = ENV_PATH) -> dict[str, Any]:
    values = parse_env_file(path)
    groups = []
    for group in SETTING_GROUPS:
        fields = []
        for field in group["fields"]:
            value = values.get(field.key, "")
            item = {
                "key": field.key,
                "label": field.label,
                "sensitive": field.sensitive,
                "configured": bool(value),
                "masked": mask_secret(value) if field.sensitive else "",
                "value": "" if field.sensitive else value,
                "help": field.help,
                "placeholder": field.placeholder,
            }
            fields.append(item)
        item = {
            "id": group["id"],
            "title": group["title"],
            "restart_hint": group["restart_hint"],
            "fields": fields,
        }
        if group["id"] == "llm":
            item["model_selector"] = llm_model_selector(values)
        groups.append(item)
    return {"groups": groups, "path": str(path)}


def build_updates(raw_values: dict[str, Any], current: dict[str, str]) -> tuple[dict[str, str], list[dict[str, str]]]:
    updates: dict[str, str] = {}
    changes: list[dict[str, str]] = []
    for key, raw_value in raw_values.items():
        if key not in FIELDS_BY_KEY:
            raise ValueError(f"不允许修改未知配置项：{key}")
        field = FIELDS_BY_KEY[key]
        value = str(raw_value or "").strip()
        old_value = current.get(key, "")
        if field.sensitive and not value:
            continue
        if value == old_value:
            continue
        updates[key] = value
        changes.append(
            {
                "key": key,
                "sensitive": "1" if field.sensitive else "0",
                "old": "<redacted>" if field.sensitive and old_value else old_value,
                "new": "<redacted>" if field.sensitive and value else value,
            }
        )
    return updates, changes


def write_env_updates(
    updates: dict[str, str],
    *,
    path: Path = ENV_PATH,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else ""
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    if updates and out and out[-1].strip():
        out.append("")
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out).rstrip() + "\n")
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def save_settings(
    raw_values: dict[str, Any],
    *,
    path: Path = ENV_PATH,
) -> dict[str, Any]:
    current = parse_env_file(path)
    updates, changes = build_updates(raw_values, current)
    if updates:
        write_env_updates(updates, path=path)
    return {"changed": changes, "changed_count": len(changes), "path": str(path)}


def switch_llm_provider(
    provider: str,
    raw_values: dict[str, Any] | None = None,
    *,
    path: Path = ENV_PATH,
) -> dict[str, Any]:
    target = canonical_llm_provider(provider)
    allowed_fields = {
        DEEPSEEK_PROVIDER: {"LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"},
        ZHIPU_GLM_PROVIDER: {"LLM_GLM_API_KEY"},
    }
    if target not in allowed_fields:
        raise ValueError("只允许切换 DeepSeek 或智谱 GLM 5.3 Flash")

    supplied = raw_values or {}
    unknown = set(supplied) - allowed_fields[target]
    if unknown:
        raise ValueError(f"当前模型切换不允许修改配置项：{sorted(unknown)}")

    current = parse_env_file(path)
    updates, changes = build_updates(supplied, current)
    effective = {**current, **updates}
    if target == DEEPSEEK_PROVIDER:
        missing = [key for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL") if not effective.get(key)]
        if missing:
            raise ValueError("请先配置完整的 DeepSeek / 兼容模型 Base URL、模型名称和 API Key")
    elif not effective.get("LLM_GLM_API_KEY"):
        raise ValueError("请先配置智谱 GLM 5.3 Flash API Key")

    old_provider = current.get("LLM_PROVIDER", "")
    if old_provider != target:
        updates["LLM_PROVIDER"] = target
        changes.append(
            {
                "key": "LLM_PROVIDER",
                "sensitive": "0",
                "old": old_provider,
                "new": target,
            }
        )
    if updates:
        write_env_updates(updates, path=path)
    return {
        "provider": target,
        "changed": changes,
        "changed_count": len(changes),
        "path": str(path),
    }
