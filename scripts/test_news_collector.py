#!/usr/bin/env python3
"""Regression checks for the media and trade-policy collector."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

import news_collector
from collector_runtime import ProcessingBatchError
from source_profiles import save_source_profile_config


ROOT = Path(__file__).resolve().parents[1]
TEST_RULE_CONFIG = ROOT / "config" / "rule_core_v1.test.json"


def test_enabled_sources_include_current_groups() -> None:
    with TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "source_profiles.local.json"
        sources = news_collector.news_sources(config_path=config_path)
        assert {
            "yicai_brief",
            "cls_telegraph_api",
            "star_market_daily_subject",
            "jin10_rsshub_important",
            "sina_finance_articles",
            "wallstreetcn_news",
        } <= set(sources)
        assert "sina_flash" not in sources
        policies = {
            source.name
            for source in news_collector.official_trade_policy_sources(config_path=config_path)
        }
        assert "ustr_press_releases" in policies
        media, policy = news_collector.selected_source_groups(
            ["ustr_press_releases"], config_path=config_path
        )
        assert media == {}
        assert [source.name for source in policy] == ["ustr_press_releases"]


def test_disabled_source_is_filtered() -> None:
    with TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "source_profiles.local.json"
        save_source_profile_config(
            {"profiles": [{"id": source, "enabled": False} for source in (
                "jin10_rsshub_important",
                "sina_finance_articles",
                "wallstreetcn_news",
            )]},
            path=config_path,
        )
        sources, _ = news_collector.selected_source_groups([], config_path=config_path)
        assert "jin10_rsshub_important" not in sources
        assert "sina_finance_articles" not in sources
        assert "wallstreetcn_news" not in sources
        assert "cls_telegraph_api" in sources


def test_collect_delegates_to_unified_source_pipelines() -> None:
    calls: list[tuple[list[str], bool]] = []
    original_media = news_collector.china_media.run_once
    original_policy = news_collector.trade_policy.run_once
    with TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "source_profiles.local.json"
        policy_sources = news_collector.official_trade_policy_sources(config_path=config_path)[:2]
    news_collector.china_media.run_once = lambda sources, notify_baseline=False: calls.append((list(sources), notify_baseline)) or 3
    news_collector.trade_policy.run_once = lambda sources, notify_baseline=False: calls.append(([s.name for s in sources], notify_baseline)) or 2
    try:
        payload = news_collector.collect_production(
            sources={"yicai_brief": "https://example.com/yicai"},
            policy_sources=policy_sources,
            notify_baseline=True,
        )
    finally:
        news_collector.china_media.run_once = original_media
        news_collector.trade_policy.run_once = original_policy

    assert payload["ok"] is True
    assert payload["counts"]["new_items"] == 5
    assert payload["counts"]["processing_failed_items"] == 0
    assert payload["counts"]["sources"] == 3
    assert calls == [
        (["yicai_brief"], True),
        ([source.name for source in policy_sources], True),
    ]


def test_processing_failure_is_explicit_in_report() -> None:
    original_media = news_collector.china_media.run_once
    news_collector.china_media.run_once = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        ProcessingBatchError(2, completed_items=4)
    )
    try:
        payload = news_collector.collect_production(
            sources={"wallstreetcn_news": "https://example.com"},
            policy_sources=[],
        )
    finally:
        news_collector.china_media.run_once = original_media

    assert payload["ok"] is False
    assert payload["counts"]["new_items"] == 4
    assert payload["counts"]["processing_failed_items"] == 2
    assert payload["counts"]["processing_aborted_due_global_failure"] is False
    assert payload["errors"] == [
        {
            "stage": "news_media",
            "error": "ProcessingBatchError: 本轮处理失败 2 条，已保留待重试",
        }
    ]


def test_global_processing_failure_marks_batch_aborted() -> None:
    original_media = news_collector.china_media.run_once
    news_collector.china_media.run_once = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        ProcessingBatchError(1, completed_items=1, global_failure=True)
    )
    try:
        payload = news_collector.collect_production(
            sources={"wallstreetcn_news": "https://example.com"},
            policy_sources=[],
        )
    finally:
        news_collector.china_media.run_once = original_media

    assert payload["counts"]["processing_aborted_due_global_failure"] is True


def main() -> int:
    previous = os.environ.get("RULE_CORE_CONFIG")
    os.environ["RULE_CORE_CONFIG"] = str(TEST_RULE_CONFIG)
    try:
        test_enabled_sources_include_current_groups()
        test_disabled_source_is_filtered()
        test_collect_delegates_to_unified_source_pipelines()
        test_processing_failure_is_explicit_in_report()
    finally:
        if previous is None:
            os.environ.pop("RULE_CORE_CONFIG", None)
        else:
            os.environ["RULE_CORE_CONFIG"] = previous
    print("news collector checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
