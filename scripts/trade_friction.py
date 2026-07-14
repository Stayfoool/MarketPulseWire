"""Source-neutral early warning for China-US and China-EU trade friction."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from rule_center import rule_enabled


RULE_ID = "trade_friction_escalation"

CORRIDOR_TERMS: dict[str, dict[str, tuple[str, ...]]] = {
    "china_us": {
        "china": (
            "china",
            "chinese",
            "people's republic of china",
            "prc",
            "中国",
            "中方",
            "中美",
            "中国商务部",
            "商务部新闻发言人",
            "商务部公告",
        ),
        "counterparty": (
            "united states",
            "u.s.",
            "u.s ",
            "american",
            "ustr",
            "white house",
            "commerce department",
            "bureau of industry and security",
            "美国",
            "美方",
            "中美",
            "白宫",
            "美国商务部",
        ),
    },
    "china_eu": {
        "china": (
            "china",
            "chinese",
            "people's republic of china",
            "prc",
            "中国",
            "中方",
            "中欧",
            "中国商务部",
            "商务部新闻发言人",
            "商务部公告",
        ),
        "counterparty": (
            "european union",
            "european commission",
            "eu trade",
            "eu tariff",
            "eu tariffs",
            "european trade",
            "netherlands",
            "dutch",
            "germany",
            "german",
            "france",
            "french",
            "italy",
            "italian",
            "spain",
            "spanish",
            "belgium",
            "sweden",
            "poland",
            "欧盟",
            "欧委会",
            "欧洲委员会",
            "中欧",
            "荷兰",
            "德国",
            "法国",
            "意大利",
            "西班牙",
            "比利时",
            "瑞典",
            "波兰",
        ),
    },
}

POLICY_TOOLS: dict[str, tuple[str, ...]] = {
    "关税/海关措施": (
        "tariff",
        "tariffs",
        "customs duty",
        "customs duties",
        "duty rate",
        "duties on imports",
        "关税",
        "加征税",
        "进口税",
        "报复性关税",
    ),
    "贸易救济调查": (
        "section 301",
        "section 232",
        "section 201",
        "anti-dumping",
        "antidumping",
        "countervailing",
        "anti-subsidy",
        "trade remedy",
        "safeguard investigation",
        "less-than-fair-value",
        "301调查",
        "301条款",
        "232调查",
        "反倾销",
        "反补贴",
        "贸易救济",
        "保障措施调查",
    ),
    "出口/技术管制": (
        "export control",
        "export controls",
        "export restriction",
        "export restrictions",
        "licensing requirement",
        "foreign direct product rule",
        "technology restriction",
        "chip curbs",
        "出口管制",
        "出口限制",
        "技术限制",
        "禁运",
        "两用物项",
    ),
    "实体/投资限制": (
        "entity list",
        "military end user list",
        "restricted list",
        "investment restriction",
        "outbound investment",
        "investment screening",
        "blacklist",
        "实体清单",
        "管控名单",
        "不可靠实体清单",
        "关注名单",
        "投资限制",
        "对外投资审查",
        "军事企业清单",
    ),
    "进口/市场准入限制": (
        "import ban",
        "import restriction",
        "market access restriction",
        "procurement restriction",
        "forced labor goods",
        "economic coercion",
        "进口禁令",
        "进口限制",
        "市场准入限制",
        "采购限制",
        "强迫劳动产品",
        "经济胁迫",
    ),
}

ACTION_STAGES: dict[str, tuple[str, ...]] = {
    "前置程序": (
        "initiat",
        "launches an investigation",
        "opens an investigation",
        "institution of antidumping",
        "institution of countervailing",
        "institution of investigations",
        "public comment",
        "request for comment",
        "requests comments",
        "seeks comment",
        "public hearing",
        "hearing on proposed",
        "proposed action",
        "proposes action",
        "preliminary determination",
        "preliminary duties",
        "considering tariffs",
        "considering restrictions",
        "plans to impose",
        "prepares tariffs",
        "recommendation to impose",
        "征求意见",
        "公开征求",
        "听证会",
        "启动调查",
        "发起调查",
        "立案调查",
        "初步裁定",
        "初裁",
        "拟加征",
        "拟采取",
        "考虑加征",
        "计划限制",
        "建议征收",
    ),
    "实施/扩大": (
        "imposes",
        "imposed",
        "will impose",
        "takes effect",
        "effective on",
        "raises tariffs",
        "increases tariffs",
        "adds to the entity list",
        "added to the entity list",
        "tightens export controls",
        "expands export controls",
        "issues remedial orders",
        "final determination",
        "决定加征",
        "宣布加征",
        "正式实施",
        "生效",
        "提高关税",
        "扩大管制",
        "收紧管制",
        "列入实体清单",
        "列入出口管制管控名单",
        "采取反制措施",
        "终裁",
    ),
    "威胁/报复": (
        "threatens tariffs",
        "threatens restrictions",
        "retaliatory measures",
        "retaliation against",
        "all options are on the table",
        "warns of consequences",
        "vows to respond",
        "will take necessary measures",
        "威胁加征",
        "威胁限制",
        "报复措施",
        "反制措施",
        "将采取必要措施",
        "保留采取措施的权利",
        "后果自负",
        "坚决反制",
    ),
}

STRONG_TENSION_TERMS = (
    "trade friction",
    "trade frictions",
    "trade war",
    "trade dispute escalat",
    "trade tensions escalate",
    "trade tensions worsen",
    "relations deteriorat",
    "talks stall",
    "talks stalled",
    "talks collapse",
    "negotiations break down",
    "presses china talks",
    "unacceptable trade practices",
    "discriminatory trade practices",
    "protectionist measures",
    "economic coercion",
    "贸易摩擦",
    "贸易战",
    "贸易争端升级",
    "经贸关系恶化",
    "谈判停滞",
    "谈判破裂",
    "磋商破裂",
    "歧视性措施",
    "保护主义措施",
)

TRADE_CONTEXT_TERMS = (
    "trade",
    "tariff",
    "customs",
    "market access",
    "export control",
    "import restriction",
    "economic coercion",
    "section 301",
    "section 232",
    "antidumping",
    "anti-dumping",
    "countervailing",
    "贸易",
    "经贸",
    "关税",
    "市场准入",
    "出口管制",
    "进口限制",
    "经济胁迫",
    "反倾销",
    "反补贴",
)

WEAK_TENSION_TERMS = (
    "trade tension",
    "trade tensions",
    "trade concern",
    "trade concerns",
    "trade dispute",
    "unfair trade",
    "protectionism",
    "market access concern",
    "reviewing trade policy",
    "trade relationship under strain",
    "贸易紧张",
    "经贸摩擦",
    "贸易争端",
    "不公平贸易",
    "保护主义",
    "市场准入关切",
    "经贸关系承压",
    "表达关切",
)

ROUTINE_ADMIN_TERMS = (
    "administrative review",
    "sunset review",
    "five-year review",
    "final results of the expedited",
    "preliminary results",
    "scheduling of",
    "continuation of antidumping duty order",
    "continuation of countervailing duty order",
    "postponement of preliminary determination",
    "notice of correction",
    "amended final results",
    "amended final determination",
    "section 337 final determination",
    "337部分终裁",
    "行政复审",
    "日落复审",
    "五年复审",
    "延期作出初步裁定",
    "更正公告",
    "维持反倾销税令",
)

TITLE_ACTION_OVERRIDE_TERMS = (
    "initiation of",
    "initiates",
    "institution of antidumping",
    "institution of countervailing",
    "institution of investigations",
    "launches an investigation",
    "opens an investigation",
    "public comment",
    "public hearing",
    "proposed action",
    "imposes",
    "will impose",
    "启动调查",
    "发起调查",
    "立案调查",
    "征求意见",
    "听证会",
    "拟采取",
    "宣布加征",
)

SECTORS: dict[str, tuple[str, ...]] = {
    "半导体/AI": ("semiconductor", "chip", "advanced computing", "ai model", "半导体", "芯片", "人工智能"),
    "汽车/电动车": ("electric vehicle", "battery electric vehicle", "automotive", "automobile", "evs", "电动汽车", "新能源汽车", "汽车"),
    "电池/关键矿产": ("battery", "critical mineral", "rare earth", "gallium", "germanium", "graphite", "电池", "关键矿产", "稀土", "镓", "锗", "石墨"),
    "光伏/清洁能源": ("solar", "photovoltaic", "wind turbine", "clean energy", "光伏", "太阳能", "风电", "清洁能源"),
    "钢铝/基础材料": ("steel", "aluminum", "aluminium", "copper", "钢铁", "铝", "铜"),
    "通信/科技设备": ("telecom", "telecommunications", "network equipment", "asml", "nexperia", "通信", "电信", "光刻机"),
    "农业/食品饮料": ("agriculture", "agricultural", "soybean", "brandy", "wine", "dairy", "农产品", "大豆", "白兰地", "葡萄酒", "乳制品"),
    "医药/医疗": ("pharmaceutical", "medical device", "biotech", "药品", "医药", "医疗器械", "生物技术"),
    "航运/物流": ("shipping", "shipbuilding", "port fee", "logistics", "航运", "造船", "港口费", "物流"),
}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _legacy_text(item: Any) -> tuple[str, str, str]:
    if hasattr(item, "title"):
        return _clean(item.title), _clean(item.summary), str(item.full_text or "").strip()
    if not isinstance(item, dict):
        return "", "", ""
    return (
        _clean(item.get("title")),
        _clean(item.get("summary") or item.get("content")),
        str(item.get("full_text") or item.get("content") or "").strip(),
    )


def _contains(text: str, term: str) -> bool:
    lowered = text.lower()
    needle = term.lower()
    if re.fullmatch(r"[a-z0-9. ]+", needle) and len(needle.strip()) <= 4:
        return re.search(rf"(?<![a-z0-9]){re.escape(needle.strip())}(?![a-z0-9])", lowered) is not None
    return needle in lowered


def _matched(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if _contains(text, term)]


def _matched_labels(text: str, mapping: dict[str, tuple[str, ...]]) -> dict[str, list[str]]:
    return {label: hits for label, terms in mapping.items() if (hits := _matched(text, terms))}


def _segments(title: str, summary: str, full_text: str) -> list[str]:
    values: list[str] = []
    for block in (title, summary, full_text):
        for segment in re.split(
            r"[\n\r]+|(?<=[!?。！？；;])\s*|(?<![A-Z]\.)(?<=\.)\s+(?=[A-Z0-9])",
            block,
        ):
            segment = _clean(segment)
            if len(segment) >= 8 and segment not in values:
                values.append(segment)
    return values[:240]


def matched_corridors(text: str) -> dict[str, dict[str, list[str]]]:
    matches: dict[str, dict[str, list[str]]] = {}
    for corridor, sides in CORRIDOR_TERMS.items():
        china = _matched(text, sides["china"])
        counterparty = _matched(text, sides["counterparty"])
        if china and counterparty:
            matches[corridor] = {"china": china, "counterparty": counterparty}
    return matches


def _affected_targets(sectors: list[str], corridors: list[str]) -> list[str]:
    targets = list(sectors)
    if "china_us" in corridors:
        targets.extend(["中美贸易预期", "人民币/美元", "全球风险偏好"])
    if "china_eu" in corridors:
        targets.extend(["中欧贸易预期", "欧元/人民币", "欧洲供应链"])
    return list(dict.fromkeys(targets))[:8]


def _dedup_key(corridors: list[str], tools: list[str], sectors: list[str], evidence: list[str]) -> str:
    if not tools or not sectors:
        return ""
    raw = "|".join([",".join(corridors), ",".join(tools), ",".join(sectors), evidence[0][:180] if evidence else ""])
    return f"{RULE_ID}:{hashlib.sha256(raw.lower().encode('utf-8')).hexdigest()[:24]}"


def trade_friction_rule(item: Any) -> dict[str, Any] | None:
    if not rule_enabled(RULE_ID):
        return None

    title, summary, full_text = _legacy_text(item)
    text = "\n".join(part for part in (title, summary, full_text) if part)
    global_corridors = matched_corridors(text)
    if not global_corridors:
        return None
    title_segments = _segments(title, "", "")
    title_corridors = matched_corridors(title) if len(title_segments) == 1 else {}
    evidence_corridors: dict[str, dict[str, list[str]]] = {}

    concrete_evidence: list[str] = []
    strong_evidence: list[str] = []
    weak_evidence: list[str] = []
    matched_tools: dict[str, list[str]] = {}
    matched_stages: dict[str, list[str]] = {}
    strong_terms: list[str] = []
    weak_terms: list[str] = []
    title_routine_terms = _matched(title, ROUTINE_ADMIN_TERMS)
    title_action_overrides = _matched(title, TITLE_ACTION_OVERRIDE_TERMS)
    title_routine_suppresses = bool(title_routine_terms and not title_action_overrides)
    routine_terms: list[str] = list(title_routine_terms)

    for segment in _segments(title, summary, full_text):
        local_corridors = matched_corridors(segment) or title_corridors
        if not local_corridors:
            continue
        tools = _matched_labels(segment, POLICY_TOOLS)
        stages = _matched_labels(segment, ACTION_STAGES)
        strong = _matched(segment, STRONG_TENSION_TERMS)
        weak = _matched(segment, WEAK_TENSION_TERMS)
        routine = _matched(segment, ROUTINE_ADMIN_TERMS)
        trade_context = _matched(segment, TRADE_CONTEXT_TERMS)
        if tools and stages and not title_routine_suppresses and (not routine or title_action_overrides):
            concrete_evidence.append(segment)
            evidence_corridors.update(local_corridors)
        if strong and (tools or trade_context):
            strong_evidence.append(segment)
            evidence_corridors.update(local_corridors)
        if tools or weak:
            weak_evidence.append(segment)
            evidence_corridors.update(local_corridors)
        for label, hits in tools.items():
            matched_tools.setdefault(label, []).extend(hits)
        for label, hits in stages.items():
            matched_stages.setdefault(label, []).extend(hits)
        strong_terms.extend(strong)
        weak_terms.extend(weak)
        routine_terms.extend(routine)

    if concrete_evidence or strong_evidence:
        action = "push"
        importance = "high"
        signal = "具体程序/措施" if concrete_evidence else "明确升级/报复信号"
        evidence = concrete_evidence + strong_evidence
    elif routine_terms and not strong_evidence and not weak_terms:
        return None
    elif matched_tools or weak_evidence:
        action = "daily"
        importance = "medium"
        signal = "贸易紧张或政策工具信号尚未进入明确行动阶段"
        evidence = weak_evidence
    else:
        return None

    if not evidence_corridors:
        return None
    sector_matches = _matched_labels(text, SECTORS)
    corridor_ids = sorted(evidence_corridors)
    tool_ids = sorted(matched_tools)
    sector_ids = sorted(sector_matches)
    targets = _affected_targets(sector_ids, corridor_ids)
    reason = f"贸易摩擦早期预警：{', '.join(corridor_ids)}；{signal}。"
    rule: dict[str, Any] = {
        "matched": True,
        "rule_id": RULE_ID,
        "decision_action": action,
        "importance": importance,
        "push_now": action == "push",
        "should_push": action == "push",
        "reason": reason,
        "brief_reason": reason,
        "corridors": corridor_ids,
        "corridor_evidence": evidence_corridors,
        "policy_tools": tool_ids,
        "policy_tool_terms": {key: list(dict.fromkeys(value)) for key, value in matched_tools.items()},
        "action_stages": sorted(matched_stages),
        "action_stage_terms": {key: list(dict.fromkeys(value)) for key, value in matched_stages.items()},
        "strong_tension_terms": list(dict.fromkeys(strong_terms)),
        "weak_tension_terms": list(dict.fromkeys(weak_terms)),
        "routine_admin_terms": list(dict.fromkeys(routine_terms)),
        "affected_sectors": sector_ids,
        "evidence": list(dict.fromkeys(evidence))[:6],
        "affected_targets": targets,
        "related_targets": [
            {"name": target, "code": "", "relation": "中美/中欧贸易摩擦预期", "direction": "uncertain"}
            for target in targets
        ],
    }
    dedup_key = _dedup_key(corridor_ids, tool_ids, sector_ids, rule["evidence"])
    if dedup_key:
        rule["dedup_key"] = dedup_key
        rule["dedup_lookback_days"] = 3
    return rule
