"""Side-effect-free v1 admission and materiality rules.

This module is intentionally not wired into production collectors or runtime.
It consumes validated snapshots and returns passive market-item contracts only.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from market_item import (
    AdmissionEvidence,
    AdmissionResult,
    DecisionResult,
    EvidenceScope,
    NormalizedMarketItem,
    RuleEvaluation,
    RuleFamily,
)


CONTRACT_VERSION = "rule-core-v1"
CONFIG_SCHEMA_VERSION = "rule-config-v1"
FAMILY_ORDER: tuple[RuleFamily, ...] = (
    "holding",
    "semiconductor_ai",
    "macro_data",
    "fed_policy",
    "trade_policy",
)
ACTION_RANK = {"ignore": 0, "archive": 1, "daily": 2, "push": 3}


class RuleConfigError(ValueError):
    pass


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _tuple_strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RuleConfigError(f"{field} must be a list")
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        text = _clean(raw)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return tuple(result)


def _mapping(value: object, field: str, expected: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise RuleConfigError(f"{field} must be an object")
    unknown = set(value) - expected
    missing = expected - set(value)
    if unknown or missing:
        raise RuleConfigError(f"{field} keys invalid: missing={sorted(missing)} unknown={sorted(unknown)}")
    return value


@dataclass(frozen=True)
class RuleConfig:
    config_version: str
    semiconductor_ai_keywords: tuple[str, ...]
    exclude_keywords: tuple[str, ...]
    macro_indicators: tuple[str, ...]
    macro_primary_indicators: tuple[str, ...]
    macro_secondary_indicators: tuple[str, ...]
    macro_context_aliases: tuple[str, ...]
    fed_event_aliases: tuple[str, ...]
    fed_actor_aliases: tuple[str, ...]
    fed_path_aliases: tuple[str, ...]
    trusted_institutions: tuple[str, ...]
    trusted_domains: tuple[str, ...]
    trade_corridors: tuple[str, ...]
    trade_instruments: tuple[str, ...]
    trade_stages: tuple[str, ...]
    trade_focus_industries: tuple[str, ...]


def parse_rule_config(payload: Mapping[str, Any]) -> RuleConfig:
    expected = {
        "schema_version",
        "config_version",
        "semiconductor_ai_keywords",
        "exclude_keywords",
        "macro_data",
        "fed_policy",
        "trusted_attribution",
        "trade_policy",
    }
    unknown = set(payload) - expected
    missing = expected - set(payload)
    if unknown or missing:
        raise RuleConfigError(f"rule config keys invalid: missing={sorted(missing)} unknown={sorted(unknown)}")
    if payload.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise RuleConfigError(f"unsupported rule config schema: {payload.get('schema_version')}")
    config_version = _clean(payload.get("config_version"))
    if not config_version:
        raise RuleConfigError("config_version is required")
    macro = _mapping(
        payload.get("macro_data"),
        "macro_data",
        {"indicators", "context_aliases", "tiers"},
    )
    tiers = _mapping(macro.get("tiers"), "macro_data.tiers", {"primary", "secondary"})
    fed = _mapping(
        payload.get("fed_policy"),
        "fed_policy",
        {"event_aliases", "actor_aliases", "path_aliases"},
    )
    trusted = _mapping(
        payload.get("trusted_attribution"),
        "trusted_attribution",
        {"institutions", "domains"},
    )
    trade = _mapping(
        payload.get("trade_policy"),
        "trade_policy",
        {"corridors", "instruments", "stages", "focus_industries"},
    )
    primary = _tuple_strings(tiers.get("primary"), "macro_data.tiers.primary")
    secondary = _tuple_strings(tiers.get("secondary"), "macro_data.tiers.secondary")
    indicators = _tuple_strings(macro.get("indicators"), "macro_data.indicators")
    if not set(primary + secondary).issubset(set(indicators)):
        raise RuleConfigError("macro tiers must reference configured indicators")
    config = RuleConfig(
        config_version=config_version,
        semiconductor_ai_keywords=_tuple_strings(
            payload.get("semiconductor_ai_keywords"), "semiconductor_ai_keywords"
        ),
        exclude_keywords=_tuple_strings(payload.get("exclude_keywords"), "exclude_keywords"),
        macro_indicators=indicators,
        macro_primary_indicators=primary,
        macro_secondary_indicators=secondary,
        macro_context_aliases=_tuple_strings(macro.get("context_aliases"), "macro_data.context_aliases"),
        fed_event_aliases=_tuple_strings(fed.get("event_aliases"), "fed_policy.event_aliases"),
        fed_actor_aliases=_tuple_strings(fed.get("actor_aliases"), "fed_policy.actor_aliases"),
        fed_path_aliases=_tuple_strings(fed.get("path_aliases"), "fed_policy.path_aliases"),
        trusted_institutions=_tuple_strings(
            trusted.get("institutions"), "trusted_attribution.institutions"
        ),
        trusted_domains=_tuple_strings(trusted.get("domains"), "trusted_attribution.domains"),
        trade_corridors=_tuple_strings(trade.get("corridors"), "trade_policy.corridors"),
        trade_instruments=_tuple_strings(trade.get("instruments"), "trade_policy.instruments"),
        trade_stages=_tuple_strings(trade.get("stages"), "trade_policy.stages"),
        trade_focus_industries=_tuple_strings(
            trade.get("focus_industries"), "trade_policy.focus_industries"
        ),
    )
    if not config.semiconductor_ai_keywords or not config.macro_indicators or not config.trade_corridors:
        raise RuleConfigError("required rule lists cannot be empty")
    return config


@dataclass(frozen=True)
class HoldingRule:
    symbol: str
    names: tuple[str, ...]
    related_news_keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    immediate_alert_keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class PortfolioRuleConfig:
    holdings: tuple[HoldingRule, ...] = ()


def parse_portfolio_config(payload: object) -> PortfolioRuleConfig:
    if not isinstance(payload, list):
        raise RuleConfigError("portfolio fixture must be a list")
    holdings: list[HoldingRule] = []
    for index, raw in enumerate(payload):
        if not isinstance(raw, dict):
            raise RuleConfigError(f"portfolio[{index}] must be an object")
        expected = {
            "symbol",
            "names",
            "related_news_keywords",
            "exclude_keywords",
            "immediate_alert_keywords",
        }
        unknown = set(raw) - expected
        missing = {"symbol", "names"} - set(raw)
        if unknown or missing:
            raise RuleConfigError(
                f"portfolio[{index}] keys invalid: missing={sorted(missing)} unknown={sorted(unknown)}"
            )
        symbol = _clean(raw.get("symbol"))
        names = _tuple_strings(raw.get("names"), f"portfolio[{index}].names")
        if not symbol or not names:
            raise RuleConfigError(f"portfolio[{index}] requires symbol and names")
        holdings.append(
            HoldingRule(
                symbol=symbol,
                names=names,
                related_news_keywords=_tuple_strings(
                    raw.get("related_news_keywords", []),
                    f"portfolio[{index}].related_news_keywords",
                ),
                exclude_keywords=_tuple_strings(
                    raw.get("exclude_keywords", []), f"portfolio[{index}].exclude_keywords"
                ),
                immediate_alert_keywords=_tuple_strings(
                    raw.get("immediate_alert_keywords", []),
                    f"portfolio[{index}].immediate_alert_keywords",
                ),
            )
        )
    return PortfolioRuleConfig(tuple(holdings))


@dataclass(frozen=True)
class SourceAdmissionPolicy:
    direct_admission_families: tuple[RuleFamily, ...] = ()

    def __post_init__(self) -> None:
        invalid = set(self.direct_admission_families) - {"trade_policy"}
        if invalid:
            raise RuleConfigError(f"unsupported direct-admission families: {sorted(invalid)}")
        if len(set(self.direct_admission_families)) != len(self.direct_admission_families):
            raise RuleConfigError("direct-admission families cannot contain duplicates")


def _contains(text: str, term: str) -> bool:
    normalized = term.casefold().strip()
    lowered = text.casefold()
    if not normalized:
        return False
    if re.fullmatch(r"[a-z0-9_.+-]+", normalized):
        return re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", lowered) is not None
    return normalized in lowered


def _matches(text: str, terms: Iterable[str]) -> tuple[str, ...]:
    return tuple(term for term in terms if _contains(text, term))


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[。！？!?；;])|(?<=\.)\s+|\n+", text) if part.strip()]


def _quote(text: str, terms: Iterable[str]) -> str:
    wanted = tuple(terms)
    for sentence in _sentences(text):
        if any(_contains(sentence, term) for term in wanted):
            return sentence[:500]
    return text[:500]


def _evidence(
    family: EvidenceScope,
    reason_code: str,
    text: str,
    terms: Iterable[str],
    *,
    subjects: Iterable[str] = (),
    relation: str = "",
) -> AdmissionEvidence:
    matched = tuple(dict.fromkeys(terms))
    return AdmissionEvidence(
        rule_family=family,
        reason_code=reason_code,
        evidence_quote=_quote(text, matched),
        matched_subjects=tuple(dict.fromkeys(subjects)),
        matched_term_ids=tuple(
            f"term:{hashlib.sha256(term.casefold().encode('utf-8')).hexdigest()[:12]}"
            for term in matched
        ),
        relation=relation,
    )


def _holding_evidence(
    item: NormalizedMarketItem, text: str, portfolio: PortfolioRuleConfig
) -> list[AdmissionEvidence]:
    evidence: list[AdmissionEvidence] = []
    item_symbols = set(item.symbols)
    for holding in portfolio.holdings:
        direct_terms = _matches(text, holding.names)
        symbol_match = holding.symbol in item_symbols or _contains(text, holding.symbol)
        if direct_terms or symbol_match:
            evidence.append(
                _evidence(
                    "holding",
                    "holding_direct_identity",
                    text,
                    direct_terms or (holding.symbol,),
                    subjects=holding.names[:1],
                    relation="direct",
                )
            )
            continue
        if _matches(text, holding.exclude_keywords):
            continue
        related = _matches(text, holding.related_news_keywords)
        if related:
            evidence.append(
                _evidence(
                    "holding",
                    "holding_related_keyword",
                    text,
                    related,
                    subjects=holding.names[:1],
                    relation="configured_related",
                )
            )
    return evidence


def admit_market_item(
    item: NormalizedMarketItem,
    *,
    rule_config: RuleConfig,
    portfolio: PortfolioRuleConfig,
    source_policy: SourceAdmissionPolicy,
) -> AdmissionResult:
    text = item.text_for_rules
    if not text:
        return AdmissionResult(
            status="excluded",
            reason_code="empty_rule_text",
            matched_families=(),
            evidence=(),
            config_version=rule_config.config_version,
        )
    evidence = _holding_evidence(item, text, portfolio)
    direct_holding = any(item.reason_code == "holding_direct_identity" for item in evidence)
    excluded_terms = _matches(text, rule_config.exclude_keywords)

    semi = _matches(text, rule_config.semiconductor_ai_keywords)
    if semi:
        evidence.append(_evidence("semiconductor_ai", "semiconductor_ai_scope", text, semi))

    indicators = _matches(text, rule_config.macro_indicators)
    macro_context = _matches(text, rule_config.macro_context_aliases)
    if indicators and macro_context:
        evidence.append(
            _evidence("macro_data", "macro_data_scope", text, (*indicators, *macro_context))
        )

    fed_terms = _matches(
        text,
        (*rule_config.fed_event_aliases, *rule_config.fed_actor_aliases, *rule_config.fed_path_aliases),
    )
    if fed_terms:
        evidence.append(_evidence("fed_policy", "fed_policy_scope", text, fed_terms))

    corridor = _matches(text, rule_config.trade_corridors)
    trade_action = _matches(text, (*rule_config.trade_instruments, *rule_config.trade_stages))
    if "trade_policy" in source_policy.direct_admission_families:
        evidence.append(
            _evidence(
                "trade_policy",
                "trade_policy_direct_scope",
                text,
                trade_action or corridor or ("direct_trade_surface",),
            )
        )
    elif corridor and trade_action:
        evidence.append(
            _evidence("trade_policy", "trade_policy_scope", text, (*corridor, *trade_action))
        )

    if excluded_terms and not direct_holding:
        return AdmissionResult(
            status="excluded",
            reason_code="global_exclude",
            matched_families=(),
            evidence=(_evidence("global", "global_exclude", text, excluded_terms),),
            config_version=rule_config.config_version,
        )
    if not evidence:
        return AdmissionResult(
            status="excluded",
            reason_code="out_of_scope",
            matched_families=(),
            evidence=(),
            config_version=rule_config.config_version,
        )
    by_family: dict[RuleFamily, list[AdmissionEvidence]] = {}
    for item_evidence in evidence:
        family = item_evidence.rule_family
        if family == "global":
            continue
        by_family.setdefault(family, []).append(item_evidence)
    families = tuple(family for family in FAMILY_ORDER if family in by_family)
    ordered_evidence = tuple(
        item_evidence
        for family in families
        for item_evidence in by_family[family]
    )
    return AdmissionResult(
        status="admitted",
        reason_code="content_scope_match",
        matched_families=families,
        evidence=ordered_evidence,
        config_version=rule_config.config_version,
    )


def _has(text: str, *terms: str) -> bool:
    return any(_contains(text, term) for term in terms)


def _all_groups(text: str, *groups: tuple[str, ...]) -> bool:
    return all(_has(text, *group) for group in groups)


def _candidate(family: RuleFamily, rule_id: str, action: str, quote: str, reason: str) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "rule_family": family,
        "decision_action": action,
        "evidence_quote": quote[:500],
        "reason": reason,
    }


def _holding_candidate(
    text: str, admission: AdmissionResult, portfolio: PortfolioRuleConfig
) -> dict[str, Any]:
    direct = any(
        item.rule_family == "holding" and item.reason_code == "holding_direct_identity"
        for item in admission.evidence
    )
    immediate = tuple(term for holding in portfolio.holdings for term in holding.immediate_alert_keywords)
    if _matches(text, immediate):
        return _candidate("holding", "holding_immediate_alert", "push", text, "命中显式即时提醒关键词。")
    if _has(text, "股东会通知", "会议通知", "审计报告", "审计附件"):
        return _candidate("holding", "holding_ordinary", "archive", text, "例行会议或审计附件不构成实质变化。")
    if _has(text, "增资", "减资", "capital increase", "capital reduction"):
        return _candidate("holding", "holding_material_event", "push", text, "持仓公司发生实质增资或减资。")
    if _all_groups(
        text,
        ("上调", "下调", "upgrade", "downgrade", "raises", "cuts"),
        ("评级", "目标价", "rating", "target price"),
    ):
        return _candidate("holding", "holding_rating_revision", "push", text, "评级或估值锚明确修订。")
    if _has(text, "维持评级", "维持目标价", "reiterates", "maintains rating") or _all_groups(
        text, ("维持", "maintains"), ("评级", "目标价", "rating", "target price")
    ):
        return _candidate("holding", "holding_ordinary", "daily", text, "相关评级维持不变。")
    if direct:
        return _candidate("holding", "holding_ordinary", "daily", text, "直接持仓普通内容默认进入日报。")
    return _candidate("holding", "holding_ordinary", "archive", text, "关联内容未形成实质变化。")


def _semiconductor_candidate(text: str) -> dict[str, Any]:
    if _has(text, "教程", "经验分享", "leaderboard", "workflow integration", "工具用法"):
        return _candidate(
            "semiconductor_ai", "semiconductor_ordinary", "archive", text, "教程或工具内容不是产业实质变化。"
        )
    non_execution = _has(
        text,
        "尚未进入供货",
        "尚未供货",
        "尚无订单",
        "没有订单",
        "尚未量产",
        "尚无量产",
        "未进入执行",
        "not yet supplying",
        "no binding order",
    )
    execution = _has(text, "开始供货", "已供货", "量产", "客户认证", "执行阶段", "binding")
    if _has(text, "框架协议", "意向合作", "战略合作", "non-binding", "framework agreement") and (
        non_execution or not execution
    ):
        return _candidate(
            "semiconductor_ai", "semiconductor_ordinary", "archive", text, "非约束性合作未进入执行阶段。"
        )
    if _all_groups(
        text,
        ("正式发布", "正式推出", "announces", "launches"),
        ("新平台", "新一代", "generation", "platform"),
        ("可用", "量产", "路线", "availability", "production", "roadmap"),
    ):
        return _candidate(
            "semiconductor_ai", "semiconductor_material_change", "push", text, "正式平台代际及可用性或路线发生变化。"
        )
    if _all_groups(
        text,
        ("短缺", "供不应求", "shortage", "supply tight"),
        ("长期合同", "全部签约", "排队", "延期", "限流", "long-term contract", "fully booked"),
    ):
        return _candidate(
            "semiconductor_ai", "ai_compute_constraint", "push", text, "供需短缺产生约束性合同或运营后果。"
        )
    if _has(text, "上调预测", "下调预测", "上修指引", "下修指引", "raises forecast", "cuts forecast") or _all_groups(
        text,
        ("上调", "下调", "上修", "下修", "raises", "cuts"),
        ("预测", "指引", "forecast", "guidance"),
    ):
        return _candidate(
            "semiconductor_ai", "industry_forecast_revision", "push", text, "产业预测或指引发生明确修订。"
        )
    if _all_groups(
        text,
        ("新签订单", "订单", "供货", "new order", "supply agreement"),
        ("ai", "算力", "gpu", "hbm", "芯片"),
    ) and not _has(text, "意向", "框架") and not non_execution:
        return _candidate(
            "semiconductor_ai", "semiconductor_material_change", "push", text, "订单或供货关系进入执行阶段。"
        )
    if _all_groups(
        text,
        ("低成本", "成本路线", "cost route", "price war"),
        ("算力需求", "资本开支", "采购", "compute demand", "capex", "procurement"),
    ):
        return _candidate(
            "semiconductor_ai", "semiconductor_material_change", "push", text, "成本路线明确改变需求或资本开支方向。"
        )
    if _all_groups(
        text,
        ("巨额亏损", "信用压力", "cds", "credit stress", "losses"),
        ("采购承诺", "采购约束", "融资约束", "purchase commitment", "procurement constraint"),
    ):
        return _candidate(
            "semiconductor_ai", "ai_credit_constraint", "push", text, "信用压力与采购约束在同一主体局部绑定。"
        )
    if _all_groups(
        text,
        ("推迟", "延后", "路线变化", "delay", "roadmap shift"),
        ("cpo", "gpu", "hbm", "芯片", "量产"),
    ):
        return _candidate(
            "semiconductor_ai", "semiconductor_material_change", "push", text, "产业时间表或技术路线明确变化。"
        )
    if _all_groups(
        text,
        ("轮动", "增配", "减配", "rotate", "rotation"),
        ("芯片", "半导体", "ai", "云服务"),
    ):
        return _candidate(
            "semiconductor_ai", "semiconductor_material_change", "push", text, "产业配置发生明确跨主题轮动。"
        )
    if _has(text, "泛谈", "长期看好", "基金经理观点", "generic outlook"):
        return _candidate(
            "semiconductor_ai", "semiconductor_ordinary", "archive", text, "泛行业观点没有实质变化。"
        )
    if _has(text, "面临阻碍", "方案发布", "技术方案", "预计增长", "outlook"):
        return _candidate(
            "semiconductor_ai", "semiconductor_ordinary", "daily", text, "相关但现有证据未达到实质变化。"
        )
    return _candidate(
        "semiconductor_ai", "semiconductor_ordinary", "archive", text, "产业相关但未命中 v1 实质变化组合。"
    )


def _macro_candidate(text: str, config: RuleConfig) -> dict[str, Any]:
    if _has(text, "综述", "回顾", "仅转述", "roundup"):
        return _candidate("macro_data", "macro_indirect_summary", "archive", text, "二次综述不是数据发布。")
    indicator_matches = _matches(text, config.macro_indicators)
    primary = any(term in config.macro_primary_indicators for term in indicator_matches)
    surprise = _has(
        text,
        "高于预期",
        "低于预期",
        "超预期",
        "不及预期",
        "意外上行",
        "意外下降",
        "above expectations",
        "below expectations",
        "unexpected",
    )
    expected = _has(text, "符合预期", "与预期一致", "in line with expectations")
    reaction = _has(
        text,
        "汇市反应",
        "美元大涨",
        "美元大跌",
        "美债收益率大涨",
        "美债收益率大跌",
        "market repricing",
    )
    if expected:
        return _candidate("macro_data", "macro_release_expected", "daily", text, "数据符合预期。")
    if surprise and (primary or reaction):
        rule_id = "macro_surprise" if primary else "macro_secondary_reaction"
        return _candidate("macro_data", rule_id, "push", text, "偏离方向明确并达到 v1 反应条件。")
    return _candidate("macro_data", "macro_release_expected", "daily", text, "数据相关但未形成可推送偏离。")


def _fed_candidate(text: str) -> dict[str, Any]:
    if _has(text, "未说明相对此前", "未证明修订", "没有路径修订", "without a revision"):
        return _candidate("fed_policy", "fed_path_unchanged", "daily", text, "只有当前预测，无法证明路径修订。")
    if _all_groups(
        text,
        ("上调", "下调", "改为", "修订", "raises", "cuts", "revises"),
        ("降息", "加息", "终端利率", "利率路径", "rate path", "terminal rate"),
    ):
        return _candidate("fed_policy", "fed_path_change", "push", text, "利率路径发生明确修订。")
    if _has(text, "维持预测", "重申", "符合预期", "unchanged", "reiterates"):
        return _candidate("fed_policy", "fed_path_unchanged", "daily", text, "既有立场或路径没有变化。")
    if _has(text, "偏鹰", "偏鸽", "强调通胀", "hawkish", "dovish"):
        return _candidate(
            "fed_policy", "fed_official_stance_change", "daily", text, "无法核验相对既有立场发生变化。"
        )
    if _has(text, "会面", "称赞", "工作组"):
        return _candidate("fed_policy", "fed_policy_non_material", "archive", text, "没有政策路径证据。")
    return _candidate("fed_policy", "fed_path_unchanged", "daily", text, "Fed 内容未证明路径变化。")


def _trade_candidate(text: str, config: RuleConfig) -> dict[str, Any]:
    if _has(text, "终止", "撤销", "豁免", "缓和", "terminate", "withdraw", "exemption"):
        return _candidate("trade_policy", "trade_deescalation", "daily", text, "政策发生有效缓和或撤销。")
    focus = _matches(text, config.trade_focus_industries)
    escalation = _matches(text, config.trade_stages)
    if focus and escalation:
        return _candidate("trade_policy", "trade_escalation", "push", text, "关注产业贸易措施进入正式升级阶段。")
    return _candidate("trade_policy", "trade_distant_or_unproven", "archive", text, "贸易行动距离关注产业较远。")


def decide_admitted_item(
    item: NormalizedMarketItem,
    admission: AdmissionResult,
    *,
    rule_config: RuleConfig,
    portfolio: PortfolioRuleConfig,
) -> DecisionResult:
    if admission.status != "admitted":
        raise ValueError("decision requires an admitted AdmissionResult")
    if admission.config_version != rule_config.config_version:
        raise ValueError("admission/config version mismatch")
    text = item.text_for_rules
    candidates: list[dict[str, Any]] = []
    for family in admission.matched_families:
        if family == "holding":
            candidates.append(_holding_candidate(text, admission, portfolio))
        elif family == "semiconductor_ai":
            candidates.append(_semiconductor_candidate(text))
        elif family == "macro_data":
            candidates.append(_macro_candidate(text, rule_config))
        elif family == "fed_policy":
            candidates.append(_fed_candidate(text))
        elif family == "trade_policy":
            candidates.append(_trade_candidate(text, rule_config))
    winning_action = max((str(item["decision_action"]) for item in candidates), key=ACTION_RANK.__getitem__)
    winners = [item for item in candidates if item["decision_action"] == winning_action]
    importance = {"push": "high", "daily": "medium", "archive": "low", "ignore": "low"}[winning_action]
    reason = " ".join(str(item["reason"]) for item in winners)
    return DecisionResult(
        action=winning_action,
        importance=importance,
        reason=reason,
        brief_reason=reason,
        rule_hits=candidates,
        candidate_rules=[item for item in candidates if item not in winners],
        audit_json={
            "rule_contract_version": CONTRACT_VERSION,
            "config_version": rule_config.config_version,
            "admission": admission.to_dict(),
            "source_metadata_not_used_for_materiality": True,
        },
    )


def evaluate_market_item(
    item: NormalizedMarketItem,
    *,
    rule_config: RuleConfig,
    portfolio: PortfolioRuleConfig,
    source_policy: SourceAdmissionPolicy,
) -> RuleEvaluation:
    admission = admit_market_item(
        item,
        rule_config=rule_config,
        portfolio=portfolio,
        source_policy=source_policy,
    )
    decision = (
        decide_admitted_item(
            item,
            admission,
            rule_config=rule_config,
            portfolio=portfolio,
        )
        if admission.status == "admitted"
        else None
    )
    return RuleEvaluation(admission=admission, decision=decision)
