#!/usr/bin/env python3
"""Regression checks for attributed high-value research across transports."""

from __future__ import annotations

import attributed_research
from attributed_research import (
    EXTRACTION_KEY,
    deterministic_extraction,
    prepare_item_for_decision,
)
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
        extraction = deterministic_extraction(media_item(source, SERENITY_CASE))
        assert extraction["institution_id"] == "semianalysis", source


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
        extraction = deterministic_extraction(media_item("future_media", text))
        assert extraction["institution_id"] == institution_id


def test_attribution_rule_does_not_depend_on_transport_role() -> None:
    item = NormalizedMarketItem(
        source="company_blog",
        source_category="official_company",
        publisher_role="official_company",
        title=SERENITY_CASE,
    )
    extraction = deterministic_extraction(item)
    assert extraction["institution_id"] == "semianalysis"


def test_mentions_criticism_and_lowercase_semi_do_not_false_positive() -> None:
    criticism = media_item(
        "sina_flash",
        "某分析师批评SemiAnalysis关于CPO的报告，认为其推迟判断错误且缺乏证据。",
    )
    mention_only = media_item("cls_telegraph_api", "文章回顾TrendForce此前的存储报告，当前没有新增数据。")
    lowercase_semi = media_item("future_media", "The company published its semi annual semiconductor report.")
    assert deterministic_extraction(criticism) == {}
    assert deterministic_extraction(mention_only) == {}
    assert deterministic_extraction(lowercase_semi) == {}


def test_llm_only_extracts_evidence() -> None:
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
    assert extraction["model"] == "fake-model"


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


def main() -> int:
    test_same_attribution_rule_applies_to_all_news_media_roles()
    test_all_monitored_research_institutions_have_default_attribution_aliases()
    test_attribution_rule_does_not_depend_on_transport_role()
    test_mentions_criticism_and_lowercase_semi_do_not_false_positive()
    test_llm_only_extracts_evidence()
    test_llm_hallucinated_quote_is_rejected_without_breaking_ingestion()
    print("attributed research checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
