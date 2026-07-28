#!/usr/bin/env python3
"""Static and behavioral checks for the market-processing architecture contract."""

from __future__ import annotations

import ast
import re
from pathlib import Path

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
    "ifind_batch.py",
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
    "article_daily.py",
    "holdings_web.py",
    "market_canonical_reader.py",
    "market_content_adapter.py",
    "market_delivery.py",
    "market_event_adapter.py",
    "market_feedback.py",
    "market_runtime.py",
    "market_store.py",
    "news_collector.py",
    "official_collector.py",
    "official_news_daily.py",
    "rule_center.py",
    "signals_extract.py",
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
    "article_gate.py",
    "official_news_gate.py",
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
)

INDEPENDENT_ROUTE_EXCEPTIONS = {
    "x_stream.py": {
        "reason": "X thread/media semantics and stream retry state use a dedicated card route.",
        "boundary": "X collection, interpretation, seen_posts state, and delivery only.",
        "test": "test_x_stream_health.py",
    },
    "jygs_actions.py": {
        "reason": "Optional JYGS action prediction remains a disabled-by-default legacy product path.",
        "boundary": "JYGS action rows and their dedicated prediction card only.",
        "test": "test_jygs_actions.py",
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
    "ifind_client.py": {
        "kind": "legacy_bounded_request",
        "reason": "The licensed iFinD client retains its current token and API error contract pending separate retirement or migration.",
        "test": "test_analysis.py",
    },
    "jygs_actions.py": {
        "kind": "independent_legacy_route",
        "reason": "Disabled-by-default JYGS prediction is an explicit independent legacy route.",
        "test": "test_jygs_actions.py",
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
    "portfolio_monitor.py": {
        "kind": "legacy_bounded_request",
        "reason": "Legacy portfolio enrichment combines several external API contracts and is not part of the CNINFO provider path.",
        "test": "test_analysis.py",
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

ALLOWED_OPERATIONAL_CALLS = {
    ("ifind_batch.py", "send_batch_summary_card", "send_card"),
}


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
    runtime = (SCRIPTS / "market_runtime.py").read_text(encoding="utf-8")
    engine = (SCRIPTS / "decision_engine.py").read_text(encoding="utf-8")
    production = (SCRIPTS / "llm_production_decision.py").read_text(encoding="utf-8")
    content_adapter = (SCRIPTS / "market_content_adapter.py").read_text(encoding="utf-8")
    event_adapter = (SCRIPTS / "market_event_adapter.py").read_text(encoding="utf-8")
    flow = (SCRIPTS / "market_flow.py").read_text(encoding="utf-8")

    assert "from decision_engine import decide_market_item_with_llm" in runtime
    assert runtime.count("decide_market_item_with_llm(") == 2
    assert "def decide_market_item_with_llm(" in engine
    assert "def decide_market_item(" not in engine
    assert "from llm_production_decision import decide_production_market_item" in engine
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
    assert "apply_skeptic_review" not in content_adapter
    assert "attach_decision_result_to_event_analysis(decision, {})" in event_adapter
    assert "decide_market_item(" not in flow
    assert not (SCRIPTS / "rule_core_v1.py").exists()
    assert not (SCRIPTS / "rule_core_runtime_shadow.py").exists()


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


def test_deployment_preserves_private_proxy_state_and_disables_shadows() -> None:
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
    for service in ("surveil-feishu-feedback.service", "surveil-x-stream.service"):
        enable = f"systemctl enable --now {service}"
        restart = f"systemctl restart {service}"
        assert installer.count(enable) == 1
        assert installer.count(restart) == 1
        assert installer.index(enable) < installer.index(restart)
    shadow_timers = (
        "surveil-research-collector-shadow.timer",
        "surveil-official-collector-shadow.timer",
        "surveil-news-collector-shadow.timer",
        "surveil-collector-shadow-digest.timer",
    )
    for timer in shadow_timers:
        assert f"systemctl disable --now {timer}" in installer
        assert f"systemctl enable --now {timer}" not in installer


def test_interval_timer_activation_policy_after_deployment() -> None:
    expectations = {
        "surveil-sina-stock-news.timer": ("OnActiveSec=5min", "OnUnitActiveSec=30min"),
        "surveil-signals-extract.timer": ("OnActiveSec=8min", "OnUnitActiveSec=10min"),
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

    signal_timers = (
        "surveil-signals-extract.timer",
        "surveil-signal-outcome.timer",
        "surveil-signal-review.timer",
        "surveil-signal-digest.timer",
    )
    for unit in signal_timers:
        assert f"systemctl enable --now {unit}" not in installer
        assert f"systemctl restart {unit}" not in installer
        assert unit in installer
    assert "投资信号复盘任务组默认关闭" in installer


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
    test_removed_compatibility_modules_do_not_return()
    test_independent_routes_are_explicit_and_tested()
    test_direct_urllib_request_usage_is_explicit_and_bounded()
    test_deployment_preserves_private_proxy_state_and_disables_shadows()
    test_interval_timer_activation_policy_after_deployment()
    test_value_directory_runs_twice_daily_without_enabling_a_disabled_timer()
    test_source_profiles_have_complete_runtime_ownership()
    print("architecture invariant checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
