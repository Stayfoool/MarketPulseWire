"""Shared model selection for the unified OpenAI-compatible LLM client."""

from __future__ import annotations

from collections.abc import Mapping


DEEPSEEK_PROVIDER = "deepseek"
ZHIPU_GLM_PROVIDER = "zhipu_glm"
OPENAI_COMPATIBLE_PROVIDER = "openai_compatible"

ZHIPU_GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
ZHIPU_GLM_MODEL = "glm-5.3-flash"

_ZHIPU_ALIASES = {ZHIPU_GLM_PROVIDER, "zhipu", "glm", ZHIPU_GLM_MODEL}


def canonical_llm_provider(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in _ZHIPU_ALIASES:
        return ZHIPU_GLM_PROVIDER
    if normalized == DEEPSEEK_PROVIDER:
        return DEEPSEEK_PROVIDER
    return normalized or OPENAI_COMPATIBLE_PROVIDER


def selected_llm_provider(values: Mapping[str, str]) -> str:
    """Return the Web-facing current model, including legacy config inference."""
    configured = canonical_llm_provider(values.get("LLM_PROVIDER", ""))
    if configured in {DEEPSEEK_PROVIDER, ZHIPU_GLM_PROVIDER}:
        return configured
    base_url = str(values.get("LLM_BASE_URL") or "").lower()
    model = str(values.get("LLM_MODEL") or "").lower()
    if "deepseek" in base_url or model.startswith("deepseek-"):
        return DEEPSEEK_PROVIDER
    if "open.bigmodel.cn" in base_url and model == ZHIPU_GLM_MODEL:
        return ZHIPU_GLM_PROVIDER
    return configured


def resolve_llm_connection(values: Mapping[str, str]) -> tuple[str, str, str] | None:
    """Resolve one active connection without creating a provider-specific call path."""
    provider = canonical_llm_provider(values.get("LLM_PROVIDER", ""))
    if provider == ZHIPU_GLM_PROVIDER:
        api_key = str(values.get("LLM_GLM_API_KEY") or "").strip()
        if not api_key:
            return None
        return api_key, ZHIPU_GLM_BASE_URL, ZHIPU_GLM_MODEL

    api_key = str(values.get("LLM_API_KEY") or "").strip()
    base_url = str(values.get("LLM_BASE_URL") or "").strip()
    model = str(values.get("LLM_MODEL") or "").strip()
    if not api_key or not base_url or not model:
        return None
    return api_key, base_url, model
