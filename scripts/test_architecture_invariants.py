#!/usr/bin/env python3
"""Static and behavioral checks for the market-processing architecture contract."""

from __future__ import annotations

import ast
import re
from dataclasses import fields
from pathlib import Path
from tempfile import TemporaryDirectory

from market_db import init_db
from market_item import DecisionResult, InterpretationResult, MarketFlowResult, NormalizedMarketItem
from source_profiles import build_profiles


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

UNIFIED_ITEM_COLLECTORS = (
    "rss_monitor.py",
    "trendforce_page_monitor.py",
    "alphabstract_monitor.py",
    "trade_policy_monitor.py",
    "china_finance_media_monitor.py",
    "sina_flash.py",
    "sina_stock_news.py",
    "company_disclosures.py",
    "value_directory_monitor.py",
)

UNIFIED_FETCHERS = {
    "research_collector.py",
    "official_collector.py",
    "news_collector.py",
    *UNIFIED_ITEM_COLLECTORS,
}

UNIFIED_STORAGE_RUNTIME_MODULES = {
    "market_daily.py",
    "holdings_web.py",
    "market_canonical_reader.py",
    "market_delivery.py",
    "market_feedback.py",
    "market_flow.py",
    "market_store.py",
    "news_collector.py",
    "official_collector.py",
    "sina_flash.py",
    "sina_stock_news.py",
    "value_directory_monitor.py",
}

LEGACY_RESULT_TABLES = (
    "article_reviews",
    "official_news_reviews",
    "events",
    "event_analyses",
)

LEGACY_RESULT_HELPERS = {
    "article_review_exists",
    "event_row_by_id",
    "insert_event_analysis_in_conn",
    "latest_event_analysis",
    "mark_article_pushed",
    "mark_official_pushed",
    "official_review_exists",
    "save_article_review",
    "save_official_review",
    "upsert_event_record",
}

REMOVED_COMPATIBILITY_MODULES = (
    "market_runtime.py",
    "market_content_adapter.py",
    "market_event_adapter.py",
    "market_flow_adapters.py",
    "market_view.py",
    "x_monitor.py",
    "rule_center.py",
    "web_evidence.py",
    "article_gate.py",
    "official_news_gate.py",
    "official_news_daily.py",
    "content_runtime.py",
    "event_runtime.py",
    "market_content_flow.py",
    "market_event_flow.py",
    "event_pipeline.py",
    "market_lifecycle_v1.py",
    "market_review_store.py",
    "market_storage_audit.py",
    "market_storage_migration.py",
    "rule_config_migration_v1.py",
    "run_production_with_rule_shadow.py",
    "collector_shadow_digest.py",
    "llm_rule_shadow.py",
    "rule_core_fixture.py",
    "rule_core_shadow_combined.py",
    "rule_core_shadow_daily.py",
    "rule_shadow_report_store.py",
    "ifind_batch.py",
    "ifind_client.py",
    "ifind_notice_pdf.py",
    "jygs_actions.py",
    "market_skills.py",
    "monitor.py",
    "portfolio_monitor.py",
    "signal_digest.py",
    "signal_outcome_update.py",
    "signal_review.py",
    "signal_store.py",
    "signals_extract.py",
    "smoke_ifind.py",
    "backfill_llm_decision_web_projection.py",
    "backfill_llm_insufficient_evidence.py",
    "investment_bank_theme_taxonomy.py",
    "migrate_admission_simplification.py",
    "migrate_media_keywords.py",
    "repair_market_feedback_snapshots.py",
)

REMOVED_OPERATOR_PATHS = (
    SCRIPTS / "install_launchd.sh",
    SCRIPTS / "uninstall_launchd.sh",
    SCRIPTS / "write_remote_ifind_token.sh",
    SCRIPTS / "write_remote_jygs_cookie.sh",
    ROOT / "config" / "investment_bank_theme_rules.example.json",
    ROOT / "docs" / "roadmap.md",
)

INDEPENDENT_ROUTE_EXCEPTIONS = {
    "x_stream.py": {
        "reason": "X thread/media semantics and stream retry state use a dedicated card route.",
        "boundary": "X collection, interpretation, seen_posts state, and delivery only.",
        "test": "test_x_stream_health.py",
    },
}

DIRECT_URLLIB_EXCEPTIONS = {
    "disclosure_document.py": {
        "kind": "bounded_stream",
        "reason": "PDF downloads enforce a byte limit while streaming to an atomic temporary file.",
        "test": "test_company_disclosures.py",
    },
    "download_mihomo.py": {
        "kind": "operator_tool",
        "reason": "Standalone installer runs before the project runtime and downloads an official release artifact.",
    },
    "feishu.py": {
        "kind": "legacy_bounded_request",
        "reason": "Legacy operational custom-webhook sender retains provider-specific signing and retry behavior pending separate migration.",
        "test": "test_market_delivery.py",
    },
    "feishu_app.py": {
        "kind": "legacy_bounded_request",
        "reason": "Application-bot API error and response contracts require a separate Feishu transport change.",
        "test": "test_market_feedback.py",
    },
    "feishu_image.py": {
        "kind": "bounded_binary",
        "reason": "Feishu image upload and download paths carry bounded binary payloads and provider-specific errors.",
        "test": "test_market_delivery.py",
    },
    "link_enrichment.py": {
        "kind": "legacy_bounded_request",
        "reason": "Generic link probing has redirect and content-boundary behavior that needs a dedicated migration.",
        "test": "test_link_enrichment.py",
    },
    "llm_analysis.py": {
        "kind": "provider_specialized",
        "reason": "LLM calls retain balance detection, provider response-body errors, model controls, and retry logging.",
        "test": "test_llm_analysis.py",
    },
    "update_mihomo_config.py": {
        "kind": "operator_tool",
        "reason": "Standalone proxy configuration updater runs outside the collector HTTP runtime.",
    },
    "value_directory_preview.py": {
        "kind": "bounded_binary",
        "reason": "ValueList preview handling downloads bounded image payloads before OCR and has dedicated model fallbacks.",
        "test": "test_value_directory_monitor.py",
    },
    "x_check.py": {
        "kind": "operator_tool",
        "reason": "Standalone X credential diagnostic is not a production collector transport.",
    },
    "x_stream.py": {
        "kind": "long_lived_stream",
        "reason": "X uses an indefinite streaming response plus dedicated reconnect and thread/media semantics.",
        "test": "test_x_stream_health.py",
    },
}

FORBIDDEN_ITEM_CALLS = {
    "deliver_article_review",
    "deliver_official_review",
    "deliver_event",
    "mark_article_pushed",
    "mark_official_pushed",
    "reserve_rule_alert",
    "save_article_review",
    "save_official_review",
    "send_card",
    "send_card_with_response",
}

ALLOWED_OPERATIONAL_CALLS: set[tuple[str, str, str]] = set()


class CallVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.function_stack: list[str] = []
        self.calls: list[tuple[str, str, int]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        else:
            name = ""
        if name:
            owner = self.function_stack[-1] if self.function_stack else "<module>"
            self.calls.append((owner, name, node.lineno))
        self.generic_visit(node)


def parsed_module(filename: str) -> ast.Module:
    return ast.parse((SCRIPTS / filename).read_text(encoding="utf-8"), filename=filename)


def test_active_runtime_has_no_legacy_result_table_reads_or_writes() -> None:
    sql_pattern = re.compile(
        r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?"
        r"(article_reviews|official_news_reviews|events|event_analyses)\b",
        re.IGNORECASE,
    )
    violations: list[str] = []
    for filename in sorted(UNIFIED_STORAGE_RUNTIME_MODULES):
        path = SCRIPTS / filename
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=filename)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for match in sql_pattern.finditer(node.value):
                    violations.append(f"{filename}:{node.lineno} SQL {match.group(1)}")
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                else:
                    name = ""
                if name in LEGACY_RESULT_HELPERS:
                    violations.append(f"{filename}:{node.lineno} call {name}")
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in LEGACY_RESULT_HELPERS:
                        violations.append(f"{filename}:{node.lineno} import {alias.name}")
    assert not violations, "active runtime depends on legacy result storage: " + "; ".join(violations)


def test_unified_collectors_use_runtime_without_owning_delivery() -> None:
    for filename in UNIFIED_ITEM_COLLECTORS:
        tree = parsed_module(filename)
        visitor = CallVisitor()
        visitor.visit(tree)
        assert any(name == "process_market_item" for _, name, _ in visitor.calls), filename
        forbidden = []
        for owner, name, lineno in visitor.calls:
            if name not in FORBIDDEN_ITEM_CALLS:
                continue
            if (filename, owner, name) in ALLOWED_OPERATIONAL_CALLS:
                continue
            forbidden.append(f"{filename}:{lineno} {owner} -> {name}")
        assert not forbidden, "collector owns store/delivery calls: " + "; ".join(forbidden)


def test_live_unified_collector_calls_cannot_omit_production_admission() -> None:
    for filename in UNIFIED_ITEM_COLLECTORS:
        tree = parsed_module(filename)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else ""
            if name != "process_market_item":
                continue
            keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
            baseline = keywords.get("baseline_only")
            is_literal_baseline = isinstance(baseline, ast.Constant) and baseline.value is True
            if is_literal_baseline:
                continue
            assert "production_admission" in keywords, (
                f"{filename}:{node.lineno} live process_market_item call omits production_admission"
            )
            assert "production_portfolio" in keywords, (
                f"{filename}:{node.lineno} live process_market_item call omits production_portfolio"
            )


def test_production_decision_boundary_is_llm_only() -> None:
    flow = (SCRIPTS / "market_flow.py").read_text(encoding="utf-8")
    engine = (SCRIPTS / "decision_engine.py").read_text(encoding="utf-8")
    production = (SCRIPTS / "llm_production_decision.py").read_text(encoding="utf-8")
    assert "from decision_engine import decide_market_item_with_llm" in flow
    assert flow.count("decide_market_item_with_llm(") == 1
    assert "def decide_market_item_with_llm(" in engine
    assert "def decide_market_item(" not in engine
    assert "from llm_production_decision import decide_production_market_item" in engine
    assert "from llm_rule_execution import LLMRuleExecution, execute_llm_rule_decision" in production
    assert 'os.environ.get("LLM_THINKING_TYPE")' in production
    assert "RULE_COMPARISON_LLM_THINKING_TYPE" not in production
    assert "class ProductionLLMInsufficientEvidence" in production
    assert 'if status == "uncertain"' in production
    for collector in (
        "rss_monitor.py",
        "trendforce_page_monitor.py",
        "alphabstract_monitor.py",
        "china_finance_media_monitor.py",
        "trade_policy_monitor.py",
        "value_directory_monitor.py",
        "sina_flash.py",
        "sina_stock_news.py",
        "company_disclosures.py",
    ):
        assert "processing_failure_status" in (SCRIPTS / collector).read_text(encoding="utf-8")
    assert "RULE_COMPARISON_CANDIDATE" not in production
    for forbidden in (
        "first_matching_push_rule",
        "apply_deterministic_source_controls",
        "apply_skeptic_review",
        "apply_event_push_rules",
        "market_delivery",
        "complete_market_review",
    ):
        assert forbidden not in production
    assert "market_content_adapter" not in flow
    assert "market_event_adapter" not in flow
    assert "importlib" not in flow
    assert "decide_market_item(" not in flow
    assert not (SCRIPTS / "rule_core_v1.py").exists()
    assert not (SCRIPTS / "rule_core_runtime_shadow.py").exists()


def test_market_information_contract_has_no_type_routing() -> None:
    schema = (SCRIPTS / "market_db.py").read_text(encoding="utf-8")
    flow = (SCRIPTS / "market_flow.py").read_text(encoding="utf-8")
    delivery = (SCRIPTS / "market_delivery.py").read_text(encoding="utf-8")
    store = (SCRIPTS / "market_store.py").read_text(encoding="utf-8")
    reader = (SCRIPTS / "market_canonical_reader.py").read_text(encoding="utf-8")
    feedback = (SCRIPTS / "market_feedback.py").read_text(encoding="utf-8")
    backend = (SCRIPTS / "holdings_web.py").read_text(encoding="utf-8")
    frontend = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    for retired in (
        "market_item_aliases",
        "item_kind",
        "legacy_item_id",
        "legacy_store_kind",
        "result_event_id",
        "trg_seen_items_market_insert",
        "trg_seen_items_market_update",
    ):
        assert retired not in schema
    for retired in (
        "store_kind",
        "item_kind",
        "source_event_id",
        "evaluate_content_item",
        "evaluate_event_item",
        "deliver_article_review",
        "deliver_official_review",
        "deliver_event",
        "repair_missing_feedback_snapshots",
        "market_ids_for_review",
    ):
        assert retired not in flow
        assert retired not in delivery
        assert retired not in store
        assert retired not in reader
        assert retired not in feedback
    assert "def normalize_market_item(" in flow
    assert "def process_market_item(" in flow
    assert flow.count("deliver_market_item(") == 1
    assert "def deliver_market_item(" in delivery
    assert "build_market_item_card" in delivery
    assert "def canonical_market_rows(" in reader
    assert 'if parsed.path == "/api/market-items"' in backend
    assert "/api/events" not in backend
    assert "/api/events" not in frontend
    assert "item.kind" not in frontend


def test_decision_result_has_no_retired_derived_fields() -> None:
    retired_decision_fields = {
        "importance",
        "need_llm_interpretation",
        "need_limited_llm_judgement",
        "should_push",
    }
    assert retired_decision_fields.isdisjoint(field.name for field in fields(DecisionResult))
    decision = DecisionResult(action="push")
    assert not hasattr(decision, "should_push")
    assert retired_decision_fields.isdisjoint(decision.to_dict())

    result = MarketFlowResult(
        item=NormalizedMarketItem(source="test", title="test"),
        decision=decision,
        interpretation=InterpretationResult(),
    )
    assert "delivery_intent" not in {field.name for field in fields(MarketFlowResult)}
    assert "delivery_intent" not in result.audit_payload()


def test_production_collectors_have_no_shadow_path() -> None:
    assert not (SCRIPTS / "overseas_media_monitor.py").exists()
    for filename in (
        "research_collector.py",
        "official_collector.py",
        "news_collector.py",
        "trade_policy_monitor.py",
        "value_directory_monitor.py",
    ):
        source = (SCRIPTS / filename).read_text(encoding="utf-8")
        for retired in (
            "collect_shadow",
            "shadow_collect",
            "shadow_dry_run",
            "save_shadow_state",
            "--shadow",
            "--production",
        ):
            assert retired not in source, f"{filename}: retired collector path returned: {retired}"

    sina_flash = (SCRIPTS / "sina_flash.py").read_text(encoding="utf-8")
    assert "sina_symbol_to_ifind" not in sina_flash
    assert "ifind" not in sina_flash.casefold()

    source_profiles = (SCRIPTS / "source_profiles.py").read_text(encoding="utf-8")
    frontend = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    editable_fields = source_profiles.split("EDITABLE_OVERRIDE_FIELDS = {", 1)[1].split("}", 1)[0]
    assert '"frequency"' not in editable_fields
    assert '"proxy_profile"' not in editable_fields
    assert 'data-field="frequency"' not in frontend
    assert 'data-field="proxy_profile"' not in frontend


def test_removed_compatibility_modules_do_not_return() -> None:
    for filename in REMOVED_COMPATIBILITY_MODULES:
        assert not (SCRIPTS / filename).exists(), filename
    forbidden_imports = {Path(name).stem for name in REMOVED_COMPATIBILITY_MODULES}
    for path in SCRIPTS.glob("*.py"):
        if path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        assert not (imports & forbidden_imports), f"{path.name}: {sorted(imports & forbidden_imports)}"
    for path in REMOVED_OPERATOR_PATHS:
        assert not path.exists(), path
    assert not list((ROOT / "launchd").glob("*.plist"))


def test_retired_management_flows_do_not_return() -> None:
    schema = (SCRIPTS / "market_db.py").read_text(encoding="utf-8")
    backend = (SCRIPTS / "holdings_web.py").read_text(encoding="utf-8")
    frontend = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    settings = (SCRIPTS / "settings_store.py").read_text(encoding="utf-8")
    for retired_table in ("relation_suggestions", "rule_config_audit", "web_evidence_runs", "web_evidence_docs"):
        assert retired_table not in schema
    assert "/api/relation-suggestions" not in backend
    assert "/api/relation-suggestions" not in frontend
    assert "WEB_EVIDENCE_" not in settings
    assert "SKEPTIC_" not in settings


def test_market_db_is_the_only_production_schema_initializer() -> None:
    schema_path = SCRIPTS / "market_db.py"
    schema_source = schema_path.read_text(encoding="utf-8")
    assert "ALTER TABLE" not in schema_source
    assert "migrate_schema" not in schema_source

    for path in SCRIPTS.glob("*.py"):
        if path.name.startswith("test_") or path == schema_path:
            continue
        source = path.read_text(encoding="utf-8")
        assert "CREATE TABLE" not in source, path.name
        assert "ALTER TABLE" not in source, path.name
        assert "init_db(" not in source, path.name

    with TemporaryDirectory() as tmpdir:
        with init_db(Path(tmpdir) / "surveil.sqlite3") as conn:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert {"seen_posts", "x_stream_health", "source_health"} <= tables
            seen_post_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(seen_posts)").fetchall()
            }
            assert {
                "delivery_status",
                "delivered_at",
                "delivery_error",
                "delivery_attempts",
            } <= seen_post_columns


def test_independent_routes_are_explicit_and_tested() -> None:
    for filename, contract in INDEPENDENT_ROUTE_EXCEPTIONS.items():
        assert (SCRIPTS / filename).exists(), filename
        assert contract["reason"].strip()
        assert contract["boundary"].strip()
        assert (SCRIPTS / contract["test"]).exists(), contract["test"]


def test_direct_urllib_request_usage_is_explicit_and_bounded() -> None:
    actual: set[str] = set()
    for path in SCRIPTS.glob("*.py"):
        if path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(alias.name == "urllib.request" for alias in node.names):
                actual.add(path.name)
            elif isinstance(node, ast.ImportFrom) and (
                node.module == "urllib.request"
                or (node.module == "urllib" and any(alias.name == "request" for alias in node.names))
            ):
                actual.add(path.name)
    assert actual == set(DIRECT_URLLIB_EXCEPTIONS), (
        f"direct urllib.request imports changed; register a specialized exception or use http_utils: "
        f"added={sorted(actual - set(DIRECT_URLLIB_EXCEPTIONS))} "
        f"removed={sorted(set(DIRECT_URLLIB_EXCEPTIONS) - actual)}"
    )
    for filename, contract in DIRECT_URLLIB_EXCEPTIONS.items():
        assert str(contract.get("kind") or "").strip(), filename
        assert str(contract.get("reason") or "").strip(), filename
        test = str(contract.get("test") or "").strip()
        if test:
            assert (SCRIPTS / test).exists(), (filename, test)


def test_deployment_preserves_private_state_and_retires_unused_units() -> None:
    deploy = (SCRIPTS / "deploy_remote.sh").read_text(encoding="utf-8")
    installer = (SCRIPTS / "install_remote_systemd.sh").read_text(encoding="utf-8")
    sync = (SCRIPTS / "remote_code_sync.sh").read_text(encoding="utf-8")
    assert 'local private_proxy_prefix="shadowsocks_"' in sync
    assert 'local private_proxy_yaml_pattern="${private_proxy_prefix}*.yaml"' in sync
    assert '--exclude "$private_proxy_yaml_pattern"' in sync
    assert "--exclude '.git/'" in sync
    assert "--exclude 'REVISION'" in sync
    assert "--exclude '.paddleocr/'" in sync
    assert "--exclude 'reports/'" in sync
    assert "--exclude 'config/llm_decision_rules.json'" in sync
    assert "remote_code_sync overlay" in deploy
    assert "--delete" not in deploy
    assert "RULE_CORE_CONFIG_PATH=" in installer
    assert "RULE_CORE_CONFIG 未配置或文件不存在" in installer
    assert "RULE_CORE_CONFIG 对生产服务账号不可读" in installer
    assert "LLM_DECISION_RULE_CONFIG_PATH=" in installer
    assert "LLM_DECISION_RULE_CONFIG 文件权限必须为 0600" in installer
    assert "LLM_DECISION_RULE_CONFIG 内容校验失败" in installer
    for service_path in (ROOT / "systemd").glob("*.service"):
        assert "UMask=0077" in service_path.read_text(encoding="utf-8"), service_path.name
    assert "find '$REMOTE_DIR/logs' -maxdepth 1 -type f -exec chmod 600 {} +" in installer
    assert "chmod 600 '$REMOTE_DIR/logs/feishu-feedback.log' '$REMOTE_DIR/logs/feishu-feedback.err.log'" in installer
    for service in ("surveil-feishu-feedback.service", "surveil-x-stream.service"):
        enable = f"systemctl enable --now {service}"
        restart = f"systemctl restart {service}"
        assert installer.count(enable) == 1
        assert installer.count(restart) == 1
        assert installer.index(enable) < installer.index(restart)
    for unit in ("surveil-article-daily.service", "surveil-article-daily.timer"):
        assert unit in installer
        assert not (ROOT / "systemd" / unit).exists()
        assert unit not in (SCRIPTS / "holdings_web.py").read_text(encoding="utf-8")
    for retired_name in (
        "collector-shadow",
        "rule-shadow",
        "surveil-ifind",
        "surveil-jygs",
        "surveil-signals",
    ):
        assert retired_name not in installer
    for timer in (
        "surveil-research-collector.timer",
        "surveil-official-collector.timer",
        "surveil-news-collector.timer",
    ):
        assert installer.count(f"systemctl enable --now {timer}") == 1
    assert "DISABLE_LEGACY_" not in installer
    assert "ENABLE_JYGS_TIMER" not in installer


def test_interval_timer_activation_policy_after_deployment() -> None:
    expectations = {
        "surveil-sina-stock-news.timer": ("OnActiveSec=5min", "OnUnitActiveSec=30min"),
    }
    for filename, expected_lines in expectations.items():
        lines = {
            line.strip()
            for line in (ROOT / "systemd" / filename).read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        assert not any(line.startswith("OnBootSec=") for line in lines), filename
        assert set(expected_lines) <= lines

    installer = (ROOT / "scripts" / "install_remote_systemd.sh").read_text(encoding="utf-8")
    for unit in ("surveil-sina-stock-news.timer",):
        enable = f"systemctl enable --now {unit}"
        restart = f"systemctl restart {unit}"
        assert installer.count(enable) == 1
        assert installer.count(restart) == 1
        assert installer.index(enable) < installer.index(restart)

def test_value_directory_runs_twice_daily_without_enabling_a_disabled_timer() -> None:
    timer = (ROOT / "systemd" / "surveil-value-directory.timer").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 05:00:00 Asia/Shanghai" in timer
    assert "OnCalendar=*-*-* 21:00:00 Asia/Shanghai" in timer
    assert "OnCalendar=*-*-* 08:00:00" not in timer

    installer = (ROOT / "scripts" / "install_remote_systemd.sh").read_text(encoding="utf-8")
    assert "systemctl is-enabled --quiet surveil-value-directory.timer" in installer
    assert installer.count("systemctl restart surveil-value-directory.timer") == 1
    assert "systemctl enable --now surveil-value-directory.timer" not in installer


def test_source_profiles_have_complete_runtime_ownership() -> None:
    profiles = build_profiles()
    ids = [profile.id for profile in profiles]
    assert len(ids) == len(set(ids))
    required_text = (
        "id",
        "category",
        "name",
        "source_type",
        "fetch_range",
        "filter_policy",
        "frequency",
        "runtime_shape",
        "pipeline",
        "fetcher",
    )
    for profile in profiles:
        for field in required_text:
            assert str(getattr(profile, field) or "").strip(), f"{profile.id}.{field}"
        assert profile.service_units, f"{profile.id}.service_units"
        assert profile.health_keys, f"{profile.id}.health_keys"
        if profile.id == "x_serenity":
            assert "x_stream.py" in profile.fetcher
            continue
        assert any(fetcher in profile.fetcher for fetcher in UNIFIED_FETCHERS), (
            profile.id,
            profile.fetcher,
        )


def main() -> int:
    test_active_runtime_has_no_legacy_result_table_reads_or_writes()
    test_unified_collectors_use_runtime_without_owning_delivery()
    test_live_unified_collector_calls_cannot_omit_production_admission()
    test_production_decision_boundary_is_llm_only()
    test_market_information_contract_has_no_type_routing()
    test_decision_result_has_no_retired_derived_fields()
    test_production_collectors_have_no_shadow_path()
    test_removed_compatibility_modules_do_not_return()
    test_retired_management_flows_do_not_return()
    test_market_db_is_the_only_production_schema_initializer()
    test_independent_routes_are_explicit_and_tested()
    test_direct_urllib_request_usage_is_explicit_and_bounded()
    test_deployment_preserves_private_state_and_retires_unused_units()
    test_interval_timer_activation_policy_after_deployment()
    test_value_directory_runs_twice_daily_without_enabling_a_disabled_timer()
    test_source_profiles_have_complete_runtime_ownership()
    print("architecture invariant checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
