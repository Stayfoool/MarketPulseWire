"""Side-effect-free v1 admission and materiality rules.

This module is intentionally not wired into production collectors or runtime.
It consumes validated snapshots and returns passive market-item contracts only.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from ai_compute_supply_demand import classify_ai_compute_supply_demand
from ai_credit_risk import classify_ai_credit_risk
from international_bank_fed import allowed_fed_path_banks, classify_international_bank_fed_path
from investment_bank_research import extract_allocation_claims, extract_rating_claims
from macro_policy import classify_macro_policy_content, generic_fed_transmission_classification
from market_item import (
    AdmissionEvidence,
    AdmissionResult,
    DecisionResult,
    EvidenceScope,
    NormalizedMarketItem,
    RuleEvaluation,
    RuleFamily,
)
from trade_friction import classify_trade_friction


CONTRACT_VERSION = "rule-core-v1"
CONFIG_SCHEMA_VERSION = "rule-config-v1"
RULE_CORE_VERSION = "rule-core-v1-20260721-5d701b1"
FAMILY_ORDER: tuple[RuleFamily, ...] = (
    "holding",
    "semiconductor_ai",
    "macro_data",
    "fed_policy",
    "trade_policy",
)
ACTION_RANK = {"ignore": 0, "archive": 1, "daily": 2, "push": 3}

SEMICONDUCTOR_CAPACITY_TERMS = (
    "capacity",
    "output",
    "fab",
    "fabs",
    "factory",
    "factories",
    "plant",
    "plants",
    "manufacturing site",
    "manufacturing facility",
    "production line",
    "产能",
    "产量",
    "工厂",
    "厂区",
    "厂址",
    "产线",
)
SEMICONDUCTOR_CAPACITY_DIRECT_TERMS = (
    "capacity expansion",
    "capacity cut",
    "production ramp",
    "mass production",
    "output increase",
    "output cut",
    "production increase",
    "production increased",
    "increase production",
    "increased production",
    "reduce production",
    "reduced production",
    "cut production",
    "扩产",
    "产能扩张",
    "新增产能",
    "产能爬坡",
    "产量提升",
    "增加产量",
    "减产",
    "停产",
    "复产",
    "投产",
    "量产",
)
SEMICONDUCTOR_CAPACITY_CHANGE_TERMS = (
    "expand",
    "expanded",
    "expansion",
    "ramp",
    "ramp-up",
    "ramp up",
    "relaunch",
    "relaunched",
    "restart",
    "restarted",
    "reopen",
    "reopened",
    "increase",
    "increased",
    "double",
    "doubled",
    "add capacity",
    "added capacity",
    "cut capacity",
    "reduce output",
    "acquires",
    "acquired",
    "acquisition",
    "重启",
    "收购厂址",
)
SEMICONDUCTOR_CAPEX_TERMS = (
    "capital expenditure",
    "capex",
    "fab investment",
    "equipment investment",
    "factory investment",
    "facility investment",
    "investment in capacity",
    "investment in production",
    "investment in manufacturing",
    "资本开支",
    "设备投资",
    "工厂投资",
    "产能投资",
    "产线投资",
    "厂区投资",
)
SEMICONDUCTOR_CAPEX_CHANGE_TERMS = (
    "accelerating",
    "increase",
    "increased",
    "raise",
    "raised",
    "additional",
    "approved",
    "acquires",
    "acquired",
    "completed",
    "cut",
    "reduced",
    "追加",
    "新增",
    "增加",
    "加速",
    "批准",
    "审议通过",
    "完成",
    "削减",
    "下调",
)
SEMICONDUCTOR_PROCUREMENT_DIRECT_TERMS = (
    "equipment procurement",
    "equipment order",
    "equipment orders",
    "placed order",
    "placed orders",
    "secured order",
    "secured orders",
    "signed order",
    "signed orders",
    "purchase order",
    "purchase orders",
    "supply agreement",
    "order backlog",
    "设备采购",
    "设备订单",
    "采购订单",
    "供货协议",
    "订单积压",
    "中标",
    "定点",
    "客户认证",
)
SEMICONDUCTOR_PROCUREMENT_PATTERNS = (
    r"(?:procure(?:ment|d)?|purchas(?:e|ed|ing)|order(?:ed|s)?).{0,32}(?:equipment|tools?|systems?|machines?)",
    r"(?:equipment|tools?|systems?|machines?).{0,32}(?:procure(?:ment|d)?|purchas(?:e|ed|ing)|order(?:ed|s)?)",
    r"(?:采购|订购|下单).{0,24}(?:设备|装备|系统|机台|工具)",
    r"(?:设备|装备|系统|机台|工具).{0,24}(?:采购|订购|下单|订单)",
)
SEMICONDUCTOR_NON_EXECUTION_TERMS = (
    "尚未",
    "尚无",
    "没有",
    "未进入",
    "未形成",
    "未执行",
    "未披露",
    "否认",
    "not yet",
    "no order",
    "no binding",
    "has not",
    "have not",
    "not disclosed",
    "deny",
    "denies",
    "denied",
)
SEMICONDUCTOR_PLANNING_TERMS = (
    "计划",
    "拟建",
    "拟投",
    "拟扩",
    "拟采购",
    "拟增加",
    "预计",
    "有望",
    "可能",
    "意向",
    "目标",
    "plans to",
    "plan to",
    "planned",
    "to expand",
    "to increase",
    "to build",
    "will expand",
    "will increase",
    "will build",
    "expects to",
    "expected to",
    "expected",
    "may",
    "could",
    "intends to",
    "aims to",
    "outlook",
    "forecast",
    "considering",
    "exploring",
    "proposed",
    "proposal",
    "draft",
    "拟议",
    "提议",
    "草案",
    "征求意见",
)
SEMICONDUCTOR_SPECULATIVE_TERMS = (
    "计划",
    "拟建",
    "拟投",
    "拟扩",
    "拟采购",
    "拟增加",
    "预计",
    "有望",
    "可能",
    "意向",
    "plans to",
    "plan to",
    "planned",
    "expects to",
    "expected to",
    "may",
    "could",
    "intends to",
    "aims to",
    "considering",
    "exploring",
    "proposed",
    "proposal",
    "draft",
    "拟议",
    "提议",
    "草案",
    "征求意见",
)
SEMICONDUCTOR_EXECUTION_TERMS = (
    "已",
    "已经",
    "审议通过",
    "董事会批准",
    "批准了",
    "批准该",
    "批准项目",
    "approved the",
    "board approved",
    "has approved",
    "have approved",
    "has signed",
    "have signed",
    "has started",
    "have started",
    "has completed",
    "have completed",
    "has acquired",
    "have acquired",
    "has expanded",
    "have expanded",
    "has relaunched",
    "have relaunched",
    "has restarted",
    "have restarted",
)
SEMICONDUCTOR_COMMERCIAL_DEVELOPMENT_TERMS = (
    "commercialization",
    "commercialisation",
    "commercial deployment",
    "commercial rollout",
    "商业化",
    "商业落地",
    "规模化应用",
    "规模化部署",
)
SEMICONDUCTOR_VALUATION_TERMS = ("valuation", "valued at", "估值")
SEMICONDUCTOR_VALUATION_CHANGE_TERMS = (
    "tops",
    "reaches",
    "reached",
    "valued at",
    "raises",
    "raised",
    "funding round",
    "融资",
    "募资",
    "达到",
    "超过",
    "突破",
    "完成",
)
SEMICONDUCTOR_SHIPMENT_EXECUTION_TERMS = (
    "started shipments",
    "began shipments",
    "commenced shipments",
    "shipping has started",
    "started delivery",
    "began delivery",
    "开始出货",
    "正式出货",
    "已出货",
    "开始交付",
    "正式交付",
    "已交付",
)
SEMICONDUCTOR_SHIPMENT_TERMS = (
    "shipment forecast",
    "shipment guidance",
    "delivery cycle",
    "delivery time",
    "出货指引",
    "出货预测",
    "交付周期",
    "交付时间",
)
SEMICONDUCTOR_SHIPMENT_CHANGE_TERMS = (
    "raises",
    "raised",
    "cuts",
    "cut",
    "revised",
    "shortened",
    "extended",
    "increased",
    "decreased",
    "上调",
    "下调",
    "上修",
    "下修",
    "缩短",
    "延长",
    "增加",
    "减少",
)
SEMICONDUCTOR_DEMAND_CHANGE_TERMS = (
    "demand surge",
    "demand surged",
    "demand jumped",
    "demand decline",
    "demand contraction",
    "volume contraction",
    "需求激增",
    "需求大增",
    "需求大幅增长",
    "需求下滑",
    "需求下降",
    "需求收缩",
    "销量收缩",
)
SEMICONDUCTOR_REGULATION_TERMS = (
    "export control",
    "trade restriction",
    "sanction",
    "sanctions",
    "export ban",
    "import ban",
    "出口管制",
    "贸易限制",
    "制裁",
    "出口禁令",
    "进口禁令",
)
SEMICONDUCTOR_REGULATION_EXECUTION_TERMS = (
    "announced",
    "imposed",
    "implemented",
    "effective",
    "takes effect",
    "tightened",
    "expanded",
    "正式生效",
    "生效",
    "实施",
    "宣布",
    "出台",
    "发布",
    "收紧",
    "扩大",
    "新增",
)
SEMICONDUCTOR_REGULATION_EASING_TERMS = (
    "tariff exception",
    "tariff exemption",
    "exemption",
    "lifted",
    "withdrawn",
    "relaxed",
    "关税例外",
    "关税豁免",
    "豁免",
    "撤销",
    "取消",
    "放宽",
)
SEMICONDUCTOR_SAMPLING_EXECUTION_TERMS = (
    "started sampling",
    "began sampling",
    "sent samples",
    "sample delivery started",
    "passed qualification",
    "completed qualification",
    "received certification",
    "obtained certification",
    "开始送样",
    "已送样",
    "完成送样",
    "样品已交付",
    "通过客户认证",
    "完成客户认证",
    "获得客户认证",
    "取得认证",
)
SEMICONDUCTOR_OPERATING_NON_EXECUTION_TERMS = (
    "否认",
    "并未",
    "尚未出货",
    "未开始出货",
    "没有出货",
    "尚未交付",
    "未开始交付",
    "没有交付",
    "尚未送样",
    "未开始送样",
    "没有送样",
    "尚未通过认证",
    "未通过认证",
    "认证尚未完成",
    "尚未生效",
    "未生效",
    "denies",
    "denied",
    "has not started shipments",
    "have not started shipments",
    "not shipping",
    "not delivered",
    "not yet sampling",
    "not qualified",
    "not yet effective",
)

CORPORATE_PLANNING_OR_UNCONFIRMED_TERMS = (
    "计划",
    "拟建",
    "拟投",
    "拟扩",
    "拟采购",
    "拟增加",
    "拟收购",
    "拟出售",
    "拟签署",
    "拟签订",
    "拟上调",
    "拟下调",
    "拟实施",
    "拟处罚",
    "传闻",
    "据传",
    "可能",
    "预计",
    "征求意见",
    "待批准",
    "等待批准",
    "尚待",
    "plan to",
    "plans to",
    "planned",
    "proposed",
    "proposal",
    "intend",
    "intends",
    "rumor",
    "rumour",
    "reportedly",
    "may",
    "could",
    "pending",
)
CORPORATE_NON_EXECUTION_TERMS = (
    "尚未",
    "尚无",
    "没有",
    "未签署",
    "未签订",
    "未中标",
    "未获得",
    "未批准",
    "未生效",
    "未实施",
    "未执行",
    "未完成",
    "未披露",
    "否认",
    "不涉及",
    "意向",
    "框架",
    "框架协议",
    "战略合作",
    "事先告知书",
    "not yet",
    "has not",
    "have not",
    "not signed",
    "not awarded",
    "not approved",
    "not effective",
    "not implemented",
    "not completed",
    "not disclosed",
    "denies",
    "denied",
    "non-binding",
    "framework agreement",
    "letter of intent",
    "preliminary notice",
)
CORPORATE_HISTORICAL_TERMS = (
    "背景资料",
    "历史资料",
    "此前曾",
    "过去曾",
    "回顾",
    "已有",
    "现有",
    "一直",
    "长期以来",
    "historically",
    "previously",
    "in the past",
    "last year",
    "existing",
    "long-standing",
)
CORPORATE_FORMAL_STAGE_TERMS = (
    "已经",
    "已",
    "正式",
    "审议通过",
    "表决通过",
    "董事会批准",
    "批准了",
    "决定",
    "生效",
    "实施",
    "执行",
    "完成",
    "签署",
    "签订",
    "获得",
    "取得",
    "中标",
    "定点",
    "announced",
    "approved",
    "effective",
    "implemented",
    "executed",
    "completed",
    "signed",
    "secured",
    "awarded",
    "won",
    "entered into",
)
CORPORATE_CONFIRMED_STAGE_TERMS = (
    "审议通过",
    "表决通过",
    "董事会批准",
    "批准了",
    "决定",
    "正式生效",
    "开始执行",
    "完成",
    "签署",
    "签订",
    "approved",
    "effective",
    "implemented",
    "executed",
    "completed",
    "signed",
    "secured",
    "awarded",
    "entered into",
)

SEMICONDUCTOR_PERFORMANCE_TERMS = (
    "revenue",
    "profit",
    "earnings",
    "业绩",
    "gross margin",
    "annual recurring revenue",
    "arr",
    "营收",
    "收入",
    "利润",
    "净利",
    "盈利",
    "亏损",
    "毛利率",
    "年度经常性收入",
)
SEMICONDUCTOR_MARKET_SIZE_TERMS = (
    "market size",
    "addressable market",
    "total addressable market",
    "tam",
    "市场规模",
    "可寻址市场",
)
SEMICONDUCTOR_PERFORMANCE_CHANGE_TERMS = (
    "大增",
    "大降",
    "激增",
    "骤降",
    "突破",
    "同比增长",
    "同比下降",
    "环比增长",
    "环比下降",
    "转盈",
    "转亏",
    "扭亏",
    "改善",
    "恶化",
    "提升",
    "下降",
    "增长",
    "减少",
    "扩大",
    "收缩",
    "翻倍",
    "减半",
    "创纪录",
    "创新高",
    "grew",
    "growth",
    "rose",
    "increased",
    "improved",
    "declined",
    "fell",
    "decreased",
    "contracted",
    "doubled",
    "halved",
    "turned profitable",
    "turned to a loss",
    "record high",
    "surged",
    "plunged",
    "jumped",
    "slumped",
    "broke through",
)
SEMICONDUCTOR_MATERIAL_FORECAST_TERMS = (
    "翻倍",
    "减半",
    "大幅增长",
    "大幅下降",
    "显著增长",
    "显著下降",
    "加速增长",
    "快速收缩",
    "doubled",
    "double",
    "halved",
    "halve",
    "surge",
    "surged",
    "plunge",
    "plunged",
    "sharp growth",
    "sharp decline",
    "accelerating growth",
)
SEMICONDUCTOR_PERFORMANCE_SURPRISE_TERMS = (
    "超预期",
    "超过预期",
    "好于预期",
    "低于预期",
    "不及预期",
    "逊于预期",
    "beat expectations",
    "beats expectations",
    "above expectations",
    "exceeded expectations",
    "missed expectations",
    "below expectations",
)
SEMICONDUCTOR_FORECAST_TERMS = (
    "预计",
    "预期",
    "预测",
    "有望",
    "将达到",
    "将增长",
    "将下降",
    "forecast",
    "forecasts",
    "forecasted",
    "expects",
    "expected",
    "projected",
    "projection",
    "outlook",
    "will reach",
    "will grow",
    "will decline",
)
RESEARCH_ATTRIBUTION_TERMS = (
    "指出",
    "表示",
    "认为",
    "预计",
    "预测",
    "强调",
    "警告",
    "报告称",
    "报告指出",
    "根据",
    "said",
    "says",
    "reported",
    "according to",
    "expects",
    "forecasts",
    "believes",
    "warned",
    "projects",
    "estimates",
)
RESEARCH_CRITICISM_TERMS = (
    "批评",
    "质疑",
    "反驳",
    "驳斥",
    "否认",
    "错误",
    "不准确",
    "criticized",
    "disputed",
    "rejected",
    "inaccurate",
)


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
class TrustedInstitution:
    institution_id: str
    aliases: tuple[str, ...]
    domains: tuple[str, ...] = ()


@dataclass(frozen=True)
class TradeCorridor:
    corridor_id: str
    china_terms: tuple[str, ...] = ()
    counterparty_terms: tuple[str, ...] = ()
    joint_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.joint_terms and not (self.china_terms and self.counterparty_terms):
            raise RuleConfigError(
                f"trade corridor {self.corridor_id} requires joint terms or both corridor sides"
            )


@dataclass(frozen=True)
class RuleConfig:
    config_version: str
    semiconductor_ai_keywords: tuple[str, ...]
    major_semiconductor_customers: tuple[str, ...]
    exclude_keywords: tuple[str, ...]
    macro_indicators: tuple[str, ...]
    macro_primary_indicators: tuple[str, ...]
    macro_secondary_indicators: tuple[str, ...]
    macro_context_aliases: tuple[str, ...]
    fed_event_aliases: tuple[str, ...]
    fed_actor_aliases: tuple[str, ...]
    fed_path_aliases: tuple[str, ...]
    trusted_institutions: tuple[TrustedInstitution, ...]
    trade_corridors: tuple[TradeCorridor, ...]
    trade_instruments: tuple[str, ...]
    trade_stages: tuple[str, ...]
    trade_focus_industries: tuple[str, ...]


def _stable_registry_id(value: object, field: str) -> str:
    text = _clean(value)
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", text):
        raise RuleConfigError(f"{field} must be a stable lower-snake-case id")
    return text


def _trusted_registry(value: object) -> tuple[TrustedInstitution, ...]:
    if not isinstance(value, dict) or not value:
        raise RuleConfigError("trusted_attribution.institutions must be a non-empty object")
    result: list[TrustedInstitution] = []
    for raw_id, raw in value.items():
        institution_id = _stable_registry_id(raw_id, "trusted institution id")
        definition = _mapping(
            raw,
            f"trusted_attribution.institutions.{institution_id}",
            {"aliases", "domains"},
        )
        aliases = _tuple_strings(
            definition.get("aliases"),
            f"trusted_attribution.institutions.{institution_id}.aliases",
        )
        domains = tuple(
            domain.casefold()
            for domain in _tuple_strings(
                definition.get("domains"),
                f"trusted_attribution.institutions.{institution_id}.domains",
            )
        )
        if not aliases:
            raise RuleConfigError(f"trusted institution {institution_id} requires aliases")
        if any(
            not re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,}", domain)
            for domain in domains
        ):
            raise RuleConfigError(f"trusted institution {institution_id} has an invalid domain")
        result.append(TrustedInstitution(institution_id, aliases, domains))
    return tuple(result)


def _trade_corridor_registry(value: object) -> tuple[TradeCorridor, ...]:
    if not isinstance(value, dict) or not value:
        raise RuleConfigError("trade_policy.corridors must be a non-empty object")
    result: list[TradeCorridor] = []
    for raw_id, raw in value.items():
        corridor_id = _stable_registry_id(raw_id, "trade corridor id")
        definition = _mapping(
            raw,
            f"trade_policy.corridors.{corridor_id}",
            {"china_terms", "counterparty_terms", "joint_terms"},
        )
        result.append(
            TradeCorridor(
                corridor_id=corridor_id,
                china_terms=_tuple_strings(
                    definition.get("china_terms"),
                    f"trade_policy.corridors.{corridor_id}.china_terms",
                ),
                counterparty_terms=_tuple_strings(
                    definition.get("counterparty_terms"),
                    f"trade_policy.corridors.{corridor_id}.counterparty_terms",
                ),
                joint_terms=_tuple_strings(
                    definition.get("joint_terms"),
                    f"trade_policy.corridors.{corridor_id}.joint_terms",
                ),
            )
        )
    return tuple(result)


def parse_rule_config(payload: Mapping[str, Any]) -> RuleConfig:
    expected = {
        "schema_version",
        "config_version",
        "semiconductor_ai_keywords",
        "major_semiconductor_customers",
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
        {"institutions"},
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
        major_semiconductor_customers=_tuple_strings(
            payload.get("major_semiconductor_customers"), "major_semiconductor_customers"
        ),
        exclude_keywords=_tuple_strings(payload.get("exclude_keywords"), "exclude_keywords"),
        macro_indicators=indicators,
        macro_primary_indicators=primary,
        macro_secondary_indicators=secondary,
        macro_context_aliases=_tuple_strings(macro.get("context_aliases"), "macro_data.context_aliases"),
        fed_event_aliases=_tuple_strings(fed.get("event_aliases"), "fed_policy.event_aliases"),
        fed_actor_aliases=_tuple_strings(fed.get("actor_aliases"), "fed_policy.actor_aliases"),
        fed_path_aliases=_tuple_strings(fed.get("path_aliases"), "fed_policy.path_aliases"),
        trusted_institutions=_trusted_registry(trusted.get("institutions")),
        trade_corridors=_trade_corridor_registry(trade.get("corridors")),
        trade_instruments=_tuple_strings(trade.get("instruments"), "trade_policy.instruments"),
        trade_stages=_tuple_strings(trade.get("stages"), "trade_policy.stages"),
        trade_focus_industries=_tuple_strings(
            trade.get("focus_industries"), "trade_policy.focus_industries"
        ),
    )
    if (
        not config.semiconductor_ai_keywords
        or not config.major_semiconductor_customers
        or not config.macro_indicators
        or not config.trade_corridors
    ):
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

    corridor_terms: list[str] = []
    for corridor in rule_config.trade_corridors:
        joint = _matches(text, corridor.joint_terms)
        china = _matches(text, corridor.china_terms)
        counterparty = _matches(text, corridor.counterparty_terms)
        if joint or (china and counterparty):
            corridor_terms.extend(joint or (*china, *counterparty))
    trade_action = _matches(text, (*rule_config.trade_instruments, *rule_config.trade_stages))
    trade_classification = classify_trade_friction(_classification_item(item))
    local_trade_terms: list[str] = []
    for sentence in _sentences(text):
        sentence_action = _matches(sentence, (*rule_config.trade_instruments, *rule_config.trade_stages))
        if not sentence_action:
            continue
        for corridor in rule_config.trade_corridors:
            joint = _matches(sentence, corridor.joint_terms)
            china = _matches(sentence, corridor.china_terms)
            counterparty = _matches(sentence, corridor.counterparty_terms)
            if joint or (china and counterparty):
                local_trade_terms.extend((*joint, *china, *counterparty, *sentence_action))
    if "trade_policy" in source_policy.direct_admission_families:
        evidence.append(
            _evidence(
                "trade_policy",
                "trade_policy_direct_scope",
                text,
                trade_action or tuple(corridor_terms) or ("direct_trade_surface",),
            )
        )
    elif local_trade_terms or trade_classification:
        classified_terms: tuple[str, ...] = ()
        if trade_classification:
            classified_terms = tuple(
                str(value)
                for key in (
                    "corridors",
                    "policy_tools",
                    "action_stages",
                    "strong_tension_terms",
                    "weak_tension_terms",
                )
                for value in trade_classification.get(key) or []
                if str(value).strip()
            )
        evidence.append(
            _evidence(
                "trade_policy",
                "trade_policy_scope",
                text,
                tuple(local_trade_terms) or classified_terms,
            )
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


def _is_market_template_title(title: str) -> bool:
    return _has(
        title,
        "ETF", "股价", "涨停", "跌停", "市值", "PE", "成交额", "后市", "估值",
        "牛股", "吸金", "机构看好", "板块", "概念", "反弹", "涨超", "跌超",
        "集体", "暴涨", "爆发",
    )


def _candidate(family: RuleFamily, rule_id: str, action: str, quote: str, reason: str) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "rule_family": family,
        "decision_action": action,
        "evidence_quote": quote[:500],
        "reason": reason,
    }


def _classification_item(item: NormalizedMarketItem) -> dict[str, Any]:
    return {
        "title": item.title,
        "summary": item.summary,
        "content": item.raw.get("content") or "",
        "full_text": item.full_text,
        "published_at": item.published_at,
        "first_seen_at": item.first_seen_at,
    }


def _classification_candidate(
    classification: Mapping[str, Any],
    *,
    family: RuleFamily = "semiconductor_ai",
    default_rule_id: str = "semiconductor_ordinary",
) -> dict[str, Any]:
    evidence = classification.get("evidence_quotes") or classification.get("evidence")
    quote = str(evidence[0]) if isinstance(evidence, list) and evidence else ""
    candidate = _candidate(
        family,
        str(classification.get("rule_id") or default_rule_id),
        str(classification.get("decision_action") or "archive"),
        quote,
        str(classification.get("reason") or ""),
    )
    candidate["event_type"] = str(classification.get("event_type") or "")
    return candidate


def _first_local_match(
    text: str,
    *groups: tuple[str, ...],
    sentence_limit: int | None = None,
    asserted_only: bool = False,
) -> str:
    sentences = _sentences(text)
    for sentence in sentences[:sentence_limit] if sentence_limit is not None else sentences:
        if asserted_only:
            question_attribution = _all_groups(
                sentence,
                ("投资者", "股民", "网友"),
                ("提问", "问道", "询问", "请问"),
            )
            answer_attribution = _has(
                sentence,
                "公司回答",
                "公司回复",
                "公司表示",
                "公司称",
                "回应称",
                "答复称",
            )
            if _has(sentence, "?", "？") or (question_attribution and not answer_attribution):
                continue
        if _all_groups(sentence, *groups):
            return sentence
    return ""


def _term_spans(text: str, terms: tuple[str, ...]) -> tuple[tuple[int, int], ...]:
    lowered = text.casefold()
    spans: list[tuple[int, int]] = []
    for term in terms:
        normalized = term.casefold().strip()
        if not normalized:
            continue
        pattern = re.escape(normalized)
        if re.fullmatch(r"[a-z0-9_.+-]+", normalized):
            pattern = rf"(?<![a-z0-9]){pattern}(?![a-z0-9])"
        spans.extend((match.start(), match.end()) for match in re.finditer(pattern, lowered))
    return tuple(spans)


def _first_local_near_match(
    text: str,
    left_terms: tuple[str, ...],
    right_terms: tuple[str, ...],
    *,
    max_gap: int = 24,
    sentence_limit: int | None = None,
) -> str:
    sentences = _sentences(text)
    for sentence in sentences[:sentence_limit] if sentence_limit is not None else sentences:
        left_spans = _term_spans(sentence, left_terms)
        right_spans = _term_spans(sentence, right_terms)
        for left_start, left_end in left_spans:
            for right_start, right_end in right_spans:
                gap = max(left_start, right_start) - min(left_end, right_end)
                if gap <= max_gap:
                    return sentence
    return ""


def _first_local_ordered_match(
    text: str,
    left_terms: tuple[str, ...],
    right_terms: tuple[str, ...],
    *,
    max_gap: int,
    sentence_limit: int | None = None,
) -> str:
    sentences = _sentences(text)
    for sentence in sentences[:sentence_limit] if sentence_limit is not None else sentences:
        for _, left_end in _term_spans(sentence, left_terms):
            for right_start, _ in _term_spans(sentence, right_terms):
                if 0 <= right_start - left_end <= max_gap:
                    return sentence
    return ""


def _references_earlier_year(item: NormalizedMarketItem, evidence: str) -> bool:
    published_year = re.match(r"(20\d{2})", item.published_at or item.first_seen_at)
    if not published_year:
        return False
    year = int(published_year.group(1))
    return any(
        int(value) < year
        for value in re.findall(r"(?<!\d)(20\d{2})(?:\s*年)?(?!\d)", evidence)
    )


def _question_without_answer(sentence: str) -> bool:
    if not _has(sentence, "?", "？"):
        return False
    return not _has(
        sentence,
        "公司回答",
        "公司回复",
        "公司表示",
        "公司确认",
        "公司称",
        "回应称",
        "答复称",
        "confirmed",
    )


def _semiconductor_hard_variable_change(
    item: NormalizedMarketItem,
    text: str,
    config: RuleConfig,
) -> tuple[str, str, str] | None:
    planned_evidence: tuple[str, str] | None = None
    sentences = _sentences(text)
    has_topic_denial = any(
        _matches(sentence, config.semiconductor_ai_keywords)
        and _has(sentence, *SEMICONDUCTOR_NON_EXECUTION_TERMS)
        for sentence in sentences
    )
    for sentence in sentences:
        if not _matches(sentence, config.semiconductor_ai_keywords):
            continue
        if _question_without_answer(sentence):
            continue
        if has_topic_denial and _has(sentence, "传闻", "rumor", "rumour", "回应"):
            continue
        if _references_earlier_year(item, sentence):
            continue

        category = ""
        if _has(sentence, *SEMICONDUCTOR_CAPACITY_DIRECT_TERMS) or _all_groups(
            sentence, SEMICONDUCTOR_CAPACITY_TERMS, SEMICONDUCTOR_CAPACITY_CHANGE_TERMS
        ):
            category = "产能或产量"
        elif (
            _has(sentence, *SEMICONDUCTOR_CAPEX_TERMS)
            or _all_groups(sentence, ("investment", "investments"), SEMICONDUCTOR_CAPACITY_TERMS)
        ) and _has(sentence, *SEMICONDUCTOR_CAPEX_CHANGE_TERMS, *SEMICONDUCTOR_CAPACITY_TERMS):
            category = "资本开支或产业投资"
        elif _has(sentence, *SEMICONDUCTOR_PROCUREMENT_DIRECT_TERMS) or any(
            re.search(pattern, sentence, flags=re.I)
            for pattern in SEMICONDUCTOR_PROCUREMENT_PATTERNS
        ):
            category = "订单或采购"
        if not category:
            continue

        non_execution = _has(sentence, *SEMICONDUCTOR_NON_EXECUTION_TERMS)
        planning = _has(sentence, *SEMICONDUCTOR_PLANNING_TERMS)
        planned_only = planning and (
            non_execution or not _has(sentence, *SEMICONDUCTOR_EXECUTION_TERMS)
        )
        if planned_only:
            planned_evidence = planned_evidence or (sentence, category)
            continue
        if non_execution:
            continue
        return "push", sentence, f"半导体/AI的{category}发生明确变化或进入执行阶段。"

    if planned_evidence:
        sentence, category = planned_evidence
        return "daily", sentence, f"半导体/AI的{category}仍处于计划或预期阶段。"
    return None


def _semiconductor_operating_change(
    item: NormalizedMarketItem,
    text: str,
    config: RuleConfig,
) -> tuple[str, str, str] | None:
    planned_evidence: tuple[str, str] | None = None
    for sentence in _sentences(text):
        if not _matches(sentence, config.semiconductor_ai_keywords):
            continue
        if _question_without_answer(sentence) or _references_earlier_year(item, sentence):
            continue

        category = ""
        confirmed = False
        if _has(sentence, *SEMICONDUCTOR_SHIPMENT_EXECUTION_TERMS):
            category = "出货或交付"
            confirmed = True
        elif _has(sentence, *SEMICONDUCTOR_SHIPMENT_TERMS):
            category = "出货或交付"
            confirmed = _has(sentence, *SEMICONDUCTOR_SHIPMENT_CHANGE_TERMS)
        elif _has(sentence, *SEMICONDUCTOR_DEMAND_CHANGE_TERMS):
            category = "需求"
            confirmed = True
        elif _has(sentence, *SEMICONDUCTOR_REGULATION_EASING_TERMS):
            category = "监管或贸易限制缓和"
        elif _has(sentence, *SEMICONDUCTOR_REGULATION_TERMS):
            category = "监管或贸易限制"
            confirmed = _has(sentence, *SEMICONDUCTOR_REGULATION_EXECUTION_TERMS)
        elif _has(sentence, *SEMICONDUCTOR_SAMPLING_EXECUTION_TERMS):
            category = "送样或客户认证"
            confirmed = True
        else:
            route_change = any(
                re.search(pattern, sentence, flags=re.IGNORECASE)
                for pattern in (
                    r"(?:switch|shift|migrate).{0,28}from.{0,28}to",
                    r"replace.{0,28}with",
                    r"(?:develop|adopt|launch|use).{0,28}custom asics?",
                    r"custom asics?.{0,28}(?:bypass|replace)",
                    r"从.{1,24}(?:切换至|替换为|转向).{1,24}",
                    r"(?:采用|开发|推出|使用).{0,24}自研(?:芯片|asic)",
                )
            )
            if route_change and _has(
                sentence,
                "投资者",
                "投资策略",
                "配置",
                "仓位",
                "持仓",
                "头寸",
                "轮动",
                "增配",
                "减配",
                "portfolio",
                "allocation",
                "exposure",
                "positioning",
            ):
                route_change = False
            if route_change or _has(sentence, "绕过英伟达", "bypass nvidia"):
                category = "技术路线替换"
                confirmed = True

        if not category:
            continue
        planning = _has(sentence, *SEMICONDUCTOR_SPECULATIVE_TERMS)
        non_execution = _has(sentence, *SEMICONDUCTOR_OPERATING_NON_EXECUTION_TERMS)
        if planning:
            planned_evidence = planned_evidence or (sentence, category)
            continue
        if non_execution:
            continue
        if not confirmed:
            planned_evidence = planned_evidence or (sentence, category)
            continue
        return "push", sentence, f"半导体/AI的{category}已发生、已生效或进入执行阶段。"

    if planned_evidence:
        sentence, category = planned_evidence
        return "daily", sentence, f"半导体/AI的{category}仍处于计划、预测、提案或未证实阶段。"
    return None


def _semiconductor_performance_change(
    item: NormalizedMarketItem,
    text: str,
    config: RuleConfig,
) -> tuple[str, str, str, str] | None:
    ordinary_evidence: tuple[str, str] | None = None
    historical_evidence: tuple[str, str] | None = None
    for sentence in _sentences(text):
        if not _matches(sentence, config.semiconductor_ai_keywords):
            continue
        performance = _has(sentence, *SEMICONDUCTOR_PERFORMANCE_TERMS)
        market_size = _has(sentence, *SEMICONDUCTOR_MARKET_SIZE_TERMS)
        if not performance and not market_size:
            continue
        category = "市场规模" if market_size else "业绩或经营指标"
        if _question_without_answer(sentence):
            continue
        if _references_earlier_year(item, sentence) or _has(
            sentence,
            "历史上",
            "此前年度",
            "过去几年",
            "回顾",
            "historically",
            "in prior years",
            "previous years",
        ):
            historical_evidence = historical_evidence or (sentence, category)
            continue
        if _has(sentence, *SEMICONDUCTOR_NON_EXECUTION_TERMS):
            ordinary_evidence = ordinary_evidence or (sentence, category)
            continue
        actual_surprise = _has(sentence, *SEMICONDUCTOR_PERFORMANCE_SURPRISE_TERMS)
        forecast = _has(sentence, *SEMICONDUCTOR_FORECAST_TERMS) and not actual_surprise
        changed = _has(sentence, *SEMICONDUCTOR_PERFORMANCE_CHANGE_TERMS)
        material_forecast = forecast and _has(sentence, *SEMICONDUCTOR_MATERIAL_FORECAST_TERMS)
        if actual_surprise or (changed and not forecast) or material_forecast:
            reason = (
                f"半导体/AI的{category}出现明确实质变化。"
                if not forecast
                else f"半导体/AI的{category}预测包含翻倍、减半或显著加速/收缩等实质变化。"
            )
            return "push", sentence, reason, "semiconductor_performance_change"
        ordinary_evidence = ordinary_evidence or (sentence, category)

    if ordinary_evidence:
        sentence, category = ordinary_evidence
        return (
            "daily",
            sentence,
            f"半导体/AI的{category}只有静态数值或普通预测，未证明相对既有判断发生实质变化。",
            "semiconductor_performance_outlook",
        )
    if historical_evidence:
        sentence, category = historical_evidence
        return (
            "archive",
            sentence,
            f"半导体/AI的{category}只属于历史回顾。",
            "semiconductor_ordinary",
        )
    return None


def _normalized_quote_in_text(text: str, quote: str) -> bool:
    normalized_text = _clean(text)
    normalized_quote = _clean(quote)
    return bool(normalized_quote) and normalized_quote in normalized_text


def _trusted_research_evidence(
    item: NormalizedMarketItem,
    text: str,
    config: RuleConfig,
) -> tuple[dict[str, str], ...]:
    institutions = {institution.institution_id: institution for institution in config.trusted_institutions}
    evidence: list[dict[str, str]] = []

    stored = item.raw.get("_attributed_research") if isinstance(item.raw, Mapping) else None
    if isinstance(stored, Mapping) and str(stored.get("attribution") or "") == "explicit":
        institution_id = str(stored.get("institution_id") or "").strip()
        institution = institutions.get(institution_id)
        attribution_quote = str(stored.get("attribution_quote") or "").strip()
        alias_match = bool(institution and _matches(attribution_quote, institution.aliases))
        if (
            institution
            and alias_match
            and _normalized_quote_in_text(text, attribution_quote)
            and not _has(attribution_quote, *RESEARCH_CRITICISM_TERMS)
        ):
            claims = stored.get("claims") if isinstance(stored.get("claims"), list) else []
            for claim in claims:
                if not isinstance(claim, Mapping):
                    continue
                claim_quote = str(claim.get("evidence_quote") or "").strip()
                if not _normalized_quote_in_text(text, claim_quote):
                    continue
                if not _matches(claim_quote, config.semiconductor_ai_keywords):
                    continue
                evidence.append(
                    {
                        "institution_id": institution_id,
                        "attribution_quote": attribution_quote,
                        "claim_quote": claim_quote,
                        "extraction_mode": str(stored.get("extraction_mode") or "stored"),
                    }
                )

    sentences = _sentences(text)
    hostname = (urlparse(item.url).hostname or "").casefold()
    for institution in config.trusted_institutions:
        matching_domain = next(
            (
                domain
                for domain in institution.domains
                if hostname == domain or hostname.endswith(f".{domain}")
            ),
            "",
        )
        if not matching_domain:
            continue
        for claim_quote in sentences:
            if not _matches(claim_quote, config.semiconductor_ai_keywords):
                continue
            evidence.append(
                {
                    "institution_id": institution.institution_id,
                    "attribution_quote": "",
                    "attribution_domain": matching_domain,
                    "claim_quote": claim_quote,
                    "extraction_mode": "official_domain",
                }
            )

    for institution in config.trusted_institutions:
        for index, sentence in enumerate(sentences):
            if not _matches(sentence, institution.aliases):
                continue
            if _has(sentence, *RESEARCH_CRITICISM_TERMS):
                continue
            if _question_without_answer(sentence):
                continue
            if not _has(sentence, *RESEARCH_ATTRIBUTION_TERMS):
                continue
            for claim_quote in sentences[index : index + 2]:
                if not _matches(claim_quote, config.semiconductor_ai_keywords):
                    continue
                evidence.append(
                    {
                        "institution_id": institution.institution_id,
                        "attribution_quote": sentence,
                        "claim_quote": claim_quote,
                        "extraction_mode": "deterministic",
                    }
                )

    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item_evidence in evidence:
        key = (
            item_evidence["institution_id"],
            _clean(item_evidence.get("attribution_quote") or item_evidence.get("attribution_domain")),
            _clean(item_evidence["claim_quote"]),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item_evidence)
    return tuple(unique[:6])


def _trusted_institution_registry(config: RuleConfig) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (institution.institution_id, institution.aliases)
        for institution in config.trusted_institutions
    )


def _official_institution_ids(
    item: NormalizedMarketItem,
    config: RuleConfig,
) -> tuple[str, ...]:
    result: list[str] = []
    hostname = (urlparse(item.url).hostname or "").casefold()
    for institution in config.trusted_institutions:
        if any(hostname == domain or hostname.endswith(f".{domain}") for domain in institution.domains):
            result.append(institution.institution_id)
    return tuple(dict.fromkeys(result))


def _stored_attributed_claim_quotes(
    item: NormalizedMarketItem,
    text: str,
    config: RuleConfig,
) -> dict[str, tuple[str, ...]]:
    stored = item.raw.get("_attributed_research") if isinstance(item.raw, Mapping) else None
    if not isinstance(stored, Mapping) or str(stored.get("attribution") or "") != "explicit":
        return {}
    institution_id = str(stored.get("institution_id") or "").strip()
    institution = next(
        (value for value in config.trusted_institutions if value.institution_id == institution_id),
        None,
    )
    attribution_quote = str(stored.get("attribution_quote") or "").strip()
    if (
        institution is None
        or not _matches(attribution_quote, institution.aliases)
        or not _normalized_quote_in_text(text, attribution_quote)
        or _has(attribution_quote, *RESEARCH_CRITICISM_TERMS)
    ):
        return {}
    claims = stored.get("claims") if isinstance(stored.get("claims"), list) else []
    quotes = tuple(
        dict.fromkeys(
            quote
            for claim in claims
            if isinstance(claim, Mapping)
            for quote in (str(claim.get("evidence_quote") or "").strip(),)
            if quote and _normalized_quote_in_text(text, quote)
        )
    )
    return {institution_id: quotes} if quotes else {}


def _holding_subject_registry(
    portfolio: PortfolioRuleConfig,
) -> tuple[tuple[str, tuple[str, ...], Mapping[str, Any]], ...]:
    return tuple(
        (
            holding.symbol,
            tuple(dict.fromkeys((*holding.names, holding.symbol, *holding.related_news_keywords))),
            {
                "symbol": holding.symbol,
                "names": list(holding.names),
                "related_news_keywords": list(holding.related_news_keywords),
            },
        )
        for holding in portfolio.holdings
    )


def _investment_bank_rating_candidate(
    item: NormalizedMarketItem,
    text: str,
    config: RuleConfig,
    portfolio: PortfolioRuleConfig,
) -> dict[str, Any] | None:
    claims = extract_rating_claims(
        text_parts=(item.title, item.summary, item.raw.get("content") or "", item.full_text),
        institutions=_trusted_institution_registry(config),
        subjects=_holding_subject_registry(portfolio),
        official_institution_ids=_official_institution_ids(item, config),
        attributed_claim_quotes=_stored_attributed_claim_quotes(item, text, config),
    )
    if not claims:
        return None
    claim = claims[0]
    change_type = str(claim["change_type"])
    subject = dict(claim["subject"])
    evidence_quote = str(claim["evidence_quote"])
    direct_subject = any(
        _contains(evidence_quote, term)
        for term in (*subject.get("names", []), str(subject.get("symbol") or ""))
        if str(term).strip()
    )
    action = "push" if direct_subject and change_type in {"revision", "coverage_start"} else "daily"
    reason = (
        "受信机构对直接持仓的评级、目标价或覆盖状态发生明确变化。"
        if action == "push"
        else "受信机构维持、静态陈述或仅涉及持仓关联关键词的评级、目标价或覆盖状态。"
    )
    candidate = _candidate(
        "holding",
        "holding_rating_revision" if action == "push" else "holding_ordinary",
        action,
        evidence_quote,
        reason,
    )
    candidate.update(
        {
            "institution_id": str(claim["institution_id"]),
            "attributed_institutions": [str(claim["institution_id"])],
            "subject": subject,
            "subject_relation": "direct" if direct_subject else "configured_related",
            "research_actions": list(claim["research_actions"]),
            "research_change_type": change_type,
            "previous_rating": str(claim["previous_rating"]),
            "revised_rating": str(claim["revised_rating"]),
            "target_price_change": dict(claim["target_price_change"]),
        }
    )
    return candidate


def _investment_bank_allocation_candidates(
    item: NormalizedMarketItem,
    text: str,
    config: RuleConfig,
    portfolio: PortfolioRuleConfig,
) -> dict[RuleFamily, dict[str, Any]]:
    holding_terms = tuple(
        dict.fromkeys(
            term
            for holding in portfolio.holdings
            for term in (*holding.names, holding.symbol, *holding.related_news_keywords)
        )
    )
    targets_by_family: dict[str, tuple[str, ...]] = {
        "holding": holding_terms,
        "semiconductor_ai": config.semiconductor_ai_keywords,
    }
    claims = extract_allocation_claims(
        text_parts=(item.title, item.summary, item.raw.get("content") or "", item.full_text),
        institutions=_trusted_institution_registry(config),
        targets_by_family=targets_by_family,
        official_institution_ids=_official_institution_ids(item, config),
        attributed_claim_quotes=_stored_attributed_claim_quotes(item, text, config),
    )
    candidates: dict[RuleFamily, dict[str, Any]] = {}
    for claim in claims:
        action = "daily" if claim["change_type"] == "maintained" else "push"
        target_families = [str(family) for family in claim["target_families"]]
        for family in FAMILY_ORDER:
            if family not in target_families:
                continue
            candidate = _candidate(
                family,
                "investment_bank_allocation_change" if action == "push" else "investment_bank_allocation_maintained",
                action,
                str(claim["evidence_quote"]),
                (
                    "受信机构明确调整已准入内容范围内的配置，或给出完整的跨主题配置轮动。"
                    if action == "push"
                    else "受信机构维持原有配置观点。"
                ),
            )
            candidate.update(
                {
                    "institution_id": str(claim["institution_id"]),
                    "attributed_institutions": [str(claim["institution_id"])],
                    "strategy_type": str(claim["strategy_type"]),
                    "allocation_actions": list(claim["actions"]),
                    "target_families": target_families,
                    "from_text": str(claim["from_text"]),
                    "to_text": str(claim["to_text"]),
                }
            )
            previous = candidates.get(family)
            if previous is None or ACTION_RANK[action] > ACTION_RANK[str(previous["decision_action"])]:
                candidates[family] = candidate
    return candidates


def _attach_trusted_research_evidence(
    candidate: dict[str, Any],
    item: NormalizedMarketItem,
    text: str,
    config: RuleConfig,
) -> dict[str, Any]:
    claims = _trusted_research_evidence(item, text, config)
    if not claims:
        return candidate
    candidate_quote = _clean(candidate.get("evidence_quote"))
    matched = tuple(
        claim
        for claim in claims
        if not candidate_quote
        or _clean(claim["claim_quote"]) in candidate_quote
        or candidate_quote in _clean(claim["claim_quote"])
    )
    if not matched:
        return candidate
    enriched = dict(candidate)
    enriched["attributed_institutions"] = list(
        dict.fromkeys(claim["institution_id"] for claim in matched)
    )
    enriched["attributed_research_evidence"] = [dict(claim) for claim in matched]
    return enriched


def _semiconductor_commercial_development(text: str, config: RuleConfig) -> str:
    for sentence in _sentences(text):
        if not _matches(sentence, config.semiconductor_ai_keywords):
            continue
        if _question_without_answer(sentence) or _has(sentence, *SEMICONDUCTOR_NON_EXECUTION_TERMS):
            continue
        if _has(sentence, *SEMICONDUCTOR_COMMERCIAL_DEVELOPMENT_TERMS):
            return sentence
        if _all_groups(
            sentence,
            SEMICONDUCTOR_VALUATION_TERMS,
            SEMICONDUCTOR_VALUATION_CHANGE_TERMS,
        ):
            return sentence
    return ""


def _routine_corporate_attachment(title: str) -> bool:
    return _has(title, "审计报告", "审计附件", "资产评估报告", "估值报告", "valuation report")


def _current_corporate_event(
    item: NormalizedMarketItem,
    text: str,
    event_terms: tuple[str, ...],
    *,
    subject_terms: tuple[str, ...] = (),
    action_terms: tuple[str, ...] = (),
    require_action_term: bool = False,
    max_action_gap: int | None = None,
    sentence_limit: int = 16,
) -> str:
    for sentence in _sentences(text)[:sentence_limit]:
        if not _has(sentence, *event_terms):
            continue
        if subject_terms and not (
            _matches(sentence, subject_terms)
            or _has(
                sentence,
                "本公司已",
                "公司已",
                "公司已经",
                "公司正式",
                "公司决定",
                "公司宣布",
                "公司董事会",
                "公司签署",
                "公司签订",
                "公司新获",
                "公司获得",
                "公司完成",
                "the company has",
                "the company announced",
                "the company approved",
                "the company signed",
            )
        ):
            continue
        if _question_without_answer(sentence) or _references_earlier_year(item, sentence):
            continue
        if _has(sentence, *CORPORATE_HISTORICAL_TERMS, *CORPORATE_NON_EXECUTION_TERMS):
            continue
        formal_stage = _has(sentence, *CORPORATE_FORMAL_STAGE_TERMS)
        confirmed_stage = _has(sentence, *CORPORATE_CONFIRMED_STAGE_TERMS)
        if _has(sentence, *CORPORATE_PLANNING_OR_UNCONFIRMED_TERMS) and not confirmed_stage:
            continue
        matched_action = bool(action_terms and _has(sentence, *action_terms))
        if matched_action and max_action_gap is not None:
            event_spans = _term_spans(sentence, event_terms)
            action_spans = _term_spans(sentence, action_terms)
            matched_action = any(
                max(event_start, action_start) - min(event_end, action_end) <= max_action_gap
                for event_start, event_end in event_spans
                for action_start, action_end in action_spans
            )
        if require_action_term and not matched_action:
            continue
        if formal_stage or matched_action:
            return sentence
    return ""


def _corporate_material_change(
    item: NormalizedMarketItem,
    text: str,
    *,
    subject_terms: tuple[str, ...] = (),
) -> tuple[str, str] | None:
    title = item.title
    if _routine_corporate_attachment(title):
        return None
    if _has(title, "增资", "减资", "capital increase", "capital reduction"):
        return title, "公司发生实质增资或减资。"

    approved_capital_change = _first_local_match(
        text,
        ("增资", "减资", "capital increase", "capital reduction"),
        (
            "审议通过",
            "表决通过",
            "董事会批准",
            "董事会同意",
            "approved by the board",
            "board approved",
        ),
        sentence_limit=16,
    )
    if approved_capital_change:
        return approved_capital_change, "公司董事会或同等决策机构已审议通过实质增资或减资。"

    formal_earnings_forecast = _has(
        title,
        "业绩预告",
        "盈利预告",
        "earnings guidance",
        "profit forecast",
    ) or _all_groups(
        title,
        ("预计", "预增", "预减", "expects", "forecasts", "guides"),
        ("净利", "归母", "营收", "earnings", "net profit", "revenue"),
    ) or _all_groups(
        title,
        ("预计", "预增", "预减"),
        ("盈利",),
        ("上半年", "下半年", "一季度", "二季度", "三季度", "四季度", "年度", "全年", "h1", "h2"),
    )
    if formal_earnings_forecast:
        return title, "公司正式披露业绩预告或业绩指引。"

    completed_acquisition = _first_local_match(
        text,
        ("收购", "并购", "acquisition", "acquire"),
        ("完成审批", "完成交割", "对价支付", "收购完成", "已经完成", "已完成", "closed", "completed"),
        sentence_limit=16,
    )
    if completed_acquisition:
        return completed_acquisition, "并购已经完成审批、交割或对价支付，属于已执行的企业实质变化。"

    project_schedule_change = _first_local_match(
        text,
        ("募投项目", "扩产项目", "建设项目", "项目建设", "capex project", "production project"),
        ("延期", "延长", "推迟", "终止", "叫停", "delay", "postpone", "cancel"),
        sentence_limit=16,
    ) or _first_local_match(
        text,
        ("产线", "工厂", "production line", "factory"),
        ("建设", "投产", "开工", "竣工", "construction", "production start"),
        ("延期", "延长", "推迟", "终止", "叫停", "delay", "postpone", "cancel"),
        sentence_limit=16,
    )
    if project_schedule_change:
        return project_schedule_change, "募投、扩产或生产项目的执行时间表发生明确变化。"

    executed_order = _current_corporate_event(
        item,
        text,
        (
            "订单",
            "大单",
            "重大合同",
            "销售合约",
            "供货协议",
            "供应协议",
            "采购订单",
            "中标",
            "定点",
            "order",
            "contract",
            "supply agreement",
            "purchase order",
        ),
        subject_terms=subject_terms,
        action_terms=(
            "签署",
            "签订",
            "新签",
            "新获",
            "新增",
            "获得",
            "取得",
            "赢得",
            "中标",
            "定点",
            "获批",
            "signed",
            "secured",
            "awarded",
            "won",
            "entered into",
        ),
        require_action_term=True,
        max_action_gap=10,
    )
    if executed_order:
        return executed_order, "公司已签署、获得或开始执行订单、重大合同或供货协议。"

    executed_price_change = _current_corporate_event(
        item,
        text,
        (
            "涨价",
            "提价",
            "价格上调",
            "降价",
            "价格下调",
            "价格调整",
            "price increase",
            "price hike",
            "price cut",
            "price adjustment",
        ),
        subject_terms=subject_terms,
        action_terms=(
            "上调",
            "下调",
            "生效",
            "实施",
            "执行",
            "raised prices",
            "cut prices",
            "takes effect",
            "implemented",
        ),
        require_action_term=True,
    )
    if executed_price_change:
        return executed_price_change, "公司已决定、实施或正式生效产品价格调整。"

    executed_capacity_change = _current_corporate_event(
        item,
        text,
        (
            "扩产",
            "产能扩张",
            "新增产能",
            "减产",
            "停产",
            "复产",
            "投产",
            "产能",
            "产量",
            "良率",
            "资本开支",
            "capacity expansion",
            "capacity cut",
            "production halt",
            "production restart",
            "production ramp",
            "output",
            "yield",
            "capital expenditure",
            "capex",
        ),
        subject_terms=subject_terms,
        action_terms=(
            "扩产",
            "减产",
            "停产",
            "复产",
            "投产",
            "上调",
            "下调",
            "提高",
            "降低",
            "提升",
            "下降",
            "增加",
            "削减",
            "expanded",
            "cut capacity",
            "halted production",
            "restarted production",
            "raised",
            "increased",
            "reduced",
            "improved",
            "declined",
        ),
        require_action_term=True,
    )
    if executed_capacity_change:
        return executed_capacity_change, "公司已批准或实施产能、产量、停复产、资本开支或良率变化。"

    executed_asset_transaction = _current_corporate_event(
        item,
        text,
        (
            "收购",
            "并购",
            "资产出售",
            "出售资产",
            "出售子公司",
            "股权出售",
            "出售股权",
            "处置资产",
            "资产处置",
            "资产剥离",
            "剥离资产",
            "acquisition",
            "acquire",
            "asset sale",
            "asset disposal",
            "divestiture",
            "divestment",
        ),
        subject_terms=subject_terms,
        action_terms=(
            "审议通过",
            "批准",
            "签署",
            "签订",
            "完成",
            "交割",
            "对价支付",
            "approved",
            "signed",
            "completed",
            "closed",
            "closing",
            "divested",
            "disposed",
        ),
        require_action_term=True,
    )
    if executed_asset_transaction:
        return executed_asset_transaction, "并购、资产出售或资产剥离已经签署、批准或完成。"

    formal_regulatory_decision = ""
    for sentence in _sentences(text)[:16]:
        if not _all_groups(
            sentence,
            (
                "监管",
                "证监会",
                "交易所",
                "法院",
                "行政机关",
                "主管部门",
                "regulator",
                "regulatory authority",
                "commission",
                "exchange",
                "court",
                "fda",
            ),
            (
                "处罚",
                "罚款",
                "批准",
                "许可",
                "否决",
                "驳回",
                "暂停",
                "终止",
                "禁令",
                "制裁",
                "penalty",
                "fine",
                "approval",
                "approved",
                "rejected",
                "suspended",
                "terminated",
                "ban",
                "sanction",
            ),
        ):
            continue
        if subject_terms and not _matches(sentence, subject_terms):
            continue
        if _question_without_answer(sentence) or _references_earlier_year(item, sentence):
            continue
        if _has(
            sentence,
            *CORPORATE_HISTORICAL_TERMS,
            *CORPORATE_NON_EXECUTION_TERMS,
            *CORPORATE_PLANNING_OR_UNCONFIRMED_TERMS,
        ):
            continue
        if _has(
            sentence,
            *CORPORATE_FORMAL_STAGE_TERMS,
            "作出",
            "下达",
            "imposed",
            "issued",
            "rejected",
            "suspended",
            "terminated",
        ):
            formal_regulatory_decision = sentence
            break
    if formal_regulatory_decision:
        return formal_regulatory_decision, "监管机关已经作出正式处罚、批准、否决、暂停或终止决定。"

    if _has(title, "业绩快报", "earnings flash", "preliminary earnings report"):
        return title, "公司正式披露业绩快报。"

    guidance_revision = _current_corporate_event(
        item,
        text,
        (
            "业绩指引",
            "盈利指引",
            "营收指引",
            "earnings guidance",
            "profit guidance",
            "revenue guidance",
        ),
        subject_terms=subject_terms,
        action_terms=(
            "上修",
            "下修",
            "上调",
            "下调",
            "提高",
            "降低",
            "raises",
            "raised",
            "cuts",
            "cut",
            "revises",
            "revised",
        ),
        require_action_term=True,
    )
    if guidance_revision:
        return guidance_revision, "公司明确上修或下修正式业绩指引。"
    return None


def _holding_candidate(
    item: NormalizedMarketItem,
    text: str,
    admission: AdmissionResult,
    portfolio: PortfolioRuleConfig,
) -> dict[str, Any]:
    direct = any(
        item.rule_family == "holding" and item.reason_code == "holding_direct_identity"
        for item in admission.evidence
    )
    item_symbols = set(item.symbols)
    direct_subjects = tuple(
        name
        for holding in portfolio.holdings
        if holding.symbol in item_symbols or _matches(text, holding.names)
        for name in holding.names
    )
    immediate = tuple(term for holding in portfolio.holdings for term in holding.immediate_alert_keywords)
    if _matches(text, immediate):
        return _candidate("holding", "holding_immediate_alert", "push", text, "命中显式即时提醒关键词。")
    routine_meeting_notice = _has(item.title, "会议通知") or _all_groups(
        item.title,
        ("股东会", "股东大会", "shareholder meeting"),
        ("通知", "notice"),
    )
    if routine_meeting_notice or _routine_corporate_attachment(item.title):
        return _candidate("holding", "holding_ordinary", "archive", text, "例行会议或审计附件不构成实质变化。")
    material_change = _corporate_material_change(item, text, subject_terms=direct_subjects)
    if material_change:
        quote, reason = material_change
        return _candidate("holding", "holding_material_event", "push", quote, reason)
    if _is_market_template_title(item.title):
        return _candidate("holding", "holding_ordinary", "archive", item.title, "市场行情模板不构成新的持仓实质变化。")
    if direct:
        return _candidate("holding", "holding_ordinary", "daily", text, "直接持仓普通内容默认进入日报。")
    return _candidate("holding", "holding_ordinary", "archive", text, "关联内容未形成实质变化。")


def _semiconductor_candidate(
    item: NormalizedMarketItem,
    text: str,
    config: RuleConfig,
) -> dict[str, Any]:
    classification_item = _classification_item(item)
    specific_candidates = [
        _classification_candidate(classification)
        for classification in (
            classify_ai_compute_supply_demand(classification_item),
            classify_ai_credit_risk(classification_item),
        )
        if classification
    ]
    specific_candidate = max(
        specific_candidates,
        key=lambda candidate: ACTION_RANK[str(candidate["decision_action"])],
        default=None,
    )
    stock_price_template = _has(item.title, "成交额") and _has(
        item.title,
        "主力净流入",
        "主力净流出",
        "后市是否有机会",
        "人气排名",
    )
    if stock_price_template and not specific_candidate:
        return _candidate(
            "semiconductor_ai",
            "semiconductor_ordinary",
            "archive",
            item.title,
            "股价行情模板附带的静态业务资料不是新的产业实质变化。",
        )
    if _routine_corporate_attachment(item.title) and not specific_candidate:
        return _candidate(
            "semiconductor_ai",
            "semiconductor_ordinary",
            "archive",
            item.title,
            "审计、估值或资产评估附件不构成新的产业实质变化。",
        )
    if not specific_candidate and _has(
        text, "教程", "经验分享", "leaderboard", "workflow integration", "工具用法"
    ):
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
    if not specific_candidate and _has(
        text, "框架协议", "意向合作", "战略合作", "non-binding", "framework agreement"
    ) and (
        non_execution or not execution
    ):
        return _candidate(
            "semiconductor_ai", "semiconductor_ordinary", "archive", text, "非约束性合作未进入执行阶段。"
        )
    if specific_candidate and specific_candidate["decision_action"] == "push":
        return specific_candidate
    material_change = _corporate_material_change(item, text)
    if material_change:
        quote, reason = material_change
        return _candidate("semiconductor_ai", "semiconductor_material_change", "push", quote, reason)
    commercial_development = _semiconductor_commercial_development(text, config)
    if _is_market_template_title(item.title) and not commercial_development and not specific_candidate:
        return _candidate("semiconductor_ai", "semiconductor_ordinary", "archive", item.title, "市场行情模板不构成新的产业实质变化。")
    hard_variable_change = _semiconductor_hard_variable_change(item, text, config)
    if hard_variable_change and hard_variable_change[0] == "push":
        action, quote, reason = hard_variable_change
        return _candidate("semiconductor_ai", "semiconductor_material_change", action, quote, reason)
    operating_change = _semiconductor_operating_change(item, text, config)
    if operating_change and operating_change[0] == "push":
        action, quote, reason = operating_change
        return _candidate("semiconductor_ai", "semiconductor_material_change", action, quote, reason)
    platform_change = _first_local_match(
        text,
        tuple(config.semiconductor_ai_keywords),
        ("正式发布", "正式推出", "announces", "launches"),
        ("新平台", "新一代", "generation", "platform"),
        ("可用", "量产", "路线", "availability", "production", "roadmap"),
    )
    if platform_change:
        return _candidate(
            "semiconductor_ai", "semiconductor_material_change", "push", platform_change, "正式平台代际及可用性或路线发生变化。"
        )
    supply_constraint = _first_local_match(
        text,
        tuple(config.semiconductor_ai_keywords),
        ("短缺", "缺货", "供不应求", "shortage", "supply tight"),
        (
            "长期合同",
            "全部签约",
            "排队",
            "延期",
            "限流",
            "供货周期",
            "交期",
            "long-term contract",
            "fully booked",
            "lead time",
        ),
    )
    if supply_constraint:
        return _candidate(
            "semiconductor_ai", "ai_compute_constraint", "push", supply_constraint, "供需短缺产生约束性合同或运营后果。"
        )
    price_template_title = _is_market_template_title(item.title)
    title_price_supply = _has(
        item.title,
        "涨价",
        "提价",
        "涨幅",
        "量价",
        "供给缺口",
        "短缺",
        "紧缺",
        "price",
        "shortage",
    )
    price_supply_change = ""
    if title_price_supply and not price_template_title:
        price_supply_change = _first_local_match(
            text,
            tuple(config.semiconductor_ai_keywords),
            ("涨价", "价格上涨", "价格持续上涨", "价格上行", "合约价涨幅", "price increase", "price rise"),
            ("持续", "大幅", "显著", "极度紧缺", "供不应求", "环比", "同比", "%", "％", "sustained", "sharp"),
            ("短缺", "紧缺", "供不应求", "供给", "需求", "涨幅", "价格调查", "shortage", "supply", "demand"),
            asserted_only=True,
        )
    if price_supply_change:
        return _candidate(
            "semiconductor_ai",
            "semiconductor_price_supply_change",
            "push",
            price_supply_change,
            "半导体价格或供需出现持续、显著或有明确幅度的实质变化。",
        )
    forecast_revision = _first_local_near_match(
        text,
        ("上调", "下调", "上修", "下修", "raises", "cuts"),
        ("预测", "指引", "forecast", "guidance"),
    )
    if (
        forecast_revision
        and _matches(forecast_revision, config.semiconductor_ai_keywords)
        and not _has(forecast_revision, *SEMICONDUCTOR_SPECULATIVE_TERMS)
        and not _has(forecast_revision, *SEMICONDUCTOR_NON_EXECUTION_TERMS)
        and not _references_earlier_year(item, forecast_revision)
    ):
        return _candidate(
            "semiconductor_ai", "industry_forecast_revision", "push", forecast_revision, "产业预测或指引发生明确修订。"
        )
    performance_change = _semiconductor_performance_change(item, text, config)
    if performance_change and performance_change[0] == "push":
        action, quote, reason, rule_id = performance_change
        return _candidate("semiconductor_ai", rule_id, action, quote, reason)
    company_supply_confirmation = _first_local_match(
        text,
        ("供货", "供应商", "供应关系", "supplies", "supplier"),
        (
            "公司回答",
            "公司回复",
            "公司表示",
            "公司确认",
            "公司称",
            "回应称",
            "答复称",
            "公告称",
            "公司披露",
            "confirmed",
        ),
        asserted_only=True,
    )
    new_execution = _has(
        company_supply_confirmation,
        "新签",
        "签署",
        "签订",
        "中标",
        "开始供货",
        "首次供货",
        "新增供货",
        "续签",
        "new order",
        "signed",
        "awarded",
    )
    denied_confirmation = _has(
        company_supply_confirmation,
        "未供货",
        "尚未供货",
        "不是供应商",
        "否认",
        "没有供货",
        "not supplying",
    )
    if company_supply_confirmation and not new_execution and not denied_confirmation:
        if _matches(company_supply_confirmation, config.major_semiconductor_customers):
            return _candidate(
                "semiconductor_ai",
                "major_customer_supply_confirmation",
                "push",
                company_supply_confirmation,
                "公司明确确认向全球主要半导体公司供货。",
            )
        return _candidate(
            "semiconductor_ai",
            "semiconductor_ordinary",
            "daily",
            company_supply_confirmation,
            "公司确认已有供货关系，但客户不在全球大厂供货关系名单内。",
        )

    existing_supply_statement = _first_local_match(
        text,
        ("供货", "供应商", "供应关系", "supplies", "supplier"),
        ("稳定供货", "长期供货", "供货名单", "供应商", "批量供货", "stable supply", "supplier list"),
        asserted_only=True,
    )
    if existing_supply_statement and not _has(
        existing_supply_statement,
        "新签",
        "签署",
        "签订",
        "中标",
        "开始供货",
        "首次供货",
        "新增供货",
        "续签",
        "new order",
        "signed",
        "awarded",
    ):
        return _candidate(
            "semiconductor_ai",
            "semiconductor_ordinary",
            "daily",
            existing_supply_statement,
            "文章只描述已有供货关系，未提供公司确认或新的执行动作。",
        )

    order_execution = _first_local_match(
        text,
        ("新签订单", "订单", "供货", "new order", "supply agreement"),
        tuple(config.semiconductor_ai_keywords),
        (
            "新签",
            "签署",
            "签订",
            "中标",
            "开始供货",
            "已供货",
            "稳定供货",
            "批量供货",
            "小批量供货",
            "订单积压",
            "订单排期",
            "订单排到",
            "排单",
            "长期合同",
            "长约",
            "续签",
            "锁定",
            "订单放量",
            "binding",
            "signed",
            "awarded",
            "backlog",
            "booked",
            "supplying",
        ),
        asserted_only=True,
    )
    obtained_order = _first_local_ordered_match(
        text,
        ("获得",),
        ("订单",),
        max_gap=28,
    )
    if obtained_order and _matches(obtained_order, config.semiconductor_ai_keywords):
        order_execution = order_execution or obtained_order
    speculative_order = _has(
        order_execution,
        "预计",
        "有望",
        "可能",
        "预期",
        "能否",
        "将持续",
        "中长期",
        "逐步迈向",
        "后续",
        "will",
    ) and not _has(
        order_execution,
        "新签",
        "签署",
        "签订",
        "中标",
        "开始供货",
        "已供货",
        "稳定供货",
        "批量供货",
        "小批量供货",
        "订单积压",
        "订单排期",
        "订单排到",
        "排单",
        "长期合同",
        "长约",
        "续签",
        "锁定",
        "binding",
        "signed",
        "awarded",
        "backlog",
        "booked",
        "supplying",
    )
    capability_only = _has(order_execution, "供货能力", "供应能力") and not _has(
        order_execution,
        "已向",
        "开始供货",
        "进入",
        "签署",
        "签订",
        "向客户",
    )
    planned_document = _has(item.title, "预案", "可行性分析报告") and not _has(
        order_execution,
        "已经",
        "已向",
        "现已",
        "当前",
        "签署",
        "签订",
    )
    if order_execution and not _has(order_execution, "意向", "框架") and not _has(
        order_execution,
        "尚未",
        "尚无",
        "没有订单",
        "没有新签",
        "未新签",
        "未签署",
        "没有执行证据",
        "订单较少",
        "订单不足",
        "尚未真正形成",
        "未规模化",
        "not yet",
        "no binding order",
    ) and not _has(order_execution, "若", "风险提示") and not capability_only and not planned_document and not speculative_order and not _references_earlier_year(item, order_execution):
        return _candidate(
            "semiconductor_ai", "semiconductor_material_change", "push", order_execution, "订单或供货关系进入执行阶段。"
        )
    cost_route_change = _first_local_match(
        text,
        ("低成本", "成本路线", "cost route", "price war"),
        ("算力需求", "资本开支", "采购", "compute demand", "capex", "procurement"),
    )
    if cost_route_change:
        return _candidate(
            "semiconductor_ai", "semiconductor_material_change", "push", cost_route_change, "成本路线明确改变需求或资本开支方向。"
        )
    credit_constraint = _first_local_match(
        text,
        ("巨额亏损", "信用压力", "cds", "credit stress", "losses"),
        ("采购承诺", "采购约束", "融资约束", "purchase commitment", "procurement constraint"),
    )
    if credit_constraint:
        return _candidate(
            "semiconductor_ai", "ai_credit_constraint", "push", credit_constraint, "信用压力与采购约束在同一主体局部绑定。"
        )
    roadmap_change = _first_local_match(
        text,
        ("推迟", "延后", "路线变化", "delay", "roadmap shift"),
        ("cpo", "gpu", "hbm", "芯片", "量产"),
    )
    if roadmap_change:
        return _candidate(
            "semiconductor_ai", "semiconductor_material_change", "push", roadmap_change, "产业时间表或技术路线明确变化。"
        )
    if specific_candidate:
        return specific_candidate
    if hard_variable_change:
        action, quote, reason = hard_variable_change
        return _candidate("semiconductor_ai", "semiconductor_ordinary", action, quote, reason)
    if operating_change:
        action, quote, reason = operating_change
        return _candidate("semiconductor_ai", "semiconductor_ordinary", action, quote, reason)
    if performance_change:
        action, quote, reason, rule_id = performance_change
        return _candidate("semiconductor_ai", rule_id, action, quote, reason)
    if commercial_development:
        return _candidate(
            "semiconductor_ai",
            "semiconductor_commercial_development",
            "daily",
            commercial_development,
            "半导体/AI商业化、融资或估值出现相关进展，但未达到即时推送条件。",
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


def _macro_candidate(item: NormalizedMarketItem, text: str, config: RuleConfig) -> dict[str, Any]:
    if _has(text, "综述", "回顾", "仅转述", "roundup"):
        return _candidate("macro_data", "macro_indirect_summary", "archive", text, "二次综述不是数据发布。")
    classification = classify_macro_policy_content(
        _classification_item(item),
        primary_keywords=(),
        us_scoped_primary_keywords=config.macro_primary_indicators,
        secondary_keywords=config.macro_secondary_indicators,
        us_context_keywords=config.macro_context_aliases,
    )
    expected_evidence = str(classification.get("expected_evidence") or "")
    surprise_evidence = str(classification.get("surprise_evidence") or "")
    if expected_evidence:
        return _candidate("macro_data", "macro_release_expected", "daily", expected_evidence, "数据符合预期。")
    if classification.get("preview"):
        return _candidate("macro_data", "macro_release_preview", "daily", text, "数据尚未发布。")
    if classification.get("primary") and classification.get("direct_release") and surprise_evidence:
        return _candidate("macro_data", "macro_surprise", "push", surprise_evidence, "核心数据出现明确偏离。")
    if (
        classification.get("secondary")
        and classification.get("direct_release")
        and surprise_evidence
        and classification.get("attributable_market_reaction")
    ):
        return _candidate(
            "macro_data",
            "macro_secondary_reaction",
            "push",
            surprise_evidence,
            "次重点数据偏离并伴随可归因的大幅市场反应。",
        )
    return _candidate("macro_data", "macro_release_expected", "daily", text, "数据相关但未形成可推送偏离。")


def _fed_candidate(item: NormalizedMarketItem, text: str, config: RuleConfig) -> dict[str, Any]:
    allowed_banks = allowed_fed_path_banks(
        alias
        for institution in config.trusted_institutions
        for alias in institution.aliases
    )
    classification = classify_international_bank_fed_path(
        _classification_item(item),
        allowed_banks=allowed_banks,
    )
    if classification:
        return _classification_candidate(
            classification,
            family="fed_policy",
            default_rule_id="fed_path_unchanged",
        )
    transmission = generic_fed_transmission_classification(_classification_item(item))
    if transmission.get("matched"):
        return _candidate(
            "fed_policy",
            "generic_fed_policy_transmission",
            "daily",
            str(transmission.get("evidence_quote") or text),
            "只有常识性的 Fed 政策到资产价格传导解释。",
        )
    transmission_exceptions = tuple(str(value) for value in transmission.get("exceptions") or ())
    if (
        transmission.get("impulse")
        and transmission.get("assets")
        and transmission.get("evidence_quote")
        and transmission_exceptions
    ):
        expected_policy = "policy_decision" in transmission_exceptions and any(
            marker in text.casefold()
            for marker in ("符合预期", "与预期一致", "in line with expectations")
        )
        if expected_policy:
            return _candidate(
                "fed_policy",
                "fed_policy_expected",
                "daily",
                str(transmission.get("evidence_quote") or text),
                "正式政策决定符合预期。",
            )
        return _candidate(
            "fed_policy",
            "fed_policy_material_exception",
            "push",
            str(transmission.get("evidence_quote") or text),
            "包含正式决定、量化重定价、实际行情、直接陈述、更正或资产硬事实。",
        )
    if _has(text, "未说明相对此前", "未证明修订", "没有路径修订", "without a revision"):
        return _candidate("fed_policy", "fed_path_unchanged", "daily", text, "只有当前预测，无法证明路径修订。")
    path_change = _first_local_near_match(
        text,
        ("上调", "下调", "改为", "修订", "raises", "cuts", "revises"),
        ("降息", "加息", "终端利率", "利率路径", "rate path", "terminal rate"),
    )
    if path_change:
        return _candidate("fed_policy", "fed_path_change", "push", path_change, "利率路径发生明确修订。")
    if _has(text, "维持预测", "重申", "符合预期", "unchanged", "reiterates"):
        return _candidate("fed_policy", "fed_path_unchanged", "daily", text, "既有立场或路径没有变化。")
    if _has(text, "偏鹰", "偏鸽", "强调通胀", "hawkish", "dovish"):
        return _candidate(
            "fed_policy", "fed_official_stance_change", "daily", text, "无法核验相对既有立场发生变化。"
        )
    if _has(text, "会面", "称赞", "工作组"):
        return _candidate("fed_policy", "fed_policy_non_material", "archive", text, "没有政策路径证据。")
    return _candidate("fed_policy", "fed_path_unchanged", "daily", text, "Fed 内容未证明路径变化。")


def _trade_candidate(item: NormalizedMarketItem, text: str, config: RuleConfig) -> dict[str, Any]:
    if _has(text, "终止", "撤销", "豁免", "缓和", "terminate", "withdraw", "exemption"):
        return _candidate("trade_policy", "trade_deescalation", "daily", text, "政策发生有效缓和或撤销。")
    classification = classify_trade_friction(_classification_item(item))
    if classification:
        action = str(classification.get("decision_action") or "archive")
        if action == "daily":
            return _classification_candidate(
                classification,
                family="trade_policy",
                default_rule_id="trade_distant_or_unproven",
            )
        evidence = classification.get("evidence") or []
        focus_terms = (*config.trade_focus_industries, *config.semiconductor_ai_keywords)
        focus_quote = next(
            (str(quote) for quote in evidence if _matches(str(quote), focus_terms)),
            "",
        )
        if not focus_quote:
            focus_sector = next(
                (
                    str(sector)
                    for sector in classification.get("affected_sectors") or []
                    if _matches(str(sector), config.trade_focus_industries)
                ),
                "",
            )
            if focus_sector:
                focus_quote = str(evidence[0]) if evidence else focus_sector
        if focus_quote:
            return _candidate(
                "trade_policy",
                "trade_friction_escalation",
                "push",
                focus_quote,
                str(classification.get("reason") or "关注产业贸易措施发生明确升级。"),
            )
        return _candidate(
            "trade_policy",
            "trade_distant_or_unproven",
            "archive",
            str(evidence[0]) if evidence else text,
            "正式贸易措施与关注产业距离较远。",
        )
    escalation = _first_local_match(
        text,
        tuple(config.trade_focus_industries),
        tuple(config.trade_stages),
    )
    if escalation:
        return _candidate("trade_policy", "trade_escalation", "push", escalation, "关注产业贸易措施进入正式升级阶段。")
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
    rating_candidate = _investment_bank_rating_candidate(item, text, rule_config, portfolio)
    allocation_candidates = _investment_bank_allocation_candidates(item, text, rule_config, portfolio)
    for family in admission.matched_families:
        if family == "holding":
            family_candidates = [_holding_candidate(item, text, admission, portfolio)]
            if rating_candidate:
                family_candidates.insert(0, rating_candidate)
        elif family == "semiconductor_ai":
            semiconductor_candidate = _semiconductor_candidate(item, text, rule_config)
            family_candidates = [
                _attach_trusted_research_evidence(
                    semiconductor_candidate,
                    item,
                    text,
                    rule_config,
                )
            ]
        elif family == "macro_data":
            family_candidates = [_macro_candidate(item, text, rule_config)]
        elif family == "fed_policy":
            family_candidates = [_fed_candidate(item, text, rule_config)]
        elif family == "trade_policy":
            family_candidates = [_trade_candidate(item, text, rule_config)]
        else:
            continue
        if family in allocation_candidates:
            family_candidates.append(allocation_candidates[family])
        candidates.append(
            max(
                family_candidates,
                key=lambda candidate: ACTION_RANK[str(candidate["decision_action"])],
            )
        )
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
