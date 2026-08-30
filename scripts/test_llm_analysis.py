#!/usr/bin/env python3
"""Regression checks for LLM analysis formatting without network calls."""

from __future__ import annotations

import os
import json

os.environ["SURVEIL_DISABLE_LLM"] = "1"

import llm_analysis
from llm_analysis import analyze_with_llm, format_llm_analysis, parse_json_object


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


def main() -> int:
    test_raw_chat_completion_returns_bounded_usage_metadata()
    test_glm_provider_uses_dedicated_fixed_connection_and_fails_closed_without_key()
    test_glm_request_forces_supported_response_preferences()
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
