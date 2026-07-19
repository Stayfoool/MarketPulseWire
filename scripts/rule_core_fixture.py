"""Strict loader for the public rule-core behavior corpus."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CONTRACT_VERSION = "rule-core-v1"
VALID_FAMILIES = {"holding", "semiconductor_ai", "macro_data", "fed_policy", "trade_policy"}
VALID_ADMISSION = {"admitted", "excluded", "not_applicable"}
VALID_ACTIONS = {"push", "daily", "archive", "ignore", None}


class FixtureContractError(ValueError):
    pass


def _exact_keys(value: object, expected: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FixtureContractError(f"{context} must be an object")
    unknown = set(value) - expected
    missing = expected - set(value)
    if unknown or missing:
        raise FixtureContractError(
            f"{context} keys invalid: missing={sorted(missing)} unknown={sorted(unknown)}"
        )
    return value


def validate_fixture_payload(payload: object) -> dict[str, Any]:
    root = _exact_keys(
        payload,
        {"contract_version", "config_fixture", "portfolio_fixtures", "cases"},
        "fixture root",
    )
    if root["contract_version"] != CONTRACT_VERSION:
        raise FixtureContractError(f"unsupported fixture contract: {root['contract_version']}")
    if not isinstance(root["config_fixture"], str) or not root["config_fixture"].strip():
        raise FixtureContractError("config_fixture is required")
    if not isinstance(root["portfolio_fixtures"], dict):
        raise FixtureContractError("portfolio_fixtures must be an object")
    if not isinstance(root["cases"], list) or not root["cases"]:
        raise FixtureContractError("cases must be a non-empty list")

    case_ids: set[str] = set()
    variant_ids: set[str] = set()
    for index, raw_case in enumerate(root["cases"]):
        case = _exact_keys(
            raw_case,
            {"case_id", "family", "text", "symbols", "portfolio_fixture", "expected", "variants"},
            f"cases[{index}]",
        )
        case_id = str(case["case_id"] or "").strip()
        if not case_id or case_id in case_ids:
            raise FixtureContractError(f"duplicate/empty case_id: {case_id}")
        case_ids.add(case_id)
        if case["family"] not in VALID_FAMILIES:
            raise FixtureContractError(f"{case_id}: invalid family")
        if not isinstance(case["text"], str) or not case["text"].strip():
            raise FixtureContractError(f"{case_id}: text is required")
        if not isinstance(case["symbols"], list) or not all(isinstance(item, str) for item in case["symbols"]):
            raise FixtureContractError(f"{case_id}: symbols must be a string list")
        portfolio_name = str(case["portfolio_fixture"] or "")
        if portfolio_name not in root["portfolio_fixtures"]:
            raise FixtureContractError(f"{case_id}: unknown portfolio fixture {portfolio_name}")
        expected = _exact_keys(
            case["expected"],
            {"admission", "action", "rule_id", "push_eligible"},
            f"{case_id}.expected",
        )
        if expected["admission"] not in VALID_ADMISSION:
            raise FixtureContractError(f"{case_id}: invalid expected admission")
        if expected["action"] not in VALID_ACTIONS:
            raise FixtureContractError(f"{case_id}: invalid expected action")
        if expected["admission"] == "admitted" and expected["action"] is None:
            raise FixtureContractError(f"{case_id}: admitted case requires action")
        if expected["admission"] != "admitted" and (
            expected["action"] is not None or expected["rule_id"] is not None
        ):
            raise FixtureContractError(f"{case_id}: excluded/not_applicable case cannot expect decision")
        if not isinstance(expected["push_eligible"], bool):
            raise FixtureContractError(f"{case_id}: push_eligible must be boolean")
        if expected["push_eligible"] != (expected["action"] == "push"):
            raise FixtureContractError(f"{case_id}: push eligibility must derive only from push action")
        if not isinstance(case["variants"], list) or not case["variants"]:
            raise FixtureContractError(f"{case_id}: variants must be a non-empty list")
        for variant_index, raw_variant in enumerate(case["variants"]):
            required = {
                "variant_id",
                "source",
                "source_category",
                "publisher_role",
                "content_type",
            }
            if not isinstance(raw_variant, dict):
                raise FixtureContractError(f"{case_id}.variants[{variant_index}] must be an object")
            allowed = required | {"direct_admission_families"}
            unknown = set(raw_variant) - allowed
            missing = required - set(raw_variant)
            if unknown or missing:
                raise FixtureContractError(
                    f"{case_id}.variants[{variant_index}] keys invalid: "
                    f"missing={sorted(missing)} unknown={sorted(unknown)}"
                )
            variant_id = str(raw_variant["variant_id"] or "").strip()
            if not variant_id or variant_id in variant_ids:
                raise FixtureContractError(f"duplicate/empty variant_id: {variant_id}")
            variant_ids.add(variant_id)
            direct = raw_variant.get("direct_admission_families", [])
            if not isinstance(direct, list) or set(direct) - {"trade_policy"}:
                raise FixtureContractError(f"{variant_id}: invalid direct admission families")
    return root


def load_fixture_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixtureContractError(f"cannot load fixture corpus: {exc}") from exc
    return validate_fixture_payload(payload)
