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
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any

from env_utils import get_env
from llm_analysis import (
    chat_completions_url,
    json_response_format_enabled,
    llm_config,
    parse_json_object,
    thinking_type,
)


SYSTEM_PROMPT = """你是投资研报第一页预览的信息抽取器。

你只能根据用户提供的研报标题、价值目录页面可见文字，以及第一页预览 OCR 文字输出 JSON。
不要猜测未展示的 PDF 正文，不要补充外部知识，不要写投资建议。

输出必须简短，重点提取：
- 主要观点 / thesis
- 看多、看空、中性或混合
- 目标价、历史收盘价及其日期、评级、上调/下调、报告日期、机构、涉及公司/行业/环节
- 如果图片不可读或信息不足，明确写 unknown / 信息不足。

只输出 JSON，不要 Markdown。"""


USER_PROMPT = """请提取这份价值目录研报可见第一页预览中的关键信息。

输出 JSON：
{
  "core_content": "一句中文概括，只基于标题和第一页可见信息",
  "stance": "bullish/bearish/neutral/mixed/unknown",
  "research_action": "buy/sell/overweight/underweight/upgrade/downgrade/initiate/long/short/none/unknown",
  "institution": "机构名或 unknown",
  "report_date": "YYYY-MM-DD 或 unknown",
  "rating": "评级或 unknown",
  "target_price": "目标价或 unknown",
  "reference_price": "报告明确标注的历史收盘价或 unknown",
  "reference_price_date": "上述历史收盘价的 YYYY-MM-DD 日期或 unknown",
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

第一页 OCR 文字：
{ocr_text}
"""


_PADDLE_OCR: Any | None = None


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


def ocr_enabled() -> bool:
    return env_bool("VALUE_DIRECTORY_PREVIEW_OCR_ENABLED", True)


def vision_fallback_enabled() -> bool:
    return env_bool("VALUE_DIRECTORY_PREVIEW_VISION_FALLBACK_ENABLED", False)


def ocr_min_chars() -> int:
    raw = os.getenv("VALUE_DIRECTORY_PREVIEW_OCR_MIN_CHARS", "").strip()
    try:
        return max(0, min(1000, int(raw))) if raw else 40
    except ValueError:
        return 40


def ocr_lang() -> str:
    return os.getenv("VALUE_DIRECTORY_PREVIEW_OCR_LANG", "ch").strip() or "ch"


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
    research_action = "unknown"
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
            research_action = label
            break
    return {
        "institution": institution or "unknown",
        "report_date": report_date or "unknown",
        "pages": pages,
        "target_price": target_price or "unknown",
        "research_action": research_action,
    }


def download_preview_image_bytes(url: str) -> tuple[bytes, str]:
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
    return data, content_type


def download_preview_image(url: str) -> tuple[str, str]:
    data, content_type = download_preview_image_bytes(url)
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{content_type};base64,{encoded}", content_type


def image_suffix(content_type: str) -> str:
    lower = content_type.lower()
    if "png" in lower:
        return ".png"
    if "webp" in lower:
        return ".webp"
    return ".jpg"


def paddle_ocr_instance() -> Any:
    global _PADDLE_OCR
    if _PADDLE_OCR is not None:
        return _PADDLE_OCR
    try:
        from paddleocr import PaddleOCR
    except Exception as exc:  # noqa: BLE001 - optional dependency
        raise RuntimeError("PaddleOCR 未安装；请安装 requirements-ocr.txt") from exc
    candidates = [
        {"lang": ocr_lang(), "use_angle_cls": True, "use_gpu": False, "show_log": False},
        {"lang": ocr_lang(), "use_textline_orientation": True, "device": "cpu"},
        {"lang": ocr_lang()},
    ]
    last_error: Exception | None = None
    for kwargs in candidates:
        try:
            _PADDLE_OCR = PaddleOCR(**kwargs)
            return _PADDLE_OCR
        except (TypeError, ValueError) as exc:
            last_error = exc
            continue
        except Exception as exc:  # noqa: BLE001 - PaddleOCR versions differ in config validation error types
            text = str(exc).lower()
            if "unknown argument" in text or "unexpected keyword" in text:
                last_error = exc
                continue
            raise
    raise RuntimeError(f"PaddleOCR 初始化失败：{last_error}")


def flatten_paddleocr_result(result: Any) -> list[tuple[str, float | None]]:
    lines: list[tuple[str, float | None]] = []

    def add(text: Any, score: Any = None) -> None:
        cleaned = compact(text, 400)
        if not cleaned:
            return
        try:
            confidence = float(score) if score is not None else None
        except (TypeError, ValueError):
            confidence = None
        lines.append((cleaned, confidence))

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            texts = node.get("rec_texts") or node.get("texts")
            scores = node.get("rec_scores") or node.get("scores") or []
            if isinstance(texts, list):
                for index, text in enumerate(texts):
                    score = scores[index] if isinstance(scores, list) and index < len(scores) else None
                    add(text, score)
                return
            if node.get("text") or node.get("transcription"):
                add(node.get("text") or node.get("transcription"), node.get("confidence") or node.get("score"))
                return
            for value in node.values():
                walk(value)
            return
        if isinstance(node, (list, tuple)):
            if (
                len(node) >= 2
                and isinstance(node[1], (list, tuple))
                and len(node[1]) >= 2
                and isinstance(node[1][0], str)
            ):
                add(node[1][0], node[1][1])
                return
            for value in node:
                walk(value)

    walk(result)
    deduped: list[tuple[str, float | None]] = []
    seen: set[str] = set()
    for text, score in lines:
        if text in seen:
            continue
        seen.add(text)
        deduped.append((text, score))
    return deduped


def paddle_ocr_image_bytes(data: bytes, content_type: str) -> dict[str, Any]:
    engine = paddle_ocr_instance()
    suffix = image_suffix(content_type)
    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(data)
        tmp.flush()
        if hasattr(engine, "ocr"):
            try:
                result = engine.ocr(tmp.name, cls=True)
            except TypeError:
                if hasattr(engine, "predict"):
                    result = engine.predict(tmp.name)
                else:
                    result = engine.ocr(tmp.name)
        else:
            if hasattr(engine, "predict"):
                result = engine.predict(tmp.name)
            else:
                raise RuntimeError("PaddleOCR 对象缺少 ocr/predict 方法")
    lines = flatten_paddleocr_result(result)
    text = "\n".join(line for line, _score in lines)
    scores = [score for _line, score in lines if score is not None]
    confidence = sum(scores) / len(scores) if scores else None
    return {
        "engine": "paddleocr",
        "status": "ok" if text else "empty",
        "text": text,
        "line_count": len(lines),
        "avg_confidence": confidence,
    }


def extract_ocr_text(image_url: str) -> dict[str, Any]:
    if not ocr_enabled():
        return {"engine": "disabled", "status": "disabled", "text": ""}
    if not image_url:
        return {"engine": "paddleocr", "status": "unavailable", "text": "", "error": "详情页没有可见第一页预览图"}
    data, content_type = download_preview_image_bytes(image_url)
    started = time.monotonic()
    result = paddle_ocr_image_bytes(data, content_type)
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    if len(compact(result.get("text"), 10000)) < ocr_min_chars():
        result = dict(result)
        result["status"] = "too_short" if result.get("text") else result.get("status", "empty")
        result["error"] = f"OCR 文字过短：{len(compact(result.get('text'), 10000))} chars"
    return result


def preview_prompt(item: dict[str, Any], preview: dict[str, Any], *, ocr_text: str = "") -> str:
    visible_text = compact(preview.get("articleText") or preview.get("bodySample"), 1600)
    return (
        USER_PROMPT.replace("{title}", str(item.get("title") or ""))
        .replace("{source_module}", str(item.get("source_module") or ""))
        .replace("{published_at}", str(item.get("published_at") or ""))
        .replace("{visible_text}", visible_text)
        .replace("{ocr_text}", compact(ocr_text, 5000))
    )


def request_preview_llm(payload: dict[str, Any], *, base_url: str, api_key: str) -> dict[str, Any]:
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
    with urllib.request.urlopen(request, timeout=preview_timeout_seconds()) as response:
        body = response.read().decode("utf-8", errors="replace")
    return json.loads(body)


def apply_preview_llm_response_preferences(payload: dict[str, Any], *, base_url: str, model: str) -> None:
    """Mirror the shared LLM client's JSON/thinking policy for preview extraction."""
    thinking = thinking_type(base_url, model).strip().lower()
    if "deepseek" in base_url.lower() and thinking == "enabled" and os.getenv("LLM_ALLOW_DEEPSEEK_THINKING", "").strip() != "1":
        thinking = "disabled"
    if thinking in {"enabled", "disabled"}:
        payload["thinking"] = {"type": thinking}
    if json_response_format_enabled(base_url):
        payload["response_format"] = {"type": "json_object"}


def parse_preview_llm_result(result: dict[str, Any]) -> dict[str, Any]:
    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError(f"LLM 响应缺少 choices：{json.dumps(result, ensure_ascii=False)[:400]}")
    message = choices[0].get("message") or {}
    raw = str(message.get("content") or message.get("output_text") or "").strip()
    if not raw:
        if str(message.get("reasoning_content") or "").strip():
            raise RuntimeError("LLM 未返回最终 content（仅返回 reasoning_content）；请检查模型 thinking 配置。")
        raise RuntimeError("LLM 响应为空")
    return parse_json_object(raw)


def call_preview_text_llm(item: dict[str, Any], preview: dict[str, Any], *, ocr_text: str = "") -> tuple[dict[str, Any], str]:
    if not env_bool("VALUE_DIRECTORY_PREVIEW_LLM_ENABLED", True):
        raise RuntimeError("VALUE_DIRECTORY_PREVIEW_LLM_ENABLED=0")
    config = preview_llm_config()
    if not config:
        raise RuntimeError("LLM 未配置")
    api_key, base_url, model = config
    prompt = preview_prompt(item, preview, ocr_text=ocr_text)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": int(os.getenv("VALUE_DIRECTORY_PREVIEW_MAX_OUTPUT_TOKENS", "700") or "700"),
    }
    apply_preview_llm_response_preferences(payload, base_url=base_url, model=model)
    attempts = max(1, min(3, int(os.getenv("VALUE_DIRECTORY_PREVIEW_LLM_RETRY_COUNT", "1") or "1") + 1))
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return parse_preview_llm_result(request_preview_llm(payload, base_url=base_url, api_key=api_key)), model
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"HTTP {exc.code}: {detail[:500]}")
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = exc
        if attempt < attempts - 1:
            time.sleep(2 + attempt * 3)
            continue
        raise RuntimeError(f"第一页 OCR 文本 LLM 提取失败：{last_error}") from last_error
    raise RuntimeError(f"第一页 OCR 文本 LLM 提取失败：{last_error}")


def call_preview_vision_llm(item: dict[str, Any], preview: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if not vision_fallback_enabled():
        raise RuntimeError("VALUE_DIRECTORY_PREVIEW_VISION_FALLBACK_ENABLED=0")
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
    prompt = preview_prompt(item, preview)
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
    }
    apply_preview_llm_response_preferences(payload, base_url=base_url, model=model)
    attempts = max(1, min(3, int(os.getenv("VALUE_DIRECTORY_PREVIEW_LLM_RETRY_COUNT", "1") or "1") + 1))
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return parse_preview_llm_result(request_preview_llm(payload, base_url=base_url, api_key=api_key)), model
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"HTTP {exc.code}: {detail[:500]}")
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = exc
        if attempt < attempts - 1:
            time.sleep(2 + attempt * 3)
            continue
        raise RuntimeError(f"第一页视觉 LLM 提取失败：{last_error}") from last_error
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


def normalize_facts(
    parsed: dict[str, Any],
    item: dict[str, Any],
    preview: dict[str, Any],
    model: str,
    *,
    ocr: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
        "research_action": str(
            parsed.get("research_action") or parsed.get("action") or meta.get("research_action") or "unknown"
        ),
        "institution": str(parsed.get("institution") or meta.get("institution") or "unknown"),
        "report_date": str(parsed.get("report_date") or meta.get("report_date") or "unknown"),
        "rating": str(parsed.get("rating") or "unknown"),
        "target_price": str(parsed.get("target_price") or meta.get("target_price") or "unknown"),
        "reference_price": str(parsed.get("reference_price") or "unknown"),
        "reference_price_date": str(parsed.get("reference_price_date") or "unknown"),
        "targets": [compact(target, 80) for target in targets if compact(target, 80)][:5],
        "key_points": [compact(point, 140) for point in key_points if compact(point, 140)][:3],
        "preview_basis": str(parsed.get("preview_basis") or "visible_first_page_only"),
        "confidence": str(parsed.get("confidence") or "low"),
        "model": model,
        "preview_image_url": first_preview_image_url(preview),
        "title_metadata": meta,
        "ocr": ocr or {},
    }
    return facts


def fallback_facts(
    item: dict[str, Any],
    preview: dict[str, Any],
    error: Exception | None = None,
    *,
    ocr: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = title_metadata(str(item.get("title") or ""))
    status = "unavailable" if not first_preview_image_url(preview) else "failed"
    return {
        "status": status,
        "core_content": compact(item.get("title"), 420),
        "stance": "unknown",
        "research_action": meta.get("research_action") or "unknown",
        "institution": meta.get("institution") or "unknown",
        "report_date": meta.get("report_date") or "unknown",
        "rating": "unknown",
        "target_price": meta.get("target_price") or "unknown",
        "reference_price": "unknown",
        "reference_price_date": "unknown",
        "targets": [],
        "key_points": [],
        "preview_basis": "visible_first_page_only",
        "confidence": "low",
        "model": "preview_failed",
        "preview_image_url": first_preview_image_url(preview),
        "title_metadata": meta,
        "ocr": ocr or {},
        "error": str(error or "第一页预览不可用")[:500],
    }


def extract_preview_facts(item: dict[str, Any], preview: dict[str, Any]) -> dict[str, Any]:
    ocr: dict[str, Any] | None = None
    try:
        if ocr_enabled():
            ocr = extract_ocr_text(first_preview_image_url(preview))
            if ocr.get("status") == "ok" and compact(ocr.get("text")):
                parsed, model = call_preview_text_llm(item, preview, ocr_text=str(ocr.get("text") or ""))
                return normalize_facts(parsed, item, preview, model, ocr=ocr)
        if vision_fallback_enabled():
            parsed, model = call_preview_vision_llm(item, preview)
            return normalize_facts(parsed, item, preview, model, ocr=ocr)
        error = ocr.get("error") if ocr else "OCR 未启用或不可用"
        return fallback_facts(item, preview, RuntimeError(str(error or "OCR 未能提取有效文字")), ocr=ocr)
    except Exception as exc:  # noqa: BLE001 - preview extraction must not fabricate facts.
        return fallback_facts(item, preview, exc, ocr=ocr)


def preview_lines(facts: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    status = str(facts.get("status") or "")
    if status == "ok":
        lines.append(f"第一页提取：{facts.get('core_content') or ''}".strip())
    elif status:
        lines.append(f"第一页提取：失败/不可用（{facts.get('error') or status}）")
    meta_parts = []
    for label, key in (
        ("机构", "institution"),
        ("日期", "report_date"),
        ("方向", "stance"),
        ("研报动作", "research_action"),
        ("评级", "rating"),
        ("目标价", "target_price"),
        ("历史收盘价", "reference_price"),
        ("收盘价日期", "reference_price_date"),
    ):
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
