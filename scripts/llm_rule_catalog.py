"""Load and validate the private human-reviewed LLM decision rules.

The repository contains the schema and validation contract only. Production
rule content is read from a gitignored JSON file selected by
``LLM_DECISION_RULE_CONFIG``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, cast

from market_item import RuleFamily


RULE_CONFIG_SCHEMA_VERSION = "llm-decision-rule-config-v3"
RULE_CONFIG_ENV = "LLM_DECISION_RULE_CONFIG"
DEFAULT_RULE_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "llm_decision_rules.json"
MAX_RULE_CONFIG_BYTES = 256_000
MAX_RULES = 64
MODEL_ACTIONS = ("push", "daily", "archive")
RULE_FAMILIES = {"holding", "semiconductor_ai", "macro_data", "fed_policy", "trade_policy"}

class LLMRuleCatalogError(RuntimeError):
    """The private LLM decision-rule file is missing or invalid."""


@dataclass(frozen=True)
class LLMRuleDefinition:
    rule_id: str
    family: RuleFamily
    title: str
    push: str | None
    daily: str | None
    version: str
    applicable_families: tuple[RuleFamily, ...] = ()

    def __post_init__(self) -> None:
        if not self.rule_id or not self.title or not self.version:
            raise ValueError("rule_id, title and version are required")
        if not self.push and not self.daily:
            raise ValueError(f"push or daily condition required for {self.rule_id}")
        if self.push is not None and not self.push.strip():
            raise ValueError(f"empty push condition for {self.rule_id}")
        if self.daily is not None and not self.daily.strip():
            raise ValueError(f"empty daily condition for {self.rule_id}")
        applicable = self.applicable_families or (self.family,)
        if self.family not in applicable:
            raise ValueError(f"primary family missing from applicable_families for {self.rule_id}")
        if len(set(applicable)) != len(applicable):
            raise ValueError(f"duplicate applicable_families for {self.rule_id}")
        if any(family not in RULE_FAMILIES for family in applicable):
            raise ValueError(f"unsupported applicable_families for {self.rule_id}")
        object.__setattr__(self, "applicable_families", tuple(applicable))

    @property
    def allowed_actions(self) -> tuple[str, ...]:
        actions = []
        if self.push:
            actions.append("push")
        if self.daily:
            actions.append("daily")
        actions.append("archive")
        return tuple(actions)

    def to_prompt_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "rule_id": self.rule_id,
            "title": self.title,
        }
        if self.push:
            result["push"] = self.push
        if self.daily:
            result["daily"] = self.daily
        return result


def configured_rule_path(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    configured = str(values.get(RULE_CONFIG_ENV) or "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_RULE_CONFIG_PATH


def _required_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LLMRuleCatalogError(f"{path} must be a non-empty string")
    return value.strip()


def _string_tuple(value: Any, path: str, *, required: bool) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise LLMRuleCatalogError(f"{path} must be an array")
    result = tuple(_required_string(item, f"{path}[{index}]") for index, item in enumerate(value))
    if required and not result:
        raise LLMRuleCatalogError(f"{path} must not be empty")
    return result


def _parse_rule(payload: Any, index: int, version: str) -> LLMRuleDefinition:
    path = f"rules[{index}]"
    if not isinstance(payload, dict):
        raise LLMRuleCatalogError(f"{path} must be an object")
    required_fields = {"rule_id", "family", "title"}
    optional_fields = {"applicable_families", "push", "daily"}
    if not required_fields <= set(payload) or set(payload) - required_fields - optional_fields:
        allowed = sorted(required_fields | optional_fields)
        raise LLMRuleCatalogError(f"{path} fields must match {allowed}")
    family = _required_string(payload["family"], f"{path}.family")
    if family not in RULE_FAMILIES:
        raise LLMRuleCatalogError(f"{path}.family is unsupported: {family}")
    raw_applicable = payload.get("applicable_families", [family])
    applicable = _string_tuple(raw_applicable, f"{path}.applicable_families", required=True)
    push = _required_string(payload["push"], f"{path}.push") if "push" in payload else None
    daily = _required_string(payload["daily"], f"{path}.daily") if "daily" in payload else None
    try:
        return LLMRuleDefinition(
            rule_id=_required_string(payload["rule_id"], f"{path}.rule_id"),
            family=cast(RuleFamily, family),
            title=_required_string(payload["title"], f"{path}.title"),
            push=push,
            daily=daily,
            version=version,
            applicable_families=cast(tuple[RuleFamily, ...], applicable),
        )
    except ValueError as exc:
        raise LLMRuleCatalogError(f"{path} is invalid: {exc}") from exc


def load_rule_catalog(path: Path | None = None) -> tuple[str, tuple[LLMRuleDefinition, ...]]:
    selected = path or configured_rule_path()
    try:
        size = selected.stat().st_size
    except OSError as exc:
        raise LLMRuleCatalogError(f"private LLM decision-rule file is unavailable: {selected}") from exc
    if size <= 0 or size > MAX_RULE_CONFIG_BYTES:
        raise LLMRuleCatalogError(f"private LLM decision-rule file has invalid size: {size}")
    try:
        payload = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LLMRuleCatalogError(f"private LLM decision-rule file is not valid JSON: {selected}") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "version", "rules"}:
        raise LLMRuleCatalogError("private LLM decision-rule file has invalid top-level fields")
    schema_version = payload["schema_version"]
    if schema_version != RULE_CONFIG_SCHEMA_VERSION:
        raise LLMRuleCatalogError("private LLM decision-rule schema_version is unsupported")
    version = _required_string(payload["version"], "version")
    raw_rules = payload["rules"]
    if not isinstance(raw_rules, list) or not raw_rules or len(raw_rules) > MAX_RULES:
        raise LLMRuleCatalogError(f"rules must contain between 1 and {MAX_RULES} entries")
    rules = tuple(
        _parse_rule(rule, index, version)
        for index, rule in enumerate(raw_rules)
    )
    ids = [rule.rule_id for rule in rules]
    if len(set(ids)) != len(ids):
        raise LLMRuleCatalogError("private LLM decision-rule file contains duplicate rule IDs")
    return version, rules


LLM_DECISION_RULE_VERSION, RULES = load_rule_catalog()
RULES_BY_ID: Mapping[str, LLMRuleDefinition] = MappingProxyType({rule.rule_id: rule for rule in RULES})


def rules_for_families(families: tuple[RuleFamily, ...]) -> tuple[LLMRuleDefinition, ...]:
    wanted = set(families)
    return tuple(rule for rule in RULES if wanted.intersection(rule.applicable_families))
