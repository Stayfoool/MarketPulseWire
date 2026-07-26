#!/usr/bin/env python3
"""Regression checks for the deterministic-rule center."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from db_utils import connect_sqlite
from rule_center import (
    RULE_BY_ID,
    _write_audit,
    configured_rule_settings,
    rule_center_payload,
    save_rule_config,
)


def test_rule_registry_exposes_only_active_evidence_configuration() -> None:
    with TemporaryDirectory() as tmpdir:
        payload = rule_center_payload(Path(tmpdir) / "surveil.sqlite3")
    ids = {item["id"] for item in payload["rules"]}
    assert ids == set(RULE_BY_ID)
    assert ids == {"macro_policy_line", "attributed_research_hard_variable"}
    attributed = next(item for item in payload["rules"] if item["id"] == "attributed_research_hard_variable")
    trusted = next(field for field in attributed["fields"] if field["key"] == "trusted_institutions")
    assert {"semianalysis", "trendforce", "semi", "digitimes", "the_elec", "nikkei_xtech"} == set(trusted["default"])


def test_private_config_normalizes_and_preserves_explicit_fields() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "push_rules.local.json"
        save_rule_config(
            {
                "rules": {
                    "macro_policy_line": {"enabled": False, "priority": 77, "extra_primary_keywords": ["就业成本"]},
                }
            },
            path,
        )
        configured = configured_rule_settings("macro_policy_line", path)
    assert configured["enabled"] is False
    assert configured["priority"] == 77
    assert configured["extra_primary_keywords"] == ["就业成本"]


def test_config_audit_is_a_non_delivery_operation() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        _write_audit(
            {"rules": {"macro_policy_line": {"enabled": True}}},
            {"rules": {"macro_policy_line": {"enabled": False}}},
            db_path,
        )
        with connect_sqlite(db_path) as conn:
            audit_count = conn.execute("SELECT COUNT(*) FROM rule_config_audit").fetchone()[0]
            delivery_count = conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
    assert audit_count == 1
    assert delivery_count == 0


def main() -> int:
    test_rule_registry_exposes_only_active_evidence_configuration()
    test_private_config_normalizes_and_preserves_explicit_fields()
    test_config_audit_is_a_non_delivery_operation()
    print("rule center checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
