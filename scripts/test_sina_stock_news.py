#!/usr/bin/env python3
"""Regression checks for Sina per-stock news relevance filters."""

from __future__ import annotations

import tempfile
from pathlib import Path

from db_utils import connect_sqlite
from market_db import init_db
from market_flow import normalize_market_item
from market_item import AdmissionEvidence, AdmissionResult
from market_store import record_production_admission
from sina_stock_news import (
    canonical_article_url,
    freshness_hint,
    is_ai_generated_content,
    is_relevant_to_holding,
    legacy_source_event_id_for_item,
    parse_news_items,
    retryable_event_review,
    similar_news_title,
    source_event_id_for_item,
    stored_market_event,
)


def assert_retryable_review_uses_stored_event() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        event = {
            "source": "sina_stock_news",
            "source_event_id": "article:retry-test",
            "event_type": "stock_news",
            "title": "测试公司确认重大订单",
            "summary": "列表摘要",
            "full_text": "数据库中保存的完整正文，必须用于同一条 review 的重试。",
            "url": "https://example.com/retry-test",
            "published_at": "2026-07-26T01:00:00+00:00",
            "symbols": ["000001.SZ"],
            "themes": ["测试主题"],
            "raw": {"source_event_id": "article:retry-test"},
        }
        normalized = normalize_market_item("sina_stock_news", event, store_kind="event")
        admission = AdmissionResult(
            status="admitted",
            reason_code="holding_match",
            matched_families=("holding",),
            evidence=(AdmissionEvidence("holding", "entity", "测试公司"),),
            config_version="test-config",
        )
        market_item_id, review_id = record_production_admission(
            normalized,
            admission,
            db_path=db_path,
        )
        with connect_sqlite(db_path) as conn:
            conn.execute(
                "UPDATE market_reviews SET review_status='failed_retryable' WHERE id=?",
                (review_id,),
            )
            conn.commit()
        retry = retryable_event_review(market_item_id, db_path)
        assert retry is not None
        assert retry["market_item_id"] == market_item_id
        assert retry["market_review_id"] == review_id
        assert retry["admission"] == admission.to_dict()
        stored = stored_market_event(market_item_id, db_path)
        assert stored is not None
        assert stored["source_event_id"] == event["source_event_id"]
        assert stored["full_text"] == event["full_text"]
        assert stored["themes"] == event["themes"]
        with connect_sqlite(db_path) as conn:
            conn.execute(
                "UPDATE market_reviews SET review_status='succeeded' WHERE id=?",
                (review_id,),
            )
            conn.commit()
        assert retryable_event_review(market_item_id, db_path) is None


def main() -> int:
    assert_retryable_review_uses_stored_event()
    fiber_item = {
        "title": "算力时代拉动光纤刚需，苏州光纤企业产能排至2027年，自主研发加车载新场景赋能光通信长效发展"
    }
    lens = {"symbol": "300433.SZ", "name": "蓝思科技"}
    yangtze = {"symbol": "601869.SH", "name": "长飞光纤"}
    if is_relevant_to_holding(fiber_item, lens)[0]:
        raise AssertionError("fiber article should not be classified as Lens Technology news")
    if not is_relevant_to_holding(fiber_item, yangtze)[0]:
        raise AssertionError("fiber article should remain relevant to YOFC")

    glass_item = {"title": "“超级玻璃”，万亿元新赛道！企业纷纷加码布局"}
    if not is_relevant_to_holding(glass_item, lens)[0]:
        raise AssertionError("glass article should remain relevant to Lens Technology")

    old_boe_text = "叫停能源科技IPO进程次日（6月10日），京东方A股价大跌7.52%，总市值一日蒸发超170亿元。"
    hint = freshness_hint("2026-06-22 22:50", old_boe_text)
    if hint.get("status") != "stale_or_rehash":
        raise AssertionError(f"old BOE article should be marked stale_or_rehash: {hint}")
    if not hint.get("mentions_price_reaction"):
        raise AssertionError(f"old BOE article should detect price reaction: {hint}")

    if not is_ai_generated_content("<div>内容由AI生成</div>"):
        raise AssertionError("explicit AI-generated Sina label should be filtered")
    if not is_ai_generated_content("本文由 AI 生成，仅供参考"):
        raise AssertionError("spaced AI-generated label should be filtered")
    if is_ai_generated_content("英伟达 AI 芯片推动液冷需求增长"):
        raise AssertionError("normal AI industry news should not be filtered as generated content")

    duplicate_item = {
        "title": "两天上涨30% 华特气体提示氦气产品价格已有明显下降趋势",
        "published_at": "2026-06-23 20:11",
        "url": "https://finance.sina.com.cn/stock/s/2026-06-23/doc-example.shtml?from=stock&wm=foo&utm_source=bar",
    }
    wat = {"symbol": "688268.SH", "name": "华特气体"}
    peer = {"symbol": "300285.SZ", "name": "国瓷材料"}
    article_id = source_event_id_for_item(duplicate_item, wat)
    if article_id != source_event_id_for_item(duplicate_item, peer):
        raise AssertionError("same Sina article should dedupe across different holdings")
    if not article_id.startswith("article:"):
        raise AssertionError(f"new Sina source_event_id should be article-level: {article_id}")
    if legacy_source_event_id_for_item(duplicate_item, wat) == legacy_source_event_id_for_item(duplicate_item, peer):
        raise AssertionError("legacy id should remain stock-specific for backward compatibility checks")
    canonical = canonical_article_url(duplicate_item["url"])
    if "utm_source" in canonical or "from=stock" in canonical:
        raise AssertionError(f"tracking params should be stripped from canonical URL: {canonical}")
    if not similar_news_title(
        "两天上涨30% 华特气体提示氦气产品价格已有明显下降趋势",
        "华特气体(688268.SH)：氦气相关产品销售价格距高位已经有明显下降的趋势",
    ):
        raise AssertionError("rewritten Sina headlines for the same fact should dedupe")
    if similar_news_title(
        "日本酸素宣布7月起氦气涨价 华特气体涨超10%创历史新高",
        "两天上涨30% 华特气体提示氦气产品价格已有明显下降趋势",
    ):
        raise AssertionError("opposite helium price headlines should not be merged")

    html_text = """
    <div class="datelist"><ul>
      <li>2026-06-29&nbsp;14:30&nbsp;&nbsp;<a target="_blank" href="/stock/s/example.shtml">测试新闻</a></li>
    </ul></div>
    """
    parsed = parse_news_items(html_text)
    if parsed[0]["published_at"] != "2026-06-29T06:30:00+00:00":
        raise AssertionError(f"Sina HTML list time should normalize to UTC ISO: {parsed[0]}")

    print("sina stock news relevance checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
