"""Source-neutral topic and hard-variable rules for market content."""

from __future__ import annotations

import re
from typing import Any, Iterable

from rule_center import effective_list, rule_enabled


RULE_ID = "industry_quantified_hardline"

INDUSTRY_TOPICS: dict[str, tuple[str, ...]] = {
    "AI基础设施": (
        "ai infrastructure",
        "ai基础设施",
        "ai 基础设施",
        "ai",
        "ai server",
        "ai servers",
        "ai服务器",
        "gpu",
        "gpus",
        "accelerator",
        "accelerators",
        "inference",
        "rag",
        "data center",
        "datacenter",
        "算力",
        "数据中心",
    ),
    "半导体": (
        "semiconductor",
        "semiconductors",
        "chip",
        "chips",
        "asic",
        "asics",
        "foundry",
        "wafer",
        "wafers",
        "fab",
        "fabs",
        "晶圆",
        "晶圆代工",
        "芯片",
        "半导体",
    ),
    "存储/HBM": (
        "hbm",
        "dram",
        "nand",
        "flash memory",
        "memory",
        "ssd",
        "存储",
        "内存",
        "闪存",
    ),
    "先进封装/测试": (
        "advanced packaging",
        "cowos",
        "hybrid bonding",
        "probe card",
        "chiplet",
        "先进封装",
        "混合键合",
        "探针卡",
        "封装测试",
    ),
    "光互联/CPO": (
        "cpo",
        "co-packaged optics",
        "photonics",
        "photonic integrated circuit",
        "optical interconnect",
        "pic capacity",
        "fau",
        "glass bridge",
        "光互联",
        "光通信",
        "硅光",
        "光子集成电路",
    ),
    "PCB/电子制造": (
        "pcb",
        "ccl",
        "package substrate",
        "glass substrate",
        "odm",
        "覆铜板",
        "玻璃基板",
        "电子制造",
    ),
    "半导体设备/材料": (
        "semiconductor equipment",
        "front-end equipment",
        "fab equipment",
        "semiconductor material",
        "photoresist",
        "helium",
        "molybdenum",
        "tungsten",
        "半导体设备",
        "半导体材料",
        "光刻胶",
        "电子特气",
        "钼",
        "钨",
    ),
    "机器人": (
        "humanoid robot",
        "robotics",
        "optimus",
        "harmonic reducer",
        "人形机器人",
        "机器人",
        "谐波减速器",
    ),
    "数据中心电力/散热": (
        "gas turbine",
        "power grid",
        "liquid cooling",
        "data center power",
        "燃气轮机",
        "电网",
        "液冷",
        "数据中心电力",
    ),
}

HARD_VARIABLES: dict[str, tuple[str, ...]] = {
    "供需缺口/瓶颈": (
        "structural shortage",
        "supply shortage",
        "supply gap",
        "supply constraint",
        "capacity constraint",
        "bottleneck",
        "shortage",
        "紧缺",
        "短缺",
        "供需缺口",
        "供应缺口",
        "供应瓶颈",
        "产能瓶颈",
    ),
    "价格": (
        "price hike",
        "price increase",
        "price cut",
        "pricing power",
        "prices to double",
        "价格翻倍",
        "涨价",
        "提价",
        "价格上调",
        "降价",
        "价格下调",
    ),
    "产能/产量": (
        "capacity expansion",
        "capacity cut",
        "production ramp",
        "mass production",
        "scaling to",
        "output increase",
        "output cut",
        "扩产",
        "产能扩张",
        "新增产能",
        "减产",
        "停产",
        "投产",
        "量产",
        "产量提升",
    ),
    "资本开支/投资": (
        "capital expenditure",
        "capex",
        "fab investment",
        "equipment investment",
        "emergency investment",
        "funding round",
        "资本开支",
        "设备投资",
        "工厂投资",
        "紧急投资",
        "融资额",
    ),
    "订单/采购": (
        "purchase order",
        "procurement",
        "supply agreement",
        "order backlog",
        "secured its first order",
        "订单",
        "采购",
        "供货协议",
        "中标",
        "定点",
        "客户认证",
    ),
    "出货/交付": (
        "shipment forecast",
        "shipment guidance",
        "delivery cycle",
        "delivery time",
        "started shipments",
        "出货指引",
        "出货预测",
        "交付周期",
        "交付时间",
        "开始出货",
    ),
    "需求": (
        "demand surge",
        "demand decline",
        "demand contraction",
        "volume contraction",
        "需求激增",
        "需求下滑",
        "需求收缩",
        "销量收缩",
    ),
    "监管/贸易": (
        "export control",
        "tariff exception",
        "tariff exemption",
        "trade restriction",
        "sanction",
        "出口管制",
        "关税豁免",
        "贸易限制",
        "制裁",
        "禁令",
    ),
    "时间表/技术路线": (
        "delayed until",
        "delayed to",
        "delay the implementation",
        "roadmap shift",
        "shift from",
        "shift to",
        "replace tungsten",
        "bypass nvidia",
        "custom asic",
        "推迟至",
        "延期至",
        "延后至",
        "技术路线",
        "切换至",
        "替代",
        "绕过",
    ),
    "业绩/市场规模": (
        "revenue forecast",
        "profit forecast",
        "gross margin",
        "market size",
        "annual recurring revenue",
        "arr",
        "业绩指引",
        "收入指引",
        "利润指引",
        "毛利率",
        "市场规模",
    ),
    "预测调整": (
        "raises forecast",
        "raised forecast",
        "cuts forecast",
        "cut forecast",
        "revised up",
        "revised down",
        "上调预测",
        "下调预测",
        "上修指引",
        "下修指引",
    ),
}

HARD_VARIABLE_PATTERNS: dict[str, tuple[str, ...]] = {
    "价格": (
        r"prices?.{0,30}(?:hike|increase|rise|double|cut|decline)",
        r"(?:hike|increase|raise|double|cut).{0,30}prices?",
        r"价格.{0,20}(?:上涨|上调|翻倍|下跌|下调)",
        r"(?:上涨|上调|翻倍|下跌|下调).{0,20}价格",
    ),
    "产能/产量": (
        r"(?:expand|expansion|increase|grow|growth|scale|scaling|ramp|cut|reduce).{0,40}(?:capacity|output|production)",
        r"(?:capacity|output|production).{0,40}(?:expand|expansion|increase|grow|growth|scale|scaling|ramp|cut|reduce|target|double|\d)",
        r"(?:扩张|扩产|增加|提升|增长|削减|减少|翻倍).{0,24}(?:产能|产量|生产)",
        r"(?:产能|产量|生产).{0,24}(?:扩张|扩产|增加|提升|增长|削减|减少|翻倍|目标|\d)",
    ),
    "资本开支/投资": (
        r"(?:additional|planned|plans? to|will|emergency).{0,36}(?:investment|funding)",
        r"(?:investment|funding|financing|capital raise).{0,24}(?:[$¥￥]\s*)?\d",
        r"(?:[$¥￥]\s*)?\d[\d,.]*\s*(?:billion|million|bn|[bm](?![a-z])|亿|万).{0,24}(?:investment|funding|融资)",
        r"raise[sd]?\s+(?:[$¥￥]\s*)?\d[\d,.]*\s*(?:billion|million|bn|[bm](?![a-z])).{0,30}(?:adr|offering|financing)",
        r"(?:追加|新增|拟|计划|将|完成).{0,36}(?:投资(?!者)|融资)",
        r"(?:投资(?!者)|融资).{0,20}\d",
    ),
    "预测调整": (
        r"(?:raise|raises|raised|cut|cuts|revised|lowered).{0,48}(?:forecast|guidance|estimate|projection)",
        r"(?:forecast|guidance|estimate|projection).{0,48}(?:raise|increase|cut|lower|double)",
        r"(?:上调|下调|上修|下修).{0,30}(?:预测|预期|指引)",
        r"(?:预测|预期|指引).{0,30}(?:上调|下调|上修|下修|翻倍)",
    ),
}

QUANTIFIED_PATTERNS = (
    r"(?:[$¥￥]\s*)?\d[\d,]*(?:\.\d+)?\s*(?:%|％|x|倍|billion|bn|million|trillion|[bm](?![a-z])|亿|万亿|兆)",
    r"\d[\d,]*(?:\.\d+)?\s*(?:wafers?|units?|tools?|台|套|条|座|片|个月|年)",
    r"20\d{2}",
)


def collect_hardline_text(*values: object) -> str:
    return " ".join(str(value or "") for value in values)


def normalize_visible_text(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|tr|h[1-6])>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def item_text(item: dict[str, Any]) -> str:
    values = (
        item.get("title"),
        item.get("summary"),
        item.get("content"),
        item.get("full_text"),
        item.get("source_module"),
        item.get("source_display"),
    )
    parts = [normalize_visible_text(value) for value in values]
    return "\n".join(part for part in parts if part)


def _contains_keyword(text: str, keyword: str) -> bool:
    lowered = text.casefold()
    normalized = keyword.casefold().strip()
    if not normalized:
        return False
    if re.fullmatch(r"[a-z0-9]+", normalized):
        return re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", lowered) is not None
    return normalized in lowered


def _matched_labels(text: str, mapping: dict[str, tuple[str, ...]]) -> list[str]:
    return [label for label, keywords in mapping.items() if any(_contains_keyword(text, keyword) for keyword in keywords)]


def _matched_pattern_labels(text: str, mapping: dict[str, tuple[str, ...]]) -> list[str]:
    return [
        label
        for label, patterns in mapping.items()
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)
    ]


def matched_industry_topics(text: str) -> list[str]:
    return _matched_labels(text, INDUSTRY_TOPICS)


def matched_hard_variables(text: str) -> list[str]:
    labels = [*_matched_labels(text, HARD_VARIABLES), *_matched_pattern_labels(text, HARD_VARIABLE_PATTERNS)]
    extras = effective_list(RULE_ID, "extra_keywords", ())
    if any(_contains_keyword(text, keyword) for keyword in extras):
        labels.append("自定义硬变量")
    return list(dict.fromkeys(labels))


def quantified_evidence(text: str) -> list[str]:
    values: list[str] = []
    for pattern in QUANTIFIED_PATTERNS:
        values.extend(match.group(0) for match in re.finditer(pattern, text, flags=re.IGNORECASE))
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))[:10]


def _sentences(text: str) -> list[str]:
    return [
        part.strip(" -\t")
        for part in re.split(r"(?<=[。！？!?；;])|(?<=\.)\s+|\n+", text)
        if part.strip(" -\t")
    ]


def topic_hard_variable_claims(text: str) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for sentence in _sentences(text):
        topics = matched_industry_topics(sentence)
        variables = matched_hard_variables(sentence)
        if topics and variables:
            claims.append(
                {
                    "topics": topics,
                    "hard_variables": variables,
                    "evidence_quote": sentence[:500],
                    "quantified_evidence": quantified_evidence(sentence),
                }
            )
        if len(claims) >= 10:
            break
    return claims


def topic_hard_variable_match(item: dict[str, Any]) -> dict[str, Any]:
    if not rule_enabled(RULE_ID):
        return {}
    text = item_text(item)
    if not text:
        return {}
    claims = topic_hard_variable_claims(text)
    if not claims:
        return {}
    topics = list(dict.fromkeys(topic for claim in claims for topic in claim["topics"]))
    variables = list(dict.fromkeys(variable for claim in claims for variable in claim["hard_variables"]))
    quotes = list(dict.fromkeys(str(claim["evidence_quote"]) for claim in claims))[:5]
    quantified = list(
        dict.fromkeys(value for claim in claims for value in claim["quantified_evidence"])
    )[:10]
    return {
        "topics": topics,
        "hard_variables": variables,
        "claims": claims,
        "evidence_quotes": quotes,
        "quantified_evidence": quantified,
    }


def industry_topic_hard_variable_rule(source: str, item: dict[str, Any]) -> dict[str, Any] | None:
    match = topic_hard_variable_match(item)
    if not match:
        return None
    topics = list(match["topics"])
    variables = list(match["hard_variables"])
    reason = (
        f"通用产业内容规则：命中重点主题{'、'.join(topics[:3])}，并包含"
        f"{'、'.join(variables[:4])}等实质硬变量；来源分类不参与重要性判断。"
    )
    return {
        "matched": True,
        "rule_id": RULE_ID,
        "importance": "high",
        "push_now": True,
        "should_push": True,
        "reason": reason,
        "brief_reason": reason,
        "affected_targets": topics[:5],
        "related_targets": [
            {"name": topic, "code": "", "relation": "重点主题 + 产业硬变量", "direction": "uncertain"}
            for topic in topics[:5]
        ],
        "claim_topics": topics,
        "hard_variable_types": variables,
        "evidence_quotes": list(match["evidence_quotes"]),
        "quantified_evidence": list(match["quantified_evidence"]),
        "source": source,
    }


def is_topic_hard_variable_item(item: dict[str, Any]) -> bool:
    return bool(topic_hard_variable_match(item))


def apply_hardline_review_override(source: str, item: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    rule = industry_topic_hard_variable_rule(source, item)
    if not rule:
        return review
    updated = dict(review)
    updated["importance"] = "high"
    updated["push_now"] = True
    updated["industry_hardline_override"] = True
    updated["affected_targets"] = list(rule["affected_targets"])
    updated["reason"] = "\n".join(filter(None, (str(updated.get("reason") or "").strip(), rule["reason"])))
    raw = dict(updated.get("raw") or {})
    raw["industry_hardline_override"] = True
    raw["industry_topic_hard_variable"] = rule
    updated["raw"] = raw
    return updated


def explain_hardline(source: str, text_parts: Iterable[object]) -> str:
    item = {"title": collect_hardline_text(*text_parts)}
    rule = industry_topic_hard_variable_rule(source, item)
    return str(rule.get("reason") or "") if rule else ""
