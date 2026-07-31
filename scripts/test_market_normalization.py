#!/usr/bin/env python3
"""Regression checks for generic NormalizedMarketItem audit metadata."""

from __future__ import annotations

from market_flow import normalize_market_item


def test_sina_flash_normalization_preserves_raw_and_context() -> None:
    raw_item = {
        "source": "sina_flash",
        "id": "flash-1",
        "content_type": "flash_news",
        "title": "美联储主席讲话后，2年期美债收益率大跌",
        "summary": "市场重新定价美联储降息路径。",
        "full_text": "市场重新定价美联储降息路径。",
        "url": "https://finance.sina.com.cn/7x24/",
        "published_at": "2026-07-12T00:30:00+00:00",
        "symbols": ["688017.SH", "688017.SH"],
        "themes": ["新浪财经快讯", "宏观流动性/美联储政策"],
        "raw": {"macro_policy_line": {"matched": True, "tier": "primary"}},
    }
    item = normalize_market_item("sina_flash", raw_item)

    assert "_normalized_market_item" not in raw_item["raw"]
    assert item.raw["macro_policy_line"] == raw_item["raw"]["macro_policy_line"]
    assert item.source_category == "news_media"
    assert item.publisher_role == "news_media"
    assert item.collector == "scripts/sina_flash.py"
    assert item.content_type == "flash_news"
    assert item.symbols == ["688017.SH"]
    assert item.themes == ["新浪财经快讯", "宏观流动性/美联储政策"]
    assert item.dedupe_key == "sina_flash:flash-1"
    assert item.raw["id"] == "flash-1"
    assert "_normalized_market_item" not in item.raw


def test_sina_stock_news_normalization_uses_portfolio_category() -> None:
    raw_item = {
        "source": "sina_stock_news",
        "id": "item:abc",
        "content_type": "portfolio_news",
        "title": "持仓公司获得 AI 服务器订单",
        "summary": "中际旭创相关新闻：持仓公司获得 AI 服务器订单",
        "full_text": "",
        "url": "https://finance.sina.com.cn/stock/s/example.shtml",
        "published_at": "2026-07-12T00:30:00+00:00",
        "symbols": ["300308.SZ"],
        "themes": ["新浪财经个股资讯"],
        "raw": {"canonical_url": "https://finance.sina.com.cn/stock/s/example.shtml"},
    }
    item = normalize_market_item("sina_stock_news", raw_item)
    assert item.source_category == "portfolio_stock_news"
    assert item.publisher_role == "news_media"
    assert item.collector == "scripts/sina_stock_news.py"
    assert item.content_type == "portfolio_news"
    assert item.dedupe_key == "sina_stock_news:item:abc"
    assert item.symbols == ["300308.SZ"]


def main() -> int:
    test_sina_flash_normalization_preserves_raw_and_context()
    test_sina_stock_news_normalization_uses_portfolio_category()
    print("market normalization checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
