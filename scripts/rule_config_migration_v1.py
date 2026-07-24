"""Redacted, no-write preview for migrating legacy keyword lists to rule-config-v1."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from rule_core_v1 import parse_rule_config


LEGACY_SCHEMA_VERSION = "legacy-rule-config-snapshot-v1"
MIGRATION_REPORT_VERSION = "rule-config-migration-preview-v1"
LEGACY_ORIGINS = {
    "focus_keywords",
    "base_keywords",
    "include_keywords",
    "semiconductor_code_keywords",
}


class MigrationPreviewError(ValueError):
    pass


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _term_id(value: str) -> str:
    return "term:" + hashlib.sha256(value.casefold().encode("utf-8")).hexdigest()[:16]


def _string_set(value: object, field: str) -> set[str]:
    if not isinstance(value, list):
        raise MigrationPreviewError(f"{field} must be a list")
    return {text for raw in value if (text := _clean(raw))}


def preview_rule_config_migration(
    legacy_payload: Mapping[str, Any], target_payload: Mapping[str, Any]
) -> dict[str, Any]:
    expected = {"schema_version", "origins"}
    unknown = set(legacy_payload) - expected
    missing = expected - set(legacy_payload)
    if unknown or missing:
        raise MigrationPreviewError(
            f"legacy snapshot keys invalid: missing={sorted(missing)} unknown={sorted(unknown)}"
        )
    if legacy_payload.get("schema_version") != LEGACY_SCHEMA_VERSION:
        raise MigrationPreviewError("unsupported legacy snapshot schema")
    origins = legacy_payload.get("origins")
    if not isinstance(origins, dict):
        raise MigrationPreviewError("origins must be an object")
    unknown_origins = set(origins) - LEGACY_ORIGINS
    missing_origins = LEGACY_ORIGINS - set(origins)
    if unknown_origins or missing_origins:
        raise MigrationPreviewError(
            f"legacy origins invalid: missing={sorted(missing_origins)} unknown={sorted(unknown_origins)}"
        )

    target = parse_rule_config(target_payload)
    origin_terms = {
        name: _string_set(origins[name], f"origins.{name}") for name in sorted(LEGACY_ORIGINS)
    }
    legacy_union = set().union(*origin_terms.values())
    target_terms = set(target.semiconductor_ai_keywords)
    retained = legacy_union & target_terms
    dropped = legacy_union - target_terms
    added = target_terms - legacy_union
    repeated = {
        term
        for term in legacy_union
        if sum(term in values for values in origin_terms.values()) > 1
    }
    trusted_domains = sum(len(item.domains) for item in target.trusted_institutions)
    trade_corridor_terms = sum(
        len(item.china_terms) + len(item.counterparty_terms) + len(item.joint_terms)
        for item in target.trade_corridors
    )
    return {
        "report_version": MIGRATION_REPORT_VERSION,
        "target_config_version": target.config_version,
        "automatic_union_applied": False,
        "origin_counts": {name: len(values) for name, values in origin_terms.items()},
        "validated_target_section_counts": {
            "semiconductor_ai_keywords": len(target.semiconductor_ai_keywords),
            "semiconductor_ai_title_keywords": len(target.semiconductor_ai_title_keywords),
            "major_semiconductor_customers": len(target.major_semiconductor_customers),
            "exclude_keywords": len(target.exclude_keywords),
            "macro_indicators": len(target.macro_indicators),
            "macro_context_aliases": len(target.macro_context_aliases),
            "fed_event_aliases": len(target.fed_event_aliases),
            "fed_actor_aliases": len(target.fed_actor_aliases),
            "fed_path_aliases": len(target.fed_path_aliases),
            "trusted_institutions": len(target.trusted_institutions),
            "trusted_domains": trusted_domains,
            "trade_corridors": len(target.trade_corridors),
            "trade_corridor_terms": trade_corridor_terms,
            "trade_instruments": len(target.trade_instruments),
            "trade_stages": len(target.trade_stages),
            "trade_focus_industries": len(target.trade_focus_industries),
        },
        "legacy_unique_count": len(legacy_union),
        "target_count": len(target_terms),
        "retained_count": len(retained),
        "dropped_count": len(dropped),
        "added_count": len(added),
        "cross_origin_duplicate_count": len(repeated),
        "retained_term_ids": sorted(_term_id(term) for term in retained),
        "dropped_term_ids": sorted(_term_id(term) for term in dropped),
        "added_term_ids": sorted(_term_id(term) for term in added),
        "cross_origin_duplicate_term_ids": sorted(_term_id(term) for term in repeated),
    }
