#!/usr/bin/env python3
"""Regression checks for thread-isolated shared HTTP helpers."""

from __future__ import annotations

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


def main() -> int:
    test_same_thread_reuses_matching_client_and_rotates_changed_key()
    test_one_thread_cannot_close_another_threads_client()
    print("http utils thread-isolation checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
