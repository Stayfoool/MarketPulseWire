#!/usr/bin/env python3
"""Regression checks for the side-effect-free v1 rule core and corpus."""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

from market_item import AdmissionEvidence, AdmissionResult, NormalizedMarketItem, RuleEvaluation
from rule_core_fixture import FixtureContractError, load_fixture_payload, validate_fixture_payload
from rule_core_v1 import (
    RuleConfigError,
    SourceAdmissionPolicy,
    evaluate_market_item,
    parse_portfolio_config,
    parse_rule_config,
)


ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "scripts" / "fixtures" / "rule_core_v1_cases.json"
CONFIG_PATH = ROOT / "config" / "rule_core_v1.test.json"


def loaded_contracts():
    corpus = load_fixture_payload(CORPUS_PATH)
    config = parse_rule_config(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    portfolios = {
        name: parse_portfolio_config(payload)
        for name, payload in corpus["portfolio_fixtures"].items()
    }
    return corpus, config, portfolios


def test_passive_admission_contract_rejects_invalid_combinations() -> None:
    evidence = AdmissionEvidence(
        rule_family="semiconductor_ai",
        reason_code="semiconductor_ai_scope",
        evidence_quote="HBM supply shortage",
        matched_term_ids=("HBM",),
    )
    admitted = AdmissionResult(
        status="admitted",
        reason_code="content_scope_match",
        matched_families=("semiconductor_ai",),
        evidence=(evidence,),
        config_version="test-v1",
    )
    assert admitted.to_dict()["rule_contract_version"] == "rule-core-v1"
    try:
        AdmissionResult(
            status="excluded",
            reason_code="out_of_scope",
            matched_families=("semiconductor_ai",),
            evidence=(),
            config_version="test-v1",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("excluded admission cannot expose matched families")
    excluded = AdmissionResult(
        status="excluded",
        reason_code="global_exclude",
        matched_families=(),
        evidence=(
            AdmissionEvidence(
                rule_family="global",
                reason_code="global_exclude",
                evidence_quote="培训广告",
            ),
        ),
        config_version="test-v1",
    )
    assert excluded.evidence[0].rule_family == "global"
    try:
        RuleEvaluation(admission=admitted, decision=None)
    except ValueError:
        pass
    else:
        raise AssertionError("admitted evaluation cannot omit DecisionResult")


def test_rule_config_and_fixture_schemas_fail_closed() -> None:
    config_payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    broken = copy.deepcopy(config_payload)
    broken["unknown"] = []
    try:
        parse_rule_config(broken)
    except RuleConfigError:
        pass
    else:
        raise AssertionError("unknown rule-config fields must fail closed")
    broken = copy.deepcopy(config_payload)
    broken["config_version"] = ""
    try:
        parse_rule_config(broken)
    except RuleConfigError:
        pass
    else:
        raise AssertionError("missing config version must fail closed")
    broken = copy.deepcopy(config_payload)
    broken["trade_policy"]["corridors"]["china_us"] = {
        "china_terms": ["中国"],
        "counterparty_terms": [],
        "joint_terms": [],
    }
    try:
        parse_rule_config(broken)
    except RuleConfigError:
        pass
    else:
        raise AssertionError("one-sided trade corridor must fail closed")
    broken = copy.deepcopy(config_payload)
    broken["trusted_attribution"]["institutions"]["trusted_research"]["domains"] = [
        "https://research.example/path"
    ]
    try:
        parse_rule_config(broken)
    except RuleConfigError:
        pass
    else:
        raise AssertionError("trusted attribution domains must be hostnames only")

    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    broken_corpus = copy.deepcopy(corpus)
    broken_corpus["cases"][0]["unknown"] = True
    try:
        validate_fixture_payload(broken_corpus)
    except FixtureContractError:
        pass
    else:
        raise AssertionError("unknown fixture fields must fail closed")
    broken_corpus = copy.deepcopy(corpus)
    broken_corpus["cases"][1]["variants"][0]["variant_id"] = broken_corpus["cases"][0]["variants"][0]["variant_id"]
    try:
        validate_fixture_payload(broken_corpus)
    except FixtureContractError:
        pass
    else:
        raise AssertionError("duplicate fixture IDs must fail closed")


def test_trusted_institution_domains_are_optional_and_non_authoritative() -> None:
    config_payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    without_domains = parse_rule_config(config_payload)
    assert all(not institution.domains for institution in without_domains.trusted_institutions)

    with_domains_payload = copy.deepcopy(config_payload)
    with_domains_payload["trusted_attribution"]["institutions"]["trusted_research"][
        "domains"
    ] = ["research.example"]
    with_domains_payload["trusted_attribution"]["institutions"]["international_bank"][
        "domains"
    ] = ["bank.example"]
    with_domains = parse_rule_config(with_domains_payload)

    portfolio = parse_portfolio_config([])
    attributed_revision = NormalizedMarketItem(
        source="finance_media",
        source_category="news_media",
        publisher_role="news_media",
        content_type="article",
        title="国际大行将此前预计美联储不调整利率改为年内加息三次",
    )
    results = [
        evaluate_market_item(
            attributed_revision,
            rule_config=config,
            portfolio=portfolio,
            source_policy=SourceAdmissionPolicy(),
        )
        for config in (without_domains, with_domains)
    ]
    assert [result.admission.status for result in results] == ["admitted", "admitted"]
    assert [result.decision.action if result.decision else None for result in results] == [
        "push",
        "push",
    ]
    assert [
        result.decision.rule_hits if result.decision else [] for result in results
    ] == [
        results[0].decision.rule_hits if results[0].decision else [],
        results[0].decision.rule_hits if results[0].decision else [],
    ]

    domain_only = evaluate_market_item(
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="research.example 发布普通机构动态",
            url="https://research.example/update",
        ),
        rule_config=with_domains,
        portfolio=portfolio,
        source_policy=SourceAdmissionPolicy(),
    )
    assert domain_only.admission.status == "excluded"
    assert domain_only.decision is None


def test_all_approved_v1_cases_are_executable() -> None:
    corpus, config, portfolios = loaded_contracts()
    assert len(corpus["cases"]) == 52
    assert sum(len(case["variants"]) for case in corpus["cases"]) == 62
    families = {case["family"] for case in corpus["cases"]}
    assert families == {"holding", "semiconductor_ai", "macro_data", "fed_policy", "trade_policy"}
    actions: set[str | None] = set()

    for case in corpus["cases"]:
        expected = case["expected"]
        variant_results: list[tuple[str, str | None]] = []
        for variant in case["variants"]:
            item = NormalizedMarketItem(
                source=variant["source"],
                source_category=variant["source_category"],
                publisher_role=variant["publisher_role"],
                content_type=variant["content_type"],
                title=case["text"],
                symbols=case["symbols"],
            )
            evaluation = evaluate_market_item(
                item,
                rule_config=config,
                portfolio=portfolios[case["portfolio_fixture"]],
                source_policy=SourceAdmissionPolicy(
                    tuple(variant.get("direct_admission_families", ()))
                ),
            )
            action = evaluation.decision.action if evaluation.decision is not None else None
            actions.add(action)
            assert evaluation.admission.status == expected["admission"], variant["variant_id"]
            assert action == expected["action"], variant["variant_id"]
            assert bool(evaluation.decision and evaluation.decision.should_push) is expected[
                "push_eligible"
            ], variant["variant_id"]
            if expected["rule_id"] is None:
                assert evaluation.decision is None, variant["variant_id"]
                if evaluation.admission.reason_code == "global_exclude":
                    assert evaluation.admission.evidence, variant["variant_id"]
                    assert {item.rule_family for item in evaluation.admission.evidence} == {"global"}
            else:
                assert evaluation.decision is not None
                assert expected["rule_id"] in {
                    hit["rule_id"] for hit in evaluation.decision.rule_hits
                }, variant["variant_id"]
                assert evaluation.decision.audit_json["config_version"] == "public-test-v1"
                assert evaluation.decision.audit_json["source_metadata_not_used_for_materiality"] is True
                for evidence in evaluation.admission.evidence:
                    assert all(term_id.startswith("term:") for term_id in evidence.matched_term_ids)
                    assert all(
                        term_id not in case["text"] for term_id in evidence.matched_term_ids
                    )
            variant_results.append((evaluation.admission.status, action))
        assert len(set(variant_results)) == 1, case["case_id"]
    assert actions == {"push", "daily", "archive", None}


def test_source_identity_and_llm_availability_cannot_change_core_result() -> None:
    corpus, config, portfolios = loaded_contracts()
    representatives = {"HOLD-003", "SEMI-008", "MACRO-001", "FED-004", "TRADE-001"}
    cases = {case["case_id"]: case for case in corpus["cases"] if case["case_id"] in representatives}
    assert set(cases) == representatives
    for case in cases.values():
        variant = case["variants"][0]
        common = {
            "source": variant["source"],
            "source_category": variant["source_category"],
            "publisher_role": variant["publisher_role"],
            "content_type": variant["content_type"],
            "title": case["text"],
            "symbols": case["symbols"],
        }
        source_policy = SourceAdmissionPolicy(tuple(variant.get("direct_admission_families", ())))
        plain = evaluate_market_item(
            NormalizedMarketItem(**common),
            rule_config=config,
            portfolio=portfolios[case["portfolio_fixture"]],
            source_policy=source_policy,
        )
        failed_llm = evaluate_market_item(
            NormalizedMarketItem(**common, raw={"llm_status": "failed"}),
            rule_config=config,
            portfolio=portfolios[case["portfolio_fixture"]],
            source_policy=source_policy,
        )
        assert failed_llm.to_dict() == plain.to_dict(), case["case_id"]


def test_holding_capital_change_uses_document_title_to_resolve_routine_attachments() -> None:
    _, config, portfolios = loaded_contracts()
    portfolio = portfolios["holding_test"]
    variants = (
        NormalizedMarketItem(
            source="company_disclosure",
            source_category="company_disclosure",
            publisher_role="company_official",
            content_type="company_disclosure",
            title="甲公司关于向控股子公司增资暨关联交易的公告",
            full_text="甲公司拟现金增资。备查文件包括标的公司审计报告。",
            symbols=["TEST1.SZ"],
        ),
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="甲公司拟向控股子公司增资",
            full_text="交易文件包含标的公司审计报告。",
            symbols=["TEST1.SZ"],
        ),
    )
    decisions = [
        evaluate_market_item(
            item,
            rule_config=config,
            portfolio=portfolio,
            source_policy=SourceAdmissionPolicy(),
        ).decision
        for item in variants
    ]
    assert all(decision is not None for decision in decisions)
    assert {decision.action for decision in decisions if decision} == {"push"}
    assert {
        hit["rule_id"] for decision in decisions if decision for hit in decision.rule_hits
    } == {"holding_material_event"}

    audit_attachment = evaluate_market_item(
        NormalizedMarketItem(
            source="company_disclosure",
            source_category="company_disclosure",
            publisher_role="company_official",
            content_type="company_disclosure",
            title="甲公司控股子公司季度审计报告",
            full_text="报告附注回顾历史增资事项。",
            symbols=["TEST1.SZ"],
        ),
        rule_config=config,
        portfolio=portfolio,
        source_policy=SourceAdmissionPolicy(),
    ).decision
    assert audit_attachment is not None and audit_attachment.action == "archive"
    assert {hit["rule_id"] for hit in audit_attachment.rule_hits} == {"holding_ordinary"}

    meeting_notice = evaluate_market_item(
        NormalizedMarketItem(
            source="company_disclosure",
            source_category="company_disclosure",
            publisher_role="company_official",
            content_type="company_disclosure",
            title="甲公司关于召开2026年第一次临时股东大会的通知",
            full_text="会议将审议定向发行和子公司增资议案。",
            symbols=["TEST1.SZ"],
        ),
        rule_config=config,
        portfolio=portfolio,
        source_policy=SourceAdmissionPolicy(),
    ).decision
    assert meeting_notice is not None and meeting_notice.action == "archive"
    assert {hit["rule_id"] for hit in meeting_notice.rule_hits} == {"holding_ordinary"}

    valuation_attachment = evaluate_market_item(
        NormalizedMarketItem(
            source="company_disclosure",
            source_category="company_disclosure",
            publisher_role="company_official",
            content_type="company_disclosure",
            title="甲公司半导体子公司拟增资涉及的资产评估报告",
            full_text="评估附件说明拟增资交易的估值依据。",
            symbols=["TEST1.SZ"],
        ),
        rule_config=config,
        portfolio=portfolio,
        source_policy=SourceAdmissionPolicy(),
    ).decision
    assert valuation_attachment is not None and valuation_attachment.action == "archive"
    assert {hit["rule_id"] for hit in valuation_attachment.rule_hits} == {
        "holding_ordinary",
        "semiconductor_ordinary",
    }


def test_approved_corporate_material_changes_are_source_neutral() -> None:
    _, config, portfolios = loaded_contracts()
    portfolio = portfolios["holding_test"]
    source_variants = (
        ("company_disclosure", "company_disclosure", "company_official", "company_disclosure"),
        ("finance_media", "news_media", "news_media", "article"),
    )
    holding_cases = (
        (
            "甲公司董事会会议决议公告",
            "董事会审议通过《关于向控股子公司增资的议案》，尚需提交股东大会审议。",
        ),
        (
            "甲公司预计上半年净利润同比增长",
            "甲公司正式发布半年度业绩预告。",
        ),
        (
            "甲公司预计上半年盈利四至五亿元",
            "甲公司正式发布半年度业绩预告。",
        ),
        (
            "甲公司完成对乙公司百分之百股权收购",
            "甲公司宣布收购已经完成审批和对价支付。",
        ),
        (
            "甲公司经营进展",
            "甲公司宣布将核心募投项目建设周期延长一年。",
        ),
    )
    for title, full_text in holding_cases:
        decisions = []
        for source, category, role, content_type in source_variants:
            evaluation = evaluate_market_item(
                NormalizedMarketItem(
                    source=source,
                    source_category=category,
                    publisher_role=role,
                    content_type=content_type,
                    title=title,
                    full_text=full_text,
                    symbols=["TEST1.SZ"],
                ),
                rule_config=config,
                portfolio=portfolio,
                source_policy=SourceAdmissionPolicy(),
            )
            decisions.append(evaluation.decision)
        assert all(decision is not None for decision in decisions)
        assert {decision.action for decision in decisions if decision} == {"push"}
        assert {
            hit["rule_id"] for decision in decisions if decision for hit in decision.rule_hits
        } >= {"holding_material_event"}

    semiconductor_decisions = []
    for source, category, role, content_type in source_variants:
        evaluation = evaluate_market_item(
            NormalizedMarketItem(
                source=source,
                source_category=category,
                publisher_role=role,
                content_type=content_type,
                title="HBM 公司预计上半年净利润同比增长",
                full_text="公司正式发布半年度业绩预告。",
            ),
            rule_config=config,
            portfolio=portfolios["empty"],
            source_policy=SourceAdmissionPolicy(),
        )
        semiconductor_decisions.append(evaluation.decision)
    assert all(decision is not None for decision in semiconductor_decisions)
    assert {decision.action for decision in semiconductor_decisions if decision} == {"push"}
    assert {
        hit["rule_id"]
        for decision in semiconductor_decisions
        if decision
        for hit in decision.rule_hits
    } == {"semiconductor_material_change"}

    pending_meeting_notice = evaluate_market_item(
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="甲公司关于召开临时股东大会的通知",
            full_text="会议通知称，股东大会将审议向控股子公司增资的议案。",
            symbols=["TEST1.SZ"],
        ),
        rule_config=config,
        portfolio=portfolio,
        source_policy=SourceAdmissionPolicy(),
    ).decision
    assert pending_meeting_notice is not None and pending_meeting_notice.action == "archive"
    assert {hit["rule_id"] for hit in pending_meeting_notice.rule_hits} == {"holding_ordinary"}


def test_corporate_background_and_existing_relationships_remain_daily() -> None:
    _, config, portfolios = loaded_contracts()
    portfolio = portfolios["holding_test"]
    items = (
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="甲公司经营动态",
            full_text="背景资料显示，公司此前发布过业绩预告。",
            symbols=["TEST1.SZ"],
        ),
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="甲公司回应客户合作",
            full_text="公司表示乙公司一直是重要客户，但未披露新订单或新协议。",
            symbols=["TEST1.SZ"],
        ),
    )
    decisions = [
        evaluate_market_item(
            item,
            rule_config=config,
            portfolio=portfolio,
            source_policy=SourceAdmissionPolicy(),
        ).decision
        for item in items
    ]
    assert all(decision is not None and decision.action == "daily" for decision in decisions)
    assert {
        hit["rule_id"] for decision in decisions if decision for hit in decision.rule_hits
    } == {"holding_ordinary"}


def test_supply_questions_require_an_affirmative_answer() -> None:
    _, config, portfolios = loaded_contracts()
    source_variants = (
        ("finance_media", "news_media", "news_media", "article"),
        ("industry_media", "research_industry_media", "research_publisher", "article"),
    )
    cases = (
        (
            "甲公司稳定供货AI芯片客户？公司回应",
            "有投资者向公司提问，甲公司稳定供货AI芯片客户。请问AI芯片订单是否持续放量？公司回答称，目前未涉及相关业务，请以公告为准。",
            "daily",
        ),
        (
            "甲公司开始供货AI芯片客户？公司回应",
            "有投资者提问，公司是否已经供货？公司回答称，已经开始向AI芯片客户批量供货。",
            "push",
        ),
        (
            "半导体材料稳定供货英伟达",
            "行业文章称，甲公司稳定供货英伟达，未披露公司回应或新订单。",
            "daily",
        ),
    )
    for title, full_text, expected_action in cases:
        decisions = []
        for source, category, role, content_type in source_variants:
            evaluation = evaluate_market_item(
                NormalizedMarketItem(
                    source=source,
                    source_category=category,
                    publisher_role=role,
                    content_type=content_type,
                    title=title,
                    full_text=full_text,
                    symbols=["TEST1.SZ"],
                ),
                rule_config=config,
                portfolio=portfolios["holding_test"],
                source_policy=SourceAdmissionPolicy(),
            )
            decisions.append(evaluation.decision)
        assert all(decision is not None for decision in decisions)
        assert {decision.action for decision in decisions if decision} == {expected_action}


def test_materiality_evidence_is_bound_to_the_current_fact() -> None:
    _, config, portfolios = loaded_contracts()
    portfolio = portfolios["holding_test"]
    ordinary_items = (
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="甲公司一季度净利大增，多家公司扭亏的业绩盘点",
            full_text="文章汇总已经披露的一季度报告。",
            symbols=["TEST1.SZ"],
        ),
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="甲公司合作项目预计2027年起贡献显著利润",
            full_text="文章讨论既有项目的远期影响。",
            symbols=["TEST1.SZ"],
        ),
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="两只存储股扭亏并大幅盈利，预计业绩爆发",
            full_text="文章汇总多家公司已经披露的业绩。",
            symbols=["TEST1.SZ"],
        ),
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="甲公司股价波动",
            full_text="2025年8月公告显示，公司已经向AI服务器项目小批量供货。",
            published_at="2026-07-19T09:00:00+08:00",
            symbols=["TEST1.SZ"],
        ),
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="甲公司产业展望",
            full_text="机构预计AI芯片订单将密集落地，但没有新签或执行证据。",
            symbols=["TEST1.SZ"],
        ),
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="甲公司机器人产业展望",
            full_text="产业链公司未来将持续收获AI机器人订单。",
            symbols=["TEST1.SZ"],
        ),
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="甲公司研究更新",
            full_text="机构上调盈利预测，但维持甲公司买入评级。",
            symbols=["TEST1.SZ"],
        ),
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="甲公司生产动态",
            full_text="公司产线满负荷运行，普通客户交期有所延长。",
            symbols=["TEST1.SZ"],
        ),
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="甲公司机器人业务更新",
            full_text="美国CPI即将发布。公司订单不及预期。",
            symbols=["TEST1.SZ"],
        ),
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="甲公司市场观点",
            full_text="AI产业链订单饱满，A股科技板块因此获得支撑。",
            symbols=["TEST1.SZ"],
        ),
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="甲公司跌5.2%，成交额20亿元，后市是否有机会？",
            full_text="静态资料称公司长期向AI服务器客户批量供货。",
            symbols=["TEST1.SZ"],
        ),
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="甲公司产品能力介绍",
            full_text="公司具备AI芯片材料大批量供货能力，但没有披露客户或已执行订单。",
            symbols=["TEST1.SZ"],
        ),
        NormalizedMarketItem(
            source="company_disclosure",
            source_category="company_disclosure",
            publisher_role="company_official",
            content_type="company_disclosure",
            title="甲公司定向发行股票募集资金使用可行性分析报告",
            full_text="项目目标是完成AI芯片材料工艺落地与批量供货。",
            symbols=["TEST1.SZ"],
        ),
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="甲公司业务风险提示",
            full_text="CPO相关业务在手订单较少，尚未形成规模化应用。",
            symbols=["TEST1.SZ"],
        ),
    )
    for item in ordinary_items:
        decision = evaluate_market_item(
            item,
            rule_config=config,
            portfolio=portfolio,
            source_policy=SourceAdmissionPolicy(),
        ).decision
        assert decision is not None and decision.action != "push", item.title

    generic_rotation = evaluate_market_item(
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="AI芯片行情",
            full_text="市场轮动中，半导体方向继续活跃。",
        ),
        rule_config=config,
        portfolio=portfolios["empty"],
        source_policy=SourceAdmissionPolicy(),
    ).decision
    assert generic_rotation is not None and generic_rotation.action == "archive"

    market_template = evaluate_market_item(
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="存储概念股集体上涨，ETF成交放量",
            full_text="DRAM价格持续上涨，供应极度紧缺，预计第三季度环比涨幅13%至18%。",
        ),
        rule_config=config,
        portfolio=portfolios["empty"],
        source_policy=SourceAdmissionPolicy(),
    ).decision
    assert market_template is not None and market_template.action == "archive"

    holding_market_template = evaluate_market_item(
        NormalizedMarketItem(
            source="stock_news",
            source_category="portfolio_stock_news",
            publisher_role="news_media",
            content_type="portfolio_news",
            title="甲公司股价涨停，板块集体走强",
            full_text="文章主要介绍盘面和资金流，没有新的公司公告或订单。",
            symbols=["TEST1.SZ"],
        ),
        rule_config=config,
        portfolio=portfolios["holding_test"],
        source_policy=SourceAdmissionPolicy(),
    ).decision
    assert holding_market_template is not None and holding_market_template.action == "archive"


def test_new_core_is_not_wired_into_production_or_side_effect_modules() -> None:
    core_path = ROOT / "scripts" / "rule_core_v1.py"
    tree = ast.parse(core_path.read_text(encoding="utf-8"), filename=core_path.name)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported == {"__future__", "hashlib", "re", "dataclasses", "typing", "market_item"}

    allowed = {
        "rule_core_v1.py",
        "rule_core_fixture.py",
        "rule_core_replay.py",
        "rule_core_history_replay.py",
        "rule_config_migration_v1.py",
        "market_lifecycle_v1.py",
    }
    production_importers: list[str] = []
    for path in (ROOT / "scripts").glob("*.py"):
        if path.name.startswith("test_") or path.name in allowed:
            continue
        module = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        for node in ast.walk(module):
            if isinstance(node, ast.Import) and any(alias.name == "rule_core_v1" for alias in node.names):
                production_importers.append(path.name)
            elif isinstance(node, ast.ImportFrom) and node.module == "rule_core_v1":
                production_importers.append(path.name)
    assert production_importers == []


def main() -> int:
    test_passive_admission_contract_rejects_invalid_combinations()
    test_rule_config_and_fixture_schemas_fail_closed()
    test_trusted_institution_domains_are_optional_and_non_authoritative()
    test_all_approved_v1_cases_are_executable()
    test_source_identity_and_llm_availability_cannot_change_core_result()
    test_holding_capital_change_uses_document_title_to_resolve_routine_attachments()
    test_approved_corporate_material_changes_are_source_neutral()
    test_corporate_background_and_existing_relationships_remain_daily()
    test_materiality_evidence_is_bound_to_the_current_fact()
    test_new_core_is_not_wired_into_production_or_side_effect_modules()
    print("rule core v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
