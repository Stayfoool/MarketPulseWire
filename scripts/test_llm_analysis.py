#!/usr/bin/env python3
"""Regression checks for LLM analysis formatting without network calls."""

from __future__ import annotations

import io
import os
import json
import time

os.environ["SURVEIL_DISABLE_LLM"] = "1"

import llm_analysis
from llm_analysis import analyze_with_llm, format_llm_analysis, parse_json_object
from llm_provider_config import BAILIAN_FIXED_TEMPERATURE_MODELS


def test_raw_chat_completion_returns_bounded_usage_metadata() -> None:
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps(
                {
                    "id": "provider-response-1",
                    "choices": [{"message": {"content": '{"ok":true}'}}],
                    "usage": {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168},
                }
            ).encode("utf-8")

    original_config = llm_analysis.llm_config
    original_urlopen = llm_analysis.urllib.request.urlopen
    original_retry_count = llm_analysis.retry_count
    try:
        llm_analysis.llm_config = lambda: ("test-key", "https://provider.example/v1", "test-model")
        llm_analysis.retry_count = lambda: 0

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse()

        llm_analysis.urllib.request.urlopen = fake_urlopen
        response = llm_analysis.call_chat_completion_raw_with_prompts(
            "system",
            "user",
            truncate_user_prompt=False,
            temperature_override=0,
        )
    finally:
        llm_analysis.llm_config = original_config
        llm_analysis.urllib.request.urlopen = original_urlopen
        llm_analysis.retry_count = original_retry_count

    assert response.content == '{"ok":true}'
    assert response.model == "test-model"
    assert response.provider == "provider.example"
    assert response.response_id == "provider-response-1"
    assert response.usage == {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168}
    assert response.attempts == 1
    assert response.elapsed_seconds >= 0
    assert captured["payload"]["temperature"] == 0
    assert captured["payload"]["messages"][1]["content"] == "user"


def test_glm_provider_uses_dedicated_fixed_connection_and_fails_closed_without_key() -> None:
    names = (
        "SURVEIL_DISABLE_LLM",
        "LLM_PROVIDER",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_GLM_API_KEY",
    )
    original = {name: os.environ.get(name) for name in names}
    try:
        os.environ.pop("SURVEIL_DISABLE_LLM", None)
        os.environ["LLM_PROVIDER"] = "zhipu_glm"
        os.environ["LLM_API_KEY"] = "deepseek-key-must-not-be-used"
        os.environ["LLM_BASE_URL"] = "https://api.deepseek.com"
        os.environ["LLM_MODEL"] = "deepseek-chat"
        os.environ["LLM_GLM_API_KEY"] = "glm-key"
        assert llm_analysis.llm_config() == (
            "glm-key",
            "https://open.bigmodel.cn/api/paas/v4",
            "glm-5.3-flash",
        )

        os.environ.pop("LLM_GLM_API_KEY")
        assert llm_analysis.llm_config() is None
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_glm_request_forces_supported_response_preferences() -> None:
    names = ("LLM_THINKING_TYPE", "LLM_RESPONSE_FORMAT_JSON")
    original = {name: os.environ.get(name) for name in names}
    original_config = llm_analysis.llm_config
    original_urlopen = llm_analysis.urllib.request.urlopen
    original_retry_count = llm_analysis.retry_count
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"id":"glm-response","choices":[{"message":{"content":"{\\"ok\\":true}"}}]}'

    try:
        os.environ["LLM_THINKING_TYPE"] = "disabled"
        os.environ["LLM_RESPONSE_FORMAT_JSON"] = "0"
        llm_analysis.llm_config = lambda: (
            "glm-key",
            "https://open.bigmodel.cn/api/paas/v4",
            "glm-5.3-flash",
        )
        llm_analysis.retry_count = lambda: 0

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        llm_analysis.urllib.request.urlopen = fake_urlopen
        llm_analysis.call_chat_completion_raw_with_prompts(
            "system",
            "user",
            thinking_override="disabled",
        )
    finally:
        llm_analysis.llm_config = original_config
        llm_analysis.urllib.request.urlopen = original_urlopen
        llm_analysis.retry_count = original_retry_count
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    assert captured["payload"]["thinking"] == {"type": "enabled"}
    assert captured["payload"]["reasoning_effort"] == "low"
    assert captured["payload"]["response_format"] == {"type": "json_object"}


def test_qwen_bailian_provider_uses_dedicated_connection_and_fails_closed_without_key() -> None:
    names = (
        "SURVEIL_DISABLE_LLM",
        "LLM_PROVIDER",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_QWEN_API_KEY",
        "LLM_QWEN_BASE_URL",
    )
    original = {name: os.environ.get(name) for name in names}
    try:
        os.environ.pop("SURVEIL_DISABLE_LLM", None)
        os.environ["LLM_PROVIDER"] = "qwen_flash_snapshot"
        os.environ["LLM_API_KEY"] = "deepseek-key-must-not-be-used"
        os.environ["LLM_BASE_URL"] = "https://api.deepseek.com"
        os.environ["LLM_MODEL"] = "deepseek-chat"
        os.environ["LLM_QWEN_API_KEY"] = "qwen-key"
        assert llm_analysis.llm_config() == (
            "qwen-key",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen3.7-flash-2026-07-15",
        )
        # 快照模型额度用尽后按百炼免费额度回退链依次切换。
        assert llm_analysis.llm_fallback_configs() == [
            (
                "qwen-key",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                model,
            )
            for model in (
                "qwen3.7-flash",
                "qwen3.8-2.4t-a95b",
                "kimi-k3",
                "glm-5.3",
                "deepseek-v4.1-flash",
            )
        ]

        os.environ["LLM_QWEN_BASE_URL"] = "https://space.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
        assert llm_analysis.llm_config()[1] == (
            "https://space.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
        )

        os.environ["LLM_PROVIDER"] = "qwen_flash"
        assert llm_analysis.llm_config()[2] == "qwen3.7-flash"
        assert [connection[2] for connection in llm_analysis.llm_fallback_configs()] == [
            "qwen3.8-2.4t-a95b",
            "kimi-k3",
            "glm-5.3",
            "deepseek-v4.1-flash",
        ]

        os.environ.pop("LLM_QWEN_API_KEY")
        assert llm_analysis.llm_config() is None
        assert llm_analysis.llm_fallback_configs() == []
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_glm_bailian_provider_uses_bailian_connection_and_chain_fallback() -> None:
    names = (
        "SURVEIL_DISABLE_LLM",
        "LLM_PROVIDER",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_GLM_API_KEY",
        "LLM_QWEN_API_KEY",
        "LLM_QWEN_BASE_URL",
    )
    original = {name: os.environ.get(name) for name in names}
    try:
        os.environ.pop("SURVEIL_DISABLE_LLM", None)
        os.environ["LLM_PROVIDER"] = "glm_bailian"
        os.environ["LLM_API_KEY"] = "deepseek-key-must-not-be-used"
        os.environ["LLM_BASE_URL"] = "https://api.deepseek.com"
        os.environ["LLM_MODEL"] = "deepseek-chat"
        os.environ["LLM_GLM_API_KEY"] = "zhipu-key-must-not-be-used"
        os.environ["LLM_QWEN_API_KEY"] = "bailian-key"
        assert llm_analysis.llm_config() == (
            "bailian-key",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "glm-5.3",
        )
        # 百炼 GLM 在免费额度回退链上，额度用尽后切换到链的下一档。
        assert llm_analysis.llm_fallback_configs() == [
            (
                "bailian-key",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "deepseek-v4.1-flash",
            )
        ]

        os.environ["LLM_QWEN_BASE_URL"] = "https://space.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
        assert llm_analysis.llm_config()[1] == (
            "https://space.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
        )
        assert [connection[1] for connection in llm_analysis.llm_fallback_configs()] == [
            "https://space.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
        ]

        os.environ.pop("LLM_QWEN_API_KEY")
        assert llm_analysis.llm_config() is None
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_glm_bailian_request_uses_bailian_response_preferences() -> None:
    names = ("LLM_THINKING_TYPE", "LLM_RESPONSE_FORMAT_JSON")
    original = {name: os.environ.get(name) for name in names}
    try:
        os.environ.pop("LLM_THINKING_TYPE", None)
        os.environ.pop("LLM_RESPONSE_FORMAT_JSON", None)
        preferences = llm_analysis.llm_response_preferences(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="glm-5.3",
        )
        assert preferences["enable_thinking"] is False
        assert preferences["response_format"] == {"type": "json_object"}
        # 百炼端点不接受智谱官方的 thinking 对象 / reasoning_effort 参数。
        assert "thinking" not in preferences
        assert "reasoning_effort" not in preferences
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_qwen_bailian_request_uses_supported_response_preferences() -> None:
    names = ("LLM_THINKING_TYPE", "LLM_RESPONSE_FORMAT_JSON")
    original = {name: os.environ.get(name) for name in names}
    original_config = llm_analysis.llm_config
    original_urlopen = llm_analysis.urllib.request.urlopen
    original_retry_count = llm_analysis.retry_count
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"id":"qwen-response","choices":[{"message":{"content":"{\\"ok\\":true}"}}]}'

    try:
        os.environ.pop("LLM_THINKING_TYPE", None)
        os.environ.pop("LLM_RESPONSE_FORMAT_JSON", None)
        llm_analysis.llm_config = lambda: (
            "qwen-key",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen3.7-flash-2026-07-15",
        )
        llm_analysis.retry_count = lambda: 0

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        llm_analysis.urllib.request.urlopen = fake_urlopen
        llm_analysis.call_chat_completion_raw_with_prompts("system", "user")
    finally:
        llm_analysis.llm_config = original_config
        llm_analysis.urllib.request.urlopen = original_urlopen
        llm_analysis.retry_count = original_retry_count
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    assert captured["payload"]["enable_thinking"] is False
    assert "thinking" not in captured["payload"]
    assert captured["payload"]["response_format"] == {"type": "json_object"}


def test_balance_insufficient_falls_back_to_qwen_flash_stable_model() -> None:
    original_config = llm_analysis.llm_config
    original_fallback = llm_analysis.llm_fallback_configs
    original_urlopen = llm_analysis.urllib.request.urlopen
    original_retry_count = llm_analysis.retry_count
    requested_models: list[str] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"id":"qwen-fallback","choices":[{"message":{"content":"{\\"ok\\":true}"}}]}'

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        requested_models.append(payload["model"])
        if payload["model"] == "qwen3.7-flash-2026-07-15":
            raise llm_analysis.urllib.error.HTTPError(
                "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                400,
                "Bad Request",
                {},
                io.BytesIO(
                    json.dumps(
                        {"error": {"code": "Arrearage", "message": "Insufficient balance."}}
                    ).encode("utf-8")
                ),
            )
        return FakeResponse()

    try:
        llm_analysis.llm_config = lambda: (
            "qwen-key",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen3.7-flash-2026-07-15",
        )
        llm_analysis.llm_fallback_configs = lambda: [
            (
                "qwen-key",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "qwen3.7-flash",
            )
        ]
        llm_analysis.retry_count = lambda: 0
        llm_analysis.urllib.request.urlopen = fake_urlopen
        response = llm_analysis.call_chat_completion_raw_with_prompts("system", "user")
    finally:
        llm_analysis.llm_config = original_config
        llm_analysis.llm_fallback_configs = original_fallback
        llm_analysis.urllib.request.urlopen = original_urlopen
        llm_analysis.retry_count = original_retry_count

    assert requested_models == ["qwen3.7-flash-2026-07-15", "qwen3.7-flash"]
    assert response.model == "qwen3.7-flash"


def test_retired_qwen38_providers_fall_back_from_chain_head() -> None:
    names = ("SURVEIL_DISABLE_LLM", "LLM_PROVIDER", "LLM_QWEN_API_KEY", "LLM_QWEN_BASE_URL")
    original = {name: os.environ.get(name) for name in names}
    try:
        os.environ.pop("SURVEIL_DISABLE_LLM", None)
        os.environ["LLM_PROVIDER"] = "qwen38_max"
        os.environ["LLM_QWEN_API_KEY"] = "qwen-key"
        os.environ.pop("LLM_QWEN_BASE_URL", None)
        assert llm_analysis.llm_config() == (
            "qwen-key",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen3.8-max",
        )
        # 免费额度已用完的旧 qwen3.8 模型不再进入回退链：
        # 额度报错后从百炼免费额度回退链头部开始切换。
        assert [connection[2] for connection in llm_analysis.llm_fallback_configs()] == [
            "qwen3.7-flash-2026-07-15",
            "qwen3.7-flash",
            "qwen3.8-2.4t-a95b",
            "kimi-k3",
            "glm-5.3",
            "deepseek-v4.1-flash",
        ]

        os.environ["LLM_PROVIDER"] = "qwen38_flash"
        assert llm_analysis.llm_config()[2] == "qwen3.8-flash"
        assert [connection[2] for connection in llm_analysis.llm_fallback_configs()] == [
            "qwen3.7-flash-2026-07-15",
            "qwen3.7-flash",
            "qwen3.8-2.4t-a95b",
            "kimi-k3",
            "glm-5.3",
            "deepseek-v4.1-flash",
        ]

        os.environ.pop("LLM_QWEN_API_KEY")
        assert llm_analysis.llm_config() is None
        assert llm_analysis.llm_fallback_configs() == []
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_kimi_and_deepseek_v41_flash_sit_at_bailian_chain_tail() -> None:
    names = ("SURVEIL_DISABLE_LLM", "LLM_PROVIDER", "LLM_QWEN_API_KEY", "LLM_QWEN_BASE_URL")
    original = {name: os.environ.get(name) for name in names}
    try:
        os.environ.pop("SURVEIL_DISABLE_LLM", None)
        os.environ["LLM_PROVIDER"] = "kimi_k3"
        os.environ["LLM_QWEN_API_KEY"] = "qwen-key"
        os.environ.pop("LLM_QWEN_BASE_URL", None)
        assert llm_analysis.llm_config() == (
            "qwen-key",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "kimi-k3",
        )
        assert [connection[2] for connection in llm_analysis.llm_fallback_configs()] == [
            "glm-5.3",
            "deepseek-v4.1-flash",
        ]

        # 链尾模型额度用尽后没有后续备用模型，保持关闭式失败。
        os.environ["LLM_PROVIDER"] = "deepseek_v41_flash"
        assert llm_analysis.llm_config()[2] == "deepseek-v4.1-flash"
        assert llm_analysis.llm_fallback_configs() == []
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_ark_providers_use_dedicated_connection_and_fails_closed_without_key() -> None:
    names = ("SURVEIL_DISABLE_LLM", "LLM_PROVIDER", "LLM_ARK_API_KEY", "LLM_ARK_BASE_URL")
    original = {name: os.environ.get(name) for name in names}
    try:
        os.environ.pop("SURVEIL_DISABLE_LLM", None)
        os.environ["LLM_PROVIDER"] = "doubao_seed_21_lite"
        os.environ["LLM_ARK_API_KEY"] = "ark-key"
        os.environ.pop("LLM_ARK_BASE_URL", None)
        assert llm_analysis.llm_config() == (
            "ark-key",
            "https://ark.cn-beijing.volces.com/api/v3",
            "doubao-seed-2-1-lite-260915",
        )
        # 方舟链首模型额度用尽后按开通页顺序切换后续方舟模型；百炼未配置时不参与。
        assert [connection[2] for connection in llm_analysis.llm_fallback_configs()] == [
            "deepseek-v4-1-flash-260910",
            "glm-5-3-flash-260828",
            "deepseek-v4-pro-ga-260813",
            "deepseek-v4-flash-ga-260731",
            "doubao-seed-2-0-pro-260215",
        ]

        # 链尾模型额度用尽后没有后续备用模型，保持关闭式失败。
        os.environ["LLM_PROVIDER"] = "doubao_seed_20_pro"
        assert llm_analysis.llm_config()[2] == "doubao-seed-2-0-pro-260215"
        assert llm_analysis.llm_fallback_configs() == []

        os.environ.pop("LLM_ARK_API_KEY")
        assert llm_analysis.llm_config() is None
        assert llm_analysis.llm_fallback_configs() == []
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_bailian_chain_falls_back_to_ark_tail() -> None:
    names = ("SURVEIL_DISABLE_LLM", "LLM_PROVIDER", "LLM_QWEN_API_KEY", "LLM_ARK_API_KEY")
    original = {name: os.environ.get(name) for name in names}
    try:
        os.environ.pop("SURVEIL_DISABLE_LLM", None)
        os.environ["LLM_PROVIDER"] = "qwen_flash_snapshot"
        os.environ["LLM_QWEN_API_KEY"] = "qwen-key"
        os.environ["LLM_ARK_API_KEY"] = "ark-key"
        configs = llm_analysis.llm_fallback_configs()
        assert [connection[2] for connection in configs] == [
            "qwen3.7-flash",
            "qwen3.8-2.4t-a95b",
            "kimi-k3",
            "glm-5.3",
            "deepseek-v4.1-flash",
            "doubao-seed-2-1-lite-260915",
            "deepseek-v4-1-flash-260910",
            "glm-5-3-flash-260828",
            "deepseek-v4-pro-ga-260813",
            "deepseek-v4-flash-ga-260731",
            "doubao-seed-2-0-pro-260215",
        ]
        # 百炼档共用千问连接，方舟档使用独立的方舟连接。
        assert [connection[0] for connection in configs[:5]] == ["qwen-key"] * 5
        assert [connection[0] for connection in configs[5:]] == ["ark-key"] * 6
        assert [connection[1] for connection in configs[5:]] == [
            "https://ark.cn-beijing.volces.com/api/v3"
        ] * 6

        # 链尾百炼模型额度用尽后继续走方舟各档。
        os.environ["LLM_PROVIDER"] = "deepseek_v41_flash"
        assert [connection[2] for connection in llm_analysis.llm_fallback_configs()] == [
            "doubao-seed-2-1-lite-260915",
            "deepseek-v4-1-flash-260910",
            "glm-5-3-flash-260828",
            "deepseek-v4-pro-ga-260813",
            "deepseek-v4-flash-ga-260731",
            "doubao-seed-2-0-pro-260215",
        ]

        # 未配置方舟 Key 时保持原有百炼链行为。
        os.environ["LLM_PROVIDER"] = "qwen_flash_snapshot"
        os.environ.pop("LLM_ARK_API_KEY")
        assert [connection[2] for connection in llm_analysis.llm_fallback_configs()] == [
            "qwen3.7-flash",
            "qwen3.8-2.4t-a95b",
            "kimi-k3",
            "glm-5.3",
            "deepseek-v4.1-flash",
        ]
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_ark_quota_error_markers_trigger_balance_fallback() -> None:
    # 方舟安心体验模式免费额度用尽：429 SetLimitExceeded。
    assert llm_analysis.is_balance_insufficient(
        json.dumps(
            {
                "error": {
                    "code": "SetLimitExceeded",
                    "message": "Your account has reached the set usage limit for the model, "
                    "and the model service has been paused.",
                }
            }
        ),
        429,
    )
    # 方舟账号欠费：403 AccountOverdueError。
    assert llm_analysis.is_balance_insufficient(
        json.dumps(
            {
                "error": {
                    "code": "AccountOverdueError",
                    "message": "当前账号欠费。",
                }
            }
        ),
        403,
    )
    # 方舟限流不是额度耗尽，不触发回退。
    assert not llm_analysis.is_balance_insufficient(
        json.dumps(
            {
                "error": {
                    "code": "RateLimitExceeded.EndpointTPMExceeded",
                    "message": "TPM exceeded.",
                }
            }
        ),
        429,
    )


def test_bailian_exhaustion_walks_chain_into_ark_connection() -> None:
    original_config = llm_analysis.llm_config
    original_fallback = llm_analysis.llm_fallback_configs
    original_urlopen = llm_analysis.urllib.request.urlopen
    original_retry_count = llm_analysis.retry_count
    requested: list[tuple[str, str, str]] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"id":"ark-fallback","choices":[{"message":{"content":"{\\"ok\\":true}"}}]}'

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        requested.append(
            (
                payload["model"],
                request.full_url,
                request.get_header("Authorization") or "",
            )
        )
        if "dashscope.aliyuncs.com" in request.full_url:
            raise llm_analysis.urllib.error.HTTPError(
                request.full_url,
                403,
                "Forbidden",
                {},
                io.BytesIO(
                    json.dumps(
                        {"error": {"code": "AllocationQuota.FreeTierOnly", "message": "Free quota exhausted."}}
                    ).encode("utf-8")
                ),
            )
        return FakeResponse()

    try:
        llm_analysis.llm_config = lambda: (
            "qwen-key",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen3.7-flash-2026-07-15",
        )
        llm_analysis.llm_fallback_configs = lambda: [
            (
                "qwen-key",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "qwen3.7-flash",
            ),
            (
                "ark-key",
                "https://ark.cn-beijing.volces.com/api/v3",
                "doubao-seed-2-1-lite-260915",
            ),
        ]
        llm_analysis.retry_count = lambda: 0
        llm_analysis.urllib.request.urlopen = fake_urlopen
        response = llm_analysis.call_chat_completion_raw_with_prompts("system", "user")
    finally:
        llm_analysis.llm_config = original_config
        llm_analysis.llm_fallback_configs = original_fallback
        llm_analysis.urllib.request.urlopen = original_urlopen
        llm_analysis.retry_count = original_retry_count

    # 百炼档与方舟档各自使用自己的端点和密钥。
    assert requested == [
        (
            "qwen3.7-flash-2026-07-15",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            "Bearer qwen-key",
        ),
        (
            "qwen3.7-flash",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            "Bearer qwen-key",
        ),
        (
            "doubao-seed-2-1-lite-260915",
            "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
            "Bearer ark-key",
        ),
    ]
    assert response.model == "doubao-seed-2-1-lite-260915"


def test_bailian_hosted_deepseek_falls_back_to_bailian_chain() -> None:
    names = (
        "SURVEIL_DISABLE_LLM",
        "LLM_PROVIDER",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_QWEN_API_KEY",
        "LLM_QWEN_BASE_URL",
    )
    original = {name: os.environ.get(name) for name in names}
    try:
        os.environ.pop("SURVEIL_DISABLE_LLM", None)
        os.environ["LLM_PROVIDER"] = "deepseek"
        os.environ["LLM_API_KEY"] = "bailian-deepseek-key"
        os.environ["LLM_BASE_URL"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        os.environ["LLM_MODEL"] = "deepseek-v4-pro-0813"
        os.environ["LLM_QWEN_API_KEY"] = "qwen-key"
        os.environ.pop("LLM_QWEN_BASE_URL", None)
        assert llm_analysis.llm_config() == (
            "bailian-deepseek-key",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "deepseek-v4-pro-0813",
        )
        # 百炼托管的 DeepSeek 额度用尽时，从百炼免费额度回退链头部开始切换，
        # 并使用单独的千问连接而不是 DeepSeek 自己的密钥。
        assert llm_analysis.llm_fallback_configs() == [
            (
                "qwen-key",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                model,
            )
            for model in (
                "qwen3.7-flash-2026-07-15",
                "qwen3.7-flash",
                "qwen3.8-2.4t-a95b",
                "kimi-k3",
                "glm-5.3",
                "deepseek-v4.1-flash",
            )
        ]

        os.environ["LLM_QWEN_BASE_URL"] = "https://space.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
        assert [connection[1] for connection in llm_analysis.llm_fallback_configs()] == [
            "https://space.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
        ] * 6

        # 官方 DeepSeek 端点不回退百炼模型。
        os.environ["LLM_BASE_URL"] = "https://api.deepseek.com"
        assert llm_analysis.llm_fallback_configs() == []

        # 缺少千问密钥时保持关闭式失败。
        os.environ["LLM_BASE_URL"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        os.environ.pop("LLM_QWEN_API_KEY")
        assert llm_analysis.llm_fallback_configs() == []
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_balance_insufficient_walks_qwen38_fallback_chain() -> None:
    original_config = llm_analysis.llm_config
    original_fallback = llm_analysis.llm_fallback_configs
    original_urlopen = llm_analysis.urllib.request.urlopen
    original_retry_count = llm_analysis.retry_count
    requested_models: list[str] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"id":"qwen38-fallback","choices":[{"message":{"content":"{\\"ok\\":true}"}}]}'

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        requested_models.append(payload["model"])
        if payload["model"] in {"qwen3.8-max", "qwen3.8-2.4t-a95b", "qwen3.8-27b"}:
            raise llm_analysis.urllib.error.HTTPError(
                "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                400,
                "Bad Request",
                {},
                io.BytesIO(
                    json.dumps(
                        {"error": {"code": "Arrearage", "message": "Insufficient balance."}}
                    ).encode("utf-8")
                ),
            )
        return FakeResponse()

    try:
        llm_analysis.llm_config = lambda: (
            "qwen-key",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen3.8-max",
        )
        llm_analysis.llm_fallback_configs = lambda: [
            ("qwen-key", "https://dashscope.aliyuncs.com/compatible-mode/v1", model)
            for model in ("qwen3.8-2.4t-a95b", "qwen3.8-27b", "qwen3.8-flash", "qwen3.8-max-0902")
        ]
        llm_analysis.retry_count = lambda: 0
        llm_analysis.urllib.request.urlopen = fake_urlopen
        response = llm_analysis.call_chat_completion_raw_with_prompts("system", "user")
    finally:
        llm_analysis.llm_config = original_config
        llm_analysis.llm_fallback_configs = original_fallback
        llm_analysis.urllib.request.urlopen = original_urlopen
        llm_analysis.retry_count = original_retry_count

    assert requested_models == ["qwen3.8-max", "qwen3.8-2.4t-a95b", "qwen3.8-27b", "qwen3.8-flash"]
    assert response.model == "qwen3.8-flash"


def test_bailian_free_quota_exhausted_falls_back_along_chain() -> None:
    names = (
        "SURVEIL_DISABLE_LLM",
        "LLM_PROVIDER",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_QWEN_API_KEY",
        "LLM_QWEN_BASE_URL",
    )
    original = {name: os.environ.get(name) for name in names}
    original_config = llm_analysis.llm_config
    original_fallback = llm_analysis.llm_fallback_configs
    original_urlopen = llm_analysis.urllib.request.urlopen
    original_retry_count = llm_analysis.retry_count
    requested_models: list[str] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"id":"qwen-fallback","choices":[{"message":{"content":"{\\"ok\\":true}"}}]}'

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        requested_models.append(payload["model"])
        if payload["model"] != "qwen3.7-flash":
            raise llm_analysis.urllib.error.HTTPError(
                "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                403,
                "Forbidden",
                {},
                io.BytesIO(
                    json.dumps(
                        {
                            "error": {
                                "message": 'Free quota exhausted. To continue accessing the model on a paid basis, please add funds or disable the "use free tier only" mode in the management console.',
                                "code": "AllocationQuota.FreeTierOnly",
                            }
                        }
                    ).encode("utf-8")
                ),
            )
        return FakeResponse()

    try:
        # 不打桩回退链：快照模型额度用尽后按真实回退链切换到稳定版。
        os.environ.pop("SURVEIL_DISABLE_LLM", None)
        os.environ["LLM_PROVIDER"] = "qwen_flash_snapshot"
        os.environ["LLM_API_KEY"] = "deepseek-key-must-not-be-used"
        os.environ["LLM_BASE_URL"] = "https://api.deepseek.com"
        os.environ["LLM_MODEL"] = "deepseek-chat"
        os.environ["LLM_QWEN_API_KEY"] = "qwen-key"
        os.environ.pop("LLM_QWEN_BASE_URL", None)
        llm_analysis.retry_count = lambda: 0
        llm_analysis.urllib.request.urlopen = fake_urlopen
        response = llm_analysis.call_chat_completion_raw_with_prompts("system", "user")
    finally:
        llm_analysis.llm_config = original_config
        llm_analysis.llm_fallback_configs = original_fallback
        llm_analysis.urllib.request.urlopen = original_urlopen
        llm_analysis.retry_count = original_retry_count
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    assert requested_models == ["qwen3.7-flash-2026-07-15", "qwen3.7-flash"]
    assert response.model == "qwen3.7-flash"


def test_balance_insufficient_without_fallback_model_still_fails_closed() -> None:
    original_config = llm_analysis.llm_config
    original_fallback = llm_analysis.llm_fallback_configs
    original_urlopen = llm_analysis.urllib.request.urlopen
    original_retry_count = llm_analysis.retry_count
    requested_models: list[str] = []

    def fake_urlopen(request, timeout):
        requested_models.append(json.loads(request.data.decode("utf-8"))["model"])
        raise llm_analysis.urllib.error.HTTPError(
            "https://provider.example/v1/chat/completions",
            402,
            "Payment Required",
            {},
            io.BytesIO(b'{"error":{"message":"Insufficient balance."}}'),
        )

    try:
        llm_analysis.llm_config = lambda: ("key", "https://provider.example/v1", "deepseek-chat")
        llm_analysis.llm_fallback_configs = lambda: []
        llm_analysis.retry_count = lambda: 0
        llm_analysis.urllib.request.urlopen = fake_urlopen
        try:
            llm_analysis.call_chat_completion_raw_with_prompts("system", "user")
        except llm_analysis.LLMBalanceInsufficientError:
            pass
        else:
            raise AssertionError("balance failure without fallback must fail closed")
    finally:
        llm_analysis.llm_config = original_config
        llm_analysis.llm_fallback_configs = original_fallback
        llm_analysis.urllib.request.urlopen = original_urlopen
        llm_analysis.retry_count = original_retry_count

    assert requested_models == ["deepseek-chat"]


def test_bailian_thinking_only_model_forces_enable_thinking() -> None:
    names = ("LLM_THINKING_TYPE", "LLM_RESPONSE_FORMAT_JSON")
    original = {name: os.environ.get(name) for name in names}
    try:
        os.environ.pop("LLM_THINKING_TYPE", None)
        os.environ.pop("LLM_RESPONSE_FORMAT_JSON", None)
        preferences = llm_analysis.llm_response_preferences(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="qwen3.8-2.4t-a95b",
        )
        # qwen3.8-2.4t-a95b 仅支持思考模式，enable_thinking=false 会被端点 400 拒绝。
        assert preferences["enable_thinking"] is True
        assert preferences["response_format"] == {"type": "json_object"}
        assert "thinking" not in preferences
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_bailian_fixed_temperature_models_normalize_temperature() -> None:
    names = ("LLM_THINKING_TYPE", "LLM_RESPONSE_FORMAT_JSON")
    original = {name: os.environ.get(name) for name in names}
    try:
        os.environ.pop("LLM_THINKING_TYPE", None)
        os.environ.pop("LLM_RESPONSE_FORMAT_JSON", None)
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        # kimi-k3 只接受固定 temperature：0.1 会被端点以 HTTP 400 拒绝。
        payload = {"model": "kimi-k3", "temperature": 0.1}
        llm_analysis.apply_llm_response_preferences(payload, base_url=base_url, model="kimi-k3")
        assert payload["temperature"] == BAILIAN_FIXED_TEMPERATURE_MODELS["kimi-k3"]
        # 其它模型保留调用方自己的取值。
        other = {"model": "qwen3.8-max", "temperature": 0.1}
        llm_analysis.apply_llm_response_preferences(other, base_url=base_url, model="qwen3.8-max")
        assert other["temperature"] == 0.1
        # payload 未带 temperature 时不新增字段。
        bare: dict[str, object] = {"model": "kimi-k3"}
        llm_analysis.apply_llm_response_preferences(bare, base_url=base_url, model="kimi-k3")
        assert "temperature" not in bare
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_provider_error_code_extracts_bounded_code() -> None:
    body = json.dumps({"error": {"code": "invalid_parameter_error", "message": "boom"}})
    assert llm_analysis.provider_error_code(body) == "invalid_parameter_error"
    assert llm_analysis.provider_error_code(json.dumps({"error": {"code": "y" * 300}})) == "y" * 200
    assert llm_analysis.provider_error_code("not-json") == ""
    assert llm_analysis.provider_error_code('{"error": "oops"}') == ""
    assert llm_analysis.provider_error_code("[1,2]") == ""


def test_fallback_chain_skips_request_error_fallback_model() -> None:
    original_config = llm_analysis.llm_config
    original_fallback = llm_analysis.llm_fallback_configs
    original_urlopen = llm_analysis.urllib.request.urlopen
    original_retry_count = llm_analysis.retry_count
    requested_models: list[str] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"id":"qwen38-skip","choices":[{"message":{"content":"{\\"ok\\":true}"}}]}'

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        requested_models.append(payload["model"])
        if payload["model"] == "qwen3.8-max":
            raise llm_analysis.urllib.error.HTTPError(
                "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                400,
                "Bad Request",
                {},
                io.BytesIO(
                    json.dumps(
                        {"error": {"code": "Arrearage", "message": "Insufficient balance."}}
                    ).encode("utf-8")
                ),
            )
        if payload["model"] == "qwen3.8-2.4t-a95b":
            raise llm_analysis.urllib.error.HTTPError(
                "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                400,
                "Bad Request",
                {},
                io.BytesIO(
                    json.dumps(
                        {
                            "error": {
                                "message": "<400> InternalError.Algo.InvalidParameter: The value of the enable_thinking parameter is restricted to True.",
                                "code": "invalid_parameter_error",
                            }
                        }
                    ).encode("utf-8")
                ),
            )
        return FakeResponse()

    try:
        llm_analysis.llm_config = lambda: (
            "qwen-key",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen3.8-max",
        )
        llm_analysis.llm_fallback_configs = lambda: [
            ("qwen-key", "https://dashscope.aliyuncs.com/compatible-mode/v1", model)
            for model in ("qwen3.8-2.4t-a95b", "qwen3.8-27b", "qwen3.8-flash")
        ]
        llm_analysis.retry_count = lambda: 0
        llm_analysis.urllib.request.urlopen = fake_urlopen
        response = llm_analysis.call_chat_completion_raw_with_prompts("system", "user")
    finally:
        llm_analysis.llm_config = original_config
        llm_analysis.llm_fallback_configs = original_fallback
        llm_analysis.urllib.request.urlopen = original_urlopen
        llm_analysis.retry_count = original_retry_count

    # 备用模型的参数类错误只跳过该模型，链上后续有余额的模型继续被尝试。
    assert requested_models == ["qwen3.8-max", "qwen3.8-2.4t-a95b", "qwen3.8-27b"]
    assert response.model == "qwen3.8-27b"


def test_fallback_chain_exhaustion_reraises_original_balance_error() -> None:
    original_config = llm_analysis.llm_config
    original_fallback = llm_analysis.llm_fallback_configs
    original_urlopen = llm_analysis.urllib.request.urlopen
    original_retry_count = llm_analysis.retry_count
    requested_models: list[str] = []

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        requested_models.append(payload["model"])
        if payload["model"] == "qwen3.8-max":
            raise llm_analysis.urllib.error.HTTPError(
                "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                403,
                "Forbidden",
                {},
                io.BytesIO(
                    json.dumps(
                        {
                            "error": {
                                "message": "Free quota exhausted.",
                                "code": "AllocationQuota.FreeTierOnly",
                            }
                        }
                    ).encode("utf-8")
                ),
            )
        raise llm_analysis.urllib.error.HTTPError(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            400,
            "Bad Request",
            {},
            io.BytesIO(
                json.dumps(
                    {
                        "error": {
                            "message": "InternalError.Algo.InvalidParameter.",
                            "code": "invalid_parameter_error",
                        }
                    }
                ).encode("utf-8")
            ),
        )

    try:
        llm_analysis.llm_config = lambda: (
            "qwen-key",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen3.8-max",
        )
        llm_analysis.llm_fallback_configs = lambda: [
            ("qwen-key", "https://dashscope.aliyuncs.com/compatible-mode/v1", model)
            for model in ("qwen3.8-2.4t-a95b", "qwen3.8-27b")
        ]
        llm_analysis.retry_count = lambda: 0
        llm_analysis.urllib.request.urlopen = fake_urlopen
        try:
            llm_analysis.call_chat_completion_raw_with_prompts("system", "user")
        except llm_analysis.LLMBalanceInsufficientError as exc:
            assert exc.http_status == 403
            assert exc.error_code == "AllocationQuota.FreeTierOnly"
        else:
            raise AssertionError("chain exhaustion must re-raise the original balance error")
    finally:
        llm_analysis.llm_config = original_config
        llm_analysis.llm_fallback_configs = original_fallback
        llm_analysis.urllib.request.urlopen = original_urlopen
        llm_analysis.retry_count = original_retry_count

    assert requested_models == ["qwen3.8-max", "qwen3.8-2.4t-a95b", "qwen3.8-27b"]


def test_hard_deadline_fallback_chain_skips_request_error_model() -> None:
    original_config = llm_analysis.llm_config
    original_fallback = llm_analysis.llm_fallback_configs
    original_client = llm_analysis.httpx.AsyncClient
    original_retries = llm_analysis.retry_count
    requested_models: list[str] = []
    model_bodies = {
        "qwen3.8-max": (
            403,
            json.dumps(
                {
                    "error": {
                        "message": "Free quota exhausted.",
                        "code": "AllocationQuota.FreeTierOnly",
                    }
                }
            ),
        ),
        "qwen3.8-2.4t-a95b": (
            400,
            json.dumps(
                {
                    "error": {
                        "message": "InternalError.Algo.InvalidParameter: enable_thinking is restricted to True.",
                        "code": "invalid_parameter_error",
                    }
                }
            ),
        ),
        "qwen3.8-27b": (
            200,
            json.dumps({"id": "ok", "choices": [{"message": {"content": '{"ok":true}'}}]}),
        ),
    }

    class FakeResponse:
        def __init__(self, status_code, text):
            self.status_code = status_code
            self.text = text
            self.is_error = status_code >= 400

        def json(self):
            return json.loads(self.text)

    # production 调用形态是 client.post(url, json=payload, headers=headers)。
    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] is None
            assert kwargs["trust_env"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, *, json=None, headers=None):
            model = json["model"]
            requested_models.append(model)
            status_code, body = model_bodies[model]
            return FakeResponse(status_code, body)

    try:
        llm_analysis.llm_config = lambda: (
            "qwen-key",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen3.8-max",
        )
        llm_analysis.llm_fallback_configs = lambda: [
            ("qwen-key", "https://dashscope.aliyuncs.com/compatible-mode/v1", model)
            for model in ("qwen3.8-2.4t-a95b", "qwen3.8-27b")
        ]
        llm_analysis.httpx.AsyncClient = FakeClient
        llm_analysis.retry_count = lambda: 0
        response = llm_analysis.call_chat_completion_raw_with_prompts_hard_deadline(
            "system",
            "user",
            deadline_monotonic=time.monotonic() + 30,
        )
    finally:
        llm_analysis.llm_config = original_config
        llm_analysis.llm_fallback_configs = original_fallback
        llm_analysis.httpx.AsyncClient = original_client
        llm_analysis.retry_count = original_retries

    assert requested_models == ["qwen3.8-max", "qwen3.8-2.4t-a95b", "qwen3.8-27b"]
    assert response.model == "qwen3.8-27b"


def main() -> int:
    test_raw_chat_completion_returns_bounded_usage_metadata()
    test_glm_provider_uses_dedicated_fixed_connection_and_fails_closed_without_key()
    test_glm_request_forces_supported_response_preferences()
    test_qwen_bailian_provider_uses_dedicated_connection_and_fails_closed_without_key()
    test_glm_bailian_provider_uses_bailian_connection_and_chain_fallback()
    test_qwen_bailian_request_uses_supported_response_preferences()
    test_bailian_thinking_only_model_forces_enable_thinking()
    test_bailian_fixed_temperature_models_normalize_temperature()
    test_provider_error_code_extracts_bounded_code()
    test_balance_insufficient_falls_back_to_qwen_flash_stable_model()
    test_retired_qwen38_providers_fall_back_from_chain_head()
    test_kimi_and_deepseek_v41_flash_sit_at_bailian_chain_tail()
    test_ark_providers_use_dedicated_connection_and_fails_closed_without_key()
    test_bailian_chain_falls_back_to_ark_tail()
    test_ark_quota_error_markers_trigger_balance_fallback()
    test_bailian_exhaustion_walks_chain_into_ark_connection()
    test_bailian_hosted_deepseek_falls_back_to_bailian_chain()
    test_balance_insufficient_walks_qwen38_fallback_chain()
    test_bailian_free_quota_exhausted_falls_back_along_chain()
    test_fallback_chain_skips_request_error_fallback_model()
    test_fallback_chain_exhaustion_reraises_original_balance_error()
    test_hard_deadline_fallback_chain_skips_request_error_model()
    test_balance_insufficient_without_fallback_model_still_fails_closed()
    if analyze_with_llm("AI ASIC demand lifts MLCC demand") is not None:
        raise AssertionError("LLM should be disabled during this test")

    parsed = parse_json_object(
        """
        ```json
        {
          "core_content": "AI ASIC 推动高端 MLCC 需求集中。",
          "themes": ["MLCC/被动元件", "AI 加速器"],
          "incremental_view": {
            "classification": "增量利好",
            "surprise_level": "中",
            "priced_in": "部分定价",
            "reason": "新增信息来自供应链扩产滞后和高端规格集中。"
          },
          "initial_impact": "偏利好高端 MLCC 供应商。",
          "a_share": {
            "positive": [
              {
                "name": "风华高科",
                "code": "000636.SZ",
                "full_name": "广东风华高新科技股份有限公司",
                "listing": "深交所主板",
                "reason": "国内 MLCC 龙头之一，受益于国产替代和高端规格需求。",
                "impact_magnitude": "中",
                "duration": "数周到数月",
                "persistence": "阶段性持续",
                "confidence": "中"
              }
            ],
            "negative": []
          },
          "global_equity": {"positive": [], "negative": []},
          "tracking_points": ["高端 MLCC 交期", "云厂商 ASIC 出货"],
          "risks": ["海外扩产快于预期"],
          "watchlist_view": "可纳入观察名单，但需验证价格和订单。"
        }
        ```
        """
    )
    lines = "\n".join(format_llm_analysis(parsed, "deepseek-chat"))
    if "增量判断：增量利好" not in lines:
        raise AssertionError("incremental view missing")
    if "风华高科 000636.SZ" not in lines:
        raise AssertionError("A-share company formatting failed")
    if "模型：deepseek-chat" not in lines:
        raise AssertionError("model line missing")

    missing_incremental = "\n".join(format_llm_analysis({"core_content": "只有摘要。"}, "deepseek-chat"))
    if "增量判断：无法判断" not in missing_incremental:
        raise AssertionError("missing incremental view should be filled with fallback")
    print("llm analysis formatting checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
