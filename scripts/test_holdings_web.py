#!/usr/bin/env python3
"""Regression checks for the local Web workbench HTML."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from holdings_web import RUN_ONCE_TARGETS, html_page, unit_actions, unit_display_metadata
from source_profiles import (
    filter_enabled_named_sources,
    filter_enabled_source_mapping,
    load_source_profile_config,
    save_source_profile_config,
    source_profile_skeptic_enabled,
    source_profiles_payload,
)


def test_embedded_script_keeps_newline_escapes() -> None:
    html = html_page(token_required=False)
    assert "showView('overview');" in html
    index = html.find("parsed.lessons.join")
    assert index > 0
    snippet = html[index : index + 40]
    assert repr("\\n") in repr(snippet)
    assert "parsed.lessons.join('\n')" not in html


def test_health_page_exposes_service_action_controls() -> None:
    html = html_page(token_required=False)
    assert "/api/service-action" in html
    assert "runServiceAction" in html
    assert "renderHealthUnits" in html
    assert "fetching_persistent" in html
    assert "重启定时器" in html
    assert "立即运行" in html


def test_source_profile_view_is_exposed() -> None:
    html = html_page(token_required=False)
    assert "showView('sources')" in html
    assert "/api/source-profiles" in html
    assert "renderSourceProfiles" in html
    assert "saveSourceProfiles" in html
    assert "保存配置" in html
    assert "信息源" in html


def test_source_profiles_group_six_categories() -> None:
    with TemporaryDirectory() as tmpdir:
        payload = source_profiles_payload(Path(tmpdir) / "surveil.sqlite3")
    labels = [item["label"] for item in payload["categories"]]
    assert labels == [
        "0. X / Serenity",
        "1. 研究机构/行业媒体",
        "2. 公司官网",
        "3. 新闻媒体",
        "4. 新浪个股新闻",
        "5. iFinD 公司公告",
    ]
    profile_ids = {item["id"] for item in payload["profiles"]}
    assert {"x_serenity", "semianalysis", "nvidia_blog", "cls_telegraph_api", "sina_stock_news", "ifind_notice"} <= profile_ids


def test_source_profiles_aggregate_wildcard_health() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE source_health (
                monitor TEXT NOT NULL,
                source TEXT NOT NULL,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                last_success_at TEXT,
                last_failure_at TEXT,
                last_error TEXT,
                last_alerted_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (monitor, source)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO source_health (
                monitor, source, consecutive_failures, last_success_at,
                last_failure_at, last_error, last_alerted_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sina_stock_news",
                "legacy:300308.SZ",
                2,
                "",
                "2026-07-07T00:00:00+00:00",
                "boom",
                "",
                "2026-07-07T00:00:00+00:00",
            ),
        )
        conn.commit()
        conn.close()
        payload = source_profiles_payload(db_path)
    profile = next(item for item in payload["profiles"] if item["id"] == "sina_stock_news")
    assert profile["health_status"] == "failing"
    assert profile["consecutive_failures"] == 2
    assert profile["last_error"] == "boom"


def test_source_profile_local_config_roundtrip() -> None:
    with TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        config_path = tmp_path / "source_profiles.local.json"
        saved = save_source_profile_config(
            {
                "profiles": [
                    {
                        "id": "semianalysis",
                        "enabled": False,
                        "frequency": "每 60 秒",
                        "skeptic_enabled": False,
                        "web_evidence_enabled": True,
                        "proxy_profile": "测试代理",
                        "notes": "测试覆盖",
                    },
                    {
                        "id": "unknown_source",
                        "enabled": False,
                        "frequency": "不会写入",
                    },
                ]
            },
            path=config_path,
        )
        assert saved["disabled_count"] == 1
        assert saved["override_count"] == 1
        raw = load_source_profile_config(config_path)
        assert raw["disabled_sources"] == ["semianalysis"]
        assert set(raw["overrides"]["semianalysis"]) == {
            "frequency",
            "skeptic_enabled",
            "proxy_profile",
            "notes",
        }
        payload = source_profiles_payload(tmp_path / "surveil.sqlite3", config_path=config_path)
    profile = next(item for item in payload["profiles"] if item["id"] == "semianalysis")
    assert profile["enabled"] is False
    assert profile["frequency"] == "每 60 秒"
    assert profile["skeptic_enabled"] is False
    assert profile["web_evidence_enabled"] is True
    assert profile["proxy_profile"] == "测试代理"
    assert profile["notes"] == "测试覆盖"
    assert profile["config_modified"] is True
    assert profile["runtime_effective"] is True
    assert payload["config_exists"] is True


def test_source_profile_runtime_filters_and_flags() -> None:
    with TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "source_profiles.local.json"
        save_source_profile_config(
            {
                "profiles": [
                    {"id": "semianalysis", "enabled": False},
                    {"id": "cls_telegraph_api", "skeptic_enabled": False},
                ]
            },
            path=config_path,
        )
        feeds = {"semianalysis": "https://example.com/feed", "nvidia_blog": "https://example.com/nvidia"}
        assert filter_enabled_source_mapping(feeds, config_path=config_path) == {
            "nvidia_blog": "https://example.com/nvidia"
        }
        assert filter_enabled_named_sources(["semianalysis", "nvidia_blog"], config_path=config_path) == [
            "nvidia_blog"
        ]
        assert source_profile_skeptic_enabled("cls_telegraph_api", config_path=config_path) is False


def test_systemd_actions_are_whitelisted() -> None:
    assert "restart" in unit_actions("surveil-rss-monitor.service")
    assert "restart" in unit_actions("surveil-trendforce-page-monitor.service")
    assert "restart_timer" in unit_actions("surveil-china-media.timer")
    assert "run_once" in unit_actions("surveil-china-media.timer")
    assert "run_once" in unit_actions("surveil-research-collector.timer")
    assert RUN_ONCE_TARGETS["surveil-research-collector.timer"] == "surveil-research-collector.service"
    assert "run_once" in unit_actions("surveil-research-collector-shadow.timer")
    assert RUN_ONCE_TARGETS["surveil-research-collector-shadow.timer"] == "surveil-research-collector-shadow.service"
    assert RUN_ONCE_TARGETS["surveil-china-media.timer"] == "surveil-china-media.service"
    assert unit_actions("surveil-holdings-web.service") == ["status"]
    assert unit_actions("ssh.service") == []


def test_unit_display_metadata_translates_oneshot_success() -> None:
    meta = unit_display_metadata(
        "surveil-china-media.service",
        {"ActiveState": "inactive", "SubState": "dead", "Result": "success"},
    )
    assert meta["group"] == "fetching_scheduled"
    assert meta["unit_type"] == "定时采集"
    assert meta["status_text"] == "上次运行成功"


def test_unit_display_metadata_translates_waiting_timer() -> None:
    meta = unit_display_metadata(
        "surveil-overseas-media.timer",
        {"ActiveState": "active", "SubState": "waiting", "Result": "success"},
    )
    assert meta["group"] == "fetching_scheduled"
    assert meta["unit_type"] == "定时器"
    assert meta["status_text"] == "等待下次触发"


def test_unit_display_metadata_groups_shadow_collectors() -> None:
    meta = unit_display_metadata(
        "surveil-news-collector-shadow.timer",
        {"ActiveState": "active", "SubState": "waiting", "Result": "success"},
    )
    assert meta["group"] == "fetching_shadow"
    assert meta["unit_type"] == "影子定时器"
    assert meta["group_label"] == "影子采集任务"


def test_unit_display_metadata_includes_research_production_collector() -> None:
    meta = unit_display_metadata(
        "surveil-research-collector.timer",
        {"ActiveState": "active", "SubState": "waiting", "Result": "success"},
    )
    assert meta["group"] == "fetching_scheduled"
    assert meta["unit_type"] == "定时器"
    assert "5 分钟" in meta["schedule"]


def main() -> int:
    test_embedded_script_keeps_newline_escapes()
    test_health_page_exposes_service_action_controls()
    test_source_profile_view_is_exposed()
    test_source_profiles_group_six_categories()
    test_source_profiles_aggregate_wildcard_health()
    test_source_profile_local_config_roundtrip()
    test_source_profile_runtime_filters_and_flags()
    test_systemd_actions_are_whitelisted()
    test_unit_display_metadata_translates_oneshot_success()
    test_unit_display_metadata_translates_waiting_timer()
    test_unit_display_metadata_groups_shadow_collectors()
    test_unit_display_metadata_includes_research_production_collector()
    print("holdings web checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
