#!/usr/bin/env python3
"""Regression checks for X stream health bridging."""

from __future__ import annotations

import tempfile
from pathlib import Path

import x_stream
from market_db import init_db


def test_stream_failure_records_unified_health() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        original_db = x_stream.DB_PATH
        original_alerts = x_stream.alerts_enabled
        try:
            x_stream.DB_PATH = Path(tmpdir) / "test.sqlite3"
            init_db(x_stream.DB_PATH).close()
            x_stream.alerts_enabled = lambda: False
            x_stream.record_stream_failure("HTTP 401: unauthorized", status_code=401, phase="stream")
            with x_stream.connect_db() as conn:
                row = conn.execute(
                    """
                    SELECT monitor, source, consecutive_failures, last_error
                    FROM source_health
                    WHERE monitor = ? AND source = ?
                    """,
                    ("x_stream", "auth"),
                ).fetchone()
                assert row is not None
                assert row[2] == 1
                assert "401" in row[3]
                detail = conn.execute(
                    "SELECT status, failure_count FROM x_stream_health WHERE issue_key = ?",
                    ("auth",),
                ).fetchone()
                assert detail is not None
                assert detail[0] == "failing"
                assert detail[1] == 1
        finally:
            x_stream.DB_PATH = original_db
            x_stream.alerts_enabled = original_alerts


def test_stream_recovery_clears_unified_health() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        original_db = x_stream.DB_PATH
        original_alerts = x_stream.alerts_enabled
        try:
            x_stream.DB_PATH = Path(tmpdir) / "test.sqlite3"
            init_db(x_stream.DB_PATH).close()
            x_stream.alerts_enabled = lambda: False
            x_stream.record_stream_failure("HTTP 503: unavailable", status_code=503, phase="stream")
            x_stream.record_stream_recovery(phase="stream_connected")
            with x_stream.connect_db() as conn:
                row = conn.execute(
                    """
                    SELECT consecutive_failures
                    FROM source_health
                    WHERE monitor = ? AND source = ?
                    """,
                    ("x_stream", "x_api_unavailable"),
                ).fetchone()
                assert row is not None
                assert row[0] == 0
        finally:
            x_stream.DB_PATH = original_db
            x_stream.alerts_enabled = original_alerts


def test_x_stream_enabled_uses_source_profile() -> None:
    original = x_stream.source_profile_enabled
    seen = []

    def fake_enabled(source_id: str) -> bool:
        seen.append(source_id)
        return False

    try:
        x_stream.source_profile_enabled = fake_enabled
        assert x_stream.x_stream_enabled() is False
        assert seen == ["x_serenity"]
    finally:
        x_stream.source_profile_enabled = original


def test_rest_backfill_baselines_first_fetch_then_delivers_new_posts() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        original_db = x_stream.DB_PATH
        original_fetch = x_stream.fetch_recent_posts
        original_deliver = x_stream.deliver_post
        delivered: list[str] = []
        fetches = [
            [
                {"id": "1", "text": "old one", "created_at": "2026-07-31T00:00:00+00:00"},
                {"id": "2", "text": "old two", "created_at": "2026-07-31T00:01:00+00:00"},
            ],
            [
                {"id": "2", "text": "old two", "created_at": "2026-07-31T00:01:00+00:00"},
                {"id": "3", "text": "new", "created_at": "2026-07-31T00:02:00+00:00"},
            ],
        ]
        try:
            x_stream.DB_PATH = Path(tmpdir) / "test.sqlite3"
            init_db(x_stream.DB_PATH).close()
            x_stream.fetch_recent_posts = lambda *_args, **_kwargs: fetches.pop(0)
            x_stream.deliver_post = lambda _username, post: delivered.append(str(post["id"])) or True

            assert x_stream.backfill_recent_posts("Serenity") == 0
            assert delivered == []
            with x_stream.connect_db() as conn:
                assert conn.execute(
                    "SELECT post_id,delivery_status FROM seen_posts ORDER BY post_id"
                ).fetchall() == [("1", "baseline"), ("2", "baseline")]
                assert conn.execute(
                    "SELECT source FROM seen_sources"
                ).fetchall() == [("x_stream:serenity",)]

            assert x_stream.backfill_recent_posts("Serenity") == 1
            assert delivered == ["3"]
        finally:
            x_stream.DB_PATH = original_db
            x_stream.fetch_recent_posts = original_fetch
            x_stream.deliver_post = original_deliver


def main() -> int:
    test_stream_failure_records_unified_health()
    test_stream_recovery_clears_unified_health()
    test_x_stream_enabled_uses_source_profile()
    test_rest_backfill_baselines_first_fetch_then_delivers_new_posts()
    print("x stream health checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
