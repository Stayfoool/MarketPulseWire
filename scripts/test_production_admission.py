#!/usr/bin/env python3
"""Production five-group range-admission checks."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from db_utils import connect_sqlite
from market_db import init_db
from market_item import NormalizedMarketItem
from production_admission import (
    admission_lifecycle_values,
    evaluate_production_admission,
    load_production_portfolio,
    load_production_rule_config,
    production_admission_context,
)


ROOT = Path(__file__).resolve().parents[1]
RULE_CONFIG = ROOT / "config" / "rule_core_v1.test.json"


def seed_holding(db_path: Path) -> None:
    init_db(db_path).close()
    raw = {
        "news_keywords": ["HBM peer"],
        "news_exclude_keywords": ["routine roundup"],
        "immediate_alert_keywords": ["emergency halt"],
    }
    with connect_sqlite(db_path) as conn:
        conn.execute(
            """
            INSERT INTO portfolio_holdings
                (symbol, name, full_name, aliases_json, enabled, raw_json, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (
                "000001.SZ",
                "测试股份",
                "测试股份有限公司",
                json.dumps(["Test Holdings"]),
                json.dumps(raw),
                "2026-07-23T00:00:00+00:00",
            ),
        )
        conn.commit()


def item(text: str, *, source: str = "digitimes", category: str = "research_industry_media") -> NormalizedMarketItem:
    return NormalizedMarketItem(
        source=source,
        source_category=category,
        publisher_role="news_media",
        content_type="article",
        title=text,
        summary=text,
        full_text=text,
        url="https://example.com/item",
    )


def test_missing_production_rule_config_fails_closed() -> None:
    try:
        load_production_rule_config({})
    except RuntimeError as exc:
        assert "RULE_CORE_CONFIG" in str(exc)
    else:
        raise AssertionError("missing RULE_CORE_CONFIG must fail closed")


def test_production_portfolio_comes_from_sqlite() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "surveil.sqlite3"
        seed_holding(db_path)
        portfolio = load_production_portfolio(db_path)
        assert len(portfolio.holdings) == 1
        holding = portfolio.holdings[0]
        assert holding.names == ("测试股份", "测试股份有限公司", "Test Holdings")
        assert holding.related_news_keywords == ("HBM peer",)
        assert holding.exclude_keywords == ("routine roundup",)
        assert holding.immediate_alert_keywords == ("emergency halt",)


def test_ordinary_sources_use_five_groups_and_holding_only_sources_do_not() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "surveil.sqlite3"
        seed_holding(db_path)
        env = {"RULE_CORE_CONFIG": str(RULE_CONFIG)}

        related = evaluate_production_admission(item("HBM peer signs a new supply agreement"), db_path=db_path, env=env)
        assert related.status == "admitted"
        assert "holding" in related.matched_families

        industry = evaluate_production_admission(item("HBM capacity expansion begins"), db_path=db_path, env=env)
        assert industry.status == "admitted"
        assert "semiconductor_ai" in industry.matched_families

        holding_only = evaluate_production_admission(
            item(
                "HBM capacity expansion begins",
                source="company_disclosures",
                category="company_disclosures",
            ),
            db_path=db_path,
            env=env,
        )
        assert holding_only.status == "excluded"
        assert holding_only.reason_code == "holding_scope_required_for_source"

        direct_holding = evaluate_production_admission(
            item(
                "测试股份发布公告",
                source="company_disclosures",
                category="company_disclosures",
            ),
            db_path=db_path,
            env=env,
        )
        assert direct_holding.status == "admitted"
        assert direct_holding.matched_families == ("holding",)


def test_official_trade_source_uses_direct_trade_admission() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "surveil.sqlite3"
        seed_holding(db_path)
        official = item(
            "Official notice",
            source="ustr_press_releases",
            category="official_policy",
        )
        official.publisher_role = "government_official"
        result = evaluate_production_admission(
            official,
            db_path=db_path,
            env={"RULE_CORE_CONFIG": str(RULE_CONFIG)},
        )
        assert result.status == "admitted"
        assert result.matched_families == ("trade_policy",)


def test_context_and_lifecycle_reuse_exact_admission() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "surveil.sqlite3"
        seed_holding(db_path)
        context = production_admission_context(
            item("Test Holdings announces guidance"),
            db_path=db_path,
            env={"RULE_CORE_CONFIG": str(RULE_CONFIG)},
        )
        assert context.result.status == "admitted"
        assert context.portfolio.holdings[0].symbol == "000001.SZ"
        values = admission_lifecycle_values(context.result, processing_status="pending")
        assert values["admission_status"] == "admitted"
        assert json.loads(values["admission_matched_families_json"])[0] == "holding"
        assert values["admission_config_version"] == context.result.config_version
        assert values["admission_rule_contract_version"] == context.result.rule_contract_version


def main() -> int:
    test_missing_production_rule_config_fails_closed()
    test_production_portfolio_comes_from_sqlite()
    test_ordinary_sources_use_five_groups_and_holding_only_sources_do_not()
    test_official_trade_source_uses_direct_trade_admission()
    test_context_and_lifecycle_reuse_exact_admission()
    print("production admission checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
