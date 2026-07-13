#!/usr/bin/env python3
"""Regression checks for event NormalizedMarketItem audit metadata."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from market_event_adapter import event_with_normalized_market_item_audit, normalized_event_item, upsert_event


def test_sina_flash_event_audit_preserves_raw_and_context() -> None:
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
    item = normalized_event_item(event)
    updated = event_with_normalized_market_item_audit(event)
    audit = updated["raw"]["_normalized_market_item"]

    assert "_normalized_market_item" not in event["raw"]
    assert updated["raw"]["macro_policy_line"] == event["raw"]["macro_policy_line"]
    assert item.source_category == "news_media"
    assert item.publisher_role == "news_media"
    assert item.collector == "sina_flash"
    assert item.content_type == "flash_news"
    assert item.symbols == ["688017.SH"]
    assert item.themes == ["新浪财经快讯", "宏观流动性/美联储政策"]
    assert item.dedupe_key == "sina_flash:flash-1"
    assert audit["source_category"] == "news_media"
    assert audit["publisher_role"] == "news_media"
    assert audit["collector"] == "sina_flash"
    assert audit["content_type"] == "flash_news"
    assert audit["symbols"] == ["688017.SH"]
    assert audit["themes"] == ["新浪财经快讯", "宏观流动性/美联储政策"]
    assert audit["dedupe_key"] == "sina_flash:flash-1"
    assert audit["source_event_id"] == "flash-1"
    assert audit["raw_keys"] == ["macro_policy_line", "source_event_id"]
    assert "raw" not in audit
    assert "full_text" not in audit
    assert audit["full_text_chars"] == len(event["full_text"])


def test_sina_stock_news_event_audit_uses_portfolio_category() -> None:
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
    audit = event_with_normalized_market_item_audit(event)["raw"]["_normalized_market_item"]
    assert audit["source_category"] == "portfolio_stock_news"
    assert audit["publisher_role"] == "news_media"
    assert audit["collector"] == "sina_stock_news"
    assert audit["content_type"] == "stock_news"
    assert audit["dedupe_key"] == "sina_stock_news:article:abc"
    assert audit["symbols"] == ["300308.SZ"]


def test_upsert_event_writes_ifind_audit_without_duplicating_full_text() -> None:
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
        conn = sqlite3.connect(db_path)
        try:
            event_id, inserted = upsert_event(event, db_path=db_path)
            assert inserted is True
            row = conn.execute("SELECT raw_json FROM events WHERE id = ?", (event_id,)).fetchone()
        finally:
            conn.close()

    raw = json.loads(row[0])
    audit = raw["_normalized_market_item"]
    assert raw["pdfURL"] == "<ifind_notice_url_redacted>"
    assert raw["_pdf_parse"] == {"status": "ok"}
    assert audit["source_category"] == "company_disclosures"
    assert audit["collector"] == "ifind_batch"
    assert audit["content_type"] == "announcement"
    assert audit["symbols"] == ["688017.SH"]
    assert audit["dedupe_key"] == "ifind_notice:688017.SH:notice-1"
    assert audit["full_text_chars"] == len(event["full_text"])
    assert "full_text" not in audit
    assert "raw" not in audit


def main() -> int:
    test_sina_flash_event_audit_preserves_raw_and_context()
    test_sina_stock_news_event_audit_uses_portfolio_category()
    test_upsert_event_writes_ifind_audit_without_duplicating_full_text()
    print("event normalization checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
