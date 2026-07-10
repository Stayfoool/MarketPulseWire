#!/usr/bin/env python3
"""Regression checks for the local Web workbench HTML."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from holdings_web import RUN_ONCE_TARGETS, fetch_events_rows, html_page, unit_actions, unit_display_metadata
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
    assert "showShadowUnits" in html
    assert "showLegacyUnits" in html
    assert "显示历史兼容单元" in html
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


def test_investment_bank_theme_rule_configuration_is_exposed() -> None:
    html = html_page(token_required=False)
    assert "/api/investment-bank-theme-rules" in html
    assert "国际投行重大主题策略" in html
    assert "saveInvestmentBankThemeRules" in html


def test_rule_center_view_is_exposed() -> None:
    html = html_page(token_required=False)
    assert "规则中心" in html
    assert "showView('rules')" in html
    assert "/api/rule-center" in html
    assert "runRuleSimulation" in html


def test_event_center_search_filters_before_per_pipeline_limit() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE article_reviews (
                source TEXT,
                item_id TEXT,
                url TEXT,
                title TEXT,
                source_module TEXT,
                published_at TEXT,
                importance TEXT,
                push_now INTEGER,
                incremental_classification TEXT,
                affected_targets_json TEXT,
                daily_summary TEXT,
                reason TEXT,
                pushed_at TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO article_reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "cls_telegraph_api",
                "2421358",
                "https://example.com/goldman",
                "高盛重磅发声：做多中国AI价值链",
                "财联社 / 电报 API",
                "2026-07-09T03:57:30+00:00",
                "medium",
                0,
                "已有预期",
                "[]",
                "高盛发布中国AI价值链策略",
                "旧门控未推送",
                "",
                "2026-07-09T03:57:58.693585+00:00",
            ),
        )
        for index in range(301):
            conn.execute(
                """
                INSERT INTO article_reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "cls_telegraph_api",
                    f"noise-{index}",
                    "",
                    f"噪音新闻 {index}",
                    "财联社 / 电报 API",
                    "2026-07-09T15:00:00+00:00",
                    "low",
                    0,
                    "",
                    "[]",
                    "普通内容",
                    "",
                    "",
                    f"2026-07-09T15:00:00.{index:03d}+00:00",
                ),
            )
        conn.commit()
        conn.close()

        rows = fetch_events_rows(
            day="2026-07-09",
            source="财联社",
            q="高盛重磅发声",
            db_path=db_path,
        )

    assert len(rows) == 1
    assert rows[0]["id"] == "2421358"
    assert rows[0]["kind"] == "article"


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
    semianalysis = next(item for item in payload["profiles"] if item["id"] == "semianalysis")
    trendforce_page = next(item for item in payload["profiles"] if item["category"] == "research_industry_media" and item["source_type"] == "公开列表页")
    assert "surveil-research-collector.timer" in semianalysis["service_units"]
    assert "surveil-rss-monitor.service" not in semianalysis["service_units"]
    assert "surveil-research-collector.timer" in trendforce_page["service_units"]
    assert "surveil-trendforce-page-monitor.service" not in trendforce_page["service_units"]


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
    assert "run_once" in unit_actions("surveil-official-collector.timer")
    assert RUN_ONCE_TARGETS["surveil-official-collector.timer"] == "surveil-official-collector.service"
    assert "run_once" in unit_actions("surveil-news-collector.timer")
    assert RUN_ONCE_TARGETS["surveil-news-collector.timer"] == "surveil-news-collector.service"
    assert "run_once" in unit_actions("surveil-value-directory.timer")
    assert RUN_ONCE_TARGETS["surveil-value-directory.timer"] == "surveil-value-directory.service"
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
    assert meta["group"] == "fetching_legacy"
    assert meta["unit_type"] == "历史兼容"
    assert meta["lifecycle"] == "legacy_cutover"
    assert meta["default_visible"] is False
    assert meta["replacement"] == "surveil-news-collector.timer"
    assert meta["status_text"] == "上次运行成功"


def test_unit_display_metadata_translates_waiting_timer() -> None:
    meta = unit_display_metadata(
        "surveil-overseas-media.timer",
        {"ActiveState": "active", "SubState": "waiting", "Result": "success"},
    )
    assert meta["group"] == "fetching_legacy"
    assert meta["unit_type"] == "历史兼容定时器"
    assert meta["lifecycle"] == "legacy_cutover"
    assert meta["replacement"] == "surveil-research-collector.timer"
    assert meta["status_text"] == "等待下次触发"


def test_unit_display_metadata_groups_shadow_collectors() -> None:
    meta = unit_display_metadata(
        "surveil-news-collector-shadow.timer",
        {"ActiveState": "active", "SubState": "waiting", "Result": "success"},
    )
    assert meta["group"] == "fetching_shadow"
    assert meta["unit_type"] == "影子定时器"
    assert meta["group_label"] == "影子采集任务"
    assert meta["lifecycle"] == "shadow"
    assert meta["default_visible"] is False


def test_unit_display_metadata_includes_research_production_collector() -> None:
    meta = unit_display_metadata(
        "surveil-research-collector.timer",
        {"ActiveState": "active", "SubState": "waiting", "Result": "success"},
    )
    assert meta["group"] == "fetching_scheduled"
    assert meta["unit_type"] == "定时器"
    assert meta["lifecycle"] == "production"
    assert meta["default_visible"] is True
    assert "5 分钟" in meta["schedule"]


def test_unit_display_metadata_includes_official_production_collector() -> None:
    meta = unit_display_metadata(
        "surveil-official-collector.timer",
        {"ActiveState": "active", "SubState": "waiting", "Result": "success"},
    )
    assert meta["group"] == "fetching_scheduled"
    assert meta["unit_type"] == "定时器"
    assert "10 分钟" in meta["schedule"]


def test_unit_display_metadata_includes_news_production_collector() -> None:
    meta = unit_display_metadata(
        "surveil-news-collector.timer",
        {"ActiveState": "active", "SubState": "waiting", "Result": "success"},
    )
    assert meta["group"] == "fetching_scheduled"
    assert meta["unit_type"] == "定时器"
    assert "2 分钟" in meta["schedule"]


def main() -> int:
    test_embedded_script_keeps_newline_escapes()
    test_health_page_exposes_service_action_controls()
    test_source_profile_view_is_exposed()
    test_investment_bank_theme_rule_configuration_is_exposed()
    test_rule_center_view_is_exposed()
    test_source_profiles_group_six_categories()
    test_source_profiles_aggregate_wildcard_health()
    test_source_profile_local_config_roundtrip()
    test_source_profile_runtime_filters_and_flags()
    test_systemd_actions_are_whitelisted()
    test_unit_display_metadata_translates_oneshot_success()
    test_unit_display_metadata_translates_waiting_timer()
    test_unit_display_metadata_groups_shadow_collectors()
    test_unit_display_metadata_includes_research_production_collector()
    test_unit_display_metadata_includes_official_production_collector()
    test_unit_display_metadata_includes_news_production_collector()
    print("holdings web checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
