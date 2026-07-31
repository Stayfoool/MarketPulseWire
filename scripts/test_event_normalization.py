#!/usr/bin/env python3
"""Regression checks for event NormalizedMarketItem audit metadata."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from market_db import init_db
from market_flow import normalize_market_item
from market_store import upsert_market_item


def test_sina_flash_normalization_preserves_raw_and_context() -> None:
    event = {
        "source": "sina_flash",
        "source_event_id": "flash-1",
        "event_type": "flash_news",
        "title": "美联储主席讲话后，2年期美债收益率大跌",
        "summary": "市场重新定价美联储降息路径。",
        "full_text": "市场重新定价美联储降息路径。",
        "url": "https://finance.sina.com.cn/7x24/",
        "published_at": "2026-07-12T00:30:00+00:00",
        "symbols": ["688017.SH", "688017.SH"],
        "themes": ["新浪财经快讯", "宏观流动性/美联储政策"],
        "raw": {"macro_policy_line": {"matched": True, "tier": "primary"}},
    }
    item = normalize_market_item("sina_flash", event, store_kind="event")

    assert "_normalized_market_item" not in event["raw"]
    assert item.raw["macro_policy_line"] == event["raw"]["macro_policy_line"]
    assert item.source_category == "news_media"
    assert item.publisher_role == "news_media"
    assert item.collector == "sina_flash"
    assert item.content_type == "flash"
    assert item.symbols == ["688017.SH"]
    assert item.themes == ["新浪财经快讯", "宏观流动性/美联储政策"]
    assert item.dedupe_key == "sina_flash:flash-1"
    assert item.raw["source_event_id"] == "flash-1"
    assert "_normalized_market_item" not in item.raw


def test_sina_stock_news_normalization_uses_portfolio_category() -> None:
    event = {
        "source": "sina_stock_news",
        "source_event_id": "article:abc",
        "event_type": "stock_news",
        "title": "持仓公司获得 AI 服务器订单",
        "summary": "中际旭创相关新闻：持仓公司获得 AI 服务器订单",
        "full_text": "",
        "url": "https://finance.sina.com.cn/stock/s/example.shtml",
        "published_at": "2026-07-12T00:30:00+00:00",
        "symbols": ["300308.SZ"],
        "themes": ["新浪财经个股资讯"],
        "raw": {"canonical_url": "https://finance.sina.com.cn/stock/s/example.shtml"},
    }
    item = normalize_market_item("sina_stock_news", event, store_kind="event")
    assert item.source_category == "portfolio_stock_news"
    assert item.publisher_role == "news_media"
    assert item.collector == "sina_stock_news"
    assert item.content_type == "portfolio_news"
    assert item.dedupe_key == "sina_stock_news:article:abc"
    assert item.symbols == ["300308.SZ"]


def test_unified_item_stores_ifind_context_without_duplicating_full_text() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        event = {
            "source": "ifind_notice",
            "source_event_id": "688017.SH:notice-1",
            "event_type": "announcement",
            "title": "公司披露重大合同公告",
            "summary": "股票：绿的谐波 688017.SH；标题：公司披露重大合同公告",
            "full_text": "公告正文" * 500,
            "url": "",
            "published_at": "2026-07-12",
            "symbols": ["688017.SH"],
            "themes": [],
            "raw": {"pdfURL": "<ifind_notice_url_redacted>", "_pdf_parse": {"status": "ok"}},
        }
        init_db(db_path).close()
        item = normalize_market_item("ifind_notice", event, store_kind="event")
        with sqlite3.connect(db_path) as conn:
            item_id = upsert_market_item(conn, item, collection_class="baseline")
            conn.commit()
            row = conn.execute(
                """
                SELECT source_category,collector,content_type,symbols_json,dedupe_key,
                       length(full_text),raw_json
                FROM market_items WHERE id=?
                """,
                (item_id,),
            ).fetchone()

    raw = json.loads(row[6])
    assert raw["pdfURL"] == "<ifind_notice_url_redacted>"
    assert raw["_pdf_parse"] == {"status": "ok"}
    assert raw["source_event_id"] == "688017.SH:notice-1"
    assert row[:6] == (
        "company_disclosures",
        "ifind_batch",
        "notice",
        '["688017.SH"]',
        "ifind_notice:688017.SH:notice-1",
        len(event["full_text"]),
    )
    assert "_normalized_market_item" not in raw


def main() -> int:
    test_sina_flash_normalization_preserves_raw_and_context()
    test_sina_stock_news_normalization_uses_portfolio_category()
    test_unified_item_stores_ifind_context_without_duplicating_full_text()
    print("event normalization checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
