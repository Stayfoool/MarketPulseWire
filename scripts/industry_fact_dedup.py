"""Delivery-only identities for bounded, repeated industry facts."""

from __future__ import annotations

import re
from typing import Any, Callable

from market_item import DecisionResult


INDUSTRY_FACT_RULE_ID = "industry_fact_dedup"
INDUSTRY_FACT_LOOKBACK_MINUTES = 36 * 60
INDUSTRY_HARDLINE_RULE_ID = "industry_quantified_hardline"

IBM_PATTERN = re.compile(r"(?<![a-z0-9])ibm(?![a-z0-9])", re.IGNORECASE)
COREWEAVE_PATTERN = re.compile(r"(?<![a-z0-9])(?:coreweave|crwv(?:\.o)?)(?![a-z0-9])", re.IGNORECASE)

CORRECTION_MARKERS = ("更正", "修正", "纠正", "此前误报", "correction", "corrected", "revised from")
OFFICIAL_RESPONSE_PATTERNS = (
    re.compile(r"(?:coreweave|公司|官方).{0,20}(?:确认|证实|否认|回应|澄清)", re.IGNORECASE),
    re.compile(r"coreweave.{0,24}(?:confirm\w*|den\w*|respond\w*|clarif\w*)", re.IGNORECASE),
    re.compile(r"(?:company|official)\s+(?:confirmation|denial|response|statement)", re.IGNORECASE),
)

CHINESE_SPENDING_SHIFT_PATTERNS = (
    re.compile(
        r"(?:客户|企业|企业IT).{0,40}(?:支出|资本开支|预算).{0,48}"
        r"(?:转向|转移|倾斜|优先).{0,48}(?:服务器|硬件|芯片|存储|内存)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:支出|资本开支|预算).{0,48}(?:转向|转移|倾斜|优先).{0,48}"
        r"(?:服务器|硬件|芯片|存储|内存).{0,48}(?:采购|供应|需求)",
        re.IGNORECASE,
    ),
)
ENGLISH_SPENDING_SHIFT_PATTERNS = (
    re.compile(
        r"(?:customers?|enterprises?|enterprise\s+it).{0,64}(?:spending|capex|budgets?).{0,64}"
        r"(?:shift\w*|mov\w*|redirect\w*|divert\w*|prioriti[sz]\w*).{0,64}"
        r"(?:hardware|servers?|chips?|storage|memory)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:memory|storage).{0,40}(?:shortage|tightness|supply constraint).{0,64}"
        r"(?:shift\w*|mov\w*|redirect\w*|divert\w*).{0,64}(?:enterprise|customer|spending|software)",
        re.IGNORECASE,
    ),
)
MEMORY_HARDWARE_MARKERS = (
    "hbm",
    "dram",
    "nand",
    "memory",
    "storage",
    "server",
    "chip",
    "存储",
    "内存",
    "服务器",
    "芯片",
    "硬件",
)
MEMORY_SUPPLIERS = (
    "sk hynix",
    "海力士",
    "micron",
    "美光",
    "samsung",
    "三星",
    "kioxia",
    "铠侠",
    "sandisk",
    "闪迪",
    "长鑫",
    "长江存储",
)
PRODUCT_SPECIFIC_MARKERS = ("hbm", "dram", "nand")
SUPPLY_ACTION_MARKERS = (
    "量产",
    "出货",
    "投产",
    "良率",
    "产能爬坡",
    "mass production",
    "shipments",
    "production ramp",
    "yield",
)

HEDGE_EXPLORATION_MARKERS = (
    "正在探索",
    "探索使用",
    "探讨",
    "考虑使用",
    "考虑通过",
    "潜在对冲",
    "explor",
    "consider",
    "evaluat",
    "potential hedge",
)
HEDGE_INSTRUMENT_MARKERS = (
    "金融衍生品",
    "衍生品",
    "看跌期权",
    "期权",
    "derivative",
    "put option",
)
HEDGE_PURPOSE_MARKERS = ("对冲", "防范", "保护", "hedg", "protect against")
STORAGE_CHIP_MARKERS = (
    "存储芯片",
    "内存芯片",
    "memory chip",
    "memory prices",
    "storage chip",
)
PRICE_DOWNSIDE_MARKERS = (
    "价格下跌",
    "价格下降",
    "价格下行",
    "跌价",
    "price decline",
    "price drop",
    "prices fall",
    "prices falling",
    "downside",
)
PRICE_DOWNSIDE_PATTERNS = (
    re.compile(
        r"(?:decline|drop|fall|downside).{0,32}(?:memory|storage).{0,20}(?:chip\s+)?prices?",
        re.IGNORECASE,
    ),
)
HEDGE_EXECUTION_MARKERS = (
    "已使用",
    "已买入",
    "已购入",
    "已建立",
    "已执行",
    "已签署",
    "正式采用",
    "entered into",
    "executed",
    "purchased",
    "bought put",
    "established a hedge",
)
HEDGE_MATERIAL_TERM_MARKERS = (
    "名义金额",
    "执行价格",
    "行权价",
    "到期日",
    "交易对手",
    "notional",
    "strike price",
    "expiration date",
    "maturity date",
    "counterparty",
)


def _text(item: dict[str, Any]) -> str:
    return " ".join(
        " ".join(str(item.get(key) or "").split())
        for key in ("title", "summary", "content", "full_text")
        if item.get(key)
    )


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(marker.casefold() in lowered for marker in markers)


def _matches_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _has_industry_rule(decision: DecisionResult) -> bool:
    return any(hit.get("rule_id") == INDUSTRY_HARDLINE_RULE_ID for hit in decision.rule_hits)


def _has_independent_memory_supply_fact(text: str) -> bool:
    return (
        _contains_any(text, MEMORY_SUPPLIERS)
        and _contains_any(text, PRODUCT_SPECIFIC_MARKERS)
        and _contains_any(text, SUPPLY_ACTION_MARKERS)
    )


def _identity(*, key: str, facts: dict[str, str]) -> dict[str, Any]:
    return {
        "rule_id": INDUSTRY_FACT_RULE_ID,
        "dedup_key": f"industry_fact:{key}",
        "dedup_lookback_minutes": INDUSTRY_FACT_LOOKBACK_MINUTES,
        "dedup_kind": "industry_fact",
        "event_facts": facts,
    }


def _ibm_spending_shift_fact(text: str) -> dict[str, Any] | None:
    if not IBM_PATTERN.search(text) or not _contains_any(text, MEMORY_HARDWARE_MARKERS):
        return None
    if _contains_any(text, CORRECTION_MARKERS) or _has_independent_memory_supply_fact(text):
        return None
    patterns = (*CHINESE_SPENDING_SHIFT_PATTERNS, *ENGLISH_SPENDING_SHIFT_PATTERNS)
    if not _matches_any(text, patterns):
        return None
    return _identity(
        key="ibm:enterprise_spending_shift:memory_hardware",
        facts={
            "subject": "IBM",
            "event_type": "enterprise_spending_shift",
            "stage": "reported",
            "object": "memory_hardware",
            "direction": "toward",
        },
    )


def _coreweave_storage_hedge_fact(text: str) -> dict[str, Any] | None:
    if not COREWEAVE_PATTERN.search(text):
        return None
    required_marker_groups = (
        HEDGE_EXPLORATION_MARKERS,
        HEDGE_INSTRUMENT_MARKERS,
        HEDGE_PURPOSE_MARKERS,
        STORAGE_CHIP_MARKERS,
    )
    if not all(_contains_any(text, markers) for markers in required_marker_groups):
        return None
    if not (_contains_any(text, PRICE_DOWNSIDE_MARKERS) or _matches_any(text, PRICE_DOWNSIDE_PATTERNS)):
        return None
    if (
        _contains_any(text, CORRECTION_MARKERS)
        or _matches_any(text, OFFICIAL_RESPONSE_PATTERNS)
        or _contains_any(text, HEDGE_EXECUTION_MARKERS)
        or _contains_any(text, HEDGE_MATERIAL_TERM_MARKERS)
    ):
        return None
    return _identity(
        key="coreweave:price_risk_hedge:exploring:storage_chip:down",
        facts={
            "subject": "CoreWeave",
            "event_type": "price_risk_hedge",
            "stage": "exploring",
            "instrument": "derivatives",
            "object": "storage_chip_price",
            "direction": "down",
        },
    )


FACT_EXTRACTORS: tuple[Callable[[str], dict[str, Any] | None], ...] = (
    _ibm_spending_shift_fact,
    _coreweave_storage_hedge_fact,
)


def industry_fact_dedup_hit(item: dict[str, Any], decision: DecisionResult) -> dict[str, Any] | None:
    """Return a conservative delivery identity after an industry push decision."""
    if not decision.should_push or not _has_industry_rule(decision):
        return None
    text = _text(item)
    if not text:
        return None
    for extractor in FACT_EXTRACTORS:
        fact = extractor(text)
        if fact:
            return fact
    return None
