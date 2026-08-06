#!/usr/bin/env python3
"""Regression checks for the X browser-backed unified collector."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import os
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace

import x_browser_monitor
from market_db import init_db
from production_admission import production_admission_context


ROOT = Path(__file__).resolve().parents[1]
TEST_RULE_CONFIG = ROOT / "config" / "rule_core_v1.test.json"


def post(post_id: str, text: str, *, username: str = "alice") -> dict:
    return {
        "id": post_id,
        "url": f"https://x.com/{username}/status/{post_id}",
        "title": f"@{username} 的 X 推文",
        "summary": text,
        "content": text,
        "full_text": text,
        "published_at": "2026-08-06T01:02:03+00:00",
        "source_category": "x_serenity",
        "publisher_role": "x_author",
        "content_type": "x_tweet",
        "source_module": "X / 正在关注",
        "body_source": "X 可见页面",
        "raw": {"id": post_id, "author_username": username, "timeline": "following"},
    }


def test_normalize_tweet_record_requires_stable_identity_and_text() -> None:
    normalized = x_browser_monitor.normalize_tweet_record(
        {
            "url": "https://x.com/alice/status/123",
            "text": "NVIDIA 推出新一代 GPU",
            "author_username": "@alice",
            "author_name": "Alice",
            "published_at": "2026-08-06T01:02:03Z",
            "quoted_texts": ["HBM 需求增加"],
            "media": [{"type": "image", "url": "https://pbs.twimg.com/media/test.jpg"}],
        }
    )
    assert normalized is not None
    assert normalized["id"] == "123"
    assert normalized["published_at"] == "2026-08-06T01:02:03+00:00"
    assert normalized["raw"]["author_username"] == "alice"
    assert "引用推文：HBM 需求增加" in normalized["full_text"]
    assert x_browser_monitor.normalize_tweet_record(
        {"url": "https://x.com/alice/status/124", "text": ""}
    ) is None


class FakeMouse:
    def wheel(self, _x: int, _y: int) -> None:
        return None


class FakePage:
    def __init__(self, snapshots: list[list[dict]]) -> None:
        self.snapshots = list(snapshots)
        self.mouse = FakeMouse()

    def evaluate(self, _script: str) -> list[dict]:
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return list(self.snapshots[0])

    def wait_for_timeout(self, _timeout: int) -> None:
        return None


class EmptyLocator:
    def count(self) -> int:
        return 0


class MissingFollowingPage:
    def locator(self, _selector: str) -> EmptyLocator:
        return EmptyLocator()


def test_visible_collection_filters_promoted_reposts_and_deduplicates() -> None:
    page = FakePage(
        [
            [
                {
                    "id": "1",
                    "url": "https://x.com/alice/status/1",
                    "text": "GPU supply update",
                    "author_username": "alice",
                    "social_context": "",
                },
                {
                    "id": "2",
                    "url": "https://x.com/ad/status/2",
                    "text": "advertisement",
                    "author_username": "ad",
                    "social_context": "Promoted",
                },
            ],
            [
                {
                    "id": "1",
                    "url": "https://x.com/alice/status/1",
                    "text": "GPU supply update",
                    "author_username": "alice",
                    "social_context": "",
                },
                {
                    "id": "3",
                    "url": "https://x.com/bob/status/3",
                    "text": "reposted item",
                    "author_username": "bob",
                    "social_context": "Bob reposted",
                },
                {
                    "id": "4",
                    "url": "https://x.com/carol/status/4",
                    "text": "HBM demand update",
                    "author_username": "carol",
                    "social_context": "",
                },
            ],
        ]
    )
    items = x_browser_monitor.collect_visible_posts(page, max_scrolls=1, max_posts=10, sleep_ms=0)
    assert [item["id"] for item in items] == ["1", "4"]


def test_missing_following_tab_fails_closed() -> None:
    try:
        x_browser_monitor.select_following_tab(MissingFollowingPage())
    except x_browser_monitor.XBrowserParseError as exc:
        assert "正在关注" in str(exc)
    else:
        raise AssertionError("missing Following tab must fail closed")


def test_disabled_source_does_not_open_browser() -> None:
    original_enabled = x_browser_monitor.source_profile_enabled
    original_collect = x_browser_monitor.collect_following_posts
    try:
        x_browser_monitor.source_profile_enabled = lambda _source: False
        x_browser_monitor.collect_following_posts = lambda: (_ for _ in ()).throw(
            AssertionError("disabled source must not launch Chromium")
        )
        report = x_browser_monitor.run_once()
    finally:
        x_browser_monitor.source_profile_enabled = original_enabled
        x_browser_monitor.collect_following_posts = original_collect
    assert report.status == "disabled"
    assert report.raw_items == 0


def test_first_run_is_baseline_and_later_run_processes_only_new_posts() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "x.sqlite3"
        init_db(db_path).close()
        original_enabled = x_browser_monitor.source_profile_enabled
        original_process = x_browser_monitor._process_new_item
        processed: list[str] = []
        try:
            x_browser_monitor.source_profile_enabled = lambda _source: True
            first = x_browser_monitor.run_once(
                db_path=db_path,
                posts=[post("1", "old GPU post"), post("2", "old HBM post")],
            )
            assert first.baseline_items == 2
            assert first.reviewed_items == 0
            with sqlite3.connect(db_path) as conn:
                assert conn.execute(
                    "SELECT source_item_id,collection_class FROM market_items ORDER BY source_item_id"
                ).fetchall() == [("1", "baseline"), ("2", "baseline")]
                assert conn.execute("SELECT COUNT(*) FROM market_reviews").fetchone()[0] == 0

            x_browser_monitor._process_new_item = (
                lambda item, *, db_path, report: processed.append(str(item["id"]))
            )
            second = x_browser_monitor.run_once(
                db_path=db_path,
                posts=[post("1", "old GPU post"), post("3", "new CPO post")],
            )
            assert second.new_items == 1
            assert processed == ["3"]
        finally:
            x_browser_monitor.source_profile_enabled = original_enabled
            x_browser_monitor._process_new_item = original_process


def test_live_item_uses_unified_admission_and_market_flow() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "x.sqlite3"
        init_db(db_path).close()
        original_admission = x_browser_monitor.production_admission_context
        original_persist = x_browser_monitor.persist_production_admission_context
        original_process = x_browser_monitor.process_market_item
        calls: list[dict] = []
        try:
            normalized = x_browser_monitor.normalize_market_item(
                x_browser_monitor.SOURCE,
                post("10", "NVIDIA GPU and HBM demand accelerate"),
                source_profile_id=x_browser_monitor.SOURCE_PROFILE_ID,
            )
            context = production_admission_context(
                normalized,
                db_path=db_path,
                env={"RULE_CORE_CONFIG": str(TEST_RULE_CONFIG)},
            )
            assert context.result.status == "admitted"
            x_browser_monitor.production_admission_context = lambda *_args, **_kwargs: context
            persisted = SimpleNamespace(
                result=context.result,
                portfolio=context.portfolio,
                market_item_id=1,
                market_review_id=2,
            )
            x_browser_monitor.persist_production_admission_context = (
                lambda *_args, **_kwargs: persisted
            )

            def fake_process(normalized_item, raw_item, **kwargs):
                calls.append({"normalized": normalized_item, "raw": raw_item, **kwargs})
                return SimpleNamespace(delivery_status="sent")

            x_browser_monitor.process_market_item = fake_process
            report = x_browser_monitor.XBrowserReport("start")
            x_browser_monitor._process_new_item(
                post("10", "NVIDIA GPU and HBM demand accelerate"),
                db_path=db_path,
                report=report,
            )
            assert len(calls) == 1
            assert calls[0]["production_admission"].status == "admitted"
            assert calls[0]["production_portfolio"] is context.portfolio
            assert report.reviewed_items == 1
            assert report.pushed_items == 1
        finally:
            x_browser_monitor.production_admission_context = original_admission
            x_browser_monitor.persist_production_admission_context = original_persist
            x_browser_monitor.process_market_item = original_process


def test_same_content_different_x_authors_has_same_admission() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "x.sqlite3"
        init_db(db_path).close()
        results = []
        for username in ("alice", "bob"):
            normalized = x_browser_monitor.normalize_market_item(
                x_browser_monitor.SOURCE,
                post(f"{username}-1", "NVIDIA GPU and HBM demand accelerate", username=username),
                source_profile_id=x_browser_monitor.SOURCE_PROFILE_ID,
            )
            results.append(
                production_admission_context(
                    normalized,
                    db_path=db_path,
                    env={"RULE_CORE_CONFIG": str(TEST_RULE_CONFIG)},
                ).result
            )
        assert [result.status for result in results] == ["admitted", "admitted"]
        assert results[0].matched_families == results[1].matched_families


def test_main_records_source_health_failure_and_recovery() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "x.sqlite3"
        init_db(db_path).close()
        original_db = x_browser_monitor.DB_PATH
        original_run_once = x_browser_monitor.run_once
        original_threshold = os.environ.get("SOURCE_HEALTH_ALERT_FAILURES")
        try:
            os.environ["SOURCE_HEALTH_ALERT_FAILURES"] = "999"
            x_browser_monitor.DB_PATH = db_path
            x_browser_monitor.run_once = lambda **_kwargs: (_ for _ in ()).throw(
                x_browser_monitor.XBrowserAccessBlocked("X 登录状态失效")
            )
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                assert x_browser_monitor.main([]) == 1
            with sqlite3.connect(db_path) as conn:
                failure = conn.execute(
                    "SELECT consecutive_failures,last_error FROM source_health WHERE monitor=? AND source=?",
                    (x_browser_monitor.HEALTH_MONITOR, x_browser_monitor.SOURCE),
                ).fetchone()
            assert failure is not None
            assert failure[0] == 1
            assert "登录状态失效" in failure[1]

            x_browser_monitor.run_once = lambda **_kwargs: x_browser_monitor.XBrowserReport(
                "start", run_finished_at="finish"
            )
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                assert x_browser_monitor.main([]) == 0
            with sqlite3.connect(db_path) as conn:
                recovered = conn.execute(
                    "SELECT consecutive_failures,last_success_at FROM source_health WHERE monitor=? AND source=?",
                    (x_browser_monitor.HEALTH_MONITOR, x_browser_monitor.SOURCE),
                ).fetchone()
            assert recovered is not None
            assert recovered[0] == 0
            assert recovered[1]
        finally:
            x_browser_monitor.DB_PATH = original_db
            x_browser_monitor.run_once = original_run_once
            if original_threshold is None:
                os.environ.pop("SOURCE_HEALTH_ALERT_FAILURES", None)
            else:
                os.environ["SOURCE_HEALTH_ALERT_FAILURES"] = original_threshold


def main() -> int:
    test_normalize_tweet_record_requires_stable_identity_and_text()
    test_visible_collection_filters_promoted_reposts_and_deduplicates()
    test_missing_following_tab_fails_closed()
    test_disabled_source_does_not_open_browser()
    test_first_run_is_baseline_and_later_run_processes_only_new_posts()
    test_live_item_uses_unified_admission_and_market_flow()
    test_same_content_different_x_authors_has_same_admission()
    test_main_records_source_health_failure_and_recovery()
    print("x browser monitor checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
