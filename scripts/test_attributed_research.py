#!/usr/bin/env python3
"""Regression checks for attributed high-value research across transports."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import attributed_research
from attributed_research import (
    EXTRACTION_KEY,
    attributed_research_rule,
    deterministic_extraction,
    prepare_item_for_decision,
)
from decision_engine import decide_market_item
from market_db import init_db
from market_event_adapter import apply_event_rules_to_analysis
from market_item import decision_result_from_payload
from market_item import NormalizedMarketItem


SERENITY_CASE = (
    "【机构：存储面临长达数年的结构性短缺 CPO大规模落地推迟至2028年底】"
    "财联社7月10日电，专注于半导体与 AI 基础设施领域的顶级研究机构"
    "SemiAnalysis创始人Dylan Patel近日接受播客专访。"
    "Dylan强调，存储面临长达数年的结构性短缺，仍有2至3倍上行空间；"
    "Dylan认为，共封装光学（CPO）大规模落地时间被推迟至2028年底至2029年。"
)


def media_item(source: str, text: str, *, publisher_role: str = "news_media") -> NormalizedMarketItem:
    return NormalizedMarketItem(
        source=source,
        source_category="news_media" if publisher_role else "",
        publisher_role=publisher_role,
        content_type="article",
        title=text,
        summary=text,
    )


def test_same_attribution_rule_applies_to_all_news_media_roles() -> None:
    for source in ("cls_telegraph_api", "jin10_rsshub_important", "sina_flash", "sina_stock_news", "future_media"):
        decision = decide_market_item(media_item(source, SERENITY_CASE))
        assert decision.action == "push", source
        assert decision.importance == "high", source
        assert decision.rule_hits[0]["rule_id"] == "attributed_research_hard_variable", source
        assert decision.rule_hits[0]["transport_source"] == source
        assert decision.rule_hits[0]["attributed_institution"] == "semianalysis"


def test_all_monitored_research_institutions_have_default_attribution_aliases() -> None:
    samples = {
        "semianalysis": "SemiAnalysis表示，HBM供应出现结构性短缺并将持续到2028年。",
        "trendforce": "TrendForce表示，DRAM价格预计上调20%。",
        "semi": "SEMI报告指出，半导体设备投资将在2027年增长15%。",
        "digitimes": "DIGITIMES报道称，HBM供应短缺将持续到2028年。",
        "the_elec": "The Elec报道称，三星HBM4量产推迟至2027年。",
        "nikkei_xtech": "日经xTECH指出，先进封装设备投资将在2027年增加30%。",
    }
    for institution_id, text in samples.items():
        rule = attributed_research_rule(media_item("future_media", text))
        assert rule is not None, institution_id
        assert rule["attributed_institution"] == institution_id


def test_attribution_rule_does_not_depend_on_transport_role() -> None:
    item = NormalizedMarketItem(
        source="company_blog",
        source_category="official_company",
        publisher_role="official_company",
        title=SERENITY_CASE,
    )
    rule = attributed_research_rule(item)
    assert rule is not None
    assert rule["attributed_institution"] == "semianalysis"
    assert rule["publisher_role"] == "official_company"


def test_mentions_criticism_and_lowercase_semi_do_not_false_positive() -> None:
    criticism = media_item(
        "sina_flash",
        "某分析师批评SemiAnalysis关于CPO的报告，认为其推迟判断错误且缺乏证据。",
    )
    mention_only = media_item("cls_telegraph_api", "文章回顾TrendForce此前的存储报告，当前没有新增数据。")
    lowercase_semi = media_item("future_media", "The company published its semi annual semiconductor report.")
    assert deterministic_extraction(criticism) == {}
    assert attributed_research_rule(criticism) is None
    assert attributed_research_rule(mention_only) is None
    assert attributed_research_rule(lowercase_semi) is None


def test_llm_only_extracts_evidence_and_deterministic_engine_decides() -> None:
    item = media_item("sina_flash", "TrendForce表示，CPO商业化最早也要等到2028年末。")
    original_config = attributed_research.llm_config
    original_call = attributed_research.call_chat_completion_with_prompts
    captured: dict[str, str] = {}
    call_count = 0

    def fake_call(system_prompt: str, user_prompt: str, **_kwargs):
        nonlocal call_count
        call_count += 1
        captured["system"] = system_prompt
        captured["user"] = user_prompt
        return (
            {
                "institution_id": "trendforce",
                "speaker": "",
                "attribution": "explicit",
                "attribution_quote": "TrendForce表示，CPO商业化最早也要等到2028年末。",
                "claims": [
                    {
                        "topic": "cpo",
                        "event_type": "deployment_delay",
                        "evidence_quote": "CPO商业化最早也要等到2028年末",
                    }
                ],
            },
            "fake-model",
        )

    try:
        attributed_research.llm_config = lambda: ("key", "https://example.com", "fake-model")
        attributed_research.call_chat_completion_with_prompts = fake_call
        prepared = prepare_item_for_decision(item)
        prepared_again = prepare_item_for_decision(prepared)
    finally:
        attributed_research.llm_config = original_config
        attributed_research.call_chat_completion_with_prompts = original_call

    extraction = prepared.raw[EXTRACTION_KEY]
    assert prepared_again is prepared
    assert call_count == 1
    assert extraction["extraction_mode"] == "llm"
    assert extraction["claims"][0]["event_type"] == "deployment_delay"
    assert "禁止输出 importance、action、push" in captured["system"]
    assert '"action"' not in captured["user"]
    decision = decide_market_item(prepared)
    assert decision.action == "push"
    assert decision.rule_hits[0]["raw"]["attributed_research"]["model"] == "fake-model"


def test_llm_hallucinated_quote_is_rejected_without_breaking_ingestion() -> None:
    item = media_item("future_media", "TrendForce表示，CPO商业化时间仍待观察。")
    original_config = attributed_research.llm_config
    original_call = attributed_research.call_chat_completion_with_prompts
    try:
        attributed_research.llm_config = lambda: ("key", "https://example.com", "fake-model")
        attributed_research.call_chat_completion_with_prompts = lambda *_args, **_kwargs: (
            {
                "institution_id": "trendforce",
                "attribution": "explicit",
                "attribution_quote": "TrendForce表示CPO推迟至2030年",
                "claims": [
                    {"topic": "cpo", "event_type": "deployment_delay", "evidence_quote": "推迟至2030年"}
                ],
            },
            "fake-model",
        )
        prepared = prepare_item_for_decision(item)
    finally:
        attributed_research.llm_config = original_config
        attributed_research.call_chat_completion_with_prompts = original_call
    assert prepared.raw[EXTRACTION_KEY]["extraction_mode"] == "not_confirmed"
    assert attributed_research_rule(prepared) is None
    decision = decide_market_item(prepared)
    assert decision.audit_json["attributed_research_extraction"]["extraction_mode"] == "not_confirmed"


def test_cross_source_claims_share_a_dedup_key() -> None:
    cls_rule = attributed_research_rule(media_item("cls_telegraph_api", SERENITY_CASE))
    jin10_rule = attributed_research_rule(
        media_item(
            "jin10_rsshub_important",
            "SemiAnalysis创始人Dylan Patel表示，存储存在结构性短缺，CPO落地推迟至2028年。",
        )
    )
    assert cls_rule is not None and jin10_rule is not None
    assert cls_rule["dedup_key"] == jin10_rule["dedup_key"]
    assert cls_rule["dedup_lookback_days"] == 3


def test_semi_equipment_forecast_rewrites_share_stable_report_identity() -> None:
    variants = (
        media_item(
            "cls_telegraph_api",
            "SEMI报告指出，预测2026年全球半导体制造设备销售额将创下1659亿美元历史新高，"
            "同比增长23.2%，设备需求激增，2028年预计达到2295亿美元。",
        ),
        media_item(
            "yicai_brief",
            "SEMI发布设备市场预测报告。SEMI表示，2026年半导体设备销售额预计达到1659亿美元，"
            "AI驱动设备需求激增。",
        ),
        media_item(
            "digitimes_en_daily",
            "According to SEMI, global semiconductor equipment sales and shipments are projected to reach "
            "US$165.9 billion in 2026, a 23.2% increase.",
        ),
    )
    rules = [attributed_research_rule(item) for item in variants]
    assert all(rule is not None for rule in rules)
    assert {rule["dedup_key"] for rule in rules if rule} == {
        "attributed_research:semi:equipment_sales_forecast:2026"
    }
    assert all(rule.get("dedup_alias_keys") for rule in rules if rule)
    assert all(rule["dedup_key"] not in rule["dedup_alias_keys"] for rule in rules if rule)

    other_report = attributed_research_rule(
        media_item("future_media", "SEMI报告指出，半导体设备投资将在2027年增长15%。")
    )
    assert other_report is not None
    assert other_report["dedup_key"] != rules[0]["dedup_key"]


def test_distinct_memory_subthemes_do_not_share_a_dedup_key() -> None:
    dram = attributed_research_rule(media_item("cls_telegraph_api", "TrendForce表示，DRAM价格上调20%。"))
    nand = attributed_research_rule(media_item("sina_flash", "TrendForce表示，NAND价格上调20%。"))
    assert dram is not None and nand is not None
    assert dram["dedup_key"] != nand["dedup_key"]


def test_sina_event_adapter_uses_the_same_attribution_decision() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        analysis = apply_event_rules_to_analysis(
            {
                "source": "sina_flash",
                "event_type": "flash_news",
                "title": "TrendForce表示，DRAM价格上调20%。",
                "summary": "",
                "full_text": "",
                "url": "",
                "published_at": "2026-07-13T00:00:00+00:00",
                "symbols_json": "[]",
                "raw_json": json.dumps({}, ensure_ascii=False),
            },
            {},
            db_path=db_path,
        )
    decision = decision_result_from_payload(analysis)
    assert decision is not None
    assert decision.action == "push"
    assert decision.rule_hits[0]["rule_id"] == "attributed_research_hard_variable"


def main() -> int:
    test_same_attribution_rule_applies_to_all_news_media_roles()
    test_all_monitored_research_institutions_have_default_attribution_aliases()
    test_attribution_rule_does_not_depend_on_transport_role()
    test_mentions_criticism_and_lowercase_semi_do_not_false_positive()
    test_llm_only_extracts_evidence_and_deterministic_engine_decides()
    test_llm_hallucinated_quote_is_rejected_without_breaking_ingestion()
    test_cross_source_claims_share_a_dedup_key()
    test_semi_equipment_forecast_rewrites_share_stable_report_identity()
    test_distinct_memory_subthemes_do_not_share_a_dedup_key()
    test_sina_event_adapter_uses_the_same_attribution_decision()
    print("attributed research checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
