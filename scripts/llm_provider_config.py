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
# 并作为百炼免费额度回退链的一档参与回退。
GLM_BAILIAN_PROVIDER = "glm_bailian"
GLM_BAILIAN_MODEL = "glm-5.3"
_GLM_BAILIAN_ALIASES = {GLM_BAILIAN_PROVIDER, GLM_BAILIAN_MODEL}

# 百炼千问 qwen3.8 系列：与 qwen3.7 快照模型共用 LLM_QWEN_API_KEY / LLM_QWEN_BASE_URL。
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
# qwen3.8-2.4t-a95b 仅支持思考模式：百炼 OpenAI 兼容端点对它要求 enable_thinking=true，
# 传 false 直接返回 HTTP 400 InternalError.Algo.InvalidParameter。
QWEN_BAILIAN_THINKING_ONLY_MODELS = {QWEN38_2_4T_A95B_MODEL}

# 百炼托管的 Kimi K3 与 DeepSeek V4.1 Flash：同样共用千问连接。
KIMI_K3_PROVIDER = "kimi_k3"
KIMI_K3_MODEL = "kimi-k3"
DEEPSEEK_V41_FLASH_PROVIDER = "deepseek_v41_flash"
DEEPSEEK_V41_FLASH_MODEL = "deepseek-v4.1-flash"

# 只接受固定 temperature 的模型：百炼 OpenAI 兼容端点对这些模型的其它取值直接返回
# HTTP 400 InternalError.Algo.InvalidParameter（kimi-k3 传 temperature=0.1 会被拒绝，
# 生产程度决策链路固定传 0 可正常返回），请求前统一归一化为该固定值。
BAILIAN_FIXED_TEMPERATURE_MODELS = {KIMI_K3_MODEL: 0}

# 百炼免费额度模型按下列顺序依次使用：当前模型额度用尽时，
# 同一轮请求切换到链上的下一个模型。glm-5.3、kimi-k3、deepseek-v4.1-flash
# 与千问模型共用 LLM_QWEN_API_KEY / LLM_QWEN_BASE_URL。
BAILIAN_FALLBACK_CHAIN = (
    (QWEN_FLASH_SNAPSHOT_PROVIDER, QWEN_FLASH_SNAPSHOT_MODEL),
    (QWEN_FLASH_PROVIDER, QWEN_FLASH_MODEL),
    (QWEN38_2_4T_A95B_PROVIDER, QWEN38_2_4T_A95B_MODEL),
    (KIMI_K3_PROVIDER, KIMI_K3_MODEL),
    (GLM_BAILIAN_PROVIDER, GLM_BAILIAN_MODEL),
    (DEEPSEEK_V41_FLASH_PROVIDER, DEEPSEEK_V41_FLASH_MODEL),
)

# 火山方舟（Volcengine Ark）OpenAI 兼容端点，北京地域。模型直接使用方舟 Model ID 调用。
# Doubao-Seed-2.0-pro（doubao-seed-2-0-pro-260215）已被方舟标注即将下线且调用 404，
# 不进入兜底链；当前在售旗舰 doubao-seed-2-1-pro-260915 需在控制台开通后才可加入。
ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DOUBAO_SEED_21_LITE_PROVIDER = "doubao_seed_21_lite"
DOUBAO_SEED_21_LITE_MODEL = "doubao-seed-2-1-lite-260915"
ARK_DEEPSEEK_V41_FLASH_PROVIDER = "ark_deepseek_v41_flash"
ARK_DEEPSEEK_V41_FLASH_MODEL = "deepseek-v4-1-flash-260910"
ARK_GLM_53_FLASH_PROVIDER = "ark_glm_53_flash"
ARK_GLM_53_FLASH_MODEL = "glm-5-3-flash-260828"
ARK_DEEPSEEK_V4_PRO_PROVIDER = "ark_deepseek_v4_pro"
ARK_DEEPSEEK_V4_PRO_MODEL = "deepseek-v4-pro-ga-260813"
ARK_DEEPSEEK_V4_FLASH_PROVIDER = "ark_deepseek_v4_flash"
ARK_DEEPSEEK_V4_FLASH_MODEL = "deepseek-v4-flash-ga-260731"

_ARK_PROVIDER_MODELS = {
    DOUBAO_SEED_21_LITE_PROVIDER: DOUBAO_SEED_21_LITE_MODEL,
    ARK_DEEPSEEK_V41_FLASH_PROVIDER: ARK_DEEPSEEK_V41_FLASH_MODEL,
    ARK_GLM_53_FLASH_PROVIDER: ARK_GLM_53_FLASH_MODEL,
    ARK_DEEPSEEK_V4_PRO_PROVIDER: ARK_DEEPSEEK_V4_PRO_MODEL,
    ARK_DEEPSEEK_V4_FLASH_PROVIDER: ARK_DEEPSEEK_V4_FLASH_MODEL,
}
_ARK_MODEL_PROVIDERS = {model: provider for provider, model in _ARK_PROVIDER_MODELS.items()}

# 火山方舟兜底链：百炼免费额度回退链整体用尽后，同一轮按开通页顺序依次使用方舟模型。
# 方舟各档共用独立的 LLM_ARK_API_KEY / LLM_ARK_BASE_URL 连接；未配置方舟 API Key 时该段关闭式跳过。
ARK_FALLBACK_CHAIN = (
    (DOUBAO_SEED_21_LITE_PROVIDER, DOUBAO_SEED_21_LITE_MODEL),
    (ARK_DEEPSEEK_V41_FLASH_PROVIDER, ARK_DEEPSEEK_V41_FLASH_MODEL),
    (ARK_GLM_53_FLASH_PROVIDER, ARK_GLM_53_FLASH_MODEL),
    (ARK_DEEPSEEK_V4_PRO_PROVIDER, ARK_DEEPSEEK_V4_PRO_MODEL),
    (ARK_DEEPSEEK_V4_FLASH_PROVIDER, ARK_DEEPSEEK_V4_FLASH_MODEL),
)

LLM_FALLBACK_CHAIN = BAILIAN_FALLBACK_CHAIN + ARK_FALLBACK_CHAIN

# 免费额度已用完的旧 qwen3.8 模型：不再进入回退链，仅保留解析，
# 历史 LLM_PROVIDER 命中后额度报错时从链头开始回退。
QWEN38_RETIRED_PROVIDERS = (
    (QWEN38_MAX_PROVIDER, QWEN38_MAX_MODEL),
    (QWEN38_27B_PROVIDER, QWEN38_27B_MODEL),
    (QWEN38_FLASH_PROVIDER, QWEN38_FLASH_MODEL),
    (QWEN38_MAX_0902_PROVIDER, QWEN38_MAX_0902_MODEL),
)

QWEN_BAILIAN_PROVIDER_MODELS = {
    QWEN_FLASH_SNAPSHOT_PROVIDER: QWEN_FLASH_SNAPSHOT_MODEL,
    QWEN_FLASH_PROVIDER: QWEN_FLASH_MODEL,
    **{provider: model for provider, model in QWEN38_RETIRED_PROVIDERS},
    QWEN38_2_4T_A95B_PROVIDER: QWEN38_2_4T_A95B_MODEL,
    KIMI_K3_PROVIDER: KIMI_K3_MODEL,
    DEEPSEEK_V41_FLASH_PROVIDER: DEEPSEEK_V41_FLASH_MODEL,
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


def is_ark_base_url(base_url: str) -> bool:
    return "volces.com" in str(base_url or "").lower()


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
    if normalized in _ARK_PROVIDER_MODELS:
        return normalized
    model_provider = _ARK_MODEL_PROVIDERS.get(normalized)
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
        | set(_ARK_PROVIDER_MODELS)
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
    if is_ark_base_url(base_url):
        return _ARK_MODEL_PROVIDERS.get(model, DOUBAO_SEED_21_LITE_PROVIDER)
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

    if provider in _ARK_PROVIDER_MODELS:
        api_key = str(values.get("LLM_ARK_API_KEY") or "").strip()
        if not api_key:
            return None
        base_url = str(values.get("LLM_ARK_BASE_URL") or "").strip() or ARK_BASE_URL
        return api_key, base_url, _ARK_PROVIDER_MODELS[provider]

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
    """Resolve the ordered fallback models used when the current model reports no balance.

    百炼档共用千问连接，火山方舟档使用独立的方舟连接；某档连接未配置时
    该档关闭式跳过，不影响其余档位继续参与回退。
    """
    provider = canonical_llm_provider(values.get("LLM_PROVIDER", ""))
    chain_providers = [chain_provider for chain_provider, _ in LLM_FALLBACK_CHAIN]
    if provider in chain_providers:
        # 链上模型额度用尽后从自己的下一档继续。
        fallback_providers = tuple(chain_providers[chain_providers.index(provider) + 1 :])
    elif provider in QWEN_BAILIAN_PROVIDER_MODELS or (
        provider == DEEPSEEK_PROVIDER
        and is_qwen_bailian_base_url(str(values.get("LLM_BASE_URL") or ""))
    ):
        # 链外百炼模型（免费额度已用完的旧 qwen3.8 系列、百炼托管的 DeepSeek）
        # 额度用尽后从链头开始切换；官方 DeepSeek 端点和智谱 GLM 不回退链上模型。
        fallback_providers = tuple(chain_providers)
    else:
        return []
    if not fallback_providers:
        return []
    qwen_connection = _resolve_qwen_connection(values)
    ark_api_key = str(values.get("LLM_ARK_API_KEY") or "").strip()
    ark_base_url = str(values.get("LLM_ARK_BASE_URL") or "").strip() or ARK_BASE_URL
    chain_models = dict(LLM_FALLBACK_CHAIN)
    connections: list[tuple[str, str, str]] = []
    for fallback_provider in fallback_providers:
        if fallback_provider in _ARK_PROVIDER_MODELS:
            if not ark_api_key:
                continue
            connections.append((ark_api_key, ark_base_url, chain_models[fallback_provider]))
        elif qwen_connection:
            connections.append(
                (qwen_connection[0], qwen_connection[1], chain_models[fallback_provider])
            )
    return connections
