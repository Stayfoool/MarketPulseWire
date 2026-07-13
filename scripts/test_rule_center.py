#!/usr/bin/env python3
"""Regression checks for the deterministic-rule center."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from market_review_store import ensure_article_reviews_table
from db_utils import connect_sqlite
from rule_center import (
    RULE_BY_ID,
    _write_audit,
    configured_rule_settings,
    rule_center_payload,
    save_rule_config,
    simulate_rules,
)


def test_rule_registry_payload_has_all_current_hard_rules() -> None:
    with TemporaryDirectory() as tmpdir:
        payload = rule_center_payload(Path(tmpdir) / "surveil.sqlite3")
    ids = {item["id"] for item in payload["rules"]}
    assert ids == set(RULE_BY_ID)
    theme = next(item for item in payload["rules"] if item["id"] == "international_bank_theme_strategy")
    assert any(field["key"] == "min_evidence_score" for field in theme["fields"])
    relation = next(item for item in payload["rules"] if item["id"] == "investment_bank_portfolio_relation")
    assert any(field["key"] == "max_relation_matches" for field in relation["fields"])
    relation_enabled = next(field for field in relation["fields"] if field["key"] == "enabled")
    assert relation_enabled["default"] is False
    keyword_alert = next(item for item in payload["rules"] if item["id"] == "holding_keyword_immediate_alert")
    assert keyword_alert["group"] == "持仓与公司"
    assert any(field["key"] == "enabled" and field["default"] is True for field in keyword_alert["fields"])
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


def test_audit_and_dry_run_are_non_delivery_operations() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        with connect_sqlite(db_path) as conn:
            ensure_article_reviews_table(conn)
            conn.execute(
                """
                INSERT INTO article_reviews (
                    source, item_id, url, title, source_module, published_at, importance,
                    push_now, market_impact, incremental_classification, affected_targets_json,
                    reason, daily_summary, confidence, gate_json, skeptic_json,
                    pre_skeptic_importance, pushed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "cls_telegraph_api",
                    "goldman-1",
                    "",
                    "高盛发布《投资策略：做多中国 AI 价值链》",
                    "财联社",
                    "2026-07-10T03:00:00+00:00",
                    "medium",
                    0,
                    "",
                    "",
                    "[]",
                    "",
                    "高盛认为市值与市场空间错配，资金出现结构性资本轮动。",
                    "",
                    json.dumps({}, ensure_ascii=False),
                    "{}",
                    "",
                    "",
                    "2026-07-10T03:00:00+00:00",
                ),
            )
            conn.commit()
        result = simulate_rules(db_path=db_path, days=60, limit=20)
        assert result["matched"] == 1
        assert result["results"][0]["matches"][0]["rule_id"] == "international_bank_theme_strategy"
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
    test_rule_registry_payload_has_all_current_hard_rules()
    test_private_config_normalizes_and_preserves_explicit_fields()
    test_audit_and_dry_run_are_non_delivery_operations()
    print("rule center checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
