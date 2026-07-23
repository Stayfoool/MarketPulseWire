#!/usr/bin/env python3
"""Run the canonical CI-safe regression suite."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

CI_SAFE_TESTS = (
    "test_ai_compute_supply_demand.py",
    "test_ai_credit_risk.py",
    "test_alphabstract_monitor.py",
    "test_analysis.py",
    "test_architecture_invariants.py",
    "test_attributed_research.py",
    "test_china_finance_media_monitor.py",
    "test_cninfo_disclosure_provider.py",
    "test_collector_direct_shadow.py",
    "test_collector_runtime.py",
    "test_collector_shadow_digest.py",
    "test_company_disclosures.py",
    "test_company_event_dedup.py",
    "test_content_flow.py",
    "test_decision_audit_integration.py",
    "test_decision_engine.py",
    "test_event_direct_dry_run.py",
    "test_event_normalization.py",
    "test_event_pipeline_convergence.py",
    "test_event_runtime.py",
    "test_gate_prompts.py",
    "test_holdings_save_flow.py",
    "test_holdings_web.py",
    "test_http_utils.py",
    "test_industry_fact_dedup.py",
    "test_industry_hardline.py",
    "test_international_bank_fed.py",
    "test_investment_bank_theme_config.py",
    "test_jygs_actions.py",
    "test_link_enrichment.py",
    "test_llm_analysis.py",
    "test_llm_json_recovery.py",
    "test_llm_rule_decision.py",
    "test_llm_rule_shadow.py",
    "test_macro_event_dedup.py",
    "test_macro_policy.py",
    "test_market_delivery.py",
    "test_market_feedback.py",
    "test_market_flow.py",
    "test_market_flow_adapters.py",
    "test_market_interpreter.py",
    "test_market_item.py",
    "test_market_review_store.py",
    "test_market_view.py",
    "test_media_keyword_config.py",
    "test_news_collector.py",
    "test_ocr_runtime.py",
    "test_official_collector.py",
    "test_portfolio_monitor.py",
    "test_production_admission.py",
    "test_push_rules.py",
    "test_research_collector.py",
    "test_rss_monitor_fetch.py",
    "test_rule_alert_dedup.py",
    "test_rule_center.py",
    "test_rule_core_v1.py",
    "test_rule_core_integration_v1.py",
    "test_rule_core_shadow.py",
    "test_rule_core_shadow_combined.py",
    "test_rule_core_shadow_daily.py",
    "test_rule_core_shadow_report.py",
    "test_rule_core_runtime_shadow.py",
    "test_rule_core_batch.py",
    "test_signals_extract.py",
    "test_sina_stock_news.py",
    "test_sina_zy_client.py",
    "test_skeptic_evaluator.py",
    "test_thin_push_cards.py",
    "test_time_utils.py",
    "test_trade_friction.py",
    "test_trade_policy_monitor.py",
    "test_trendforce_page_monitor.py",
    "test_value_directory_flow.py",
    "test_value_directory_monitor.py",
    "test_wallstreetcn_monitor.py",
    "test_web_evidence.py",
    "test_x_stream_health.py",
)

OPERATOR_SMOKE_TESTS = {
    "test_feishu.py": "Loads private configuration and sends a real Feishu message.",
    "test_feishu_card.py": "Fetches live X content and sends a real Feishu card.",
    "test_feishu_image.py": "Fetches live X media and uploads a real Feishu image.",
}


class ManifestError(RuntimeError):
    pass


def validate_manifest() -> None:
    discovered = {path.name for path in SCRIPTS.glob("test_*.py")}
    safe = set(CI_SAFE_TESTS)
    operator = set(OPERATOR_SMOKE_TESTS)
    problems: list[str] = []

    if len(safe) != len(CI_SAFE_TESTS):
        problems.append("CI_SAFE_TESTS contains duplicate entries")
    overlap = safe & operator
    if overlap:
        problems.append(f"tests classified as both CI-safe and operator smoke: {sorted(overlap)}")
    unclassified = discovered - safe - operator
    if unclassified:
        problems.append(f"unclassified test scripts: {sorted(unclassified)}")
    missing = (safe | operator) - discovered
    if missing:
        problems.append(f"manifest entries missing from scripts/: {sorted(missing)}")

    for path in (ROOT / ".github/workflows/ci.yml", ROOT / "Justfile"):
        text = path.read_text(encoding="utf-8")
        if text.count("scripts/run_test_suite.py") != 1:
            problems.append(f"{path.relative_to(ROOT)} must invoke scripts/run_test_suite.py exactly once")
        direct_tests = sorted(set(re.findall(r"scripts/(test_[A-Za-z0-9_]+\.py)", text)))
        if direct_tests:
            problems.append(
                f"{path.relative_to(ROOT)} maintains direct test commands outside the manifest: {direct_tests}"
            )

    if problems:
        raise ManifestError("; ".join(problems))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate classification without running tests")
    parser.add_argument("--list", action="store_true", help="print the classified test scripts")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        validate_manifest()
    except ManifestError as exc:
        print(f"test manifest invalid: {exc}", file=sys.stderr)
        return 2

    print(
        f"test manifest valid: {len(CI_SAFE_TESTS)} CI-safe, "
        f"{len(OPERATOR_SMOKE_TESTS)} operator-only",
        flush=True,
    )
    if args.list:
        for filename in CI_SAFE_TESTS:
            print(f"ci-safe\t{filename}")
        for filename, reason in OPERATOR_SMOKE_TESTS.items():
            print(f"operator-smoke\t{filename}\t{reason}")
    if args.check or args.list:
        return 0

    test_env = os.environ.copy()
    test_env["RULE_CORE_CONFIG"] = str(ROOT / "config" / "rule_core_v1.test.json")
    test_env["RULE_CORE_SHADOW_CONFIG"] = str(ROOT / "config" / "rule_core_v1.test.json")
    for index, filename in enumerate(CI_SAFE_TESTS, start=1):
        print(f"[{index}/{len(CI_SAFE_TESTS)}] {filename}", flush=True)
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / filename)],
            cwd=ROOT,
            env=test_env,
            check=False,
        )
        if result.returncode != 0:
            print(f"FAILED: {filename} (exit {result.returncode})", file=sys.stderr)
            return result.returncode

    print(f"all {len(CI_SAFE_TESTS)} CI-safe regression scripts passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
