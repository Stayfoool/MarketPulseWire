"""Trusted research attribution across normalized market content.

The LLM, when needed, only extracts structured claims with verbatim evidence.
This module validates that evidence before the deterministic decision engine
can turn the extraction into a push rule.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import replace
from typing import Any

from llm_analysis import call_chat_completion_with_prompts, llm_config
from market_item import NormalizedMarketItem
from rule_center import effective_list, rule_enabled


RULE_ID = "attributed_research_hard_variable"
EXTRACTION_KEY = "_attributed_research"
PROMPT_VERSION = "attributed_research_v1"
DEFAULT_TRUSTED_INSTITUTIONS = (
    "semianalysis",
    "trendforce",
    "semi",
    "digitimes",
    "the_elec",
    "nikkei_xtech",
)

INSTITUTIONS: dict[str, dict[str, Any]] = {
    "semianalysis": {
        "name": "SemiAnalysis",
        "aliases": ("SemiAnalysis", "Semi Analysis"),
        "speakers": ("Dylan Patel", "Dylan"),
    },
    "trendforce": {
        "name": "TrendForce",
        "aliases": ("TrendForce", "集邦咨询", "集邦科技"),
        "speakers": (),
    },
    "semi": {
        "name": "SEMI",
        "aliases": ("SEMI", "国际半导体产业协会", "国际半导体行业协会", "semi.org"),
        "speakers": (),
    },
    "digitimes": {
        "name": "DIGITIMES",
        "aliases": ("DIGITIMES", "DigiTimes", "电子时报"),
        "speakers": (),
    },
    "the_elec": {
        "name": "The Elec",
        "aliases": ("The Elec", "THE ELEC", "韩媒The Elec", "韩媒 The Elec"),
        "speakers": (),
    },
    "nikkei_xtech": {
        "name": "Nikkei xTECH",
        "aliases": ("Nikkei xTECH", "Nikkei xTech", "日经xTECH", "日经 xTECH", "日经科技"),
        "speakers": (),
    },
}

TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "hbm": ("hbm", "高带宽内存", "高頻寬記憶體"),
    "dram": ("dram",),
    "nand": ("nand", "闪存", "flash memory"),
    "memory": ("存储", "内存", "memory"),
    "cpo": ("cpo", "共封装光学", "co-packaged optics", "co packaged optics"),
    "optical_interconnect": ("光互联", "光通信", "硅光", "铜缆", "连接器", "optical interconnect"),
    "advanced_packaging": ("先进封装", "cowos", "chiplet", "hybrid bonding", "混合键合"),
    "semiconductor_equipment": ("半导体设备", "刻蚀", "薄膜", "量测", "探针卡", "test equipment"),
    "semiconductor_materials": ("半导体材料", "光刻胶", "电子特气", "玻璃基板", "glass substrate"),
    "foundry": ("晶圆代工", "晶圆厂", "foundry", "wafer", "台积电", "tsmc"),
    "pcb": ("pcb", "中板", "背板", "高多层板"),
    "ai_infrastructure": ("ai基础设施", "ai 基础设施", "ai服务器", "ai 服务器", "gpu", "算力", "数据中心"),
    "semiconductor": ("半导体", "芯片", "semiconductor", "chip"),
}

EVENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "structural_shortage": (
        "结构性短缺",
        "长期短缺",
        "供应短缺",
        "供不应求",
        "供应缺口",
        "产能瓶颈",
        "capacity constraint",
        "supply shortage",
        "shortage",
        "bottleneck",
    ),
    "deployment_delay": ("推迟", "延期", "延后", "延至", "不早于", "delay", "delayed", "postpone", "not expected before"),
    "deployment_acceleration": ("提前", "加速落地", "加快量产", "提前投产", "accelerate", "ahead of schedule"),
    "price_change": ("涨价", "降价", "上调", "下调", "价格翻倍", "上行空间", "price increase", "price hike", "pricing"),
    "capacity_change": ("扩产", "减产", "产能扩张", "产能调整", "新增产能", "capacity expansion", "capacity cut"),
    "capex_change": ("资本开支", "设备投资", "紧急投资", "capex", "capital expenditure"),
    "order_change": ("订单", "采购", "中标", "backlog", "procurement"),
    "shipment_change": ("出货", "交付", "shipment", "delivery cycle"),
    "demand_change": ("需求激增", "需求下滑", "需求收缩", "demand surge", "demand decline"),
    "regulatory_change": ("出口管制", "禁令", "关税", "监管", "export control", "ban", "tariff"),
    "roadmap_shift": ("路线转向", "技术路线", "替代", "绕过", "切换至", "roadmap", "shift to", "replace"),
}

TOPIC_LABELS = {
    "hbm": "HBM",
    "dram": "DRAM",
    "nand": "NAND/闪存",
    "memory": "存储",
    "cpo": "CPO",
    "optical_interconnect": "光互联/铜缆连接",
    "advanced_packaging": "先进封装",
    "semiconductor_equipment": "半导体设备",
    "semiconductor_materials": "半导体材料",
    "foundry": "晶圆制造",
    "pcb": "PCB/中板",
    "ai_infrastructure": "AI基础设施",
    "semiconductor": "半导体",
}

EVENT_LABELS = {
    "structural_shortage": "结构性短缺/供应瓶颈",
    "deployment_delay": "落地或量产延期",
    "deployment_acceleration": "落地或量产提前",
    "price_change": "价格或上行空间变化",
    "capacity_change": "产能变化",
    "capex_change": "资本开支变化",
    "order_change": "订单/采购变化",
    "shipment_change": "出货/交付变化",
    "demand_change": "需求变化",
    "regulatory_change": "监管/出口管制变化",
    "roadmap_shift": "技术路线变化",
}

EVENT_DEDUP_FAMILIES = {
    "structural_shortage": "supply_demand",
    "price_change": "supply_demand",
    "demand_change": "supply_demand",
    "deployment_delay": "deployment_timing",
    "deployment_acceleration": "deployment_timing",
    "capacity_change": "capacity_investment",
    "capex_change": "capacity_investment",
    "order_change": "orders_shipments",
    "shipment_change": "orders_shipments",
    "regulatory_change": "regulation",
    "roadmap_shift": "roadmap",
}

ATTRIBUTION_TERMS = (
    "指出",
    "表示",
    "认为",
    "预计",
    "预测",
    "强调",
    "警告",
    "报告称",
    "报告指出",
    "报道称",
    "消息称",
    "接受采访",
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

CRITICISM_TERMS = (
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

QUANTIFIED_PATTERNS = (
    r"\d+(?:\.\d+)?\s*(?:%|％)",
    r"\d+(?:\.\d+)?\s*(?:至|-|~|—)\s*\d+(?:\.\d+)?\s*倍",
    r"\d+(?:\.\d+)?\s*倍",
    r"20\d{2}\s*年?",
    r"\d+(?:\.\d+)?\s*(?:亿|万亿|兆|billion|bn|million|trillion)",
    r"\d+(?:\.\d+)?\s*(?:台|套|片|wafers?|units?)",
)

ALLOWED_TOPICS = frozenset(TOPIC_KEYWORDS)
ALLOWED_EVENTS = frozenset(EVENT_KEYWORDS)

LLM_SYSTEM_PROMPT = """你是市场信息结构化抽取器，不是推送裁判。
只识别给定候选机构是否在原文中明确表达了半导体或 AI 基础设施观点，并摘录原文证据。
禁止输出 importance、action、push、投资建议或原文没有的信息。只输出 JSON。"""

LLM_USER_PROMPT = """候选机构：{institutions}

输出 JSON：
{{
  "institution_id": "只能从候选机构 ID 中选择；无法确认则为空",
  "speaker": "原文明确出现的人物，否则为空",
  "attribution": "explicit/unclear/not_attributed",
  "attribution_quote": "证明该机构或人物明确表达观点的原文连续片段",
  "claims": [
    {{
      "topic": "{topics}",
      "event_type": "{events}",
      "evidence_quote": "证明该事件的原文连续片段"
    }}
  ]
}}

要求：
- attribution_quote 和 evidence_quote 必须逐字来自原文，不能改写。
- 只是提及、引用别人评价该机构、批评机构、或无法确定是谁的观点时，attribution 不得为 explicit。
- 不要判断是否重要或是否推送。

原文：
{content}"""


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _item_value(item: NormalizedMarketItem | dict[str, Any], key: str) -> Any:
    if isinstance(item, NormalizedMarketItem):
        return getattr(item, key, "")
    return item.get(key)


def item_text(item: NormalizedMarketItem | dict[str, Any]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for key in ("title", "summary", "full_text"):
        value = str(_item_value(item, key) or "").strip()
        normalized = _clean_text(value)
        if not value or normalized in seen:
            continue
        seen.add(normalized)
        parts.append(value)
    return "\n".join(parts)


def publisher_role(item: NormalizedMarketItem | dict[str, Any]) -> str:
    direct = _clean_text(_item_value(item, "publisher_role"))
    if direct:
        return direct
    raw = _item_value(item, "raw")
    if isinstance(raw, dict) and raw.get("publisher_role"):
        return _clean_text(raw.get("publisher_role"))
    return ""


def trusted_institution_ids() -> tuple[str, ...]:
    configured = effective_list(
        RULE_ID,
        "trusted_institutions",
        DEFAULT_TRUSTED_INSTITUTIONS,
        replace_when_set=True,
    )
    return tuple(value for value in configured if value in INSTITUTIONS)


def _extra_aliases() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for value in effective_list(RULE_ID, "extra_aliases", ()):
        institution_id, separator, alias = str(value).partition("=")
        institution_id = institution_id.strip()
        alias = alias.strip()
        if separator and institution_id in INSTITUTIONS and alias:
            result.setdefault(institution_id, []).append(alias)
    return result


def institution_aliases(institution_id: str) -> tuple[str, ...]:
    definition = INSTITUTIONS.get(institution_id) or {}
    values = list(definition.get("aliases") or ()) + list(definition.get("speakers") or ())
    values.extend(_extra_aliases().get(institution_id, []))
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _alias_pattern(alias: str) -> re.Pattern[str]:
    if alias == "SEMI":
        return re.compile(r"(?<![A-Za-z])SEMI(?![A-Za-z])")
    escaped = re.escape(alias)
    if re.fullmatch(r"[A-Za-z0-9 ._-]+", alias):
        return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", flags=re.IGNORECASE)
    return re.compile(escaped, flags=re.IGNORECASE)


def institution_mentions(text: str) -> list[dict[str, str]]:
    mentions: list[dict[str, str]] = []
    for institution_id in trusted_institution_ids():
        definition = INSTITUTIONS[institution_id]
        for alias in institution_aliases(institution_id):
            match = _alias_pattern(alias).search(text)
            if not match:
                continue
            mentions.append(
                {
                    "institution_id": institution_id,
                    "institution_name": str(definition["name"]),
                    "matched_alias": match.group(0),
                }
            )
            break
    return mentions


def _contains_any(text: str, values: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(value.casefold() in lowered for value in values)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[。！？!?；;])|\n+", text) if part.strip()]


def _sentence_has_alias(sentence: str, institution_id: str) -> bool:
    return any(_alias_pattern(alias).search(sentence) for alias in institution_aliases(institution_id))


def _attribution_indexes(sentences: list[str], institution_id: str) -> list[int]:
    indexes: list[int] = []
    for index, sentence in enumerate(sentences):
        if _sentence_has_alias(sentence, institution_id) and _contains_any(sentence, CRITICISM_TERMS):
            continue
        if _sentence_has_alias(sentence, institution_id) and _contains_any(sentence, ATTRIBUTION_TERMS):
            indexes.append(index)
            continue
        if re.search(r"(?:据|根据)\s*", sentence) and _sentence_has_alias(sentence, institution_id):
            indexes.append(index)
    return indexes


def _matched_keys(text: str, mapping: dict[str, tuple[str, ...]]) -> list[str]:
    lowered = text.casefold()
    return [key for key, values in mapping.items() if any(value.casefold() in lowered for value in values)]


def _quantified_evidence(text: str) -> list[str]:
    matches: list[tuple[int, int, str]] = []
    for pattern in QUANTIFIED_PATTERNS:
        matches.extend((match.start(), match.end(), match.group(0)) for match in re.finditer(pattern, text, flags=re.IGNORECASE))
    selected: list[tuple[int, int, str]] = []
    for start, end, value in sorted(matches, key=lambda item: (item[0], -(item[1] - item[0]))):
        if any(start >= prior_start and end <= prior_end for prior_start, prior_end, _ in selected):
            continue
        selected.append((start, end, value))
    return [value for _, _, value in selected[:8]]


def deterministic_extraction(item: NormalizedMarketItem | dict[str, Any]) -> dict[str, Any]:
    text = item_text(item)
    mentions = institution_mentions(text)
    if not mentions:
        return {}
    sentences = _sentences(text)
    for mention in mentions:
        institution_id = mention["institution_id"]
        attribution_indexes = _attribution_indexes(sentences, institution_id)
        if not attribution_indexes:
            continue
        claims: list[dict[str, Any]] = []
        for attribution_index in attribution_indexes:
            for sentence in sentences[attribution_index : attribution_index + 2]:
                topics = _matched_keys(sentence, TOPIC_KEYWORDS)
                events = _matched_keys(sentence, EVENT_KEYWORDS)
                if not topics or not events:
                    continue
                for topic in topics:
                    for event_type in events:
                        claims.append(
                            {
                                "topic": topic,
                                "event_type": event_type,
                                "evidence_quote": sentence,
                                "quantified_evidence": _quantified_evidence(sentence),
                            }
                        )
        if not claims:
            continue
        unique_claims = []
        seen: set[tuple[str, str, str]] = set()
        for claim in claims:
            key = (claim["topic"], claim["event_type"], claim["evidence_quote"])
            if key in seen:
                continue
            seen.add(key)
            unique_claims.append(claim)
        attribution_quote = sentences[attribution_indexes[0]]
        speaker = next(
            (
                speaker_alias
                for speaker_alias in INSTITUTIONS[institution_id].get("speakers", ())
                if _alias_pattern(str(speaker_alias)).search(attribution_quote)
            ),
            "",
        )
        return {
            "schema": "AttributedResearchExtraction/v1",
            "institution_id": institution_id,
            "institution_name": mention["institution_name"],
            "matched_alias": mention["matched_alias"],
            "speaker": speaker,
            "attribution": "explicit",
            "attribution_quote": attribution_quote,
            "claims": unique_claims[:10],
            "extraction_mode": "deterministic",
            "prompt_version": PROMPT_VERSION,
        }
    return {}


def _normalized_contains(text: str, quote: str) -> bool:
    normalized_text = _clean_text(text)
    normalized_quote = _clean_text(quote)
    return bool(normalized_quote) and normalized_quote in normalized_text


def validate_extraction(
    item: NormalizedMarketItem | dict[str, Any],
    extraction: dict[str, Any],
    *,
    candidate_ids: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(extraction, dict):
        return {}
    text = item_text(item)
    institution_id = str(extraction.get("institution_id") or "").strip()
    allowed_ids = candidate_ids if candidate_ids is not None else {item["institution_id"] for item in institution_mentions(text)}
    if institution_id not in trusted_institution_ids() or institution_id not in allowed_ids:
        return {}
    if str(extraction.get("attribution") or "").strip() != "explicit":
        return {}
    attribution_quote = str(extraction.get("attribution_quote") or "").strip()
    if not _normalized_contains(text, attribution_quote):
        return {}
    if _contains_any(attribution_quote, CRITICISM_TERMS):
        return {}
    alias_in_attribution = any(_alias_pattern(alias).search(attribution_quote) for alias in institution_aliases(institution_id))
    speaker = str(extraction.get("speaker") or "").strip()
    known_speakers = {str(value).casefold() for value in INSTITUTIONS[institution_id].get("speakers", ())}
    known_speaker_in_attribution = (
        bool(speaker)
        and speaker.casefold() in known_speakers
        and _normalized_contains(attribution_quote, speaker)
    )
    if not alias_in_attribution and not known_speaker_in_attribution:
        return {}
    claims = extraction.get("claims") if isinstance(extraction.get("claims"), list) else []
    validated_claims: list[dict[str, Any]] = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        topic = str(claim.get("topic") or "").strip()
        event_type = str(claim.get("event_type") or "").strip()
        evidence_quote = str(claim.get("evidence_quote") or "").strip()
        if topic not in ALLOWED_TOPICS or event_type not in ALLOWED_EVENTS:
            continue
        if not _normalized_contains(text, evidence_quote):
            continue
        validated_claims.append(
            {
                "topic": topic,
                "event_type": event_type,
                "evidence_quote": evidence_quote,
                "quantified_evidence": _quantified_evidence(evidence_quote),
            }
        )
    if not validated_claims:
        return {}
    definition = INSTITUTIONS[institution_id]
    return {
        "schema": "AttributedResearchExtraction/v1",
        "institution_id": institution_id,
        "institution_name": str(definition["name"]),
        "matched_alias": str(extraction.get("matched_alias") or ""),
        "speaker": speaker,
        "attribution": "explicit",
        "attribution_quote": attribution_quote,
        "claims": validated_claims[:10],
        "extraction_mode": str(extraction.get("extraction_mode") or "llm"),
        "model": str(extraction.get("model") or ""),
        "prompt_version": str(extraction.get("prompt_version") or PROMPT_VERSION),
    }


def llm_extraction(item: NormalizedMarketItem, mentions: list[dict[str, str]]) -> dict[str, Any]:
    candidate_ids = {mention["institution_id"] for mention in mentions}
    if not candidate_ids or os.getenv("ATTRIBUTED_RESEARCH_LLM_ENABLED", "1").strip() == "0":
        return {}
    if llm_config() is None:
        return {}
    content = item_text(item)[:8000]
    prompt = (
        LLM_USER_PROMPT.replace("{institutions}", json.dumps(sorted(candidate_ids), ensure_ascii=False))
        .replace("{topics}", "/".join(sorted(ALLOWED_TOPICS)))
        .replace("{events}", "/".join(sorted(ALLOWED_EVENTS)))
        .replace("{content}", content)
    )
    parsed, model = call_chat_completion_with_prompts(
        LLM_SYSTEM_PROMPT,
        prompt,
        user_agent="surveil-attributed-research/0.1",
        truncate_user_prompt=False,
        thinking_override=os.getenv("ATTRIBUTED_RESEARCH_LLM_THINKING_TYPE", "disabled"),
        max_tokens_override=int(os.getenv("ATTRIBUTED_RESEARCH_LLM_MAX_OUTPUT_TOKENS", "900")),
    )
    candidate = dict(parsed)
    candidate["extraction_mode"] = "llm"
    candidate["model"] = model
    candidate["prompt_version"] = PROMPT_VERSION
    return validate_extraction(item, candidate, candidate_ids=candidate_ids)


def prepare_item_for_decision(item: NormalizedMarketItem) -> NormalizedMarketItem:
    if not rule_enabled(RULE_ID):
        return item
    text = item_text(item)
    mentions = institution_mentions(text)
    if not mentions:
        return item
    extraction = validate_extraction(item, deterministic_extraction(item))
    error = ""
    if not extraction:
        try:
            extraction = llm_extraction(item, mentions)
        except Exception as exc:  # noqa: BLE001 - attribution failure must not break ingestion
            error = str(exc).strip()[:500]
    raw = dict(item.raw)
    raw[EXTRACTION_KEY] = extraction or {
        "schema": "AttributedResearchExtraction/v1",
        "candidate_institutions": mentions,
        "extraction_mode": "failed" if error else "not_confirmed",
        "error": error,
        "prompt_version": PROMPT_VERSION,
    }
    return replace(item, raw=raw)


def extraction_for_rule(item: NormalizedMarketItem | dict[str, Any]) -> dict[str, Any]:
    raw = _item_value(item, "raw")
    stored = raw.get(EXTRACTION_KEY) if isinstance(raw, dict) else None
    if isinstance(stored, dict):
        validated = validate_extraction(item, stored)
        if validated:
            return validated
    return validate_extraction(item, deterministic_extraction(item))


def claim_dedup_key(extraction: dict[str, Any]) -> str:
    institution_id = str(extraction.get("institution_id") or "unknown")
    claims = extraction.get("claims") if isinstance(extraction.get("claims"), list) else []
    topics = sorted({str(claim.get("topic")) for claim in claims if isinstance(claim, dict) and claim.get("topic")})
    event_families = sorted(
        {
            EVENT_DEDUP_FAMILIES.get(str(claim.get("event_type")), str(claim.get("event_type")))
            for claim in claims
            if isinstance(claim, dict) and claim.get("event_type")
        }
    )
    dimensions = [*(f"topic:{topic}" for topic in topics), *(f"event:{event}" for event in event_families)]
    evidence_text = " ".join(
        str(claim.get("evidence_quote") or "") for claim in claims if isinstance(claim, dict)
    )
    years = sorted(set(re.findall(r"20\d{2}", evidence_text)))
    if years:
        dimensions.append(f"year:{years[0]}")
    digest = hashlib.sha256("|".join([institution_id, *dimensions]).encode("utf-8")).hexdigest()[:20]
    return f"attributed_research:{institution_id}:{digest}"


def attributed_research_rule(item: NormalizedMarketItem | dict[str, Any]) -> dict[str, Any] | None:
    if not rule_enabled(RULE_ID):
        return None
    extraction = extraction_for_rule(item)
    if not extraction:
        return None
    claims = extraction["claims"]
    topics = list(dict.fromkeys(str(claim["topic"]) for claim in claims))
    events = list(dict.fromkeys(str(claim["event_type"]) for claim in claims))
    institution_name = str(extraction.get("institution_name") or extraction.get("institution_id") or "研究机构")
    evidence = [str(claim.get("evidence_quote") or "") for claim in claims if claim.get("evidence_quote")]
    targets = [TOPIC_LABELS.get(topic, topic) for topic in topics]
    event_labels = [EVENT_LABELS.get(event, event) for event in events]
    reason = (
        f"高价值行业研究源明确署名转述：{institution_name} 对半导体/AI 基础设施给出"
        f"{'、'.join(event_labels)}等实质判断；证据已回验原文，由确定性规则即时推送。"
    )
    source = str(_item_value(item, "source") or "")
    return {
        "matched": True,
        "rule_id": RULE_ID,
        "importance": "high",
        "push_now": True,
        "should_push": True,
        "reason": reason,
        "brief_reason": reason,
        "affected_targets": targets[:5],
        "related_targets": [
            {"name": target, "code": "", "relation": f"{institution_name} 明确观点", "direction": "uncertain"}
            for target in targets[:5]
        ],
        "source": source,
        "transport_source": source,
        "publisher_role": publisher_role(item),
        "attributed_institution": extraction.get("institution_id"),
        "attributed_speaker": extraction.get("speaker"),
        "claim_topics": topics,
        "claim_event_types": events,
        "evidence_quotes": evidence[:6],
        "dedup_key": claim_dedup_key(extraction),
        "dedup_lookback_days": 3,
        "protected_from_llm_downgrade": True,
        "raw": {"attributed_research": extraction},
    }
