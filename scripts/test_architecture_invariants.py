#!/usr/bin/env python3
"""Static and behavioral checks for the market-processing architecture contract."""

from __future__ import annotations

import ast
from pathlib import Path

from decision_engine import decide_market_item
from market_item import NormalizedMarketItem
from push_rules import ORDERED_FIRST_MATCH_RULE_IDS
from rule_center import ORDERED_FIRST_MATCH, PARALLEL_MERGE, RULE_DEFINITIONS
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

REMOVED_COMPATIBILITY_MODULES = (
    "article_gate.py",
    "official_news_gate.py",
    "content_runtime.py",
    "event_runtime.py",
    "market_content_flow.py",
    "market_event_flow.py",
    "event_pipeline.py",
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
        "test": "test_value_directory_flow.py",
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
    assert 'PRIVATE_PROXY_PREFIX="shadowsocks_"' in deploy
    assert 'PRIVATE_PROXY_YAML_PATTERN="${PRIVATE_PROXY_PREFIX}*.yaml"' in deploy
    assert '--exclude "$PRIVATE_PROXY_YAML_PATTERN"' in deploy
    assert "--exclude '.paddleocr/'" in deploy
    assert "--exclude 'reports/'" in deploy
    shadow_timers = (
        "surveil-research-collector-shadow.timer",
        "surveil-official-collector-shadow.timer",
        "surveil-news-collector-shadow.timer",
        "surveil-collector-shadow-digest.timer",
    )
    for timer in shadow_timers:
        assert f"systemctl disable --now {timer}" in installer
        assert f"systemctl enable --now {timer}" not in installer


def test_rule_center_execution_modes_match_runtime_ordering() -> None:
    ordered_runtime_ids = set(ORDERED_FIRST_MATCH_RULE_IDS)
    ordered_definition_ids: set[str] = set()
    for rule in RULE_DEFINITIONS:
        rule_id = str(rule["id"])
        mode = str(rule.get("execution_mode") or "")
        field_keys = {str(field["key"]) for field in rule.get("fields") or ()}
        assert mode in {ORDERED_FIRST_MATCH, PARALLEL_MERGE}, rule_id
        if mode == ORDERED_FIRST_MATCH:
            ordered_definition_ids.add(rule_id)
            assert "priority" in field_keys, rule_id
        else:
            assert "priority" not in field_keys, rule_id
    assert ordered_definition_ids == ordered_runtime_ids


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


def test_common_rule_is_stable_across_transport_metadata() -> None:
    text = "HBM supply shortage will persist until 2028 and prices are projected to double."
    variants = (
        NormalizedMarketItem(
            source="trendforce_semiconductors",
            source_category="research_industry_media",
            publisher_role="research_publisher",
            collector="rss_monitor",
            content_type="article",
            title=text,
        ),
        NormalizedMarketItem(
            source="sina_flash",
            source_category="news_media",
            publisher_role="news_media",
            collector="sina_flash",
            content_type="flash",
            title=text,
        ),
    )
    decisions = [decide_market_item(item, holdings=[]) for item in variants]
    assert {decision.action for decision in decisions} == {"push"}
    assert {decision.rule_hits[0]["rule_id"] for decision in decisions} == {"industry_quantified_hardline"}


def test_trade_friction_rule_is_stable_across_transport_metadata() -> None:
    text = "European Commission initiates an anti-subsidy investigation into battery electric vehicles from China."
    variants = (
        NormalizedMarketItem(
            source="eu_press_corner_trade_policy",
            source_category="official_policy",
            publisher_role="government_official",
            collector="trade_policy_monitor",
            content_type="official_policy",
            title=text,
        ),
        NormalizedMarketItem(
            source="cls_telegraph_api",
            source_category="news_media",
            publisher_role="news_media",
            collector="china_finance_media_monitor",
            content_type="article",
            title=text,
        ),
    )
    decisions = [decide_market_item(item, holdings=[]) for item in variants]
    assert {decision.action for decision in decisions} == {"push"}
    assert {decision.rule_hits[0]["rule_id"] for decision in decisions} == {"trade_friction_escalation"}


def test_ai_compute_rule_is_stable_across_transport_metadata() -> None:
    text = "Meta正在构建一项云业务，以出售其过剩的AI算力。"
    variants = (
        NormalizedMarketItem(
            source="cls_telegraph_api",
            source_category="news_media",
            publisher_role="news_media",
            collector="china_finance_media_monitor",
            content_type="article",
            title=text,
        ),
        NormalizedMarketItem(
            source="future_company_feed",
            source_category="official_company",
            publisher_role="company_official",
            collector="rss_monitor",
            content_type="official_news",
            title=text,
        ),
    )
    decisions = [decide_market_item(item, holdings=[]) for item in variants]
    assert {decision.action for decision in decisions} == {"push"}
    assert {decision.rule_hits[0]["rule_id"] for decision in decisions} == {"ai_compute_supply_demand"}
    assert {decision.dedup["dedup_key"] for decision in decisions} == {decisions[0].dedup["dedup_key"]}


def test_candidate_rule_core_is_side_effect_free_and_has_one_report_only_importer() -> None:
    core = parsed_module("rule_core_v1.py")
    imports: set[str] = set()
    for node in ast.walk(core):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert imports == {
        "__future__",
        "ai_compute_supply_demand",
        "ai_credit_risk",
        "hashlib",
        "international_bank_fed",
        "macro_policy",
        "re",
        "dataclasses",
        "trade_friction",
        "typing",
        "market_item",
    }
    for classifier_name in (
        "ai_compute_supply_demand.py",
        "ai_credit_risk.py",
        "international_bank_fed.py",
        "macro_policy.py",
        "trade_friction.py",
    ):
        classifier = parsed_module(classifier_name)
        top_level_imports: set[str] = set()
        for node in classifier.body:
            if isinstance(node, ast.Import):
                top_level_imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level_imports.add(node.module.split(".")[0])
        assert "rule_center" not in top_level_imports
    inactive_modules = {
        "rule_core_v1.py",
        "rule_core_fixture.py",
        "rule_core_replay.py",
        "rule_core_history_replay.py",
        "rule_core_shadow.py",
        "rule_core_shadow_combined.py",
        "rule_core_shadow_daily.py",
        "rule_core_shadow_report.py",
        "rule_core_runtime_shadow.py",
        "rule_config_migration_v1.py",
        "market_lifecycle_v1.py",
    }
    for path in SCRIPTS.glob("*.py"):
        if path.name.startswith("test_") or path.name in inactive_modules:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name not in {Path(name).stem for name in inactive_modules} for alias in node.names), path.name
            elif isinstance(node, ast.ImportFrom):
                assert node.module not in {Path(name).stem for name in inactive_modules}, path.name

    runtime_shadow = parsed_module("rule_core_runtime_shadow.py")
    runtime_imports: set[str] = set()
    for node in ast.walk(runtime_shadow):
        if isinstance(node, ast.Import):
            runtime_imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            runtime_imports.add(node.module.split(".")[0])
    assert "rule_core_v1" in runtime_imports
    assert "rule_core_shadow" in runtime_imports
    runtime_text = (SCRIPTS / "rule_core_runtime_shadow.py").read_text(encoding="utf-8")
    assert "market_delivery" not in runtime_text
    assert "connect_sqlite" not in runtime_text
    assert '"full_text"' not in runtime_text


def main() -> int:
    test_unified_collectors_use_runtime_without_owning_delivery()
    test_removed_compatibility_modules_do_not_return()
    test_independent_routes_are_explicit_and_tested()
    test_direct_urllib_request_usage_is_explicit_and_bounded()
    test_deployment_preserves_private_proxy_state_and_disables_shadows()
    test_rule_center_execution_modes_match_runtime_ordering()
    test_source_profiles_have_complete_runtime_ownership()
    test_common_rule_is_stable_across_transport_metadata()
    test_trade_friction_rule_is_stable_across_transport_metadata()
    test_ai_compute_rule_is_stable_across_transport_metadata()
    test_candidate_rule_core_is_side_effect_free_and_has_one_report_only_importer()
    print("architecture invariant checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
