#!/usr/bin/env python3
"""Regression checks for the local Web workbench HTML."""

from __future__ import annotations

import http.client
import json
import os
import sqlite3
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

import holdings_web
from holdings_web import (
    RUN_ONCE_TARGETS,
    SERVICE_UNITS,
    HoldingsHandler,
    active_source_health_keys,
    build_health_summary,
    build_health_tasks,
    event_feedback_summary,
    fetch_events_rows,
    html_page,
    parse_systemctl_show_output,
    utc_window_for_range,
    unit_actions,
    unit_display_metadata,
)
from market_db import init_db
from source_profiles import (
    filter_enabled_named_sources,
    filter_enabled_source_mapping,
    load_source_profile_config,
    save_source_profile_config,
    source_profile_enabled,
    source_profile_skeptic_enabled,
    source_profiles_payload,
)


def insert_unified_result(
    conn: sqlite3.Connection,
    *,
    item_kind: str,
    source: str,
    item_id: str,
    title: str,
    published_at: str,
    seen_at: str,
    summary: str = "",
    action: str = "archive",
    importance: str = "low",
    delivered: bool = False,
    collection_class: str = "live",
    content_type: str = "article",
    source_label: str = "",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO market_items (
            source,source_item_id,dedupe_key,source_category,publisher_role,
            collector,content_type,title,summary,full_text,url,published_at,
            first_seen_at,symbols_json,themes_json,raw_json,access_note,
            content_hash,collection_class,processability_status,
            processability_reason,processing_status,processing_error,
            created_at,updated_at
        ) VALUES (?, ?, ?, '', '', 'test', ?, ?, ?, '', '', ?, ?, '[]', '[]', '{}', '',
                  ?, ?, 'succeeded', '', ?, '', ?, ?)
        """,
        (
            source,
            item_id,
            f"{source}:{item_id}",
            content_type,
            title,
            summary,
            published_at,
            seen_at,
            f"hash:{source}:{item_id}",
            collection_class,
            "succeeded" if collection_class == "live" else "not_applicable",
            seen_at,
            seen_at,
        ),
    )
    market_item_id = int(cur.lastrowid)
    conn.execute(
        """
        INSERT INTO market_item_aliases (
            market_item_id,item_kind,source,legacy_item_id,legacy_store_kind,created_at
        ) VALUES (?, ?, ?, ?, 'market_items', ?)
        """,
        (market_item_id, item_kind, source, item_id, seen_at),
    )
    if collection_class == "baseline":
        return market_item_id
    decision = {"action": action, "importance": importance, "reason": "测试决策"}
    interpretation = {"core_content": summary, "model": "fixed-test"}
    legacy_payload = {"_legacy_row": {"source_module": source_label or source}}
    review_id = int(
        conn.execute(
            """
            INSERT INTO market_reviews (
                market_item_id,task,run_key,is_current,review_status,
                admission_status,admission_reason,admission_matched_families_json,
                admission_evidence_json,admission_json,decision_action,importance,
                decision_json,interpretation_json,legacy_payload_json,
                application_revision,created_at,completed_at
            ) VALUES (?, 'production', ?, 1, 'succeeded', 'admitted', 'test',
                      '[]', '[]', '{}', ?, ?, ?, ?, ?, 'test', ?, ?)
            """,
            (
                market_item_id,
                f"test:{source}:{item_id}",
                action,
                importance,
                json.dumps(decision, ensure_ascii=False),
                json.dumps(interpretation, ensure_ascii=False),
                json.dumps(legacy_payload, ensure_ascii=False),
                seen_at,
                seen_at,
            ),
        ).lastrowid
    )
    if delivered:
        conn.execute(
            """
            INSERT INTO deliveries (
                market_item_id,market_review_id,channel,status,
                decision_action,attempted_at,sent_at,payload_json
            ) VALUES (?, ?, 'feishu', 'sent', ?, ?, ?, '{}')
            """,
            (market_item_id, review_id, action, seen_at, seen_at),
        )
    return market_item_id


def frontend_source() -> str:
    return "\n".join(
        (
            html_page(token_required=False),
            (holdings_web.WEB_ROOT / "app.js").read_text(encoding="utf-8"),
        )
    )


def test_page_uses_extracted_assets_and_bounded_placeholders() -> None:
    html = html_page(token_required=False)
    assert '<link rel="stylesheet" href="/static/styles.css">' in html
    assert '<script src="/static/app.js" defer></script>' in html
    assert "<style>" not in html
    assert "<script>" not in html
    assert "__WORKBENCH_" not in html
    assert "未配置访问令牌，仅限 SSH 隧道使用" in html


def test_media_keywords_use_one_master_list_and_a_title_only_subset() -> None:
    source = frontend_source()
    assert 'id="semiconductorAiKeywords"' in source
    assert 'id="excludeKeywords"' in source
    assert "semiconductor_ai_keywords" in source
    for retired_name in (
        "baseKeywords",
        "includeKeywords",
        "codeDefaultKeywords",
        "baseOverrideStatus",
        "resetBaseKeywords",
    ):
        assert retired_name not in source

    with TemporaryDirectory() as tmpdir:
        rule_path = Path(tmpdir) / "private-rule-config.json"
        public_path = Path(__file__).resolve().parents[1] / "config" / "rule_core_v1.test.json"
        rule_path.write_text(public_path.read_text(encoding="utf-8"), encoding="utf-8")
        previous = os.environ.get("RULE_CORE_CONFIG")
        os.environ["RULE_CORE_CONFIG"] = str(rule_path)
        server = ThreadingHTTPServer(("127.0.0.1", 0), HoldingsHandler)
        server.token = "test-token"
        server.restart_sina_flash = False
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            headers = {"X-Holdings-Token": "test-token"}
            connection.request("GET", "/api/media-keywords", headers=headers)
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
            assert set(payload) == {
                "ok",
                "semiconductor_ai_keywords",
                "semiconductor_ai_title_keywords",
                "exclude_keywords",
                "config_version",
            }

            body = json.dumps(
                {
                    "semiconductor_ai_keywords": ["HBM", "SMIC"],
                    "semiconductor_ai_title_keywords": ["SMIC"],
                    "exclude_keywords": ["培训广告"],
                }
            )
            headers["Content-Type"] = "application/json"
            connection.request("POST", "/api/media-keywords", body=body, headers=headers)
            response = connection.getresponse()
            saved = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
            assert saved["semiconductor_ai_keywords"] == ["HBM", "SMIC"]
            assert saved["semiconductor_ai_title_keywords"] == ["SMIC"]
            assert saved["exclude_keywords"] == ["培训广告"]
            assert "backup_path" not in saved
            on_disk = json.loads(rule_path.read_text(encoding="utf-8"))
            assert on_disk["semiconductor_ai_keywords"] == ["HBM", "SMIC"]
            assert "macro_data" in on_disk
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            if previous is None:
                os.environ.pop("RULE_CORE_CONFIG", None)
            else:
                os.environ["RULE_CORE_CONFIG"] = previous


def test_llm_decision_view_is_read_only_and_distinguishes_uncertain() -> None:
    source = frontend_source()
    assert "大模型决策" in source
    assert "/api/llm-decisions" in source
    assert "未生成 action" in source
    assert "证据不足" in source
    assert "insufficient_evidence" in source
    assert "uncertain" in source
    assert "查看判断理由和证据" in source
    assert "打开原文" in source


def test_static_asset_routes_are_allowlisted() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), HoldingsHandler)
    server.token = "test-token"
    server.restart_sina_flash = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        expected = {
            "/": "text/html; charset=utf-8",
            "/static/styles.css": "text/css; charset=utf-8",
            "/static/app.js": "text/javascript; charset=utf-8",
        }
        for path, content_type in expected.items():
            connection.request("GET", path)
            response = connection.getresponse()
            body = response.read()
            assert response.status == 200
            assert response.getheader("Content-Type") == content_type
            assert response.getheader("Cache-Control") == "no-cache"
            assert response.getheader("X-Content-Type-Options") == "nosniff"
            assert body

        for path in ("/static/missing.css", "/static/../AGENTS.md", "/api/missing"):
            connection.request("GET", path)
            response = connection.getresponse()
            response.read()
            assert response.status == 404

        connection.request("GET", "/api/overview")
        response = connection.getresponse()
        response.read()
        assert response.status == 401
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_health_page_exposes_service_action_controls() -> None:
    html = frontend_source()
    assert "/api/service-action" in html
    assert "runServiceAction" in html
    assert "renderHealthTasks" in html
    assert "fetching_persistent" in html
    assert "showShadowUnits" not in html
    assert "showLegacyUnits" not in html
    assert "显示历史兼容单元" not in html
    assert "信号复盘" not in html
    assert "调度状态" in html
    assert "最近执行" in html
    assert "重启定时器" in html
    assert "立即运行" in html
    assert "/api/health/summary" in html
    assert "healthAlertBadge" in html
    assert "healthAlertSummary" in html
    assert "任务健康，无当前故障" in html
    assert "sourceAlertBadge" in html
    assert "sourceAlertSummary" in html
    assert "信息源，无当前故障" in html
    assert "isFailingSourceProfile" in html
    assert "连续失败" in html
    assert "60000" in html
    assert "visibilitychange" in html


def test_retired_shadow_tasks_and_rule_comparison_are_not_exposed() -> None:
    source = frontend_source()
    backend = Path(holdings_web.__file__).read_text(encoding="utf-8")
    assert "显示影子任务" not in source
    assert "规则对比报告" not in source
    assert "/api/rule-shadow-reports" not in backend
    assert all("-shadow" not in unit for unit in holdings_web.ALLOWED_SYSTEMD_UNITS)


def test_parse_batched_systemctl_show_output() -> None:
    parsed = parse_systemctl_show_output(
        """NextElapseUSecRealtime=Sat 2026-07-18 08:00:00 CST
Result=success
Id=surveil-value-directory.timer
LoadState=loaded
ActiveState=active
SubState=waiting

Result=exit-code
ExecMainStatus=1
Id=surveil-value-directory.service
LoadState=loaded
ActiveState=failed
SubState=failed
"""
    )
    assert parsed["surveil-value-directory.timer"]["ActiveState"] == "active"
    assert parsed["surveil-value-directory.timer"]["NextElapseUSecRealtime"].startswith("Sat 2026")
    assert parsed["surveil-value-directory.service"]["Result"] == "exit-code"
    assert parsed["surveil-value-directory.service"]["ExecMainStatus"] == "1"


def test_source_profile_view_is_exposed() -> None:
    html = frontend_source()
    assert "showView('sources')" in html
    assert "/api/source-profiles" in html
    assert "renderSourceProfiles" in html
    assert "saveSourceProfiles" in html
    assert "保存配置" in html
    assert "信息源" in html


def test_retired_rule_center_is_not_exposed() -> None:
    html = frontend_source()
    assert "/api/current-rules" in html
    assert "/api/rule-center" not in html
    assert "runRuleSimulation" not in html


def test_feedback_quality_view_is_exposed() -> None:
    html = frontend_source()
    assert "反馈质量" in html
    assert "showView('feedback')" in html
    assert "/api/feedback-quality" in html
    assert "loadFeedbackQuality" in html
    assert "未反馈保持未知" in html


def test_holdings_page_marks_environment_and_related_keywords() -> None:
    html = frontend_source()
    assert f"环境：{holdings_web.workbench_environment_label()}" in html
    assert "关联新闻关键词" in html

    original_root = holdings_web.ROOT
    try:
        holdings_web.ROOT = Path("/opt/surveil")
        assert holdings_web.workbench_environment_label() == "服务器生产配置"
    finally:
        holdings_web.ROOT = original_root


def test_event_center_search_filters_before_per_pipeline_limit() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        with sqlite3.connect(db_path) as conn:
            insert_unified_result(
                conn,
                item_kind="article",
                source="cls_telegraph_api",
                item_id="2421358",
                title="高盛重磅发声：做多中国AI价值链",
                summary="高盛发布中国AI价值链策略",
                published_at="2026-07-09T03:57:30+00:00",
                seen_at="2026-07-09T03:57:58.693585+00:00",
                importance="medium",
                source_label="财联社 / 电报 API",
            )
            for index in range(301):
                insert_unified_result(
                    conn,
                    item_kind="article",
                    source="cls_telegraph_api",
                    item_id=f"noise-{index}",
                    title=f"噪音新闻 {index}",
                    summary="普通内容",
                    published_at="2026-07-09T15:00:00+00:00",
                    seen_at=f"2026-07-09T15:00:00.{index:03d}+00:00",
                    source_label="财联社 / 电报 API",
                )

        rows = fetch_events_rows(
            day="2026-07-09",
            source="财联社",
            q="高盛重磅发声",
            db_path=db_path,
        )

    assert len(rows) == 1
    assert rows[0]["id"] == "2421358"
    assert rows[0]["kind"] == "article"


def test_event_center_date_range_is_inclusive_in_beijing_time() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        with sqlite3.connect(db_path) as conn:
            for item_id, title, seen_at in (
                ("start", "起始日", "2026-07-01T00:00:00+00:00"),
                ("end", "结束日", "2026-07-02T15:59:59+00:00"),
                ("after", "结束日之后", "2026-07-02T16:00:00+00:00"),
            ):
                insert_unified_result(
                    conn,
                    item_kind="article",
                    source="source",
                    item_id=item_id,
                    title=title,
                    published_at="",
                    seen_at=seen_at,
                )

        selected = fetch_events_rows(start_day="2026-07-01", end_day="2026-07-02", db_path=db_path)
        same_day = fetch_events_rows(day="2026-07-01", db_path=db_path)

    assert [row["id"] for row in selected] == ["end", "start"]
    assert [row["id"] for row in same_day] == ["start"]

    start_utc, end_utc, display_start, display_end = utc_window_for_range("2026-07-01", "2026-07-02")
    assert start_utc == "2026-06-30T16:00:00+00:00"
    assert end_utc == "2026-07-02T16:00:00+00:00"
    assert (display_start, display_end) == ("2026-07-01", "2026-07-02")


def test_event_center_date_range_rejects_partial_or_inverted_dates() -> None:
    for kwargs, expected in (
        ({"start_day": "2026-07-01"}, "必须同时填写"),
        ({"start_day": "2026-07-03", "end_day": "2026-07-01"}, "不能晚于"),
        ({"start_day": "2026/07/01", "end_day": "2026-07-01"}, "YYYY-MM-DD"),
    ):
        try:
            fetch_events_rows(**kwargs)
        except ValueError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError(f"expected ValueError for {kwargs}")


def test_event_center_can_show_baselines_and_filter_by_published_time() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        with sqlite3.connect(db_path) as conn:
            insert_unified_result(
                conn,
                item_kind="article",
                source="value_directory_ib_stocks",
                item_id="baseline-1",
                title="价值目录首次基线研报",
                summary="首次采集",
                published_at="2026-07-09T16:00:00+00:00",
                seen_at="2026-07-10T08:52:24+00:00",
                collection_class="baseline",
            )
            insert_unified_result(
                conn,
                item_kind="article",
                source="value_directory_ib_stocks",
                item_id="reviewed-1",
                title="价值目录后续研报",
                summary="后续采集",
                published_at="2026-07-10T15:00:00+00:00",
                seen_at="2026-07-11T00:00:10+00:00",
                importance="medium",
                source_label="价值目录 / 国际投行-个股",
            )

        default_rows = fetch_events_rows(
            day="2026-07-10",
            source="value_directory_ib_stocks",
            db_path=db_path,
        )
        baseline_rows = fetch_events_rows(
            day="2026-07-10",
            source="value_directory_ib_stocks",
            include_baseline=True,
            db_path=db_path,
        )
        published_rows = fetch_events_rows(
            day="2026-07-10",
            source="value_directory_ib_stocks",
            time_basis="published",
            db_path=db_path,
        )

    assert default_rows == []
    assert len(baseline_rows) == 1
    assert baseline_rows[0]["id"] == "baseline-1"
    assert baseline_rows[0]["kind"] == "baseline"
    assert baseline_rows[0]["baseline_only"] is True
    assert len(published_rows) == 1
    assert published_rows[0]["id"] == "reviewed-1"
    assert published_rows[0]["published_at"].startswith("2026-07-10")
    assert published_rows[0]["source_id"] == "value_directory_ib_stocks"


def test_event_center_shows_company_disclosure_baseline_only_when_requested() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        with sqlite3.connect(db_path) as conn:
            insert_unified_result(
                conn,
                item_kind="event",
                source="company_disclosures",
                item_id="announcement:1225426155",
                title="股票交易异常波动公告",
                summary="基线公告",
                published_at="2026-07-16T18:00:00+08:00",
                seen_at="2026-07-16T11:41:48+00:00",
                collection_class="baseline",
                content_type="announcement",
            )

        hidden = fetch_events_rows(
            day="2026-07-16",
            source="company_disclosures",
            db_path=db_path,
        )
        visible = fetch_events_rows(
            day="2026-07-16",
            source="company_disclosures",
            include_baseline=True,
            db_path=db_path,
        )

    assert hidden == []
    assert len(visible) == 1
    assert visible[0]["id"] == "announcement:1225426155"
    assert visible[0]["source_id"] == "company_disclosures"
    assert visible[0]["baseline_only"] is True
    assert visible[0]["delivery_status"] == ""


def test_event_center_does_not_duplicate_seen_item_projected_to_event() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        with sqlite3.connect(db_path) as conn:
            insert_unified_result(
                conn,
                item_kind="event",
                source="sina_flash",
                item_id="flash-admitted",
                title="已准入快讯",
                summary="已生成统一记录",
                published_at="2026-07-23T01:00:00+00:00",
                seen_at="2026-07-23T01:00:01+00:00",
                content_type="flash",
            )
            insert_unified_result(
                conn,
                item_kind="event",
                source="sina_flash",
                item_id="flash-excluded",
                title="未准入快讯",
                summary="只保留基线记录",
                published_at="2026-07-23T01:01:00+00:00",
                seen_at="2026-07-23T01:01:01+00:00",
                collection_class="baseline",
                content_type="flash",
            )

        rows = fetch_events_rows(
            day="2026-07-23",
            source="sina_flash",
            include_baseline=True,
            db_path=db_path,
        )

    assert len(rows) == 2
    assert sum(row["kind"] == "flash" and row["title"] == "已准入快讯" for row in rows) == 1
    assert sum(row["kind"] == "baseline" and row["id"] == "flash-admitted" for row in rows) == 0
    assert sum(
        row["kind"] == "flash" and row["id"] == "flash-excluded" and row["baseline_only"]
        for row in rows
    ) == 1


def test_event_center_source_filter_uses_grouped_dropdown() -> None:
    html = frontend_source()
    assert '<select id="eventSource"' in html
    assert 'id="eventQueryButton"' in html
    assert '全部来源' in html
    assert 'loadEventSourceOptions' in html
    assert 'eventSourceFilterValue' in html
    assert 'eventTimeBasis' in html
    assert 'eventFromDate' in html
    assert 'eventToDate' in html
    assert "params.set('from', startDate)" in html
    assert "eventAbortController?.abort()" in html
    assert "operationId !== eventOperationId" in html
    assert "正在查询..." in html
    assert "queryButton.disabled = true" in html
    assert "params.set('to', endDate)" in html
    assert 'eventIncludeBaseline' in html
    assert '显示基线条目' in html
    assert "x:serenity" in html


def insert_feedback(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    item_kind: str,
    source: str,
    item_id: str,
    label: str,
    operator: str,
    clicked_at_us: int,
    reasons: str = "[]",
    active_labels: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO market_feedback (
            feedback_event_id, item_kind, source, item_id, label, active_labels_json, reason_tags_json,
            operator_id, rule_ids_json, clicked_at_us, received_at, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, '{}')
        """,
        (
            event_id,
            item_kind,
            source,
            item_id,
            label,
            active_labels,
            reasons,
            operator,
            clicked_at_us,
            f"2026-07-15T10:00:{clicked_at_us % 60:02d}+00:00",
        ),
    )


def test_event_center_projects_current_feedback_across_active_stores() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        with sqlite3.connect(db_path) as conn:
            insert_unified_result(
                conn,
                item_kind="article",
                source="cls_telegraph_api",
                item_id="article-1",
                title="文章",
                summary="摘要",
                published_at="2026-07-15T09:00:00+00:00",
                seen_at="2026-07-15T09:00:01+00:00",
                action="push",
                importance="high",
                delivered=True,
                source_label="财联社",
            )
            insert_unified_result(
                conn,
                item_kind="article",
                source="cls_telegraph_api",
                item_id="article-unsent",
                title="未投递文章",
                published_at="2026-07-15T09:01:00+00:00",
                seen_at="2026-07-15T09:01:01+00:00",
                source_label="财联社",
            )
            insert_unified_result(
                conn,
                item_kind="official",
                source="nvidia_blog",
                item_id="official-1",
                title="官网新闻",
                summary="摘要",
                published_at="2026-07-15T09:02:00+00:00",
                seen_at="2026-07-15T09:02:01+00:00",
                action="push",
                importance="high",
                delivered=True,
                content_type="official_news",
            )
            event_id = insert_unified_result(
                conn,
                item_kind="event",
                source="sina_flash",
                item_id="event-1",
                title="事件",
                summary="摘要",
                published_at="2026-07-15T09:03:00+00:00",
                seen_at="2026-07-15T09:03:01+00:00",
                action="push",
                importance="high",
                delivered=True,
                content_type="flash_news",
            )
            insert_unified_result(
                conn,
                item_kind="article",
                source="baseline_source",
                item_id="baseline-1",
                title="基线",
                published_at="2026-07-15T09:04:00+00:00",
                seen_at="2026-07-15T09:04:01+00:00",
                collection_class="baseline",
            )
            insert_feedback(conn, event_id="f1", item_kind="article", source="cls_telegraph_api", item_id="article-1", label="high_value", operator="operator-a", clicked_at_us=100)
            insert_feedback(conn, event_id="f2", item_kind="article", source="cls_telegraph_api", item_id="article-1", label="duplicate", operator="operator-a", clicked_at_us=300, reasons='["stale"]', active_labels='["high_value", "duplicate"]')
            insert_feedback(conn, event_id="f3", item_kind="article", source="cls_telegraph_api", item_id="article-1", label="high_value", operator="operator-b", clicked_at_us=200)
            insert_feedback(conn, event_id="f4", item_kind="official", source="nvidia_blog", item_id="official-1", label="invalid", operator="operator-a", clicked_at_us=400, reasons='["weak_evidence"]')
            insert_feedback(conn, event_id="f5", item_kind="official", source="nvidia_blog", item_id="official-1", label="cleared", operator="operator-a", clicked_at_us=500)
            insert_feedback(conn, event_id="f6", item_kind="event", source="sina_flash", item_id="event-1", label="duplicate", operator="operator-a", clicked_at_us=600, active_labels='["high_value", "duplicate"]')
            conn.commit()
            before = conn.execute("SELECT COUNT(*) FROM market_feedback").fetchone()[0]

        rows = fetch_events_rows(day="2026-07-15", include_baseline=True, limit=20, db_path=db_path)
        by_id = {str(row["id"]): row for row in rows}
        article = by_id["article-1"]
        assert article["feedback_state"] == "mixed"
        assert article["feedback_labels"] == ["high_value", "duplicate"]
        assert article["feedback_operator_count"] == 2
        assert "特别有用 2" in article["feedback_display"] and "重复 1" in article["feedback_display"]
        assert by_id["official-1"]["feedback_state"] == "unlabelled"
        assert by_id["official-1"]["feedback_display"] == "未反馈"
        assert by_id["event-1"]["feedback_state"] == "mixed"
        assert by_id["event-1"]["feedback_labels"] == ["high_value", "duplicate"]
        assert by_id["article-unsent"]["feedback_state"] == "not_delivered"
        assert by_id["baseline-1"]["feedback_state"] == "not_applicable"
        assert "operator_id" not in article and "operator-a" not in str(article)
        summary = event_feedback_summary(rows)
        assert summary == {"delivered": 3, "labelled": 2, "high_value": 2, "duplicate": 2, "invalid": 0}

        high_value_rows = fetch_events_rows(day="2026-07-15", feedback="high_value", db_path=db_path)
        duplicate_rows = fetch_events_rows(day="2026-07-15", feedback="duplicate", db_path=db_path)
        invalid_rows = fetch_events_rows(day="2026-07-15", feedback="invalid", db_path=db_path)
        unlabelled_rows = fetch_events_rows(day="2026-07-15", feedback="unlabelled", db_path=db_path)
        assert {str(row["id"]) for row in high_value_rows} == {"article-1", "event-1"}
        assert {str(row["id"]) for row in duplicate_rows} == {"article-1", "event-1"}
        assert invalid_rows == []
        assert {str(row["id"]) for row in unlabelled_rows} == {"official-1"}
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM market_feedback").fetchone()[0] == before


def test_event_center_feedback_filter_applies_before_article_limit() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        with sqlite3.connect(db_path) as conn:
            for index in range(301):
                insert_unified_result(
                    conn,
                    item_kind="article",
                    source="cls_telegraph_api",
                    item_id=f"article-{index}",
                    title=f"文章 {index}",
                    published_at="2026-07-15T09:00:00+00:00",
                    seen_at=f"2026-07-15T09:{index // 60:02d}:{index % 60:02d}+00:00",
                    action="push",
                    importance="high",
                    delivered=True,
                    source_label="财联社",
                )
            insert_feedback(conn, event_id="oldest-feedback", item_kind="article", source="cls_telegraph_api", item_id="article-0", label="duplicate", operator="operator-a", clicked_at_us=100)
            conn.commit()
        rows = fetch_events_rows(day="2026-07-15", feedback="duplicate", db_path=db_path)
    assert [row["id"] for row in rows] == ["article-0"]


def test_source_profiles_group_six_categories() -> None:
    with TemporaryDirectory() as tmpdir:
        payload = source_profiles_payload(Path(tmpdir) / "surveil.sqlite3")
    labels = [item["label"] for item in payload["categories"]]
    assert labels == [
        "0. X / Serenity",
        "1. 研究机构/行业媒体",
        "2. 公司官网",
        "3. 官方贸易政策",
        "4. 新闻媒体",
        "5. 新浪个股新闻",
        "6. 公司公告",
    ]
    profile_ids = {item["id"] for item in payload["profiles"]}
    assert {
        "x_serenity",
        "semianalysis",
        "alphabstract_summaries",
        "value_directory_ib_industry_macro",
        "nvidia_blog",
        "ustr_press_releases",
        "cls_telegraph_api",
        "sina_stock_news",
        "company_disclosures",
    } <= profile_ids
    semianalysis = next(item for item in payload["profiles"] if item["id"] == "semianalysis")
    alphabstract = next(item for item in payload["profiles"] if item["id"] == "alphabstract_summaries")
    cls = next(item for item in payload["profiles"] if item["id"] == "cls_telegraph_api")
    ustr = next(item for item in payload["profiles"] if item["id"] == "ustr_press_releases")
    sina_flash = next(item for item in payload["profiles"] if item["id"] == "sina_flash")
    sina_stock_news = next(item for item in payload["profiles"] if item["id"] == "sina_stock_news")
    trendforce_page = next(item for item in payload["profiles"] if item["category"] == "research_industry_media" and item["source_type"] == "公开列表页")
    assert "surveil-research-collector.timer" in semianalysis["service_units"]
    assert "surveil-research-collector.timer" in alphabstract["service_units"]
    assert alphabstract["publisher_role"] == "third_party_research_summary"
    assert "surveil-rss-monitor.service" not in semianalysis["service_units"]
    assert "surveil-research-collector.timer" in trendforce_page["service_units"]
    assert "surveil-trendforce-page-monitor.service" not in trendforce_page["service_units"]
    assert cls["publisher_role"] == "news_media"
    assert ustr["publisher_role"] == "government_official"
    assert "surveil-news-collector.timer" in ustr["service_units"]
    assert sina_flash["publisher_role"] == "news_media"
    assert sina_stock_news["publisher_role"] == "news_media"


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
                        "publisher_role": "news_media",
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
            "publisher_role",
            "skeptic_enabled",
            "proxy_profile",
            "notes",
        }
        payload = source_profiles_payload(tmp_path / "surveil.sqlite3", config_path=config_path)
    profile = next(item for item in payload["profiles"] if item["id"] == "semianalysis")
    assert profile["enabled"] is False
    assert profile["frequency"] == "每 60 秒"
    assert profile["publisher_role"] == "news_media"
    assert profile["skeptic_enabled"] is False
    assert profile["web_evidence_enabled"] is True
    assert profile["proxy_profile"] == "测试代理"
    assert profile["notes"] == "测试覆盖"
    assert profile["config_modified"] is True
    assert profile["runtime_effective"] is True
    assert payload["config_exists"] is True


def test_source_profile_local_config_stays_private_across_replacement() -> None:
    with TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "source_profiles.local.json"
        previous_umask = os.umask(0)
        try:
            save_source_profile_config(
                {"profiles": [{"id": "semianalysis", "enabled": False}]},
                path=config_path,
            )
            assert config_path.stat().st_mode & 0o777 == 0o600

            os.chmod(config_path, 0o644)
            save_source_profile_config(
                {"profiles": [{"id": "semianalysis", "enabled": True}]},
                path=config_path,
            )
            assert config_path.stat().st_mode & 0o777 == 0o600
            assert load_source_profile_config(config_path)["disabled_sources"] == []
            assert not config_path.with_suffix(config_path.suffix + ".tmp").exists()
        finally:
            os.umask(previous_umask)


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


def test_company_disclosure_provider_is_the_only_provider_runtime_override() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        config_path = root / "source_profiles.local.json"
        saved = save_source_profile_config(
            {
                "profiles": [
                    {
                        "id": "company_disclosures",
                        "enabled": True,
                        "provider": "future_provider",
                    }
                ]
            },
            path=config_path,
        )
        payload = source_profiles_payload(root / "surveil.sqlite3", config_path=config_path)
    profile = next(item for item in payload["profiles"] if item["id"] == "company_disclosures")
    assert saved["override_count"] == 1
    assert profile["provider"] == "future_provider"
    assert profile["overrides"] == {"provider": "future_provider"}
    assert "provider=future_provider" in profile["runtime_note"]


def test_company_disclosure_source_can_be_disabled_privately() -> None:
    with TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "source_profiles.local.json"
        save_source_profile_config(
            {"profiles": [{"id": "company_disclosures", "enabled": False}]},
            path=config_path,
        )
        assert source_profile_enabled("company_disclosures", config_path=config_path) is False


def test_source_profile_can_explicitly_remove_news_media_role() -> None:
    with TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        config_path = tmp_path / "source_profiles.local.json"
        save_source_profile_config(
            {"profiles": [{"id": "cls_telegraph_api", "enabled": True, "publisher_role": ""}]},
            path=config_path,
        )
        raw = load_source_profile_config(config_path)
        assert raw["overrides"]["cls_telegraph_api"]["publisher_role"] == ""
        payload = source_profiles_payload(tmp_path / "surveil.sqlite3", config_path=config_path)
    profile = next(item for item in payload["profiles"] if item["id"] == "cls_telegraph_api")
    assert profile["publisher_role"] == ""


def test_source_profile_runtime_note_reports_effective_counts() -> None:
    with TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        config_path = tmp_path / "source_profiles.local.json"
        initial = source_profiles_payload(tmp_path / "surveil.sqlite3", config_path=config_path)
        save_source_profile_config(
            {
                "profiles": [
                    {
                        "id": profile["id"],
                        "enabled": True,
                        "skeptic_enabled": False,
                        "web_evidence_enabled": False,
                    }
                    for profile in initial["profiles"]
                ]
            },
            path=config_path,
        )
        payload = source_profiles_payload(tmp_path / "surveil.sqlite3", config_path=config_path)
    status = payload["runtime_status"]
    assert status["skeptic_sources"] == 0
    assert status["web_evidence_sources"] == 0
    assert "Skeptic 实际启用 0 个" in payload["runtime_note"]
    assert "Tavily/Web Evidence 实际可触发 0 个" in payload["runtime_note"]
    assert "覆盖已接入" not in payload["runtime_note"]


def test_systemd_actions_are_whitelisted() -> None:
    assert unit_actions("surveil-rss-monitor.service") == []
    assert unit_actions("surveil-trendforce-page-monitor.service") == []
    assert unit_actions("surveil-china-media.timer") == []
    assert "run_once" in unit_actions("surveil-research-collector.timer")
    assert RUN_ONCE_TARGETS["surveil-research-collector.timer"] == "surveil-research-collector.service"
    assert "run_once" in unit_actions("surveil-official-collector.timer")
    assert RUN_ONCE_TARGETS["surveil-official-collector.timer"] == "surveil-official-collector.service"
    assert "run_once" in unit_actions("surveil-news-collector.timer")
    assert RUN_ONCE_TARGETS["surveil-news-collector.timer"] == "surveil-news-collector.service"
    assert "run_once" in unit_actions("surveil-value-directory.timer")
    assert RUN_ONCE_TARGETS["surveil-value-directory.timer"] == "surveil-value-directory.service"
    assert all("-shadow" not in unit for unit in holdings_web.ALLOWED_SYSTEMD_UNITS)
    assert "05:00 / 21:00" in holdings_web.UNIT_METADATA["surveil-value-directory.timer"]["schedule"]
    assert unit_actions("surveil-holdings-web.service") == ["status"]
    assert unit_actions("ssh.service") == []
    assert "surveil-company-disclosures.service" in SERVICE_UNITS


def systemd_fixture(unit: str, values: dict[str, str]) -> dict[str, object]:
    payload: dict[str, object] = {"Id": unit, **values}
    payload.update(unit_display_metadata(unit, payload))
    payload["actions"] = unit_actions(unit)
    return payload


def test_health_tasks_pair_timer_with_service_and_prefer_execution_result() -> None:
    timer = systemd_fixture(
        "surveil-company-disclosures.timer",
        {
            "ActiveState": "active",
            "SubState": "waiting",
            "Result": "success",
            "NextElapseUSecRealtime": "Sun 2026-07-12 20:00:00 CST",
        },
    )
    service = systemd_fixture(
        "surveil-company-disclosures.service",
        {
            "ActiveState": "failed",
            "SubState": "failed",
            "Result": "failed",
            "ExecMainStatus": "1",
            "ExecMainStartTimestamp": "Sun 2026-07-12 08:00:01 CST",
        },
    )
    tasks = build_health_tasks([timer, service])
    task = next(item for item in tasks if item["Id"] == "surveil-company-disclosures")
    assert task["label"] == "公司公告 / 巨潮资讯"
    assert task["schedule_status"] == "等待下次触发"
    assert task["execution_status"] == "最近运行失败（exit 1）"
    assert task["action_unit"]["Id"] == "surveil-company-disclosures.timer"
    assert task["timer"]["Id"] == "surveil-company-disclosures.timer"
    assert task["service"]["Id"] == "surveil-company-disclosures.service"


def test_health_tasks_show_disabled_timer_and_last_success_separately() -> None:
    timer = systemd_fixture(
        "surveil-news-collector.timer",
        {"ActiveState": "inactive", "SubState": "dead", "Result": "success"},
    )
    service = systemd_fixture(
        "surveil-news-collector.service",
        {"ActiveState": "inactive", "SubState": "dead", "Result": "success", "ExecMainStatus": "0"},
    )
    task = next(item for item in build_health_tasks([timer, service]) if item["Id"] == "surveil-news-collector")
    assert task["schedule_status"] == "定时器未启用"
    assert task["execution_status"] == "上次运行成功"


def test_health_summary_counts_one_failed_logical_task() -> None:
    timer = systemd_fixture(
        "surveil-value-directory.timer",
        {"ActiveState": "active", "SubState": "waiting", "Result": "success"},
    )
    service = systemd_fixture(
        "surveil-value-directory.service",
        {"ActiveState": "failed", "SubState": "failed", "Result": "failed", "ExecMainStatus": "1"},
    )
    tasks = build_health_tasks([timer, service])
    summary = build_health_summary(tasks, [])
    assert summary["total_failures"] == 1
    assert summary["task_failures"] == 1
    assert summary["source_failures"] == 0
    assert summary["issues"][0]["id"] == "surveil-value-directory"
    assert summary["issues"][0]["reason"] == "最近运行失败（exit 1）"


def test_health_summary_flags_disabled_production_timer_and_stopped_service() -> None:
    timer = systemd_fixture(
        "surveil-news-collector.timer",
        {"ActiveState": "inactive", "SubState": "dead", "Result": "success"},
    )
    timer_service = systemd_fixture(
        "surveil-news-collector.service",
        {"ActiveState": "inactive", "SubState": "dead", "Result": "success", "ExecMainStatus": "0"},
    )
    persistent = systemd_fixture(
        "surveil-sina-flash.service",
        {"ActiveState": "inactive", "SubState": "dead", "Result": "success", "ExecMainStatus": "0"},
    )
    summary = build_health_summary(build_health_tasks([timer, timer_service, persistent]), [])
    assert summary["task_failures"] == 2
    assert {item["reason"] for item in summary["issues"]} == {
        "生产定时器未启用",
        "常驻生产服务未运行",
    }


def test_health_summary_counts_only_enabled_failing_sources() -> None:
    profiles = [
        {
            "id": "enabled_source",
            "name": "启用来源",
            "enabled": True,
            "health_status": "failing",
            "consecutive_failures": 3,
        },
        {
            "id": "disabled_source",
            "name": "停用来源",
            "enabled": False,
            "health_status": "failing",
            "consecutive_failures": 8,
        },
        {
            "id": "healthy_source",
            "name": "健康来源",
            "enabled": True,
            "health_status": "ok",
            "consecutive_failures": 0,
        },
    ]
    summary = build_health_summary([], profiles)
    assert summary["total_failures"] == 1
    assert summary["source_failures"] == 1
    assert summary["issues"][0]["id"] == "enabled_source"
    assert summary["issues"][0]["reason"] == "来源连续失败 3 次"


def test_health_summary_excludes_raw_x_detail_without_failing_source_profile() -> None:
    source = {
        "monitor": "x_stream_detail",
        "source": "connection",
        "status": "failing",
        "consecutive_failures": 4,
    }
    enabled = [{"id": "x_serenity", "name": "X", "enabled": True, "health_status": "ok"}]
    assert ("x_stream_detail", "connection") in active_source_health_keys(enabled, [source])
    assert build_health_summary([], enabled)["source_failures"] == 0


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
    test_page_uses_extracted_assets_and_bounded_placeholders()
    test_media_keywords_use_one_master_list_and_a_title_only_subset()
    test_static_asset_routes_are_allowlisted()
    test_health_page_exposes_service_action_controls()
    test_retired_shadow_tasks_and_rule_comparison_are_not_exposed()
    test_source_profile_view_is_exposed()
    test_retired_rule_center_is_not_exposed()
    test_feedback_quality_view_is_exposed()
    test_holdings_page_marks_environment_and_related_keywords()
    test_event_center_search_filters_before_per_pipeline_limit()
    test_event_center_projects_current_feedback_across_active_stores()
    test_event_center_feedback_filter_applies_before_article_limit()
    test_event_center_can_show_baselines_and_filter_by_published_time()
    test_event_center_shows_company_disclosure_baseline_only_when_requested()
    test_event_center_does_not_duplicate_seen_item_projected_to_event()
    test_event_center_source_filter_uses_grouped_dropdown()
    test_source_profiles_group_six_categories()
    test_source_profiles_aggregate_wildcard_health()
    test_source_profile_local_config_roundtrip()
    test_source_profile_local_config_stays_private_across_replacement()
    test_source_profile_runtime_filters_and_flags()
    test_company_disclosure_provider_is_the_only_provider_runtime_override()
    test_company_disclosure_source_can_be_disabled_privately()
    test_source_profile_can_explicitly_remove_news_media_role()
    test_source_profile_runtime_note_reports_effective_counts()
    test_systemd_actions_are_whitelisted()
    test_health_tasks_pair_timer_with_service_and_prefer_execution_result()
    test_health_tasks_show_disabled_timer_and_last_success_separately()
    test_unit_display_metadata_includes_research_production_collector()
    test_unit_display_metadata_includes_official_production_collector()
    test_unit_display_metadata_includes_news_production_collector()
    print("holdings web checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
