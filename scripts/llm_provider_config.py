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

# 百炼托管的智谱 GLM 5.3：与百炼千问模型共用 LLM_QWEN_API_KEY / LLM_QWEN_BASE_URL，
# 但不参与千问快照模型的余额回退（回退仅限同品牌 qwen3.7-flash）。
GLM_BAILIAN_PROVIDER = "glm_bailian"
GLM_BAILIAN_MODEL = "glm-5.3"
_GLM_BAILIAN_ALIASES = {GLM_BAILIAN_PROVIDER, GLM_BAILIAN_MODEL}

# 百炼千问 qwen3.8 系列：与 qwen3.7 快照模型共用 LLM_QWEN_API_KEY / LLM_QWEN_BASE_URL，
# 按下列顺序组成余额回退链；当前模型额度用尽时同一轮切换到链上的下一个模型。
QWEN38_MAX_PROVIDER = "qwen38_max"
QWEN38_MAX_MODEL = "qwen3.8-max"
QWEN38_2_4T_A95B_PROVIDER = "qwen38_2_4t_a95b"
QWEN38_2_4T_A95B_MODEL = "qwen3.8-2.4t-a95b"
QWEN38_27B_PROVIDER = "qwen38_27b"
QWEN38_27B_MODEL = "qwen3.8-27b"
QWEN38_FLASH_PROVIDER = "qwen38_flash"
QWEN38_FLASH_MODEL = "qwen3.8-flash"
QWEN38_MAX_0902_PROVIDER = "qwen38_max_0902"
QWEN38_MAX_0902_MODEL = "qwen3.8-max-0902"
QWEN38_BAILIAN_CHAIN = (
    (QWEN38_MAX_PROVIDER, QWEN38_MAX_MODEL),
    (QWEN38_2_4T_A95B_PROVIDER, QWEN38_2_4T_A95B_MODEL),
    (QWEN38_27B_PROVIDER, QWEN38_27B_MODEL),
    (QWEN38_FLASH_PROVIDER, QWEN38_FLASH_MODEL),
    (QWEN38_MAX_0902_PROVIDER, QWEN38_MAX_0902_MODEL),
)

# 千问快照模型余额不足时改用同一端点的稳定版模型；qwen3.8 系列按声明顺序回退。
QWEN_BAILIAN_PROVIDER_MODELS = {
    QWEN_FLASH_SNAPSHOT_PROVIDER: QWEN_FLASH_SNAPSHOT_MODEL,
    QWEN_FLASH_PROVIDER: QWEN_FLASH_MODEL,
    **{provider: model for provider, model in QWEN38_BAILIAN_CHAIN},
}
QWEN_BAILIAN_FALLBACK_PROVIDERS = {
    QWEN_FLASH_SNAPSHOT_PROVIDER: (QWEN_FLASH_PROVIDER,),
    **{
        provider: tuple(next_provider for next_provider, _ in QWEN38_BAILIAN_CHAIN[index + 1 :])
        for index, (provider, _) in enumerate(QWEN38_BAILIAN_CHAIN)
    },
}
_QWEN_BAILIAN_MODEL_PROVIDERS = {model: provider for provider, model in QWEN_BAILIAN_PROVIDER_MODELS.items()}
_QWEN_SNAPSHOT_ALIASES = {
    "qwen",
    "qwen_bailian",
    "bailian",
    "dashscope",
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
    if normalized in _GLM_BAILIAN_ALIASES:
        return GLM_BAILIAN_PROVIDER
    if normalized in QWEN_BAILIAN_PROVIDER_MODELS:
        return normalized
    model_provider = _QWEN_BAILIAN_MODEL_PROVIDERS.get(normalized)
    if model_provider:
        return model_provider
    if normalized in _QWEN_SNAPSHOT_ALIASES:
        return QWEN_FLASH_SNAPSHOT_PROVIDER
    return normalized or OPENAI_COMPATIBLE_PROVIDER


def selected_llm_provider(values: Mapping[str, str]) -> str:
    """Return the Web-facing current model, including legacy config inference."""
    configured = canonical_llm_provider(values.get("LLM_PROVIDER", ""))
    if (
        configured
        in {DEEPSEEK_PROVIDER, ZHIPU_GLM_PROVIDER, GLM_BAILIAN_PROVIDER}
        | set(QWEN_BAILIAN_PROVIDER_MODELS)
    ):
        return configured
    base_url = str(values.get("LLM_BASE_URL") or "").lower()
    model = str(values.get("LLM_MODEL") or "").lower()
    if "deepseek" in base_url or model.startswith("deepseek-"):
        return DEEPSEEK_PROVIDER
    if "open.bigmodel.cn" in base_url and model == ZHIPU_GLM_MODEL:
        return ZHIPU_GLM_PROVIDER
    if is_qwen_bailian_base_url(base_url):
        if model == GLM_BAILIAN_MODEL:
            return GLM_BAILIAN_PROVIDER
        return _QWEN_BAILIAN_MODEL_PROVIDERS.get(model, QWEN_FLASH_SNAPSHOT_PROVIDER)
    return configured


def resolve_llm_connection(values: Mapping[str, str]) -> tuple[str, str, str] | None:
    """Resolve one active connection without creating a provider-specific call path."""
    provider = canonical_llm_provider(values.get("LLM_PROVIDER", ""))
    if provider == ZHIPU_GLM_PROVIDER:
        api_key = str(values.get("LLM_GLM_API_KEY") or "").strip()
        if not api_key:
            return None
        return api_key, ZHIPU_GLM_BASE_URL, ZHIPU_GLM_MODEL

    if provider == GLM_BAILIAN_PROVIDER:
        api_key = str(values.get("LLM_QWEN_API_KEY") or "").strip()
        if not api_key:
            return None
        base_url = str(values.get("LLM_QWEN_BASE_URL") or "").strip() or QWEN_BAILIAN_BASE_URL
        return api_key, base_url, GLM_BAILIAN_MODEL

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


def _resolve_qwen_connection(values: Mapping[str, str]) -> tuple[str, str] | None:
    """Resolve the shared 千问 connection used by every 百炼千问 / qwen3.8 model."""
    api_key = str(values.get("LLM_QWEN_API_KEY") or "").strip()
    if not api_key:
        return None
    return api_key, str(values.get("LLM_QWEN_BASE_URL") or "").strip() or QWEN_BAILIAN_BASE_URL


def resolve_llm_fallback_connections(values: Mapping[str, str]) -> list[tuple[str, str, str]]:
    """Resolve the ordered 阿里云百炼 fallback models used when the current model reports no balance."""
    provider = canonical_llm_provider(values.get("LLM_PROVIDER", ""))
    if provider in QWEN_BAILIAN_PROVIDER_MODELS:
        fallback_providers = QWEN_BAILIAN_FALLBACK_PROVIDERS.get(provider, ())
        connection = _resolve_qwen_connection(values)
    elif provider == DEEPSEEK_PROVIDER and is_qwen_bailian_base_url(
        str(values.get("LLM_BASE_URL") or "")
    ):
        # 百炼托管的 DeepSeek 额度用尽时，从 qwen3.8 回退链头部开始切换；
        # 官方 DeepSeek 端点不回退百炼模型。
        fallback_providers = tuple(chain_provider for chain_provider, _ in QWEN38_BAILIAN_CHAIN)
        connection = _resolve_qwen_connection(values)
    else:
        return []
    if not fallback_providers or not connection:
        return []
    api_key, base_url = connection
    return [
        (api_key, base_url, QWEN_BAILIAN_PROVIDER_MODELS[fallback_provider])
        for fallback_provider in fallback_providers
    ]
