#!/usr/bin/env python3
"""End-to-end checks for ValueList on the unified market flow."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import market_content_adapter
import market_delivery
import market_flow
from market_db import init_db
from market_item import decision_result_from_payload
from market_review_store import article_review_exists
from market_runtime import process_market_item
from value_directory_browser import source_config
from value_directory_monitor import normalized_value_directory_item


def value_item(
    item_id: str,
    title: str,
    *,
    source_id: str,
    preview_status: str = "",
    push_on_preview_failure: bool = True,
) -> dict:
    item = {
        "id": item_id,
        "url": f"https://www.valuelist.cn/{item_id}.html",
        "title": title,
        "summary": title,
        "content": title,
        "full_text": title,
        "published_at": "2026-07-13T00:00:00+00:00",
        "source_module": source_config(source_id).module,
        "raw": {
            "source": source_id,
            "value_directory_policy": {
                "preview_enabled": bool(preview_status),
                "push_on_preview_failure": push_on_preview_failure,
            },
        },
    }
    if preview_status == "ok":
        core = "瑞银认为智能体 AI 将继续推动半导体与硬件上行。"
        item["summary"] = core
        item["content"] = f"{title}\n{core}"
        item["full_text"] = item["content"]
        item["raw"]["value_directory_preview"] = {
            "facts": {
                "status": "ok",
                "core_content": core,
                "research_action": "overweight",
                "targets": ["半导体", "AI 硬件"],
                "key_points": ["半导体景气上行"],
                "preview_basis": "visible_first_page_ocr",
                "model": "preview-model",
                "ocr": {"status": "ok", "text": "Agentic AI to carry Semis further"},
            }
        }
    elif preview_status:
        item["raw"]["value_directory_preview"] = {
            "facts": {
                "status": preview_status,
                "model": "preview_failed",
                "error": "OCR unavailable",
            }
        }
    return item


def run_item(item: dict, source_id: str, db_path: Path):
    normalized = normalized_value_directory_item(item, source_config(source_id))
    return process_market_item(
        normalized,
        item,
        store_kind="article",
        source_profile_id=source_id,
        db_path=db_path,
        deliver=True,
        use_rule_dedup=True,
    )


def test_value_directory_uses_unified_store_decision_and_delivery() -> None:
    original_direct = os.environ.get("SURVEIL_MARKET_FLOW_DIRECT_PATH")
    original_holdings = market_content_adapter.load_enabled_holdings_for_rules
    original_interpreter = market_flow.interpret_market_item
    original_send = market_delivery.send_card
    sent_cards: list[dict] = []
    try:
        os.environ["SURVEIL_MARKET_FLOW_DIRECT_PATH"] = "1"
        market_content_adapter.load_enabled_holdings_for_rules = lambda: []
        market_flow.interpret_market_item = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ValueList source enrichment must not invoke a second LLM")
        )
        market_delivery.send_card = lambda card: sent_cards.append(card) or True

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "surveil.sqlite3"
            init_db(db_path).close()

            source_id = "value_directory_ib_industry_macro"
            enriched = value_item(
                "flow-ok",
                "瑞银-亚太科技策略：Agentic AI to carry Semis&Hardware further",
                source_id=source_id,
                preview_status="ok",
            )
            enriched_outcome = run_item(enriched, source_id, db_path)

            blocked_source = "value_directory_ib_stocks"
            blocked = value_item(
                "flow-blocked",
                "高盛-交易思路：做多中国人工智能价值链",
                source_id=blocked_source,
                preview_status="failed",
                push_on_preview_failure=False,
            )
            blocked_outcome = run_item(blocked, blocked_source, db_path)

            fallback = value_item(
                "flow-fallback",
                "高盛-交易思路：做多中国人工智能价值链",
                source_id=blocked_source,
                preview_status="failed",
                push_on_preview_failure=True,
            )
            fallback_outcome = run_item(fallback, blocked_source, db_path)

            archive = value_item(
                "flow-archive",
                "高盛-其他公司：例行观点",
                source_id=blocked_source,
            )
            archive_outcome = run_item(archive, blocked_source, db_path)

            with sqlite3.connect(db_path) as conn:
                enriched_review = article_review_exists(conn, source_id, "flow-ok")
                blocked_review = article_review_exists(conn, blocked_source, "flow-blocked")
                fallback_review = article_review_exists(conn, blocked_source, "flow-fallback")
                archive_review = article_review_exists(conn, blocked_source, "flow-archive")
    finally:
        market_content_adapter.load_enabled_holdings_for_rules = original_holdings
        market_flow.interpret_market_item = original_interpreter
        market_delivery.send_card = original_send
        if original_direct is None:
            os.environ.pop("SURVEIL_MARKET_FLOW_DIRECT_PATH", None)
        else:
            os.environ["SURVEIL_MARKET_FLOW_DIRECT_PATH"] = original_direct

    assert enriched_outcome.flow_result.decision.action == "push"
    assert enriched_outcome.delivery_status == "sent"
    assert enriched_review is not None and enriched_review["pushed_at"]
    enrichment = enriched_review["raw"]["raw"]["_source_enrichment"]
    assert enrichment["value_directory_preview"]["facts"]["research_action"] == "overweight"

    assert blocked_outcome.flow_result.decision.action == "archive"
    assert blocked_outcome.delivery_status == "skipped"
    assert blocked_review is not None and not blocked_review["pushed_at"]
    blocked_decision = decision_result_from_payload(blocked_review)
    assert blocked_decision is not None and blocked_decision.action == "archive"

    assert fallback_outcome.flow_result.decision.action == "push"
    assert fallback_outcome.delivery_status == "sent"
    assert fallback_review is not None and fallback_review["pushed_at"]

    assert archive_outcome.flow_result.decision.action == "archive"
    assert archive_outcome.flow_result.decision.importance == "unknown"
    assert archive_outcome.delivery_status == "skipped"
    assert archive_review is not None and archive_review["importance"] == "unknown"
    assert len(sent_cards) == 2


def main() -> int:
    test_value_directory_uses_unified_store_decision_and_delivery()
    print("value directory unified flow checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
