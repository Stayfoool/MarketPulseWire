"""Rules for semiconductor/AI industry hard-variable sources.

This module keeps narrow source-level overrides separate from broader
portfolio news sources. The quantified industry hardline set is intentionally
limited to the five sources requested by the user:
SEMI, TrendForce, DIGITIMES, The Elec, and Nikkei xTECH.
SemiAnalysis is handled as a source-priority override because the user wants
all reports from this source to be treated as important real-time alerts.
"""

from __future__ import annotations

import os
import re
from typing import Iterable


HARDLINE_SOURCE_PREFIXES = (
    "semi_prnewswire_semiconductors",
    "trendforce_",
    "digitimes_",
    "nikkei_xtech_",
    "thelec_",
)


HARDLINE_SOURCE_NAMES = (
    "semi_prnewswire_semiconductors",
    "trendforce_page",
    "digitimes_tw_semiconductors_components",
    "digitimes_tw_ic_design",
    "digitimes_tw_ic_manufacturing",
    "digitimes_tw_ai_focus",
    "digitimes_tw_server",
    "digitimes_en_daily",
    "nikkei_xtech_all",
    "thelec_kr_semiconductor",
    "thelec_kr_all",
)


SOURCE_PRIORITY_IMMEDIATE_SOURCES = {
    "semianalysis",
}

EVENT_FIRST_DEFAULT_MAX_CHARS = 300


HARDLINE_KEYWORDS = (
    "equipment",
    "device",
    "equipment",
    "material",
    "materials",
    "capex",
    "capital expenditure",
    "investment",
    "invest",
    "funding",
    "factory",
    "fab",
    "plant",
    "capacity",
    "expansion",
    "output",
    "production",
    "price",
    "pricing",
    "raise",
    "raise prices",
    "increase",
    "shortage",
    "tighten",
    "tightening",
    "supply",
    "demand",
    "order",
    "orders",
    "backlog",
    "shipment",
    "shipment",
    "restriction",
    "ban",
    "export control",
    "control",
    "tariff",
    "HBM",
    "DRAM",
    "NAND",
    "MLCC",
    "glass core",
    "advanced packaging",
    "CPO",
    "optical",
    "photonics",
    "liquid cooling",
    "power",
    "grid",
)

STRONG_HARDLINE_KEYWORDS = (
    "capex",
    "capital expenditure",
    "investment",
    "invest",
    "equipment",
    "material",
    "materials",
    "capacity",
    "expansion",
    "price",
    "pricing",
    "shortage",
    "tighten",
    "order",
    "orders",
    "backlog",
    "restriction",
    "export control",
    "ban",
    "资本开支",
    "投资",
    "设备",
    "材料",
    "产能",
    "扩产",
    "涨价",
    "价格",
    "短缺",
    "紧缺",
    "订单",
    "管制",
    "出口管制",
    "禁令",
)


def is_hardline_source(source: str) -> bool:
    return source in HARDLINE_SOURCE_NAMES or source.startswith(HARDLINE_SOURCE_PREFIXES)


def effective_source(source: str, item: dict | None = None) -> str:
    if item and source == "trendforce_page":
        return str(item.get("page_source") or source)
    return source


def hardline_heuristic_matches(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(keyword.lower() in lowered for keyword in HARDLINE_KEYWORDS)


def has_strong_keyword(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(keyword.lower() in lowered for keyword in STRONG_HARDLINE_KEYWORDS)


def has_quantified_signal(text: str) -> bool:
    patterns = (
        r"\d+(?:\.\d+)?\s*(?:亿|万亿|兆)\s*(?:韩元|美元|人民币|元)?",
        r"\d+(?:\.\d+)?\s*(?:billion|bn|million|trillion)\s*(?:won|usd|dollars|rmb|yuan)?",
        r"\d+(?:\.\d+)?\s*(?:%|％)",
        r"\d+(?:\.\d+)?\s*(?:台|套|条|座|家|片|wafers?|units?|tools?)",
    )
    return any(re.search(pattern, str(text or ""), flags=re.IGNORECASE) for pattern in patterns)


def is_quantified_hardline_item(source: str, item: dict) -> bool:
    effective = effective_source(source, item)
    if not is_hardline_source(effective):
        return False
    text = collect_hardline_text(
        item.get("title"),
        item.get("summary"),
        item.get("content"),
        item.get("full_text"),
        item.get("source_module"),
        item.get("source_display"),
    )
    return has_strong_keyword(text) and has_quantified_signal(text)


def apply_hardline_review_override(source: str, item: dict, review: dict) -> dict:
    """Keep quantified hard-variable items from narrow industry sources immediate.

    This only applies to SEMI/TrendForce/DIGITIMES/The Elec/Nikkei xTECH.
    It does not apply to domestic finance wires such as Yicai or CLS.
    """
    if not is_quantified_hardline_item(source, item):
        return review
    updated = dict(review)
    updated["importance"] = "high"
    updated["push_now"] = True
    updated["industry_hardline_override"] = True
    targets = list(updated.get("affected_targets") or [])
    for target in ("产业硬变量", "受益/受损标的待确认"):
        if target not in targets:
            targets.append(target)
    updated["affected_targets"] = targets[:5]
    note = (
        "产业硬变量线覆盖：来源属于 SEMI/TrendForce/DIGITIMES/The Elec/Nikkei xTECH，"
        "且内容包含设备、材料、产能、资本开支、涨价、管制或订单等量化硬变量；"
        "即使具体 A 股映射待确认，也先即时推送并标注待验证。"
    )
    reason = str(updated.get("reason") or "").strip()
    if note not in reason:
        updated["reason"] = f"{reason}\n{note}".strip()
    raw = dict(updated.get("raw") or {})
    raw["industry_hardline_override"] = True
    updated["raw"] = raw
    return updated


def is_source_priority_immediate(source: str) -> bool:
    return str(source or "").strip().lower() in SOURCE_PRIORITY_IMMEDIATE_SOURCES


def apply_source_priority_override(source: str, item: dict, review: dict) -> dict:
    """Force selected high-trust sources into immediate push review flow."""
    if not is_source_priority_immediate(source):
        return review
    skeptic = review.get("skeptic") if isinstance(review.get("skeptic"), dict) else {}
    if review.get("skeptic_blocked") or str(skeptic.get("skeptic_verdict") or "").lower() == "block":
        return review
    updated = dict(review)
    updated["importance"] = "high"
    updated["push_now"] = True
    updated["source_priority_override"] = True
    targets = list(updated.get("affected_targets") or [])
    for target in ("SemiAnalysis", "产业链影响待确认"):
        if target not in targets:
            targets.append(target)
    updated["affected_targets"] = targets[:5]
    note = (
        "来源优先级覆盖：SemiAnalysis 属于高价值半导体/AI 产业研究源，"
        "按当前策略其报告默认视为重要并即时推送；具体受益/受损标的和官方确认情况在正文中标注待验证。"
    )
    reason = str(updated.get("reason") or "").strip()
    if note not in reason:
        updated["reason"] = f"{reason}\n{note}".strip()
    summary = str(updated.get("daily_summary") or "").strip()
    if summary and "待验证" not in summary and "待确认" not in summary:
        updated["daily_summary"] = f"{summary}（SemiAnalysis 来源优先级覆盖，标的映射待验证。）"
    raw = dict(updated.get("raw") or {})
    raw["source_priority_override"] = "semianalysis"
    updated["raw"] = raw
    return updated


def event_first_max_chars() -> int:
    raw = os.getenv("RESEARCH_MEDIA_EVENT_FIRST_MAX_CHARS", str(EVENT_FIRST_DEFAULT_MAX_CHARS)).strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return EVENT_FIRST_DEFAULT_MAX_CHARS


def normalize_visible_text(*values: object) -> str:
    text = collect_hardline_text(*values)
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_research_industry_source(source: str, item: dict | None = None) -> bool:
    effective = effective_source(source, item)
    return is_hardline_source(effective) or is_source_priority_immediate(source)


def should_event_first_hardline_item(source: str, item: dict, *, max_chars: int | None = None) -> bool:
    """Return whether a research/industry-media item should use fast event-first gating.

    The fast path is intentionally narrow: short title/summary-level items only,
    and only for source-priority reports or quantified hard-variable items from
    the research/industry-media source family.
    """
    if os.getenv("RESEARCH_MEDIA_EVENT_FIRST_ENABLED", "1").strip() == "0":
        return False
    limit = event_first_max_chars() if max_chars is None else max_chars
    if limit <= 0:
        return False
    if not is_research_industry_source(source, item):
        return False
    text = normalize_visible_text(
        item.get("title"),
        item.get("summary"),
        item.get("content"),
        item.get("full_text"),
        item.get("source_module"),
        item.get("source_display"),
    )
    if not text or len(text) > limit:
        return False
    if is_source_priority_immediate(source):
        return True
    return is_quantified_hardline_item(source, item)


def event_first_hardline_review(source: str, item: dict) -> dict | None:
    """Build a high-importance article review for short hard-variable items.

    This keeps storage/daily/signal extraction on the existing article review
    path while applying an event-first decision policy for short hard variables.
    """
    if not should_event_first_hardline_item(source, item):
        return None
    effective = effective_source(source, item)
    family = "SemiAnalysis" if is_source_priority_immediate(source) else source_family(effective)
    title = str(item.get("title") or "").strip()
    summary = str(item.get("summary") or item.get("content") or item.get("full_text") or "").strip()
    reason = (
        f"event-first 快速门控：{family} 属于研究机构/行业媒体高价值来源，"
        "且本条为短文本硬变量/标题级信号；先按事件流即时推送，后续可在日报或二次校验中补充完整文章分析。"
    )
    review = {
        "importance": "high",
        "push_now": True,
        "market_impact": "研究机构/行业媒体短硬变量可能迅速改变半导体/AI 产业链预期，需即时关注。",
        "incremental_classification": "无法判断",
        "affected_targets": [family, "产业硬变量", "受益/受损标的待确认"],
        "daily_summary": title or summary[:120],
        "reason": reason,
        "confidence": "中",
        "model": "event_first_hardline",
        "event_first": True,
        "raw": {
            "event_first_hardline": True,
            "event_first_source_family": family,
            "event_first_policy": "research_industry_media_short_hard_variable",
            "event_first_max_chars": event_first_max_chars(),
        },
    }
    return review


def collect_hardline_text(*values: object) -> str:
    return " ".join(str(value or "") for value in values)


def source_family(source: str) -> str:
    if source.startswith("digitimes_"):
        return "DIGITIMES"
    if source.startswith("trendforce"):
        return "TrendForce"
    if source.startswith("nikkei_xtech"):
        return "Nikkei xTECH"
    if source.startswith("thelec_"):
        return "The Elec"
    if source.startswith("semi_"):
        return "SEMI"
    return source


def explain_hardline(source: str, text_parts: Iterable[object]) -> str:
    text = collect_hardline_text(*text_parts)
    effective = effective_source(source)
    if not is_hardline_source(effective):
        return ""
    if hardline_heuristic_matches(text):
        return f"{source_family(effective)} 命中设备/材料/产能/涨价/管制/订单等硬变量。"
    return f"{source_family(effective)} 属于产业硬变量线，但当前内容未明显命中硬变量关键词。"
