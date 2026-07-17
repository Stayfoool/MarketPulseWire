#!/usr/bin/env python3
"""Regression checks for thread-isolated shared HTTP helpers."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from typing import Any

import http_utils


class FakeClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SequenceClient:
    def __init__(self, responses: list[http_utils.httpx.Response]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> http_utils.httpx.Response:
        self.requests.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def test_same_thread_reuses_matching_client_and_rotates_changed_key() -> None:
    original_client = http_utils.httpx.Client
    created: list[FakeClient] = []

    def factory(**kwargs: Any) -> FakeClient:
        client = FakeClient(**kwargs)
        created.append(client)
        return client

    try:
        http_utils.httpx.Client = factory
        http_utils.reset_http_client()
        first = http_utils.get_http_client(timeout=10)
        assert http_utils.get_http_client(timeout=10) is first
        second = http_utils.get_http_client(timeout=20)
        assert second is not first
        assert first.closed is True
        assert second.closed is False
    finally:
        http_utils.reset_http_client()
        http_utils.httpx.Client = original_client

    assert len(created) == 2


def test_one_thread_cannot_close_another_threads_client() -> None:
    original_client = http_utils.httpx.Client
    created: list[FakeClient] = []
    created_lock = Lock()
    both_ready = Barrier(2)
    thread_b_reset = Event()

    def factory(**kwargs: Any) -> FakeClient:
        client = FakeClient(**kwargs)
        with created_lock:
            created.append(client)
        return client

    def thread_a() -> tuple[FakeClient, FakeClient, bool]:
        first = http_utils.get_http_client(timeout=10)
        both_ready.wait(timeout=5)
        assert thread_b_reset.wait(timeout=5)
        reused = http_utils.get_http_client(timeout=10)
        return first, reused, first.closed

    def thread_b() -> tuple[FakeClient, bool]:
        second = http_utils.get_http_client(timeout=20)
        both_ready.wait(timeout=5)
        http_utils.reset_http_client()
        thread_b_reset.set()
        return second, second.closed

    try:
        http_utils.httpx.Client = factory
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_a = executor.submit(thread_a)
            future_b = executor.submit(thread_b)
            first, reused, first_closed = future_a.result(timeout=10)
            second, second_closed = future_b.result(timeout=10)
    finally:
        http_utils.httpx.Client = original_client

    assert first is reused
    assert first is not second
    assert first_closed is False
    assert second_closed is True
    assert len(created) == 2


def response(status_code: int, content: bytes) -> http_utils.httpx.Response:
    request = http_utils.httpx.Request("POST", "https://example.com/query")
    return http_utils.httpx.Response(status_code, request=request, content=content)


def test_post_json_retries_status_and_decode_in_one_bounded_loop() -> None:
    client = SequenceClient(
        [
            response(503, b"temporarily unavailable"),
            response(200, b"{invalid"),
            response(200, b'{"ok": true}'),
        ]
    )
    original_get_client = http_utils.get_http_client
    original_sleep = http_utils.time.sleep
    sleeps: list[float] = []
    try:
        http_utils.get_http_client = lambda _timeout=None: client
        http_utils.time.sleep = sleeps.append
        result = http_utils.http_post_json(
            "https://example.com/query",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            content=b"key=value",
            timeout=7,
            retries=2,
        )
    finally:
        http_utils.get_http_client = original_get_client
        http_utils.time.sleep = original_sleep

    assert result == {"ok": True}
    assert len(client.requests) == 3
    assert all(item["method"] == "POST" for item in client.requests)
    assert client.requests[0]["content"] == b"key=value"
    assert client.requests[0]["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert sleeps == [http_utils.retry_sleep(0), http_utils.retry_sleep(1)]


def test_post_json_decode_exhaustion_remains_visible() -> None:
    client = SequenceClient([response(200, b"not-json"), response(200, b"still-not-json")])
    original_get_client = http_utils.get_http_client
    original_sleep = http_utils.time.sleep
    try:
        http_utils.get_http_client = lambda _timeout=None: client
        http_utils.time.sleep = lambda _seconds: None
        try:
            http_utils.http_post_json("https://example.com/query", retries=1)
        except json.JSONDecodeError:
            pass
        else:
            raise AssertionError("malformed JSON must fail after the bounded retry count")
    finally:
        http_utils.get_http_client = original_get_client
        http_utils.time.sleep = original_sleep

    assert len(client.requests) == 2


def main() -> int:
    test_same_thread_reuses_matching_client_and_rotates_changed_key()
    test_one_thread_cannot_close_another_threads_client()
    test_post_json_retries_status_and_decode_in_one_bounded_loop()
    test_post_json_decode_exhaustion_remains_visible()
    print("http utils thread-isolation checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
