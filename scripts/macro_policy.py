"""Macro liquidity and Fed-policy relevance rules.

This line is separate from semiconductor industry hard-variable monitoring.
It focuses on US monetary-policy expectations and market liquidity shocks
that can affect A-share risk appetite, growth-stock valuation, FX, and rates.
"""

from __future__ import annotations

import re
from typing import Any

from rule_center import effective_list, rule_enabled


PRIMARY_DATA_KEYWORDS = (
    "非农",
    "nonfarm",
    "payroll",
    "nfp",
)

US_SCOPED_PRIMARY_DATA_KEYWORDS = (
    "cpi",
    "消费者价格指数",
    "pce",
    "个人消费支出",
    "核心pce",
    "core pce",
)

US_CONTEXT_KEYWORDS = (
    "美国",
    "美國",
    "美联储",
    "美聯儲",
    "联储",
    "federal reserve",
    "fed",
    "fomc",
    "warsh",
    "沃什",
    "沃尔什",
    "powell",
    "鲍威尔",
    "美债",
    "美元",
    "dxy",
    "u.s.",
    "us ",
    "usa",
    "united states",
    "treasury",
)

FED_EVENT_KEYWORDS = (
    "美联储",
    "联储",
    "federal reserve",
    "fed",
    "fomc",
    "沃什",
    "沃尔什",
    "warsh",
    "鲍威尔",
    "powell",
    "主席讲话",
    "议息",
    "会议纪要",
    "点阵图",
    "降息",
    "加息",
    "利率路径",
)

FED_OFFICIAL_EVENT_KEYWORDS = (
    "fomc",
    "沃什",
    "沃尔什",
    "warsh",
    "鲍威尔",
    "powell",
    "主席讲话",
    "票委",
    "理事",
    "议息",
    "会议纪要",
    "点阵图",
    "利率决议",
    "press conference",
    "minutes",
    "dot plot",
)

SECONDARY_DATA_KEYWORDS = (
    "adp",
    "jolts",
    "职位空缺",
    "初请",
    "续请",
    "ppi",
    "生产者价格指数",
    "ism",
    "制造业pmi",
    "服务业pmi",
)

IGNORED_DATA_KEYWORDS = (
    "零售销售",
    "retail sales",
)

MARKET_REACTION_KEYWORDS = (
    "2年期美债",
    "二年期美债",
    "两年期美债",
    "10年期美债",
    "十年期美债",
    "美债收益率",
    "treasury yield",
    "ust yield",
    "dxy",
    "美元指数",
    "美元走强",
    "美元走弱",
    "纳指期货",
    "标普期货",
    "黄金",
    "人民币",
    "离岸人民币",
    "risk appetite",
    "风险偏好",
)

LARGE_MOVE_PATTERNS = (
    r"(?:大跌|大涨|跳水|飙升|急跌|急升|重挫|拉升|明显下行|明显上行|创.*新低|创.*新高)",
    r"(?:下跌|上涨|回落|上行|下行).{0,12}(?:\d+(?:\.\d+)?\s*(?:bp|基点|个基点|%|％))",
    r"(?:\d+(?:\.\d+)?\s*(?:bp|基点|个基点)).{0,12}(?:下跌|上涨|回落|上行|下行)",
)

SURPRISE_PATTERNS = (
    r"(?:高于|低于|不及|超过|逊于|强于|弱于).{0,12}(?:预期|市场预期)",
    r"(?:预期|市场预期).{0,12}(?:高于|低于|不及|超过|逊于|强于|弱于)",
    r"(?:意外|超预期|不及预期|大幅偏离|显著偏离)",
)

FED_EASING_MARKERS = (
    "降息",
    "货币宽松",
    "寬鬆",
    "鸽派",
    "鴿派",
    "利率下降",
    "利率下行",
    "rate cut",
    "lower rates",
    "monetary easing",
    "dovish",
)

FED_TIGHTENING_MARKERS = (
    "加息",
    "货币收紧",
    "貨幣收緊",
    "鹰派",
    "鷹派",
    "利率上升",
    "利率上行",
    "rate hike",
    "higher rates",
    "monetary tightening",
    "hawkish",
)

TRANSMISSION_ASSET_MARKERS = (
    "黄金",
    "黃金",
    "金价",
    "金價",
    "白银",
    "白銀",
    "比特币",
    "比特幣",
    "以太坊",
    "数字货币",
    "數字貨幣",
    "加密货币",
    "加密貨幣",
    "美元",
    "非美货币",
    "非美貨幣",
    "人民币",
    "人民幣",
    "有色金属",
    "有色金屬",
    "贵金属",
    "貴金屬",
    "工业金属",
    "工業金屬",
    "大宗商品",
    "美债",
    "美債",
    "股票",
    "股市",
    "gold",
    "silver",
    "bitcoin",
    "crypto",
    "dollar",
    "currencies",
    "metals",
    "commodities",
    "treasuries",
    "equities",
)

GENERIC_TRANSMISSION_PATTERNS = (
    r"(?:降息|宽松|寬鬆|鸽派|鴿派|利率(?:下降|下行)).{0,36}(?:利好|受益|有利于|有利於|提振|支撑|支撐|推动|推動|助推|承压|承壓|利空)",
    r"(?:利好|受益|有利于|有利於|提振|支撑|支撐|推动|推動|助推|承压|承壓|利空).{0,36}(?:降息|宽松|寬鬆|鸽派|鴿派|利率(?:下降|下行))",
    r"(?:rate cuts?|lower rates|monetary easing|dovish).{0,48}(?:benefit|boost|support|bullish|tailwind|weigh on)",
)

POLICY_DECISION_PATTERNS = (
    r"(?:宣布|决定|決定|投票|实施|實施|正式).{0,24}(?:降息|加息|下调.{0,8}利率|下調.{0,8}利率|上调.{0,8}利率|上調.{0,8}利率)",
    r"(?:降息|加息|下调.{0,8}利率|下調.{0,8}利率|上调.{0,8}利率|上調.{0,8}利率).{0,24}(?:个基点|個基點|基点|基點|bp|bps)",
    r"(?:announced|decided|voted|implemented).{0,32}(?:rate cut|rate hike|lowered rates|raised rates)",
)

QUANTIFIED_REPRICING_PATTERNS = (
    r"(?:降息|加息|利率).{0,40}(?:概率|機率|几率|幾率|可能性).{0,16}\d+(?:\.\d+)?\s*[%％]",
    r"\d+(?:\.\d+)?\s*[%％].{0,16}(?:概率|機率|几率|幾率|可能性).{0,40}(?:降息|加息|利率)",
    r"(?:降息|加息).{0,28}\d+(?:\.\d+)?\s*(?:次|个基点|個基點|基点|基點|bp|bps)",
    r"(?:押注|预期|預期|定价|定價).{0,32}(?:上调|上調|下调|下調|推迟至|推遲至|提前至).{0,24}(?:降息|加息|利率|\d)",
    r"(?:cut|hike).{0,28}\d+(?:\.\d+)?\s*(?:times?|bp|bps|%)",
)

OBSERVED_ASSET_MOVE_PATTERNS = (
    r"(?:上涨|上漲|下跌|涨超|漲超|跌超|走高|走低|跳涨|跳漲|跳水|拉升|回落|涨至|漲至|升至|跌至).{0,18}\d+(?:\.\d+)?\s*(?:[%％]|美元|点|點|个基点|個基點|基点|基點|bp|bps)",
    r"\d+(?:\.\d+)?\s*(?:[%％]|美元|点|點|个基点|個基點|基点|基點|bp|bps).{0,18}(?:上涨|上漲|下跌|涨|漲|跌|走高|走低|拉升|回落)",
    r"(?:rose|fell|gained|lost|rallied|slid).{0,18}\d+(?:\.\d+)?\s*(?:%|dollars?|points?|bp|bps)",
)

DIRECT_FED_STATEMENT_PATTERN = re.compile(
    r"(?:沃什|沃尔什|沃爾什|沃勒|鲍威尔|鮑威爾|美联储主席|美聯儲主席|美联储理事|美聯儲理事|Fed (?:chair|governor))"
    r".{0,24}(?:[:：]|表示|称|稱|指出|强调|強調|重申|警告|认为|認為|said|stated|warned|testified)",
    re.IGNORECASE,
)

ASSET_HARD_FACT_MARKERS = (
    "央行购金",
    "央行購金",
    "etf流入",
    "etf 流入",
    "etf增持",
    "etf 增持",
    "资金流入",
    "資金流入",
    "库存下降",
    "庫存下降",
    "供应中断",
    "供應中斷",
    "矿山停产",
    "礦山停產",
    "制裁",
    "central bank buying",
    "etf inflow",
    "supply disruption",
    "mine closure",
)


def text_blob(*values: Any) -> str:
    return " ".join(str(value or "") for value in values)


def contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def has_large_move(text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in LARGE_MOVE_PATTERNS)


def has_surprise(text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in SURPRISE_PATTERNS)


def fed_policy_impulse(text: str) -> str:
    easing = contains_any(text, FED_EASING_MARKERS)
    tightening = contains_any(text, FED_TIGHTENING_MARKERS)
    if easing and tightening:
        return ""
    if easing:
        return "easing"
    if tightening:
        return "tightening"
    return ""


def generic_fed_transmission_classification(item: dict[str, Any]) -> dict[str, Any]:
    """Classify obvious policy-to-asset explanations using only local evidence."""
    text = text_blob(item.get("title"), item.get("summary"), item.get("content"), item.get("full_text"))
    impulse = fed_policy_impulse(text)
    assets = [marker for marker in TRANSMISSION_ASSET_MARKERS if marker.casefold() in text.casefold()]
    relationship = next(
        (match.group(0) for pattern in GENERIC_TRANSMISSION_PATTERNS if (match := re.search(pattern, text, re.IGNORECASE))),
        "",
    )
    exceptions: list[str] = []
    checks = (
        ("policy_decision", POLICY_DECISION_PATTERNS),
        ("quantified_repricing", QUANTIFIED_REPRICING_PATTERNS),
        ("observed_asset_move", OBSERVED_ASSET_MOVE_PATTERNS),
    )
    for name, patterns in checks:
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
            exceptions.append(name)
    if DIRECT_FED_STATEMENT_PATTERN.search(text):
        exceptions.append("direct_fed_statement")
    if contains_any(text, ASSET_HARD_FACT_MARKERS):
        exceptions.append("asset_hard_fact")
    if contains_any(text, ("更正", "修正", "误报", "誤報", "correction", "corrected")):
        exceptions.append("correction")
    if re.search(r"(?:但|却|卻|反而|不涨反跌|不漲反跌|despite|even as).{0,36}(?:上涨|上漲|下跌|走高|走低|走强|走強|走弱|涨|漲|跌|rose|fell)", text, re.IGNORECASE):
        exceptions.append("unexpected_relationship")
    return {
        "matched": bool(impulse and assets and relationship and not exceptions),
        "impulse": impulse,
        "assets": list(dict.fromkeys(assets))[:8],
        "evidence_quote": relationship[:500],
        "exceptions": exceptions,
    }


def is_retail_sales_only(text: str) -> bool:
    return contains_any(text, IGNORED_DATA_KEYWORDS) and not (
        contains_any(text, PRIMARY_DATA_KEYWORDS)
        or contains_any(text, US_SCOPED_PRIMARY_DATA_KEYWORDS)
        or contains_any(text, FED_EVENT_KEYWORDS)
        or contains_any(text, MARKET_REACTION_KEYWORDS)
    )


def has_us_context(text: str) -> bool:
    padded = f"{text.lower()} "
    return any(keyword.lower() in padded for keyword in US_CONTEXT_KEYWORDS)


def primary_data_match(text: str) -> bool:
    primary_keywords = effective_list("macro_policy_line", "extra_primary_keywords", PRIMARY_DATA_KEYWORDS)
    us_scoped_primary = effective_list("macro_policy_line", "extra_primary_keywords", US_SCOPED_PRIMARY_DATA_KEYWORDS)
    if contains_any(text, primary_keywords):
        return True
    return contains_any(text, us_scoped_primary) and has_us_context(text)


def fed_event_match(text: str, *, market_reaction: bool, large_move: bool, surprise: bool) -> bool:
    if not contains_any(text, FED_EVENT_KEYWORDS):
        return False
    if contains_any(text, FED_OFFICIAL_EVENT_KEYWORDS):
        return True
    if has_us_context(text) and (market_reaction or large_move or surprise):
        return True
    return False


def macro_policy_match(item: dict[str, Any]) -> dict[str, Any]:
    if not rule_enabled("macro_policy_line"):
        return {"matched": False, "tier": "disabled", "reason": "宏观政策线规则已停用。"}
    text = text_blob(item.get("title"), item.get("summary"), item.get("content"), item.get("full_text"))
    if is_retail_sales_only(text):
        return {"matched": False, "tier": "ignored", "reason": "零售销售不纳入宏观政策线。"}

    primary = primary_data_match(text)
    secondary = contains_any(text, effective_list("macro_policy_line", "extra_secondary_keywords", SECONDARY_DATA_KEYWORDS))
    market_reaction = contains_any(text, MARKET_REACTION_KEYWORDS)
    large_move = has_large_move(text)
    surprise = has_surprise(text)
    fed_event = fed_event_match(text, market_reaction=market_reaction, large_move=large_move, surprise=surprise)

    if primary or fed_event:
        return {
            "matched": True,
            "tier": "primary",
            "push_bias": "high",
            "reason": "命中美联储/FOMC/现任主席沃什、前主席鲍威尔相关报道，或非农、CPI、PCE 等核心宏观事件。",
            "tags": [
                tag
                for tag, ok in (
                    ("primary_data", primary),
                    ("fed_event", fed_event),
                    ("market_reaction", market_reaction),
                    ("large_move", large_move),
                    ("surprise", surprise),
                )
                if ok
            ],
        }
    if secondary and (large_move or surprise or market_reaction):
        return {
            "matched": True,
            "tier": "secondary_major",
            "push_bias": "conditional",
            "reason": "命中 ADP/JOLTS/初请/PPI/ISM 等次重点数据，且伴随重大偏离或市场反应。",
            "tags": [
                tag
                for tag, ok in (
                    ("secondary_data", secondary),
                    ("market_reaction", market_reaction),
                    ("large_move", large_move),
                    ("surprise", surprise),
                )
                if ok
            ],
        }
    if market_reaction and large_move:
        return {
            "matched": True,
            "tier": "market_reaction",
            "push_bias": "conditional",
            "reason": "美债收益率、美元或主要风险资产出现明显波动，可能影响 A 股风险偏好。",
            "tags": ["market_reaction", "large_move"],
        }
    return {"matched": False, "tier": "", "reason": ""}


def macro_prompt_note(item: dict[str, Any]) -> str:
    match = macro_policy_match(item)
    if not match.get("matched"):
        return ""
    return (
        "宏观流动性/美联储政策线提示："
        f"{match.get('reason')} 重点判断其对美债收益率、美元、纳指期货、人民币、"
        "A 股风险偏好、成长股/半导体估值的影响；区分偏鸽利好、衰退恐慌、事件前避险和已定价。"
    )


def apply_macro_review_override(review: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    match = macro_policy_match(item)
    if not match.get("matched"):
        return review
    updated = dict(review)
    if match.get("tier") == "primary":
        updated["importance"] = "high"
        updated["push_now"] = True
    elif str(updated.get("importance") or "").lower() == "high":
        updated["push_now"] = True
    updated["macro_policy_line"] = match
    targets = list(updated.get("affected_targets") or [])
    for target in ("美债收益率/美元", "A股风险偏好", "成长股估值"):
        if target not in targets:
            targets.append(target)
    updated["affected_targets"] = targets[:5]
    note = (
        "宏观政策线覆盖：该条涉及美联储/FOMC/主席沃什、前主席鲍威尔、非农/CPI/PCE，"
        "或次重点数据的重大偏离/市场反应；按对 A 股风险偏好和成长股估值的影响优先处理。"
    )
    reason = str(updated.get("reason") or "").strip()
    if note not in reason:
        updated["reason"] = f"{reason}\n{note}".strip()
    raw = dict(updated.get("raw") or {})
    raw["macro_policy_line"] = match
    updated["raw"] = raw
    return updated


def is_macro_event(item: dict[str, Any]) -> bool:
    return bool(macro_policy_match(item).get("matched"))
