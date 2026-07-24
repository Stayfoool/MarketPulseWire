"""Shared schema validation for the private global rule configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


CONFIG_SCHEMA_VERSION = "rule-config-v1"


class RuleConfigError(ValueError):
    pass


def clean_value(value: object) -> str:
    return " ".join(str(value or "").split())


def tuple_strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RuleConfigError(f"{field} must be a list")
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        text = clean_value(raw)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return tuple(result)


def mapping(value: object, field: str, expected: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise RuleConfigError(f"{field} must be an object")
    unknown = set(value) - expected
    missing = expected - set(value)
    if unknown or missing:
        raise RuleConfigError(
            f"{field} keys invalid: missing={sorted(missing)} unknown={sorted(unknown)}"
        )
    return value


@dataclass(frozen=True)
class TrustedInstitution:
    institution_id: str
    aliases: tuple[str, ...]
    domains: tuple[str, ...] = ()


@dataclass(frozen=True)
class TradeCorridor:
    corridor_id: str
    china_terms: tuple[str, ...] = ()
    counterparty_terms: tuple[str, ...] = ()
    joint_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.joint_terms and not (self.china_terms and self.counterparty_terms):
            raise RuleConfigError(
                f"trade corridor {self.corridor_id} requires joint terms or both corridor sides"
            )


@dataclass(frozen=True)
class RuleConfig:
    config_version: str
    semiconductor_ai_keywords: tuple[str, ...]
    semiconductor_ai_title_keywords: tuple[str, ...]
    major_semiconductor_customers: tuple[str, ...]
    exclude_keywords: tuple[str, ...]
    macro_indicators: tuple[str, ...]
    macro_context_aliases: tuple[str, ...]
    fed_event_aliases: tuple[str, ...]
    fed_actor_aliases: tuple[str, ...]
    fed_path_aliases: tuple[str, ...]
    trusted_institutions: tuple[TrustedInstitution, ...]
    trade_corridors: tuple[TradeCorridor, ...]
    trade_instruments: tuple[str, ...]
    trade_stages: tuple[str, ...]
    trade_focus_industries: tuple[str, ...]


def _stable_registry_id(value: object, field: str) -> str:
    text = clean_value(value)
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", text):
        raise RuleConfigError(f"{field} must be a stable lower-snake-case id")
    return text


def _trusted_registry(value: object) -> tuple[TrustedInstitution, ...]:
    if not isinstance(value, dict) or not value:
        raise RuleConfigError("trusted_attribution.institutions must be a non-empty object")
    result: list[TrustedInstitution] = []
    for raw_id, raw in value.items():
        institution_id = _stable_registry_id(raw_id, "trusted institution id")
        definition = mapping(
            raw,
            f"trusted_attribution.institutions.{institution_id}",
            {"aliases", "domains"},
        )
        aliases = tuple_strings(
            definition.get("aliases"),
            f"trusted_attribution.institutions.{institution_id}.aliases",
        )
        domains = tuple(
            domain.casefold()
            for domain in tuple_strings(
                definition.get("domains"),
                f"trusted_attribution.institutions.{institution_id}.domains",
            )
        )
        if not aliases:
            raise RuleConfigError(f"trusted institution {institution_id} requires aliases")
        if any(
            not re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,}", domain)
            for domain in domains
        ):
            raise RuleConfigError(f"trusted institution {institution_id} has an invalid domain")
        result.append(TrustedInstitution(institution_id, aliases, domains))
    return tuple(result)


def _trade_corridor_registry(value: object) -> tuple[TradeCorridor, ...]:
    if not isinstance(value, dict) or not value:
        raise RuleConfigError("trade_policy.corridors must be a non-empty object")
    result: list[TradeCorridor] = []
    for raw_id, raw in value.items():
        corridor_id = _stable_registry_id(raw_id, "trade corridor id")
        definition = mapping(
            raw,
            f"trade_policy.corridors.{corridor_id}",
            {"china_terms", "counterparty_terms", "joint_terms"},
        )
        result.append(
            TradeCorridor(
                corridor_id=corridor_id,
                china_terms=tuple_strings(
                    definition.get("china_terms"),
                    f"trade_policy.corridors.{corridor_id}.china_terms",
                ),
                counterparty_terms=tuple_strings(
                    definition.get("counterparty_terms"),
                    f"trade_policy.corridors.{corridor_id}.counterparty_terms",
                ),
                joint_terms=tuple_strings(
                    definition.get("joint_terms"),
                    f"trade_policy.corridors.{corridor_id}.joint_terms",
                ),
            )
        )
    return tuple(result)


def parse_rule_config(payload: Mapping[str, Any]) -> RuleConfig:
    expected = {
        "schema_version",
        "config_version",
        "semiconductor_ai_keywords",
        "semiconductor_ai_title_keywords",
        "major_semiconductor_customers",
        "exclude_keywords",
        "macro_data",
        "fed_policy",
        "trusted_attribution",
        "trade_policy",
    }
    unknown = set(payload) - expected
    missing = expected - set(payload)
    # The title-only subset is optional for one deployment step so an old
    # private config can be read before its explicit migration is applied.
    missing.discard("semiconductor_ai_title_keywords")
    if unknown or missing:
        raise RuleConfigError(
            f"rule config keys invalid: missing={sorted(missing)} unknown={sorted(unknown)}"
        )
    if payload.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise RuleConfigError(f"unsupported rule config schema: {payload.get('schema_version')}")
    config_version = clean_value(payload.get("config_version"))
    if not config_version:
        raise RuleConfigError("config_version is required")
    raw_macro = payload.get("macro_data")
    if not isinstance(raw_macro, dict):
        raise RuleConfigError("macro_data must be an object")
    unknown_macro = set(raw_macro) - {"indicators", "context_aliases", "tiers"}
    missing_macro = {"indicators", "context_aliases"} - set(raw_macro)
    if unknown_macro or missing_macro:
        raise RuleConfigError(
            f"macro_data keys invalid: missing={sorted(missing_macro)} unknown={sorted(unknown_macro)}"
        )
    macro = raw_macro
    fed = mapping(
        payload.get("fed_policy"),
        "fed_policy",
        {"event_aliases", "actor_aliases", "path_aliases"},
    )
    trusted = mapping(
        payload.get("trusted_attribution"),
        "trusted_attribution",
        {"institutions"},
    )
    trade = mapping(
        payload.get("trade_policy"),
        "trade_policy",
        {"corridors", "instruments", "stages", "focus_industries"},
    )
    indicators = tuple_strings(macro.get("indicators"), "macro_data.indicators")
    tiers = macro.get("tiers")
    if tiers is not None:
        tiers = mapping(tiers, "macro_data.tiers", {"primary", "secondary"})
        primary = tuple_strings(tiers.get("primary"), "macro_data.tiers.primary")
        secondary = tuple_strings(tiers.get("secondary"), "macro_data.tiers.secondary")
        if not set(primary + secondary).issubset(set(indicators)):
            raise RuleConfigError("macro tiers must reference configured indicators")
        # Transitional compatibility: old configs retain their primary list,
        # while secondary indicators stop entering production admission.
        indicators = primary
    semiconductor_keywords = tuple_strings(
        payload.get("semiconductor_ai_keywords"), "semiconductor_ai_keywords"
    )
    title_keywords = tuple_strings(
        payload.get("semiconductor_ai_title_keywords", []),
        "semiconductor_ai_title_keywords",
    )
    semiconductor_keyword_keys = {value.casefold() for value in semiconductor_keywords}
    if not {value.casefold() for value in title_keywords}.issubset(semiconductor_keyword_keys):
        raise RuleConfigError(
            "semiconductor_ai_title_keywords must be a subset of semiconductor_ai_keywords"
        )
    config = RuleConfig(
        config_version=config_version,
        semiconductor_ai_keywords=semiconductor_keywords,
        semiconductor_ai_title_keywords=title_keywords,
        major_semiconductor_customers=tuple_strings(
            payload.get("major_semiconductor_customers"), "major_semiconductor_customers"
        ),
        exclude_keywords=tuple_strings(payload.get("exclude_keywords"), "exclude_keywords"),
        macro_indicators=indicators,
        macro_context_aliases=tuple_strings(
            macro.get("context_aliases"), "macro_data.context_aliases"
        ),
        fed_event_aliases=tuple_strings(fed.get("event_aliases"), "fed_policy.event_aliases"),
        fed_actor_aliases=tuple_strings(fed.get("actor_aliases"), "fed_policy.actor_aliases"),
        fed_path_aliases=tuple_strings(fed.get("path_aliases"), "fed_policy.path_aliases"),
        trusted_institutions=_trusted_registry(trusted.get("institutions")),
        trade_corridors=_trade_corridor_registry(trade.get("corridors")),
        trade_instruments=tuple_strings(trade.get("instruments"), "trade_policy.instruments"),
        trade_stages=tuple_strings(trade.get("stages"), "trade_policy.stages"),
        trade_focus_industries=tuple_strings(
            trade.get("focus_industries"), "trade_policy.focus_industries"
        ),
    )
    if (
        not config.semiconductor_ai_keywords
        or not config.major_semiconductor_customers
        or not config.macro_indicators
        or not config.trade_corridors
    ):
        raise RuleConfigError("required rule lists cannot be empty")
    return config
