#!/usr/bin/env python3
"""Regression checks for the authenticated current-rules Web projection."""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

import holdings_web
from current_rules_web import current_rules_payload
from holdings_web import HoldingsHandler, html_page
from market_db import init_db


ROOT = Path(__file__).resolve().parents[1]
RULE_CONFIG = ROOT / "config" / "rule_core_v1.test.json"
LLM_RULE_CONFIG = ROOT / "config" / "llm_decision_rules.test.json"


def test_current_rules_projection_uses_strict_current_sources() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        with init_db(db_path) as conn:
            conn.execute(
                """
                INSERT INTO portfolio_holdings (
                    symbol, name, full_name, aliases_json, enabled, raw_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "000001.SZ",
                    "虚构公司",
                    "虚构公司股份有限公司",
                    json.dumps(["虚构别名"], ensure_ascii=False),
                    1,
                    json.dumps(
                        {
                            "news_keywords": ["虚构关联词"],
                            "news_exclude_keywords": ["虚构排除词"],
                        },
                        ensure_ascii=False,
                    ),
                    "2026-07-26T00:00:00+00:00",
                ),
            )
            conn.commit()
        payload = current_rules_payload(
            db_path=db_path,
            env={
                "RULE_CORE_CONFIG": str(RULE_CONFIG),
                "LLM_DECISION_RULE_CONFIG": str(LLM_RULE_CONFIG),
            },
        )

    admission = payload["range_admission"]
    assert admission["status"] == "loaded"
    assert admission["relation"] == "or"
    assert [group["family"] for group in admission["groups"]] == [
        "holding",
        "semiconductor_ai",
        "macro_data",
        "fed_policy",
        "trade_policy",
    ]
    holding_fields = admission["groups"][0]["fields"]
    assert holding_fields[0]["values"] == ["000001.SZ · 虚构公司、虚构公司股份有限公司、虚构别名"]
    assert "虚构关联词" in holding_fields[1]["values"][0]
    assert "虚构排除词" in holding_fields[2]["values"][0]
    assert admission["source_boundaries"]

    llm = payload["llm_decision"]
    assert llm["status"] == "loaded"
    assert llm["rule_count"] == len(llm["rules"])
    assert llm["rules"]
    assert set(llm["rules"][0]) == {
        "rule_id",
        "family",
        "family_label",
        "applicable_families",
        "applicable_family_labels",
        "title",
        "allowed_actions",
        "action_conditions",
        "required_facts",
        "exclusions",
        "version",
    }
    shared_rules = {
        rule["rule_id"]: rule for rule in llm["rules"]
        if set(rule["applicable_families"]) == {"holding", "semiconductor_ai"}
    }
    assert set(shared_rules) == {
        "equity_rating_revision",
        "capital_control_share_change",
        "industry_price_supply_change",
        "company_industry_execution_change",
        "company_performance_change",
        "company_credit_financing_constraint",
        "investment_bank_allocation_change",
    }
    for rule in shared_rules.values():
        assert rule["applicable_families"] == ["holding", "semiconductor_ai"]
        assert rule["applicable_family_labels"] == ["持仓", "半导体/AI"]


def test_current_rules_projection_fails_closed_without_leaking_paths() -> None:
    payload = current_rules_payload(
        db_path=Path("/missing/private-production.sqlite3"),
        env={
            "RULE_CORE_CONFIG": "/missing/private-rule-core.json",
            "LLM_DECISION_RULE_CONFIG": "/missing/private-llm-rules.json",
        },
    )
    admission = payload["range_admission"]
    llm = payload["llm_decision"]
    assert admission["status"] == "error"
    assert llm["status"] == "error"
    assert "groups" not in admission
    assert "rules" not in llm
    assert "/missing/" not in admission["error"]
    assert "/missing/" not in llm["error"]

    independent = current_rules_payload(
        db_path=Path("/missing/private-production.sqlite3"),
        env={
            "RULE_CORE_CONFIG": "/missing/private-rule-core.json",
            "LLM_DECISION_RULE_CONFIG": str(LLM_RULE_CONFIG),
        },
    )
    assert independent["range_admission"]["status"] == "error"
    assert independent["llm_decision"]["status"] == "loaded"


def test_current_rules_endpoint_is_authenticated_and_not_cached() -> None:
    original_payload = holdings_web.current_rules_payload
    holdings_web.current_rules_payload = lambda: {
        "generated_at": "2026-07-26T00:00:00+00:00",
        "range_admission": {"status": "loaded", "groups": []},
        "llm_decision": {"status": "loaded", "rules": []},
    }
    server = ThreadingHTTPServer(("127.0.0.1", 0), HoldingsHandler)
    server.token = "test-token"
    server.restart_sina_flash = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        connection.request("GET", "/api/current-rules")
        response = connection.getresponse()
        response.read()
        assert response.status == 401
        assert response.getheader("Cache-Control") == "no-store"

        connection.request(
            "GET",
            "/api/current-rules",
            headers={"X-Holdings-Token": "test-token"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert response.getheader("Cache-Control") == "no-store"
        assert response.getheader("X-Content-Type-Options") == "nosniff"
        assert payload["ok"] is True
        assert payload["range_admission"]["status"] == "loaded"
        assert payload["llm_decision"]["status"] == "loaded"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        holdings_web.current_rules_payload = original_payload


def test_current_rules_frontend_is_read_only_and_distinct_from_review() -> None:
    source = "\n".join(
        (
            html_page(token_required=False),
            (ROOT / "web" / "app.js").read_text(encoding="utf-8"),
        )
    )
    assert "大模型决策回顾" in source
    assert "范围准入规则" in source
    assert "大模型决策规则" in source
    assert "showView('rules')" in source
    assert "/api/current-rules" in source
    assert "loadCurrentRules" in source
    assert "renderRangeAdmissionRules" in source
    assert "renderLlmRules" in source
    assert "allowed_actions" in source
    assert "action_conditions" in source
    assert "required_facts" in source
    assert "exclusions" in source
    assert "api('/api/current-rules', {method: 'POST'" not in source
    assert "api(\"/api/current-rules\", {method: \"POST\"" not in source


def main() -> int:
    test_current_rules_projection_uses_strict_current_sources()
    test_current_rules_projection_fails_closed_without_leaking_paths()
    test_current_rules_endpoint_is_authenticated_and_not_cached()
    test_current_rules_frontend_is_read_only_and_distinct_from_review()
    print("current rules web checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
