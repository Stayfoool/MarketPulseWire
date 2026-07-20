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

    static_outlook = "HBM市场规模预计增长20%。"
    official_research = evaluate_market_item(
        NormalizedMarketItem(
            source="research_site",
            source_category="research_industry_media",
            publisher_role="research_publisher",
            title=static_outlook,
            url="https://reports.research.example/hbm-outlook",
        ),
        rule_config=with_domains,
        portfolio=portfolio,
        source_policy=SourceAdmissionPolicy(),
    )
    media_reprint = evaluate_market_item(
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            title=static_outlook,
            url="https://finance.example/hbm-outlook",
        ),
        rule_config=with_domains,
        portfolio=portfolio,
        source_policy=SourceAdmissionPolicy(),
    )
    assert official_research.decision is not None and media_reprint.decision is not None
    assert official_research.decision.action == media_reprint.decision.action == "daily"
    assert official_research.decision.rule_hits[0]["attributed_institutions"] == ["trusted_research"]
    assert "attributed_institutions" not in media_reprint.decision.rule_hits[0]


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


def test_migrated_holding_corporate_events_are_source_neutral() -> None:
    _, config, portfolios = loaded_contracts()
    portfolio = parse_portfolio_config(
        [
            {
                "symbol": "TEST1.SZ",
                "names": ["甲公司", "Company A"],
                "related_news_keywords": [],
                "exclude_keywords": [],
                "immediate_alert_keywords": [],
            }
        ]
    )
    source_variants = (
        ("company_disclosure", "company_disclosure", "company_official", "company_disclosure"),
        ("finance_media", "news_media", "news_media", "article"),
    )
    cases = (
        (
            "甲公司签署重大供货合同",
            "甲公司已与乙公司签署供货协议，合同自今日起生效。",
        ),
        (
            "甲公司获得新订单",
            "甲公司新获客户订单并已开始执行。",
        ),
        (
            "甲公司产品价格调整",
            "甲公司决定从本月起将主要产品价格上调。",
        ),
        (
            "甲公司调整生产安排",
            "甲公司已正式减产，并暂停一条生产线。",
        ),
        (
            "甲公司资本开支调整",
            "甲公司董事会批准扩产，并上调年度资本开支。",
        ),
        (
            "甲公司工艺改造完成",
            "甲公司已完成工艺改造，产品良率得到提升。",
        ),
        (
            "甲公司收购事项进展",
            "甲公司董事会审议通过收购乙公司的议案。",
        ),
        (
            "甲公司出售子公司股权",
            "甲公司已签署出售子公司股权的正式协议。",
        ),
        (
            "监管机构批准甲公司产品上市",
            "监管机构正式批准甲公司的产品上市申请。",
        ),
        (
            "甲公司收到行政处罚决定",
            "主管部门已经作出对甲公司的正式罚款决定。",
        ),
        (
            "甲公司2026年半年度业绩快报",
            "公司正式披露半年度主要财务数据。",
        ),
        (
            "甲公司下修全年业绩指引",
            "甲公司正式下修全年营收指引。",
        ),
        (
            "Company A signs customer contract",
            "Company A has signed a new supply agreement with a customer.",
        ),
        (
            "Company A revises capital spending",
            "Company A has reduced annual capex and halted one production line.",
        ),
    )
    for title, full_text in cases:
        decisions = [
            evaluate_market_item(
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
            ).decision
            for source, category, role, content_type in source_variants
        ]
        assert all(decision is not None for decision in decisions), title
        assert {decision.action for decision in decisions if decision} == {"push"}, title
        assert {
            hit["rule_id"]
            for decision in decisions
            if decision
            for hit in decision.rule_hits
        } == {"holding_material_event"}, title


def test_unexecuted_holding_corporate_events_remain_daily_across_sources() -> None:
    _, config, portfolios = loaded_contracts()
    source_variants = (
        ("company_disclosure", "company_disclosure", "company_official", "company_disclosure"),
        ("finance_media", "news_media", "news_media", "article"),
    )
    cases = (
        (
            "甲公司扩产计划",
            "甲公司计划明年扩产，但项目尚未获得批准或启动。",
        ),
        (
            "甲公司签署框架供货协议",
            "甲公司签署非约束性框架供货协议，尚未形成订单。",
        ),
        (
            "甲公司回应收购传闻",
            "市场传闻甲公司可能收购乙公司，公司没有确认交易。",
        ),
        (
            "甲公司订单情况问答",
            "投资者提问：甲公司是否已经获得新订单？",
        ),
        (
            "甲公司经营回顾",
            "背景资料显示，甲公司2025年已完成资产出售。",
        ),
        (
            "甲公司补充监管备案材料",
            "监管机构要求甲公司补充备案材料，尚未批准、否决、暂停或终止申请。",
        ),
        (
            "甲公司收到行政处罚事先告知书",
            "主管部门向甲公司送达拟处罚事先告知书，尚未作出正式决定。",
        ),
        (
            "甲公司产品市场价格变化",
            "市场价格上涨，但甲公司没有决定或实施产品价格调整。",
        ),
        (
            "甲公司行业观点",
            "行业整体产能已经增加。甲公司仅就行业趋势发表观点。",
        ),
        (
            "甲公司客户关系更新",
            "甲公司一直向乙公司供货，但没有新订单或新协议。",
        ),
        (
            "甲公司研究更新",
            "分析师上调甲公司盈利预测，但公司没有修订业绩指引。",
        ),
    )
    for title, full_text in cases:
        decisions = [
            evaluate_market_item(
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
            ).decision
            for source, category, role, content_type in source_variants
        ]
        assert all(decision is not None for decision in decisions), title
        assert {decision.action for decision in decisions if decision} == {"daily"}, title
        assert {
            hit["rule_id"]
            for decision in decisions
            if decision
            for hit in decision.rule_hits
        } == {"holding_ordinary"}, title


def test_migrated_semiconductor_hard_variables_are_source_neutral() -> None:
    config_payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config_payload["semiconductor_ai_keywords"].extend(
        ["PCB", "data center", "advanced packaging", "memory"]
    )
    config = parse_rule_config(config_payload)
    portfolio = parse_portfolio_config([])
    source_variants = (
        ("digitimes_en_daily", "research_industry_media", "research_publisher", "article"),
        ("finance_media", "news_media", "news_media", "flash"),
    )
    cases = (
        (
            "Samsung preps multi-billion-dollar P5 orders as AI boom speeds up memory demand",
            "Samsung Electronics' latest multi-billion-dollar equipment procurement reflects "
            "the company's accelerating investment in AI memory capacity.",
            "push",
            "semiconductor_material_change",
        ),
        (
            "Intel reassigns global fabs, eyes early 14A and EMIB-T AI wins",
            "Intel has relaunched its worldwide manufacturing sites, pushing ahead with Intel "
            "18A mass production and advanced packaging programs including EMIB-T.",
            "push",
            "semiconductor_material_change",
        ),
        (
            "Compeq acquires Guanyin site to expand optical and data center PCB capacity",
            "Compeq approved the acquisition of a new manufacturing site to provide additional "
            "PCB capacity for optical interconnects and data center applications.",
            "push",
            "semiconductor_material_change",
        ),
        (
            "HBM测试设备采购启动",
            "公司已采购200台HBM测试设备，并开始安排设备交付。",
            "push",
            "semiconductor_material_change",
        ),
        (
            "Edge AI becomes China's next battleground as ModelBest tops US$2.8 billion valuation",
            "China's latest wave of investment in edge AI signals a shift toward large-scale "
            "commercialization and deployment across consumer and industrial hardware.",
            "daily",
            "semiconductor_commercial_development",
        ),
    )
    for title, full_text, expected_action, expected_rule_id in cases:
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
                ),
                rule_config=config,
                portfolio=portfolio,
                source_policy=SourceAdmissionPolicy(),
            )
            decisions.append(evaluation.decision)
        assert all(decision is not None for decision in decisions), title
        assert {decision.action for decision in decisions if decision} == {expected_action}, title
        assert {
            hit["rule_id"]
            for decision in decisions
            if decision
            for hit in decision.rule_hits
        } == {expected_rule_id}, title


def test_migrated_semiconductor_operating_changes_are_source_neutral() -> None:
    _, _, portfolios = loaded_contracts()
    config_payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config_payload["semiconductor_ai_keywords"].append("semiconductor")
    config = parse_rule_config(config_payload)
    source_variants = (
        ("industry_media", "research_industry_media", "research_publisher", "article"),
        ("finance_media", "news_media", "news_media", "flash"),
    )
    cases = (
        ("HBM供应商已开始出货新一代产品，交付周期同步缩短。", "push", "semiconductor_material_change"),
        ("HBM供应商已开始出货，但未披露本批交付数量。", "push", "semiconductor_material_change"),
        ("AI服务器需求激增，产业链进入新一轮备货。", "push", "semiconductor_material_change"),
        ("先进GPU出口管制正式生效，并扩大受限芯片范围。", "push", "semiconductor_material_change"),
        ("芯片厂已从钨切换至钼互连工艺。", "push", "semiconductor_material_change"),
        ("CPO芯片已完成送样并通过客户认证。", "push", "semiconductor_material_change"),
        ("CPO芯片已通过目标客户认证并进入交付准备。", "push", "semiconductor_material_change"),
        (
            "Semiconductor equipment supplier raised its 2026 shipment guidance.",
            "push",
            "semiconductor_material_change",
        ),
        ("机构预计NAND出货预测将下调，但尚未发布新指引。", "daily", "semiconductor_ordinary"),
        ("HBM供应商发布出货指引，但没有相对此前发生修订。", "daily", "semiconductor_ordinary"),
        ("监管机构拟议扩大AI芯片出口管制，草案仍在征求意见。", "daily", "semiconductor_ordinary"),
        ("芯片公司计划从通用GPU转向自研ASIC，尚未开始实施。", "daily", "semiconductor_ordinary"),
        ("HBM产品计划下季度开始送样，目前尚未交付样品。", "daily", "semiconductor_ordinary"),
        ("AI芯片关税豁免正式生效。", "daily", "semiconductor_ordinary"),
    )
    for text, expected_action, expected_rule_id in cases:
        decisions = [
            evaluate_market_item(
                NormalizedMarketItem(
                    source=source,
                    source_category=category,
                    publisher_role=role,
                    content_type=content_type,
                    title=text,
                ),
                rule_config=config,
                portfolio=portfolios["empty"],
                source_policy=SourceAdmissionPolicy(),
            ).decision
            for source, category, role, content_type in source_variants
        ]
        assert all(decision is not None for decision in decisions), text
        assert {decision.action for decision in decisions if decision} == {expected_action}, text
        assert {
            hit["rule_id"]
            for decision in decisions
            if decision
            for hit in decision.rule_hits
        } == {expected_rule_id}, text

    denied = evaluate_market_item(
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="HBM公司否认已经开始出货，当前尚无客户认证。",
        ),
        rule_config=config,
        portfolio=portfolios["empty"],
        source_policy=SourceAdmissionPolicy(),
    ).decision
    assert denied is not None and denied.action == "archive"
    assert {hit["rule_id"] for hit in denied.rule_hits} == {"semiconductor_ordinary"}


def test_migrated_semiconductor_performance_and_market_size_are_source_neutral() -> None:
    _, config, portfolios = loaded_contracts()
    source_variants = (
        ("industry_media", "research_industry_media", "research_publisher", "article"),
        ("finance_media", "news_media", "news_media", "flash"),
    )
    cases = (
        (
            "HBM厂商本季度营收同比增长，毛利率明显提升。",
            "",
            "push",
            "semiconductor_performance_change",
        ),
        (
            "HBM厂商本季度营收大增并突破历史纪录。",
            "",
            "push",
            "semiconductor_performance_change",
        ),
        (
            "HBM厂商本季度业绩超预期，毛利率达到45%。",
            "",
            "push",
            "semiconductor_performance_change",
        ),
        (
            "HBM supplier revenue surged while gross margin improved.",
            "",
            "push",
            "semiconductor_performance_change",
        ),
        (
            "AI服务器软件年度经常性收入ARR已翻倍。",
            "",
            "push",
            "semiconductor_performance_change",
        ),
        (
            "HBM市场规模同比增长并创历史新高。",
            "",
            "push",
            "semiconductor_performance_change",
        ),
        (
            "受信研究机构预计HBM市场规模将在未来五年翻倍。",
            "",
            "push",
            "semiconductor_performance_change",
        ),
        (
            "受信研究机构将HBM市场规模预测从100亿美元上调至120亿美元。",
            "",
            "push",
            "industry_forecast_revision",
        ),
        (
            "受信研究机构预计HBM市场规模到2028年达到1000亿美元。",
            "",
            "daily",
            "semiconductor_performance_outlook",
        ),
        (
            "HBM厂商本季度毛利率为45%。",
            "",
            "daily",
            "semiconductor_performance_outlook",
        ),
        (
            "机构预计HBM厂商明年毛利率将改善。",
            "",
            "daily",
            "semiconductor_performance_outlook",
        ),
        (
            "HBM市场规模预计增长20%。",
            "",
            "daily",
            "semiconductor_performance_outlook",
        ),
        (
            "HBM产业历史回顾",
            "2025年HBM市场规模同比增长。",
            "archive",
            "semiconductor_ordinary",
        ),
    )
    for title, full_text, expected_action, expected_rule_id in cases:
        decisions = [
            evaluate_market_item(
                NormalizedMarketItem(
                    source=source,
                    source_category=category,
                    publisher_role=role,
                    content_type=content_type,
                    title=title,
                    full_text=full_text,
                    published_at="2026-07-20T09:00:00+08:00",
                ),
                rule_config=config,
                portfolio=portfolios["empty"],
                source_policy=SourceAdmissionPolicy(),
            ).decision
            for source, category, role, content_type in source_variants
        ]
        assert all(decision is not None for decision in decisions), title
        assert {decision.action for decision in decisions if decision} == {expected_action}, title
        assert {
            hit["rule_id"]
            for decision in decisions
            if decision
            for hit in decision.rule_hits
        } == {expected_rule_id}, title


def test_trusted_research_is_audited_without_changing_semiconductor_action() -> None:
    _, config, portfolios = loaded_contracts()
    source_variants = (
        ("industry_media", "research_industry_media", "research_publisher", "article"),
        ("finance_media", "news_media", "news_media", "flash"),
    )
    attributed_cases = (
        (
            "受信研究机构表示，HBM市场规模预测已从100亿美元上调至120亿美元。",
            "push",
            "industry_forecast_revision",
        ),
        (
            "受信研究机构预计，HBM市场规模到2028年达到1000亿美元。",
            "daily",
            "semiconductor_performance_outlook",
        ),
    )
    for text, expected_action, expected_rule_id in attributed_cases:
        decisions = [
            evaluate_market_item(
                NormalizedMarketItem(
                    source=source,
                    source_category=category,
                    publisher_role=role,
                    content_type=content_type,
                    title=text,
                ),
                rule_config=config,
                portfolio=portfolios["empty"],
                source_policy=SourceAdmissionPolicy(),
            ).decision
            for source, category, role, content_type in source_variants
        ]
        assert all(decision is not None for decision in decisions), text
        assert {decision.action for decision in decisions if decision} == {expected_action}, text
        for decision in decisions:
            assert decision is not None
            assert {hit["rule_id"] for hit in decision.rule_hits} == {expected_rule_id}
            hit = decision.rule_hits[0]
            assert hit["attributed_institutions"] == ["trusted_research"]
            assert hit["attributed_research_evidence"][0]["claim_quote"] == text

    criticism = evaluate_market_item(
        NormalizedMarketItem(
            source="finance_media",
            title="业内人士反驳受信研究机构此前关于HBM市场规模的预测，当前没有新数据。",
        ),
        rule_config=config,
        portfolio=portfolios["empty"],
        source_policy=SourceAdmissionPolicy(),
    ).decision
    assert criticism is not None and criticism.action != "push"
    assert "attributed_research_evidence" not in criticism.rule_hits[0]

    mention_only = evaluate_market_item(
        NormalizedMarketItem(
            source="finance_media",
            title="文章介绍受信研究机构。HBM市场规模预计增长20%。",
        ),
        rule_config=config,
        portfolio=portfolios["empty"],
        source_policy=SourceAdmissionPolicy(),
    ).decision
    assert mention_only is not None and mention_only.action == "daily"
    assert "attributed_research_evidence" not in mention_only.rule_hits[0]

    stored_label_cannot_upgrade = evaluate_market_item(
        NormalizedMarketItem(
            source="finance_media",
            title="受信研究机构表示，HBM市场规模到2028年为1000亿美元。",
            raw={
                "_attributed_research": {
                    "institution_id": "trusted_research",
                    "attribution": "explicit",
                    "attribution_quote": "受信研究机构表示，HBM市场规模到2028年为1000亿美元。",
                    "claims": [
                        {
                            "event_type": "price_change",
                            "evidence_quote": "受信研究机构表示，HBM市场规模到2028年为1000亿美元。",
                        }
                    ],
                    "extraction_mode": "llm",
                }
            },
        ),
        rule_config=config,
        portfolio=portfolios["empty"],
        source_policy=SourceAdmissionPolicy(),
    ).decision
    assert stored_label_cannot_upgrade is not None
    assert stored_label_cannot_upgrade.action == "daily"
    stored_hit = stored_label_cannot_upgrade.rule_hits[0]
    assert stored_hit["rule_id"] == "semiconductor_performance_outlook"
    assert stored_hit["attributed_institutions"] == ["trusted_research"]


def test_migrated_ai_compute_and_credit_actions_are_source_neutral() -> None:
    _, config, portfolios = loaded_contracts()
    source_variants = (
        ("industry_media", "research_industry_media", "research_publisher", "article"),
        ("finance_media", "news_media", "news_media", "flash"),
    )
    cases = (
        (
            "Meta正在构建一项云业务，以出售其过剩的AI算力。",
            "push",
            "ai_compute_supply_demand",
        ),
        (
            "谷歌算力告急并限制Meta使用GPU算力，多个项目被迫推迟。",
            "push",
            "ai_compute_supply_demand",
        ),
        (
            "CoreWeave取消一项AI算力租赁合同并缩减GPU云容量。",
            "push",
            "ai_compute_supply_demand",
        ),
        (
            "Oracle GPU算力服务价格上调20%。",
            "push",
            "ai_compute_supply_demand",
        ),
        (
            "Meta的AI算力利用率上升至95%。",
            "push",
            "ai_compute_supply_demand",
        ),
        (
            "Meta计划将AI计算能力由7GW提升至14GW，实现算力翻倍。",
            "push",
            "ai_compute_supply_demand",
        ),
        (
            "美国纽约州州长宣布暂停新建耗电50兆瓦及以上的AI数据中心。",
            "push",
            "ai_compute_supply_demand",
        ),
        (
            "Meta否认算力过剩，并表示出租部分AI算力比自用更有价值。",
            "push",
            "ai_compute_supply_demand",
        ),
        (
            "Meta表示其AI算力利用率仅为40%，存在闲置算力。",
            "push",
            "ai_compute_supply_demand",
        ),
        (
            "Oracle预计GPU算力服务价格将上涨，但尚未调整定价。",
            "daily",
            "ai_compute_supply_demand",
        ),
        (
            "CoreWeave签署一项20亿美元非约束性AI算力框架协议。",
            "daily",
            "ai_compute_supply_demand",
        ),
        (
            "Meta plans to expand capacity for AI compute.",
            "daily",
            "ai_compute_supply_demand",
        ),
        (
            "联想表示AI算力需求旺盛，但未披露合同或容量变化。",
            "daily",
            "ai_compute_supply_demand",
        ),
        (
            "Microsoft issued $40 billion of bonds to finance AI infrastructure.",
            "daily",
            "ai_hyperscaler_credit_stress",
        ),
        (
            "Oracle issued bonds for AI data centers, but market absorption was difficult.",
            "daily",
            "ai_hyperscaler_credit_stress",
        ),
        (
            "Amazon issued bonds for AI infrastructure; investor demand was weak and the new bonds traded below issue price.",
            "push",
            "ai_hyperscaler_credit_stress",
        ),
        (
            "OpenAI postponed its AI infrastructure bond financing because investor demand was weak.",
            "push",
            "ai_hyperscaler_credit_stress",
        ),
        (
            "Microsoft's AI debt for AI infrastructure was downgraded and placed on negative outlook.",
            "push",
            "ai_hyperscaler_credit_stress",
        ),
        (
            "Oracle disclosed a liquidity shortfall in debt used for AI infrastructure.",
            "push",
            "ai_hyperscaler_credit_stress",
        ),
    )
    for text, expected_action, expected_rule_id in cases:
        decisions = []
        for source, category, role, content_type in source_variants:
            evaluation = evaluate_market_item(
                NormalizedMarketItem(
                    source=source,
                    source_category=category,
                    publisher_role=role,
                    content_type=content_type,
                    title=text,
                ),
                rule_config=config,
                portfolio=portfolios["empty"],
                source_policy=SourceAdmissionPolicy(),
            )
            decisions.append(evaluation.decision)
        assert all(decision is not None for decision in decisions), text
        assert {decision.action for decision in decisions if decision} == {expected_action}, text
        assert {
            hit["rule_id"]
            for decision in decisions
            if decision
            for hit in decision.rule_hits
        } == {expected_rule_id}, text

    content_overrides = (
        (
            "Meta成交额放大，主力净流入居前",
            "Meta正在构建一项云业务，以出售其过剩的AI算力。",
            "push",
            "ai_compute_supply_demand",
        ),
        (
            "Microsoft资产评估报告",
            "Microsoft issued $40 billion of bonds to finance AI infrastructure.",
            "daily",
            "ai_hyperscaler_credit_stress",
        ),
    )
    for title, full_text, expected_action, expected_rule_id in content_overrides:
        decisions = [
            evaluate_market_item(
                NormalizedMarketItem(
                    source=source,
                    source_category=category,
                    publisher_role=role,
                    content_type=content_type,
                    title=title,
                    full_text=full_text,
                ),
                rule_config=config,
                portfolio=portfolios["empty"],
                source_policy=SourceAdmissionPolicy(),
            ).decision
            for source, category, role, content_type in source_variants
        ]
        assert all(decision is not None for decision in decisions), title
        assert {decision.action for decision in decisions if decision} == {expected_action}, title
        assert {
            hit["rule_id"]
            for decision in decisions
            if decision
            for hit in decision.rule_hits
        } == {expected_rule_id}, title

    rumor = evaluate_market_item(
        NormalizedMarketItem(
            source="finance_media",
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title="A rumor says Microsoft may reportedly issue bonds for AI infrastructure.",
        ),
        rule_config=config,
        portfolio=portfolios["empty"],
        source_policy=SourceAdmissionPolicy(),
    ).decision
    assert rumor is not None and rumor.action == "archive"
    assert {hit["rule_id"] for hit in rumor.rule_hits} == {"semiconductor_ordinary"}


def test_migrated_fed_path_and_trade_actions_are_source_neutral() -> None:
    _, config, portfolios = loaded_contracts()
    source_variants = (
        ("industry_media", "research_industry_media", "research_publisher", "article"),
        ("finance_media", "news_media", "news_media", "flash"),
    )
    cases = (
        (
            "美银证券此前预计美联储年内不会调整利率，现将预测改为2026年9月、10月和12月各加息25个基点，累计加息75个基点。",
            "push",
            "international_bank_fed_rate_path_revision",
        ),
        (
            "高盛将美联储今年降息预期从3次下调至1次。",
            "push",
            "international_bank_fed_rate_path_revision",
        ),
        (
            "JPMorgan pushes back its first Fed rate cut from September to December.",
            "push",
            "international_bank_fed_rate_path_revision",
        ),
        (
            "UBS raises its Fed terminal rate forecast to 4.5% from 4.0%.",
            "push",
            "international_bank_fed_rate_path_revision",
        ),
        (
            "巴克莱预计美联储将在2026年12月降息25个基点。",
            "daily",
            "international_bank_fed_rate_path_revision",
        ),
        (
            "美银维持此前美联储年内降息两次的预测不变。",
            "daily",
            "fed_path_unchanged",
        ),
        (
            "USTR seeks public comment on a proposed Section 301 tariff action concerning China semiconductor imports.",
            "push",
            "trade_friction_escalation",
        ),
        (
            "China and the United States semiconductor trade talks collapse over export controls.",
            "push",
            "trade_friction_escalation",
        ),
        (
            "European Commission initiates an anti-subsidy investigation into battery electric vehicles from China.",
            "archive",
            "trade_distant_or_unproven",
        ),
        (
            "EU tariffs push Chinese carmakers to seek deeper ties in Europe.",
            "daily",
            "trade_friction_escalation",
        ),
        (
            "China and the United States withdraw semiconductor export controls.",
            "daily",
            "trade_deescalation",
        ),
    )
    for text, expected_action, expected_rule_id in cases:
        decisions = [
            evaluate_market_item(
                NormalizedMarketItem(
                    source=source,
                    source_category=category,
                    publisher_role=role,
                    content_type=content_type,
                    title=text,
                ),
                rule_config=config,
                portfolio=portfolios["empty"],
                source_policy=SourceAdmissionPolicy(),
            ).decision
            for source, category, role, content_type in source_variants
        ]
        assert all(decision is not None for decision in decisions), text
        assert {decision.action for decision in decisions if decision} == {expected_action}, text
        assert {
            hit["rule_id"]
            for decision in decisions
            if decision
            for hit in decision.rule_hits
        } == {expected_rule_id}, text

    for text in (
        "中国商务部与美国商会代表团会面，就中美经贸关系和企业合作交换意见。",
        "U.S. Commerce Department: Certain Aluminum Foil From the People's Republic of China: Preliminary Results of Antidumping Duty Administrative Review; 2024-2025.",
    ):
        evaluation = evaluate_market_item(
            NormalizedMarketItem(source="finance_media", title=text),
            rule_config=config,
            portfolio=portfolios["empty"],
            source_policy=SourceAdmissionPolicy(),
        )
        assert evaluation.admission.status == "excluded", text
        assert evaluation.decision is None, text


def test_migrated_macro_reactions_and_fed_transmission_are_source_neutral() -> None:
    _, config, portfolios = loaded_contracts()
    source_variants = (
        ("industry_media", "research_industry_media", "research_publisher", "article"),
        ("finance_media", "news_media", "news_media", "flash"),
    )
    cases = (
        ("美国CPI高于预期，2年期美债收益率大涨8个基点。", "push", "macro_surprise"),
        ("美国CPI与市场预期一致。", "daily", "macro_release_expected"),
        ("美国非农就业报告将于明晚公布。", "daily", "macro_release_preview"),
        ("美国ADP就业人数不及预期，但没有明显市场反应。", "daily", "macro_release_expected"),
        (
            "美国ADP就业人数不及预期。数据公布后，2年期美债收益率大跌8个基点。",
            "push",
            "macro_secondary_reaction",
        ),
        (
            "美联储降息将利好黄金、比特币和非美货币。",
            "daily",
            "generic_fed_policy_transmission",
        ),
        (
            "美联储宣布降息25个基点，降息将利好黄金。",
            "push",
            "fed_policy_material_exception",
        ),
        (
            "美联储按预期宣布降息25个基点，决议符合预期，降息将利好黄金。",
            "daily",
            "fed_policy_expected",
        ),
        (
            "交易员将美联储降息概率从40%上调至80%，降息将利好黄金。",
            "push",
            "fed_policy_material_exception",
        ),
        (
            "美联储降息预期利好比特币，比特币实际上涨3.2%。",
            "push",
            "fed_policy_material_exception",
        ),
        (
            "美联储主席沃什表示降息有利于经济，降息将利好黄金。",
            "push",
            "fed_policy_material_exception",
        ),
        (
            "美联储降息将利好黄金，同时黄金ETF流入创三个月新高。",
            "push",
            "fed_policy_material_exception",
        ),
    )
    for text, expected_action, expected_rule_id in cases:
        decisions = [
            evaluate_market_item(
                NormalizedMarketItem(
                    source=source,
                    source_category=category,
                    publisher_role=role,
                    content_type=content_type,
                    title=text,
                ),
                rule_config=config,
                portfolio=portfolios["empty"],
                source_policy=SourceAdmissionPolicy(),
            ).decision
            for source, category, role, content_type in source_variants
        ]
        assert all(decision is not None for decision in decisions), text
        assert {decision.action for decision in decisions if decision} == {expected_action}, text
        assert {
            hit["rule_id"]
            for decision in decisions
            if decision
            for hit in decision.rule_hits
        } == {expected_rule_id}, text

    for text in (
        "美国零售销售数据今晚公布。",
        "2年期美债收益率大跌8个基点，但报道没有说明具体原因。",
    ):
        evaluations = [
            evaluate_market_item(
                NormalizedMarketItem(
                    source=source,
                    source_category=category,
                    publisher_role=role,
                    content_type=content_type,
                    title=text,
                ),
                rule_config=config,
                portfolio=portfolios["empty"],
                source_policy=SourceAdmissionPolicy(),
            )
            for source, category, role, content_type in source_variants
        ]
        assert {evaluation.admission.status for evaluation in evaluations} == {"excluded"}, text
        assert all(evaluation.decision is None for evaluation in evaluations), text


def test_semiconductor_hard_variables_require_current_asserted_change() -> None:
    _, config, portfolios = loaded_contracts()
    cases = (
        (
            NormalizedMarketItem(
                source="finance_media",
                source_category="news_media",
                publisher_role="news_media",
                content_type="article",
                title="AI芯片产能规划",
                full_text="公司计划未来扩产AI芯片产能，但尚未批准或启动项目。",
            ),
            "daily",
        ),
        (
            NormalizedMarketItem(
                source="finance_media",
                source_category="news_media",
                publisher_role="news_media",
                content_type="article",
                title="AI芯片产业回顾",
                full_text="2025年公司完成AI芯片产能扩张，本文回顾当时的建设过程。",
                published_at="2026-07-20T09:00:00+08:00",
            ),
            "archive",
        ),
        (
            NormalizedMarketItem(
                source="finance_media",
                source_category="news_media",
                publisher_role="news_media",
                content_type="article",
                title="AI芯片公司回应扩产传闻",
                full_text="公司否认已经启动AI芯片扩产，目前尚无新增产能。",
            ),
            "archive",
        ),
        (
            NormalizedMarketItem(
                source="finance_media",
                source_category="news_media",
                publisher_role="news_media",
                content_type="article",
                title="AI芯片制造成本变化",
                full_text="能源价格上涨导致AI芯片生产成本增加，但产能和产量没有变化。",
            ),
            "archive",
        ),
    )
    for item, expected_action in cases:
        decision = evaluate_market_item(
            item,
            rule_config=config,
            portfolio=portfolios["empty"],
            source_policy=SourceAdmissionPolicy(),
        ).decision
        assert decision is not None and decision.action == expected_action, item.title


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


def test_new_core_has_only_the_report_only_production_importer() -> None:
    core_path = ROOT / "scripts" / "rule_core_v1.py"
    tree = ast.parse(core_path.read_text(encoding="utf-8"), filename=core_path.name)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported == {
        "__future__",
        "ai_compute_supply_demand",
        "ai_credit_risk",
        "hashlib",
        "international_bank_fed",
        "macro_policy",
        "re",
        "dataclasses",
        "trade_friction",
        "typing",
        "urllib",
        "market_item",
    }

    for classifier_name in (
        "ai_compute_supply_demand.py",
        "ai_credit_risk.py",
        "international_bank_fed.py",
        "macro_policy.py",
        "trade_friction.py",
    ):
        classifier_path = ROOT / "scripts" / classifier_name
        classifier_tree = ast.parse(
            classifier_path.read_text(encoding="utf-8"), filename=classifier_path.name
        )
        top_level_imports: set[str] = set()
        for node in classifier_tree.body:
            if isinstance(node, ast.Import):
                top_level_imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level_imports.add(node.module.split(".")[0])
        assert "rule_center" not in top_level_imports

    allowed = {
        "rule_core_v1.py",
        "rule_core_fixture.py",
        "rule_core_replay.py",
        "rule_core_history_replay.py",
        "rule_core_shadow.py",
        "rule_core_shadow_combined.py",
        "rule_core_shadow_report.py",
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
    assert production_importers == ["rule_core_runtime_shadow.py"]


def main() -> int:
    test_passive_admission_contract_rejects_invalid_combinations()
    test_rule_config_and_fixture_schemas_fail_closed()
    test_trusted_institution_domains_are_optional_and_non_authoritative()
    test_all_approved_v1_cases_are_executable()
    test_source_identity_and_llm_availability_cannot_change_core_result()
    test_holding_capital_change_uses_document_title_to_resolve_routine_attachments()
    test_approved_corporate_material_changes_are_source_neutral()
    test_migrated_holding_corporate_events_are_source_neutral()
    test_unexecuted_holding_corporate_events_remain_daily_across_sources()
    test_migrated_semiconductor_hard_variables_are_source_neutral()
    test_migrated_semiconductor_operating_changes_are_source_neutral()
    test_migrated_semiconductor_performance_and_market_size_are_source_neutral()
    test_trusted_research_is_audited_without_changing_semiconductor_action()
    test_migrated_ai_compute_and_credit_actions_are_source_neutral()
    test_migrated_fed_path_and_trade_actions_are_source_neutral()
    test_migrated_macro_reactions_and_fed_transmission_are_source_neutral()
    test_semiconductor_hard_variables_require_current_asserted_change()
    test_corporate_background_and_existing_relationships_remain_daily()
    test_materiality_evidence_is_bound_to_the_current_fact()
    test_new_core_has_only_the_report_only_production_importer()
    print("rule core v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
