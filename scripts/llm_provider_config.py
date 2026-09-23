"""Shared model selection for the unified OpenAI-compatible LLM client."""

from __future__ import annotations

from collections.abc import Mapping


DEEPSEEK_PROVIDER = "deepseek"
ZHIPU_GLM_PROVIDER = "zhipu_glm"
OPENAI_COMPATIBLE_PROVIDER = "openai_compatible"

ZHIPU_GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
ZHIPU_GLM_MODEL = "glm-5.3-flash"

_ZHIPU_ALIASES = {ZHIPU_GLM_PROVIDER, "zhipu", "glm", ZHIPU_GLM_MODEL}

# 阿里云百炼（DashScope）OpenAI 兼容端点。北京地域默认端点不需要业务空间 ID；
# 使用业务空间专属端点（*.cn-beijing.maas.aliyuncs.com）时在 Web 面板覆盖该地址。
QWEN_BAILIAN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_FLASH_SNAPSHOT_PROVIDER = "qwen_flash_snapshot"
QWEN_FLASH_PROVIDER = "qwen_flash"
QWEN_FLASH_SNAPSHOT_MODEL = "qwen3.7-flash-2026-07-15"
QWEN_FLASH_MODEL = "qwen3.7-flash"

# 千问快照模型是当前模型；余额不足时改用同一端点的稳定版模型。
QWEN_BAILIAN_PROVIDER_MODELS = {
    QWEN_FLASH_SNAPSHOT_PROVIDER: QWEN_FLASH_SNAPSHOT_MODEL,
    QWEN_FLASH_PROVIDER: QWEN_FLASH_MODEL,
}
_QWEN_SNAPSHOT_ALIASES = {
    "qwen",
    "qwen_bailian",
    "bailian",
    "dashscope",
    QWEN_FLASH_SNAPSHOT_MODEL,
}


def is_qwen_bailian_base_url(base_url: str) -> bool:
    lowered = str(base_url or "").lower()
    return "dashscope.aliyuncs.com" in lowered or "maas.aliyuncs.com" in lowered


def canonical_llm_provider(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in _ZHIPU_ALIASES:
        return ZHIPU_GLM_PROVIDER
    if normalized == DEEPSEEK_PROVIDER:
        return DEEPSEEK_PROVIDER
    if normalized in QWEN_BAILIAN_PROVIDER_MODELS:
        return normalized
    if normalized in _QWEN_SNAPSHOT_ALIASES:
        return QWEN_FLASH_SNAPSHOT_PROVIDER
    if normalized == QWEN_FLASH_MODEL:
        return QWEN_FLASH_PROVIDER
    return normalized or OPENAI_COMPATIBLE_PROVIDER


def selected_llm_provider(values: Mapping[str, str]) -> str:
    """Return the Web-facing current model, including legacy config inference."""
    configured = canonical_llm_provider(values.get("LLM_PROVIDER", ""))
    if (
        configured
        in {DEEPSEEK_PROVIDER, ZHIPU_GLM_PROVIDER} | set(QWEN_BAILIAN_PROVIDER_MODELS)
    ):
        return configured
    base_url = str(values.get("LLM_BASE_URL") or "").lower()
    model = str(values.get("LLM_MODEL") or "").lower()
    if "deepseek" in base_url or model.startswith("deepseek-"):
        return DEEPSEEK_PROVIDER
    if "open.bigmodel.cn" in base_url and model == ZHIPU_GLM_MODEL:
        return ZHIPU_GLM_PROVIDER
    if is_qwen_bailian_base_url(base_url):
        return QWEN_FLASH_PROVIDER if model == QWEN_FLASH_MODEL else QWEN_FLASH_SNAPSHOT_PROVIDER
    return configured


def resolve_llm_connection(values: Mapping[str, str]) -> tuple[str, str, str] | None:
    """Resolve one active connection without creating a provider-specific call path."""
    provider = canonical_llm_provider(values.get("LLM_PROVIDER", ""))
    if provider == ZHIPU_GLM_PROVIDER:
        api_key = str(values.get("LLM_GLM_API_KEY") or "").strip()
        if not api_key:
            return None
        return api_key, ZHIPU_GLM_BASE_URL, ZHIPU_GLM_MODEL

    if provider in QWEN_BAILIAN_PROVIDER_MODELS:
        api_key = str(values.get("LLM_QWEN_API_KEY") or "").strip()
        if not api_key:
            return None
        base_url = str(values.get("LLM_QWEN_BASE_URL") or "").strip() or QWEN_BAILIAN_BASE_URL
        return api_key, base_url, QWEN_BAILIAN_PROVIDER_MODELS[provider]

    api_key = str(values.get("LLM_API_KEY") or "").strip()
    base_url = str(values.get("LLM_BASE_URL") or "").strip()
    model = str(values.get("LLM_MODEL") or "").strip()
    if not api_key or not base_url or not model:
        return None
    return api_key, base_url, model


def resolve_llm_fallback_connection(values: Mapping[str, str]) -> tuple[str, str, str] | None:
    """Resolve the 阿里云百炼 fallback model used when the current model reports no balance."""
    provider = canonical_llm_provider(values.get("LLM_PROVIDER", ""))
    model = QWEN_BAILIAN_PROVIDER_MODELS.get(provider)
    if not model or model == QWEN_FLASH_MODEL:
        return None
    connection = resolve_llm_connection(values)
    if not connection:
        return None
    api_key, base_url, _ = connection
    return api_key, base_url, QWEN_FLASH_MODEL
