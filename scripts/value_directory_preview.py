"""First-page preview extraction for ValueList reports.

The preview path is intentionally narrow: it only reads title/list metadata and
the first preview image already visible on the detail page. It never clicks
purchase/download controls and never fetches report PDFs.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

from env_utils import get_env
from llm_analysis import chat_completions_url, llm_config, parse_json_object


SYSTEM_PROMPT = """你是投资研报第一页预览的信息抽取器。

你只能根据用户提供的研报标题、价值目录页面可见文字，以及第一页预览图中能直接看见的信息输出 JSON。
不要猜测未展示的 PDF 正文，不要补充外部知识，不要写投资建议。

输出必须简短，重点提取：
- 主要观点 / thesis
- 看多、看空、中性或混合
- 目标价、评级、上调/下调、报告日期、机构、涉及公司/行业/环节
- 如果图片不可读或信息不足，明确写 unknown / 信息不足。

只输出 JSON，不要 Markdown。"""


USER_PROMPT = """请提取这份价值目录研报可见第一页预览中的关键信息。

输出 JSON：
{
  "core_content": "一句中文概括，只基于标题和第一页可见信息",
  "stance": "bullish/bearish/neutral/mixed/unknown",
  "action": "buy/sell/overweight/underweight/upgrade/downgrade/initiate/long/short/none/unknown",
  "institution": "机构名或 unknown",
  "report_date": "YYYY-MM-DD 或 unknown",
  "rating": "评级或 unknown",
  "target_price": "目标价或 unknown",
  "targets": ["公司/股票/行业/环节，最多5个"],
  "key_points": ["第一页可见要点1", "第一页可见要点2", "第一页可见要点3"],
  "preview_basis": "visible_first_page_only",
  "confidence": "high/medium/low"
}

标题：{title}
来源模块：{source_module}
发布时间：{published_at}
页面可见文字：
{visible_text}
"""


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw in {"1", "true", "yes", "on", "y", "是"}:
        return True
    if raw in {"0", "false", "no", "off", "n", "否"}:
        return False
    return default


def preview_llm_config() -> tuple[str, str, str] | None:
    base = llm_config()
    api_key = get_env("VALUE_DIRECTORY_PREVIEW_API_KEY")
    base_url = get_env("VALUE_DIRECTORY_PREVIEW_BASE_URL")
    model = get_env("VALUE_DIRECTORY_PREVIEW_MODEL")
    if base:
        api_key = api_key or base[0]
        base_url = base_url or base[1]
        model = model or base[2]
    if not api_key or not base_url or not model:
        return None
    return api_key, base_url, model


def preview_timeout_seconds() -> int:
    raw = os.getenv("VALUE_DIRECTORY_PREVIEW_LLM_TIMEOUT_SECONDS", "").strip()
    try:
        return max(10, min(90, int(raw))) if raw else 45
    except ValueError:
        return 45


def max_image_bytes() -> int:
    raw = os.getenv("VALUE_DIRECTORY_PREVIEW_MAX_IMAGE_BYTES", "").strip()
    try:
        return max(50_000, min(5_000_000, int(raw))) if raw else 2_000_000
    except ValueError:
        return 2_000_000


def compact(value: Any, limit: int = 1200) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def title_metadata(title: str) -> dict[str, Any]:
    text = str(title or "")
    institution = text.split("-", 1)[0].strip() if "-" in text else ""
    report_date = ""
    match = re.search(r"(20\d{2})(\d{2})(\d{2})", text)
    if match:
        report_date = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    pages = ""
    match = re.search(r"【(\d+)页】", text)
    if match:
        pages = match.group(1)
    target_price = ""
    match = re.search(r"(?:目标价|target price|PT|tp)[^0-9€$￥¥]{0,16}([€$￥¥]?\s*\d+(?:\.\d+)?)", text, re.I)
    if match:
        target_price = match.group(1).replace(" ", "")
    action = "unknown"
    lowered = text.lower()
    action_map = (
        ("做多", "long"),
        ("做空", "short"),
        ("买入", "buy"),
        ("buy", "buy"),
        ("卖出", "sell"),
        ("sell", "sell"),
        ("超配", "overweight"),
        ("overweight", "overweight"),
        ("低配", "underweight"),
        ("underweight", "underweight"),
        ("上调", "upgrade"),
        ("raise pt", "upgrade"),
        ("下调", "downgrade"),
        ("lower pt", "downgrade"),
        ("首次覆盖", "initiate"),
        ("initiating", "initiate"),
        ("initiate", "initiate"),
    )
    for needle, label in action_map:
        if needle.lower() in lowered:
            action = label
            break
    return {
        "institution": institution or "unknown",
        "report_date": report_date or "unknown",
        "pages": pages,
        "target_price": target_price or "unknown",
        "action": action,
    }


def download_preview_image(url: str) -> tuple[str, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 MarketPulseWire ValueList preview/0.1",
            "Accept": "image/*,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=preview_timeout_seconds()) as response:
        content_type = response.headers.get("content-type", "image/jpeg").split(";", 1)[0]
        data = response.read(max_image_bytes() + 1)
    if not content_type.startswith("image/"):
        raise RuntimeError(f"预览图 content-type 不是图片：{content_type}")
    if len(data) > max_image_bytes():
        raise RuntimeError(f"预览图过大：{len(data)} bytes")
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{content_type};base64,{encoded}", content_type


def call_preview_llm(item: dict[str, Any], preview: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if not env_bool("VALUE_DIRECTORY_PREVIEW_LLM_ENABLED", True):
        raise RuntimeError("VALUE_DIRECTORY_PREVIEW_LLM_ENABLED=0")
    config = preview_llm_config()
    if not config:
        raise RuntimeError("LLM 未配置")
    api_key, base_url, model = config
    image_url = first_preview_image_url(preview)
    if not image_url:
        raise RuntimeError("详情页没有可见第一页预览图")
    data_url, _content_type = download_preview_image(image_url)
    visible_text = compact(preview.get("articleText") or preview.get("bodySample"), 1600)
    prompt = (
        USER_PROMPT.replace("{title}", str(item.get("title") or ""))
        .replace("{source_module}", str(item.get("source_module") or ""))
        .replace("{published_at}", str(item.get("published_at") or ""))
        .replace("{visible_text}", visible_text)
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        "temperature": 0.1,
        "max_tokens": int(os.getenv("VALUE_DIRECTORY_PREVIEW_MAX_OUTPUT_TOKENS", "700") or "700"),
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        chat_completions_url(base_url),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "market-pulse-wire-value-directory-preview/0.1",
        },
        method="POST",
    )
    attempts = max(1, min(3, int(os.getenv("VALUE_DIRECTORY_PREVIEW_LLM_RETRY_COUNT", "1") or "1") + 1))
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=preview_timeout_seconds()) as response:
                body = response.read().decode("utf-8", errors="replace")
            result = json.loads(body)
            choices = result.get("choices") or []
            if not choices:
                raise RuntimeError(f"LLM 响应缺少 choices：{body[:400]}")
            message = choices[0].get("message") or {}
            raw = str(message.get("content") or message.get("output_text") or "").strip()
            if not raw:
                raise RuntimeError("LLM 响应为空")
            return parse_json_object(raw), model
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(2 + attempt * 3)
                continue
            raise RuntimeError(f"第一页预览 LLM 提取失败：{exc}") from exc
    raise RuntimeError(f"第一页预览 LLM 提取失败：{last_error}")


def first_preview_image_url(preview: dict[str, Any]) -> str:
    images = preview.get("previewImages")
    if not isinstance(images, list):
        return ""
    for image in images:
        if not isinstance(image, dict):
            continue
        src = str(image.get("src") or "").strip()
        if src.startswith("http"):
            return src
    return ""


def normalize_facts(parsed: dict[str, Any], item: dict[str, Any], preview: dict[str, Any], model: str) -> dict[str, Any]:
    meta = title_metadata(str(item.get("title") or ""))
    key_points = parsed.get("key_points")
    if not isinstance(key_points, list):
        key_points = []
    targets = parsed.get("targets")
    if not isinstance(targets, list):
        targets = []
    core = compact(parsed.get("core_content"), 420)
    if not core:
        core = compact(item.get("title"), 420)
    facts = {
        "status": "ok",
        "core_content": core,
        "stance": str(parsed.get("stance") or "unknown"),
        "action": str(parsed.get("action") or meta.get("action") or "unknown"),
        "institution": str(parsed.get("institution") or meta.get("institution") or "unknown"),
        "report_date": str(parsed.get("report_date") or meta.get("report_date") or "unknown"),
        "rating": str(parsed.get("rating") or "unknown"),
        "target_price": str(parsed.get("target_price") or meta.get("target_price") or "unknown"),
        "targets": [compact(target, 80) for target in targets if compact(target, 80)][:5],
        "key_points": [compact(point, 140) for point in key_points if compact(point, 140)][:3],
        "preview_basis": str(parsed.get("preview_basis") or "visible_first_page_only"),
        "confidence": str(parsed.get("confidence") or "low"),
        "model": model,
        "preview_image_url": first_preview_image_url(preview),
        "title_metadata": meta,
    }
    return facts


def fallback_facts(item: dict[str, Any], preview: dict[str, Any], error: Exception | None = None) -> dict[str, Any]:
    meta = title_metadata(str(item.get("title") or ""))
    status = "unavailable" if not first_preview_image_url(preview) else "failed"
    return {
        "status": status,
        "core_content": compact(item.get("title"), 420),
        "stance": "unknown",
        "action": meta.get("action") or "unknown",
        "institution": meta.get("institution") or "unknown",
        "report_date": meta.get("report_date") or "unknown",
        "rating": "unknown",
        "target_price": meta.get("target_price") or "unknown",
        "targets": [],
        "key_points": [],
        "preview_basis": "visible_first_page_only",
        "confidence": "low",
        "model": "preview_failed",
        "preview_image_url": first_preview_image_url(preview),
        "title_metadata": meta,
        "error": str(error or "第一页预览不可用")[:500],
    }


def extract_preview_facts(item: dict[str, Any], preview: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed, model = call_preview_llm(item, preview)
        return normalize_facts(parsed, item, preview, model)
    except Exception as exc:  # noqa: BLE001 - preview extraction must not fabricate facts.
        return fallback_facts(item, preview, exc)


def preview_lines(facts: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    status = str(facts.get("status") or "")
    if status == "ok":
        lines.append(f"第一页提取：{facts.get('core_content') or ''}".strip())
    elif status:
        lines.append(f"第一页提取：失败/不可用（{facts.get('error') or status}）")
    meta_parts = []
    for label, key in (("机构", "institution"), ("日期", "report_date"), ("方向", "stance"), ("动作", "action"), ("评级", "rating"), ("目标价", "target_price")):
        value = str(facts.get(key) or "").strip()
        if value and value.lower() != "unknown":
            meta_parts.append(f"{label}：{value}")
    if meta_parts:
        lines.append("；".join(meta_parts))
    points = [str(point).strip() for point in facts.get("key_points") or [] if str(point).strip()]
    if points:
        lines.append("要点：" + "；".join(points[:3]))
    return [line for line in lines if line.strip()]


def apply_preview_to_item(item: dict[str, Any], preview: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(item)
    raw = dict(enriched.get("raw") or {})
    raw["value_directory_preview"] = {
        "detail_state": preview.get("state"),
        "preview_image_url": first_preview_image_url(preview),
        "has_purchase_button": bool(preview.get("hasPurchaseButton")),
        "facts": facts,
    }
    enriched["raw"] = raw
    enriched["preview_lines"] = preview_lines(facts)
    enriched["preview_image_url"] = facts.get("preview_image_url") or first_preview_image_url(preview)
    if facts.get("status") == "ok" and facts.get("core_content"):
        enriched["summary"] = str(facts["core_content"])
        details = "\n".join(enriched["preview_lines"])
        enriched["content"] = f"{enriched.get('title', '')}\n{details}".strip()
        enriched["full_text"] = enriched["content"]
        enriched["body_source"] = "价值目录详情页可见第一页预览"
    return enriched
