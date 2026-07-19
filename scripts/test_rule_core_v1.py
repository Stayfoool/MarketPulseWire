#!/usr/bin/env python3
"""Regression checks for the side-effect-free v1 rule core and corpus."""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

from market_item import AdmissionEvidence, AdmissionResult, NormalizedMarketItem, RuleEvaluation
from rule_core_fixture import FixtureContractError, load_fixture_payload, validate_fixture_payload
from rule_core_v1 import (
    RuleConfigError,
    SourceAdmissionPolicy,
    evaluate_market_item,
    parse_portfolio_config,
    parse_rule_config,
)


ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "scripts" / "fixtures" / "rule_core_v1_cases.json"
CONFIG_PATH = ROOT / "config" / "rule_core_v1.test.json"


def loaded_contracts():
    corpus = load_fixture_payload(CORPUS_PATH)
    config = parse_rule_config(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    portfolios = {
        name: parse_portfolio_config(payload)
        for name, payload in corpus["portfolio_fixtures"].items()
    }
    return corpus, config, portfolios


def test_passive_admission_contract_rejects_invalid_combinations() -> None:
    evidence = AdmissionEvidence(
        rule_family="semiconductor_ai",
        reason_code="semiconductor_ai_scope",
        evidence_quote="HBM supply shortage",
        matched_term_ids=("HBM",),
    )
    admitted = AdmissionResult(
        status="admitted",
        reason_code="content_scope_match",
        matched_families=("semiconductor_ai",),
        evidence=(evidence,),
        config_version="test-v1",
    )
    assert admitted.to_dict()["rule_contract_version"] == "rule-core-v1"
    try:
        AdmissionResult(
            status="excluded",
            reason_code="out_of_scope",
            matched_families=("semiconductor_ai",),
            evidence=(),
            config_version="test-v1",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("excluded admission cannot expose matched families")
    excluded = AdmissionResult(
        status="excluded",
        reason_code="global_exclude",
        matched_families=(),
        evidence=(
            AdmissionEvidence(
                rule_family="global",
                reason_code="global_exclude",
                evidence_quote="培训广告",
            ),
        ),
        config_version="test-v1",
    )
    assert excluded.evidence[0].rule_family == "global"
    try:
        RuleEvaluation(admission=admitted, decision=None)
    except ValueError:
        pass
    else:
        raise AssertionError("admitted evaluation cannot omit DecisionResult")


def test_rule_config_and_fixture_schemas_fail_closed() -> None:
    config_payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    broken = copy.deepcopy(config_payload)
    broken["unknown"] = []
    try:
        parse_rule_config(broken)
    except RuleConfigError:
        pass
    else:
        raise AssertionError("unknown rule-config fields must fail closed")
    broken = copy.deepcopy(config_payload)
    broken["config_version"] = ""
    try:
        parse_rule_config(broken)
    except RuleConfigError:
        pass
    else:
        raise AssertionError("missing config version must fail closed")

    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    broken_corpus = copy.deepcopy(corpus)
    broken_corpus["cases"][0]["unknown"] = True
    try:
        validate_fixture_payload(broken_corpus)
    except FixtureContractError:
        pass
    else:
        raise AssertionError("unknown fixture fields must fail closed")
    broken_corpus = copy.deepcopy(corpus)
    broken_corpus["cases"][1]["variants"][0]["variant_id"] = broken_corpus["cases"][0]["variants"][0]["variant_id"]
    try:
        validate_fixture_payload(broken_corpus)
    except FixtureContractError:
        pass
    else:
        raise AssertionError("duplicate fixture IDs must fail closed")


def test_all_approved_v1_cases_are_executable() -> None:
    corpus, config, portfolios = loaded_contracts()
    assert len(corpus["cases"]) == 47
    assert sum(len(case["variants"]) for case in corpus["cases"]) == 52
    families = {case["family"] for case in corpus["cases"]}
    assert families == {"holding", "semiconductor_ai", "macro_data", "fed_policy", "trade_policy"}
    actions: set[str | None] = set()

    for case in corpus["cases"]:
        expected = case["expected"]
        variant_results: list[tuple[str, str | None]] = []
        for variant in case["variants"]:
            item = NormalizedMarketItem(
                source=variant["source"],
                source_category=variant["source_category"],
                publisher_role=variant["publisher_role"],
                content_type=variant["content_type"],
                title=case["text"],
                symbols=case["symbols"],
            )
            evaluation = evaluate_market_item(
                item,
                rule_config=config,
                portfolio=portfolios[case["portfolio_fixture"]],
                source_policy=SourceAdmissionPolicy(
                    tuple(variant.get("direct_admission_families", ()))
                ),
            )
            action = evaluation.decision.action if evaluation.decision is not None else None
            actions.add(action)
            assert evaluation.admission.status == expected["admission"], variant["variant_id"]
            assert action == expected["action"], variant["variant_id"]
            assert bool(evaluation.decision and evaluation.decision.should_push) is expected[
                "push_eligible"
            ], variant["variant_id"]
            if expected["rule_id"] is None:
                assert evaluation.decision is None, variant["variant_id"]
                if evaluation.admission.reason_code == "global_exclude":
                    assert evaluation.admission.evidence, variant["variant_id"]
                    assert {item.rule_family for item in evaluation.admission.evidence} == {"global"}
            else:
                assert evaluation.decision is not None
                assert expected["rule_id"] in {
                    hit["rule_id"] for hit in evaluation.decision.rule_hits
                }, variant["variant_id"]
                assert evaluation.decision.audit_json["config_version"] == "public-test-v1"
                assert evaluation.decision.audit_json["source_metadata_not_used_for_materiality"] is True
                for evidence in evaluation.admission.evidence:
                    assert all(term_id.startswith("term:") for term_id in evidence.matched_term_ids)
                    assert all(
                        term_id not in case["text"] for term_id in evidence.matched_term_ids
                    )
            variant_results.append((evaluation.admission.status, action))
        assert len(set(variant_results)) == 1, case["case_id"]
    assert actions == {"push", "daily", "archive", None}


def test_source_identity_and_llm_availability_cannot_change_core_result() -> None:
    corpus, config, portfolios = loaded_contracts()
    representatives = {"HOLD-003", "SEMI-008", "MACRO-001", "FED-004", "TRADE-001"}
    cases = {case["case_id"]: case for case in corpus["cases"] if case["case_id"] in representatives}
    assert set(cases) == representatives
    for case in cases.values():
        variant = case["variants"][0]
        common = {
            "source": variant["source"],
            "source_category": variant["source_category"],
            "publisher_role": variant["publisher_role"],
            "content_type": variant["content_type"],
            "title": case["text"],
            "symbols": case["symbols"],
        }
        source_policy = SourceAdmissionPolicy(tuple(variant.get("direct_admission_families", ())))
        plain = evaluate_market_item(
            NormalizedMarketItem(**common),
            rule_config=config,
            portfolio=portfolios[case["portfolio_fixture"]],
            source_policy=source_policy,
        )
        failed_llm = evaluate_market_item(
            NormalizedMarketItem(**common, raw={"llm_status": "failed"}),
            rule_config=config,
            portfolio=portfolios[case["portfolio_fixture"]],
            source_policy=source_policy,
        )
        assert failed_llm.to_dict() == plain.to_dict(), case["case_id"]


def test_new_core_is_not_wired_into_production_or_side_effect_modules() -> None:
    core_path = ROOT / "scripts" / "rule_core_v1.py"
    tree = ast.parse(core_path.read_text(encoding="utf-8"), filename=core_path.name)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported == {"__future__", "hashlib", "re", "dataclasses", "typing", "market_item"}

    allowed = {
        "rule_core_v1.py",
        "rule_core_fixture.py",
        "rule_core_replay.py",
        "rule_config_migration_v1.py",
        "market_lifecycle_v1.py",
    }
    production_importers: list[str] = []
    for path in (ROOT / "scripts").glob("*.py"):
        if path.name.startswith("test_") or path.name in allowed:
            continue
        module = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        for node in ast.walk(module):
            if isinstance(node, ast.Import) and any(alias.name == "rule_core_v1" for alias in node.names):
                production_importers.append(path.name)
            elif isinstance(node, ast.ImportFrom) and node.module == "rule_core_v1":
                production_importers.append(path.name)
    assert production_importers == []


def main() -> int:
    test_passive_admission_contract_rejects_invalid_combinations()
    test_rule_config_and_fixture_schemas_fail_closed()
    test_all_approved_v1_cases_are_executable()
    test_source_identity_and_llm_availability_cannot_change_core_result()
    test_new_core_is_not_wired_into_production_or_side_effect_modules()
    print("rule core v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
