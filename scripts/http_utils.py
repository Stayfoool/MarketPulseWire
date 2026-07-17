"""Shared HTTP helpers for monitor fetchers."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from threading import local
from typing import Any, Mapping

import httpx


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_CLIENT_LOCAL = local()


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    url: str
    headers: httpx.Headers
    content: bytes


def default_user_agent() -> str:
    return os.getenv("SURVEIL_USER_AGENT", "").strip() or DEFAULT_USER_AGENT


def default_proxy() -> str:
    return (
        os.getenv("SURVEIL_HTTP_PROXY", "").strip()
        or os.getenv("HTTPS_PROXY", "").strip()
        or os.getenv("HTTP_PROXY", "").strip()
    )


def get_http_client(timeout: float | None = None) -> httpx.Client:
    proxy = default_proxy()
    user_agent = default_user_agent()
    timeout_value = timeout or float(os.getenv("SURVEIL_HTTP_TIMEOUT_SECONDS", "20") or "20")
    key = (proxy, user_agent, timeout_value)
    client = getattr(_CLIENT_LOCAL, "client", None)
    if client is not None and getattr(_CLIENT_LOCAL, "key", None) == key:
        return client
    if client is not None:
        client.close()
    kwargs = {
        "headers": {"User-Agent": user_agent},
        "timeout": httpx.Timeout(timeout_value),
        "follow_redirects": True,
        "http2": True,
        "trust_env": not bool(proxy),
    }
    if proxy:
        kwargs["proxy"] = proxy
    client = httpx.Client(**kwargs)
    _CLIENT_LOCAL.client = client
    _CLIENT_LOCAL.key = key
    return client


def reset_http_client() -> None:
    client = getattr(_CLIENT_LOCAL, "client", None)
    if client is not None:
        client.close()
    _CLIENT_LOCAL.client = None
    _CLIENT_LOCAL.key = None


def retry_count(default: int = 2) -> int:
    raw = os.getenv("SURVEIL_HTTP_RETRY_COUNT", "").strip()
    try:
        return max(0, min(5, int(raw))) if raw else default
    except ValueError:
        return default


def retry_sleep(attempt: int) -> float:
    raw = os.getenv("SURVEIL_HTTP_RETRY_BACKOFF_SECONDS", "").strip()
    try:
        base = float(raw) if raw else 2.0
    except ValueError:
        base = 2.0
    return min(60.0, max(0.0, base) * (2**attempt))


def should_retry_status(status_code: int) -> bool:
    return status_code in {408, 425, 429, 500, 502, 503, 504}


def _request(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    content: bytes | str | None = None,
    json_data: Any = None,
    timeout: float | None = None,
    retries: int | None = None,
    decode_json: bool = False,
) -> HttpResponse | Any:
    attempts = (retry_count() if retries is None else max(0, retries)) + 1
    request_headers = dict(headers or {})
    last_error: Exception | None = None
    for attempt in range(attempts):
        client = get_http_client(timeout)
        try:
            response = client.request(
                method,
                url,
                headers=request_headers,
                content=content,
                json=json_data,
            )
            if should_retry_status(response.status_code) and attempt < attempts - 1:
                time.sleep(retry_sleep(attempt))
                continue
            if method == "GET" and response.status_code == 304:
                pass
            else:
                response.raise_for_status()
            result = HttpResponse(
                status_code=response.status_code,
                url=str(response.url),
                headers=response.headers,
                content=response.content,
            )
            if not decode_json:
                return result
            try:
                return json.loads(result.content.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                last_error = exc
                if attempt >= attempts - 1:
                    raise
                time.sleep(retry_sleep(attempt))
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            last_error = exc
            reset_http_client()
            if attempt >= attempts - 1:
                raise
            time.sleep(retry_sleep(attempt))
        except httpx.HTTPStatusError:
            raise
    raise RuntimeError(f"HTTP {method} 请求失败：{last_error}")


def http_get(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float | None = None,
    retries: int | None = None,
) -> HttpResponse:
    return _request(
        "GET",
        url,
        headers=headers,
        timeout=timeout,
        retries=retries,
    )


def http_post(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    content: bytes | str | None = None,
    json_data: Any = None,
    timeout: float | None = None,
    retries: int | None = None,
) -> HttpResponse:
    return _request(
        "POST",
        url,
        headers=headers,
        content=content,
        json_data=json_data,
        timeout=timeout,
        retries=retries,
    )


def http_post_json(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    content: bytes | str | None = None,
    json_data: Any = None,
    timeout: float | None = None,
    retries: int | None = None,
) -> Any:
    """POST and decode a UTF-8 JSON response inside the shared retry loop."""

    return _request(
        "POST",
        url,
        headers=headers,
        content=content,
        json_data=json_data,
        timeout=timeout,
        retries=retries,
        decode_json=True,
    )
