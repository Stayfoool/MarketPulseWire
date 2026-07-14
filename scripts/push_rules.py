"""Deterministic push rules for high-confidence investment alerts.

These rules sit above LLM judgement. They are intentionally narrow and
auditable: when they match, the realtime path should send a compact alert even
if the model labels the item as old news or already priced in.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from investment_bank_theme_config import load_config
from investment_bank_theme_taxonomy import ROTATION_THEME_BUCKETS, ROTATION_THEME_BY_ID
from investment_universe import investment_universe_match
from international_banks import INTERNATIONAL_BANK_ALIASES, bank_alias_matches, matched_bank_names
from media_keyword_config import keyword_matches_text
from rule_center import effective_list, rule_enabled, rule_priority, rule_settings
from stock_relations import portfolio_relation_matches


RATING_OR_TARGET_KEYWORDS = (
    "目标价",
    "target price",
    "tp",
    "评级",
    "rating",
    "上调",
    "下调",
    "调高",
    "调低",
    "看多",
    "看空",
    "买入",
    "卖出",
    "中性",
    "增持",
    "减持",
    "首次覆盖",
    "恢复覆盖",
    "覆盖",
    "upgrade",
    "downgrade",
    "initiates",
    "initiate",
    "coverage",
    "buy",
    "sell",
    "neutral",
    "overweight",
    "underweight",
    "outperform",
    "underperform",
)

THEME_STRATEGY_ACTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("做多", ("做多", "long", "go long")),
    ("做空", ("做空", "short", "go short")),
    ("超配", ("超配", "overweight")),
    ("低配", ("低配", "underweight")),
    ("加仓", ("加仓", "增配", "增持", "add exposure")),
    ("减仓", ("减仓", "减配", "减持", "reduce exposure")),
    ("买入", ("买入", "买进", "buy")),
    ("卖出", ("卖出", "卖出", "sell")),
    ("配置转向", ("配置转向", "资金切换", "资金轮动", "切换至", "rotate", "rotation", "switch to")),
)

THEME_STRATEGY_THEMES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("AI/算力价值链", ("ai价值链", "人工智能价值链", "ai基础设施", "算力", "大模型", "ai")),
    ("半导体", ("半导体", "芯片", "晶圆", "先进封装", "设备", "材料")),
    ("存储/HBM", ("hbm", "dram", "nand", "存储", "内存", "ssd")),
    ("数据中心电力/液冷", ("数据中心", "data center", "液冷", "散热", "电力", "电源")),
    ("光通信/光互联", ("光模块", "光通信", "硅光", "cpo", "光互联")),
    ("PCB/电子制造", ("pcb", "覆铜板", "电子制造", "中板")),
    ("机器人", ("人形机器人", "机器人", "谐波减速器", "丝杠")),
)

THEME_EVIDENCE_PATTERNS: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    (
        "资金/地区/行业切换",
        2,
        (
            "资金切换",
            "资本轮动",
            "结构性资本轮动",
            "配置转向",
            "地区轮动",
            "行业轮动",
            "从(?:韩国|美国|中国|欧洲).{0,28}(?:转向|切换)",
            "switch from",
            "rotate from",
        ),
    ),
    ("完整估值错配/比较", 2, ("估值错配", "市值与市场空间", "估值与市场空间", "valuation mismatch", "valuation.*market")),
    (
        "量化估值/市场比较",
        2,
        (
            "目标(?:回报|收益).{0,12}(?:\\d|%|倍)",
            "(?:市值|估值|市场规模|tam).{0,18}(?:\\d|%|倍|亿|万)",
            "(?:\\d|%|倍|亿|万).{0,18}(?:市值|估值|市场规模|tam)",
            "资金流.{0,18}(?:\\d|%|亿|万)",
        ),
    ),
)

ROTATION_DIRECT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:从|由)\s*(?P<from>[^，,。；;！？!?]{1,80}?)\s*"
        r"(?:(?:轮动|切换|调仓|换仓|撤出|调整)\s*(?:到|至|向|进入|转入)|转向)\s*"
        r"(?P<to>[^。；;！？!?]{1,100})",
        re.I,
    ),
    re.compile(
        r"(?:rotat(?:e|es|ed|ing)|rotation|switch(?:es|ed|ing)?|shift(?:s|ed|ing)?|"
        r"reallocat(?:e|es|ed|ing)|mov(?:e|es|ed|ing))"
        r"(?:\s+(?:capital|funds|money|allocation|allocations|exposure|positioning|investors?)){0,3}"
        r"\s+(?:away\s+)?from\s+(?P<from>[^,.;!?]{1,100}?)\s+"
        r"(?:to|into|toward|towards)\s+(?P<to>[^.;!?]{1,120})",
        re.I,
    ),
)

ROTATION_PAIRED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:减持|减配|低配|卖出|减仓|降低配置|削减配置)\s*"
        r"(?P<from>[^，,。；;！？!?]{1,80}?)\s*(?:，|,|；|;|、|并(?:且)?|同时|转而)\s*"
        r"(?:增持|增配|超配|买入|加仓|提高配置)\s*(?P<to>[^。；;！？!?]{1,100})",
        re.I,
    ),
    re.compile(
        r"(?:underweight|reduce|trim|sell|cut)\s+(?:exposure|allocation|holdings|positions)?\s*(?:to|in)?\s*"
        r"(?P<from>[^,.;!?]{1,100}?)\s*(?:,|;|\band\b|\bwhile\b)\s*"
        r"(?:overweight|add|increase|buy)\s+(?:exposure|allocation|holdings|positions)?\s*(?:to|in)?\s*"
        r"(?P<to>[^.;!?]{1,120})",
        re.I,
    ),
)

ROTATION_RETROSPECTIVE_MARKERS = (
    "去年",
    "前年",
    "上季度",
    "此前曾",
    "回顾",
    "历史上",
    "last year",
    "last quarter",
    "historically",
    "in retrospect",
    "had rotated",
    "previously rotated",
)

ROTATION_RUMOR_OR_NEGATION_MARKERS = (
    "传闻",
    "网传",
    "据传",
    "未经证实",
    "并未建议",
    "没有建议",
    "不建议",
    "否认",
    "rumor",
    "unconfirmed",
    "does not recommend",
    "did not recommend",
    "not recommend",
    "denied",
)

ROTATION_PRICE_ACTION_MARKERS = (
    "市场资金从",
    "板块资金从",
    "股价轮动",
    "涨幅轮动",
    "成交轮动",
    "market rotated",
    "market rotation",
    "price action",
    "fund flows rotated",
)

ROTATION_NON_ALLOCATION_PATTERNS = (
    re.compile(r"(?:capex|capital expenditure).{0,30}(?:opex|operating expenditure)", re.I),
    re.compile(r"(?:资本开支|资本支出).{0,30}(?:运营开支|运营支出)", re.I),
    re.compile(r"(?:商业模式|盈利模式|会计处理).{0,30}(?:轮动|转向|切换)", re.I),
)

ROTATION_ADVISORY_MARKERS = (
    "建议",
    "提示投资者",
    "主张",
    "配置策略",
    "投资策略",
    "调整配置",
    "recommend",
    "advise",
    "strategy",
    "allocation",
    "positioning",
    "calls for",
    "urges investors",
)

ROTATION_ALLOCATION_CONTEXT_MARKERS = (
    "投资者",
    "投资策略",
    "资金",
    "配置",
    "仓位",
    "持仓",
    "头寸",
    "调仓",
    "减配",
    "增配",
    "超配",
    "低配",
    "买入",
    "卖出",
    "资产轮动",
    "portfolio",
    "investor",
    "funds",
    "allocation",
    "exposure",
    "positioning",
    "overweight",
    "underweight",
    "buy",
    "sell",
    "reallocate",
)

VALUE_DIRECTORY_STRATEGY_TITLE_MARKERS = (
    "交易思路",
    "trade idea",
    "投资策略",
    "investment strategy",
    "策略报告",
)

GENERIC_VALUATION_PATTERNS = (
    "估值上行空间",
    "估值偏低",
    "低估",
    "undervalued",
    "upside",
)

DIRECT_HOLDING_HARD_VARIABLE_KEYWORDS = (
    "订单",
    "大单",
    "合同",
    "销售合约",
    "供货",
    "供应协议",
    "客户认证",
    "客户验证",
    "定点",
    "涨价",
    "提价",
    "价格上调",
    "降价",
    "价格下调",
    "扩产",
    "产能",
    "投产",
    "停产",
    "减产",
    "良率",
    "资本开支",
    "capex",
    "并购",
    "收购",
    "剥离",
    "出口管制",
    "制裁",
    "禁令",
    "业绩预告",
    "业绩快报",
    "指引",
    "上修",
    "下修",
    "order",
    "contract",
    "supply agreement",
    "qualification",
    "certification",
    "price increase",
    "price cut",
    "capacity",
    "expansion",
    "production halt",
    "cut output",
    "guidance",
    "raise guidance",
    "cut guidance",
)

OFFICIAL_COMPANY_SOURCES = {
    "openai_news",
    "nvidia_blog",
    "nvidia_developer_blog",
    "samsung_semiconductor_news",
    "samsung_global_semiconductor",
    "skhynix_newsroom",
    "micron_news_releases",
}

OFFICIAL_COMPANY_HARD_VARIABLE_KEYWORDS = (
    "hbm",
    "hbm3",
    "hbm3e",
    "hbm4",
    "dram",
    "nand",
    "gpu",
    "blackwell",
    "rubin",
    "ai factory",
    "data center",
    "datacenter",
    "rack-scale",
    "liquid cooling",
    "liquid-cooled",
    "advanced packaging",
    "mass production",
    "volume production",
    "sampling",
    "sample",
    "qualification",
    "capacity",
    "capex",
    "capital expenditure",
    "investment",
    "supply agreement",
    "供应",
    "供货",
    "量产",
    "样品",
    "送样",
    "客户认证",
    "认证",
    "产能",
    "扩产",
    "资本开支",
    "投资",
    "液冷",
    "先进封装",
    "高带宽内存",
)

MACRO_TARGETS = ("美债收益率/美元", "A股风险偏好", "成长股估值")
VALUE_DIRECTORY_SOURCE = "value_directory_ib_stocks"
VALUE_DIRECTORY_SOURCES = {"value_directory_ib_stocks", "value_directory_ib_industry_macro"}


def compact_text(*values: object) -> str:
    return re.sub(r"\s+", " ", " ".join(str(value or "") for value in values)).strip()


def contains_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    for keyword in keywords:
        normalized = keyword.lower().strip()
        if not normalized:
            continue
        if re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", normalized):
            if re.search(rf"\b{re.escape(normalized)}\b", lowered):
                return True
        elif normalized in lowered:
            return True
    return False


def holding_tokens(holding: dict[str, Any]) -> list[str]:
    values = [
        str(holding.get("symbol") or ""),
        str(holding.get("name") or ""),
        str(holding.get("full_name") or ""),
        *(str(item) for item in holding.get("aliases") or []),
    ]
    tokens: list[str] = []
    for value in values:
        value = value.strip()
        if not value:
            continue
        tokens.append(value)
        if "." in value:
            tokens.append(value.split(".", 1)[0])
    return tokens


def holding_news_keywords(holding: dict[str, Any], key: str) -> list[str]:
    values = holding.get(key)
    if not isinstance(values, list):
        raw = holding.get("raw") if isinstance(holding.get("raw"), dict) else {}
        values = raw.get(key) if isinstance(raw.get(key), list) else []
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        keyword = str(value or "").strip()
        if not keyword or keyword.casefold() in seen:
            continue
        seen.add(keyword.casefold())
        result.append(keyword)
    return result


def matched_holdings(text: str, holdings: list[dict[str, Any]], symbols: set[str] | None = None) -> list[dict[str, Any]]:
    lowered = text.lower()
    symbols = {symbol.upper() for symbol in symbols or set() if symbol}
    result: list[dict[str, Any]] = []
    for holding in holdings:
        symbol = str(holding.get("symbol") or "").upper()
        if symbol and symbol in symbols:
            result.append(holding)
            continue
        if any(token and token.lower() in lowered for token in holding_tokens(holding)):
            result.append(holding)
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for holding in result:
        key = str(holding.get("symbol") or holding.get("name") or "").upper()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(holding)
    return deduped


def matched_holding_news_keywords(text: str, holdings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return configured holding-specific related keywords that match text.

    These terms are deliberately separate from aliases: a peer or industry
    keyword should trigger an alert for the holding without being represented
    as a direct company mention.
    """
    matches: list[dict[str, Any]] = []
    for holding in holdings:
        excludes = holding_news_keywords(holding, "news_exclude_keywords")
        if any(keyword_matches_text(keyword, text) for keyword in excludes):
            continue
        keywords = holding_news_keywords(holding, "news_keywords")
        matched = [keyword for keyword in keywords if keyword_matches_text(keyword, text)]
        if matched:
            matches.append({"holding": holding, "keywords": matched})
    return matches


def load_enabled_holdings_for_rules(db_path: Path | None = None) -> list[dict[str, Any]]:
    """Load enabled holdings without importing the event pipeline."""
    from db_utils import connect_sqlite  # Local import keeps this rule module lightweight.
    from market_db import DEFAULT_DB_PATH, init_db

    path = db_path or DEFAULT_DB_PATH
    init_db(path).close()
    with connect_sqlite(path) as conn:
        rows = conn.execute(
            """
            SELECT symbol, name, full_name, aliases_json, raw_json
            FROM portfolio_holdings
            WHERE enabled = 1
            ORDER BY symbol
            """
        ).fetchall()
    holdings: list[dict[str, Any]] = []
    for symbol, name, full_name, aliases_json, raw_json in rows:
        aliases: list[str] = []
        raw: dict[str, Any] = {}
        try:
            import json

            aliases = json.loads(aliases_json or "[]")
        except Exception:  # noqa: BLE001 - invalid local config should not break rules.
            aliases = []
        try:
            import json

            raw = json.loads(raw_json or "{}")
        except Exception:  # noqa: BLE001
            raw = {}
        holdings.append(
            {
                "symbol": symbol,
                "name": name,
                "full_name": full_name or "",
                "aliases": aliases if isinstance(aliases, list) else [],
                "news_keywords": raw.get("news_keywords") if isinstance(raw.get("news_keywords"), list) else [],
                "news_exclude_keywords": (
                    raw.get("news_exclude_keywords") if isinstance(raw.get("news_exclude_keywords"), list) else []
                ),
                "raw": raw if isinstance(raw, dict) else {},
            }
        )
    return holdings


def _to_float(value: str) -> float | None:
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def extract_target_gap(text: str) -> dict[str, Any]:
    """Best-effort extraction of target/current price gap from short news text."""
    patterns = (
        re.compile(
            r"(?P<current>\d+(?:\.\d+)?)\s*(?:元|港元|美元)?\s*(?:股价|现价|当前价|current price|share price)?\s*"
            r"(?:vs|VS|对|/|，|,|\s+)\s*(?P<target>\d+(?:\.\d+)?)\s*(?:元|港元|美元)?\s*(?:目标价|target price|TP|tp)",
            re.I,
        ),
        re.compile(
            r"(?:目标价|target price|TP|tp)\D{0,18}(?P<target>\d+(?:\.\d+)?).*?"
            r"(?:现价|当前价|股价|current price|share price)\D{0,18}(?P<current>\d+(?:\.\d+)?)",
            re.I | re.S,
        ),
        re.compile(
            r"(?:现价|当前价|股价|current price|share price)\D{0,18}(?P<current>\d+(?:\.\d+)?).*?"
            r"(?:目标价|target price|TP|tp)\D{0,18}(?P<target>\d+(?:\.\d+)?)",
            re.I | re.S,
        ),
    )
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        current = _to_float(match.group("current"))
        target = _to_float(match.group("target"))
        if not current or not target or current <= 0:
            continue
        gap = (target - current) / current
        return {"current_price": current, "target_price": target, "target_gap_pct": gap}
    return {}


def _matches_pattern(text: str, pattern: str) -> bool:
    if ".*" in pattern or "\\d" in pattern:
        return re.search(pattern, text, flags=re.I | re.S) is not None
    return keyword_matches_text(pattern, text)


def _strategy_actions(text: str, extra_actions: list[str]) -> list[str]:
    actions: list[str] = []
    for label, phrases in THEME_STRATEGY_ACTIONS:
        if any(keyword_matches_text(phrase, text) for phrase in phrases):
            actions.append(label)
    for action in extra_actions:
        if keyword_matches_text(action, text):
            actions.append(action)
    return list(dict.fromkeys(actions))


def _strategy_themes(text: str, extra_themes: list[str]) -> list[str]:
    themes: list[str] = []
    for label, phrases in THEME_STRATEGY_THEMES:
        if any(keyword_matches_text(phrase, text) for phrase in phrases):
            themes.append(label)
    for theme in extra_themes:
        if keyword_matches_text(theme, text):
            themes.append(theme)
    return list(dict.fromkeys(themes))


def _rotation_aliases(extra_aliases: list[str]) -> dict[str, tuple[str, ...]]:
    aliases = {
        str(bucket["id"]): tuple(str(alias) for alias in bucket["aliases"])
        for bucket in ROTATION_THEME_BUCKETS
    }
    additions: dict[str, list[str]] = {}
    for mapping in extra_aliases:
        theme_id, separator, alias = str(mapping).partition("=")
        theme_id = theme_id.strip()
        alias = alias.strip()
        if separator and theme_id in aliases and alias:
            additions.setdefault(theme_id, []).append(alias)
    return {
        theme_id: tuple(dict.fromkeys((*values, *additions.get(theme_id, []))))
        for theme_id, values in aliases.items()
    }


def _rotation_themes(text: str, extra_aliases: list[str]) -> list[str]:
    aliases = _rotation_aliases(extra_aliases)
    return [
        str(bucket["id"])
        for bucket in ROTATION_THEME_BUCKETS
        if any(keyword_matches_text(alias, text) for alias in aliases[str(bucket["id"])])
    ]


def _rotation_theme_labels(theme_ids: list[str]) -> list[str]:
    return [str(ROTATION_THEME_BY_ID[theme_id]["label"]) for theme_id in theme_ids if theme_id in ROTATION_THEME_BY_ID]


def _rotation_statement_is_attributed(statement: str) -> bool:
    lowered = statement.casefold()
    if any(marker.casefold() in lowered for marker in ROTATION_ADVISORY_MARKERS):
        return True
    for _display, aliases in INTERNATIONAL_BANK_ALIASES:
        for alias in aliases:
            escaped = re.escape(alias).replace(r"\ ", r"\s+")
            if re.search(rf"{escaped}.{{0,20}}(?:[:：]|称|表示|认为|指出|says?|sees?)", statement, flags=re.I):
                return True
    return False


def _rotation_has_allocation_context(statement: str) -> bool:
    lowered = statement.casefold()
    return any(marker.casefold() in lowered for marker in ROTATION_ALLOCATION_CONTEXT_MARKERS)


def _rotation_match_is_relevant(theme_ids: list[str], config: dict[str, Any], universe: dict[str, Any]) -> bool:
    if any(bool(ROTATION_THEME_BY_ID[theme_id]["style"]) for theme_id in theme_ids):
        if not config["allow_broad_style_rotation"]:
            return False
    if not config["require_investment_universe_leg"]:
        return True
    approved_bucket = any(
        not bool(ROTATION_THEME_BY_ID[theme_id]["style"]) or config["allow_broad_style_rotation"]
        for theme_id in theme_ids
    )
    return approved_bucket or bool(universe.get("matched"))


def _rotation_candidate(text: str) -> bool:
    return any(pattern.search(text) for pattern in (*ROTATION_DIRECT_PATTERNS, *ROTATION_PAIRED_PATTERNS))


def _extract_rotation_strategy(
    text: str,
    *,
    config: dict[str, Any],
    universe: dict[str, Any],
) -> dict[str, Any] | None:
    for pattern in (*ROTATION_DIRECT_PATTERNS, *ROTATION_PAIRED_PATTERNS):
        for match in pattern.finditer(text):
            statement_start = max(0, text.rfind("。", 0, match.start()) + 1)
            statement_end = text.find("。", match.end())
            statement = text[statement_start : statement_end if statement_end >= 0 else len(text)].strip()
            lowered = statement.casefold()
            retrospective = any(marker.casefold() in lowered for marker in ROTATION_RETROSPECTIVE_MARKERS)
            if retrospective:
                continue
            if any(marker.casefold() in lowered for marker in ROTATION_RUMOR_OR_NEGATION_MARKERS):
                continue
            if any(marker.casefold() in lowered for marker in ROTATION_PRICE_ACTION_MARKERS):
                continue
            if any(pattern.search(statement) for pattern in ROTATION_NON_ALLOCATION_PATTERNS):
                continue
            if not _rotation_statement_is_attributed(statement):
                continue
            if not _rotation_has_allocation_context(statement):
                continue
            from_text = match.group("from").strip(" ：:，,、")
            to_text = match.group("to").strip(" ：:，,、")
            from_themes = _rotation_themes(from_text, config["extra_rotation_theme_aliases"])
            to_themes = _rotation_themes(to_text, config["extra_rotation_theme_aliases"])
            if not from_themes or not to_themes or set(from_themes) & set(to_themes):
                continue
            all_themes = list(dict.fromkeys((*from_themes, *to_themes)))
            if not _rotation_match_is_relevant(all_themes, config, universe):
                continue
            return {
                "strategy_type": "rotation",
                "from_themes": from_themes,
                "to_themes": to_themes,
                "from_labels": _rotation_theme_labels(from_themes),
                "to_labels": _rotation_theme_labels(to_themes),
                "evidence_quotes": [statement[:320]],
                "retrospective": False,
            }
    return None


def _strategy_evidence(text: str, themes: list[str]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for kind, score, patterns in THEME_EVIDENCE_PATTERNS:
        matched = next((pattern for pattern in patterns if _matches_pattern(text, pattern)), "")
        if matched:
            evidence.append({"kind": kind, "score": score, "snippet": matched})
    if not any(item["kind"] == "完整估值错配/比较" for item in evidence):
        matched = next((pattern for pattern in GENERIC_VALUATION_PATTERNS if _matches_pattern(text, pattern)), "")
        if matched:
            evidence.append({"kind": "泛估值上行/低估表述", "score": 1, "snippet": matched})
    if len(themes) >= 3 or any(keyword_matches_text(pattern, text) for pattern in ("行业篮子", "标的篮子", "多环节", "产业链")):
        evidence.append({"kind": "多环节/行业篮子", "score": 1, "snippet": "多环节主题覆盖"})
    return evidence


def _report_reference(text: str) -> str:
    quoted = re.search(r"《([^》]{3,120})》", text)
    if quoted:
        return quoted.group(1).strip()
    for pattern in (
        r"(?:报告|研报|投资策略|策略报告|research|strategy)\s*[:：-]\s*([^。；;\n]{3,120})",
        r"([^。；;\n]{3,120})(?:报告|研报)",
    ):
        match = re.search(pattern, text, flags=re.I)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    return ""


def _source_tier(source: str, item: dict[str, Any]) -> str:
    if source in VALUE_DIRECTORY_SOURCES:
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
        preview = raw.get("value_directory_preview") if isinstance(raw.get("value_directory_preview"), dict) else {}
        facts = preview.get("facts") if isinstance(preview.get("facts"), dict) else {}
        if facts.get("status") == "ok":
            return "价值目录研报索引（含可见第一页预览）"
        return "价值目录研报索引（仅标题元数据）"
    source_text = compact_text(source, item.get("source_module"), item.get("source_display"), item.get("url"))
    if any(token in source_text.casefold() for token in ("goldmansachs.com", "morganstanley.com", "jpmorgan.com", "citigroup.com")):
        return "机构公开材料"
    text = compact_text(item.get("title"), item.get("summary"), item.get("content"), item.get("full_text"))
    if any(token in text for token in ("网传", "传闻", "未经证实", "市场消息")):
        return "二手转述/待原报告确认"
    return "媒体明确署名转述"


def _published_day(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "undated"
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        match = re.search(r"\d{4}-\d{2}-\d{2}", raw)
        return match.group(0) if match else "undated"


def _theme_dedup_key(bank: str, report_title: str, themes: list[str], action: str, published_at: object) -> str:
    identity = report_title or "|".join(themes[:3]) or action
    normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", identity.casefold())
    digest = hashlib.sha256(f"{bank}|{_published_day(published_at)}|{normalized}|{action}".encode("utf-8")).hexdigest()[:20]
    return f"ib_theme:{digest}"


def _rotation_dedup_key(bank: str, from_themes: list[str], to_themes: list[str]) -> str:
    identity = f"{bank}|from:{'|'.join(sorted(from_themes))}|to:{'|'.join(sorted(to_themes))}"
    digest = hashlib.sha256(identity.casefold().encode("utf-8")).hexdigest()[:20]
    return f"ib_rotation:{digest}"


def _value_directory_strategy_title_evidence(source: str, item: dict[str, Any]) -> list[dict[str, Any]]:
    """Treat an explicit strategy-report title as auditable index metadata.

    ValueList is intentionally collected as title/date/URL metadata only. For
    this one designated index source, a title such as "Trade Idea: Long
    China AI Value Chain" identifies a concrete multi-leg strategy report,
    rather than a generic opinion. It still has to pass the bank, action, and
    investment-universe checks in the caller.
    """
    if source not in VALUE_DIRECTORY_SOURCES:
        return []
    title = compact_text(item.get("title"))
    for marker in VALUE_DIRECTORY_STRATEGY_TITLE_MARKERS:
        if keyword_matches_text(marker, title):
            return [{"kind": "价值目录策略研报标题", "score": 2, "snippet": marker}]
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    preview = raw.get("value_directory_preview") if isinstance(raw.get("value_directory_preview"), dict) else {}
    facts = preview.get("facts") if isinstance(preview.get("facts"), dict) else {}
    if facts.get("status") == "ok" and any(
        keyword_matches_text(marker, compact_text(facts.get("core_content"), " ".join(facts.get("key_points") or [])))
        for marker in VALUE_DIRECTORY_STRATEGY_TITLE_MARKERS
    ):
        return [{"kind": "价值目录第一页策略证据", "score": 2, "snippet": "visible_first_page_only"}]
    return []


def international_bank_theme_strategy_rule(
    *,
    source: str,
    item: dict[str, Any],
    holdings: list[dict[str, Any]],
    symbols: set[str] | None = None,
) -> dict[str, Any] | None:
    """Force major, relevant international-bank allocation strategy reports to push."""
    del symbols
    config = load_config()
    if not config["enabled"]:
        return None
    text = compact_text(
        item.get("title"),
        item.get("summary"),
        item.get("content"),
        item.get("full_text"),
        item.get("source_module"),
        item.get("source_display"),
    )
    if not text:
        return None
    allowed = {value.casefold() for value in config["allowed_banks"]}
    banks = matched_bank_names(text, allowed_banks=allowed or None)
    if not banks:
        return None
    universe = investment_universe_match(source, item)
    rotation = _extract_rotation_strategy(text, config=config, universe=universe) if _rotation_candidate(text) else None
    if _rotation_candidate(text) and rotation is None:
        return None
    if rotation is None and any(pattern.search(text) for pattern in ROTATION_NON_ALLOCATION_PATTERNS):
        return None
    if rotation:
        actions = ["配置轮动"]
        themes = list(dict.fromkeys((*rotation["from_labels"], *rotation["to_labels"])))
        evidence = [
            {
                "kind": "明确双腿配置轮动",
                "score": 2,
                "snippet": rotation["evidence_quotes"][0],
            }
        ]
        evidence.extend(_strategy_evidence(text, themes))
    else:
        actions = _strategy_actions(text, config["extra_action_keywords"])
        if not actions:
            return None
        themes = _strategy_themes(text, config["extra_theme_keywords"])
        if not themes and not universe.get("matched"):
            return None
        evidence = _strategy_evidence(text, themes)
        evidence.extend(_value_directory_strategy_title_evidence(source, item))
    evidence_score = sum(int(item["score"]) for item in evidence)
    if evidence_score < config["min_evidence_score"]:
        return None
    tier = _source_tier(source, item)
    if tier != "机构公开材料" and not config["allow_secondary_sources"]:
        return None
    report_title = _report_reference(text)
    bank = banks[0]
    action = actions[0]
    rotation_display = ""
    targets = themes[:4]
    if rotation:
        rotation_display = f"{'、'.join(rotation['from_labels'])} -> {'、'.join(rotation['to_labels'])}"
        targets = [rotation_display, *rotation["to_labels"], *rotation["from_labels"]]
    for holding in matched_holdings(text, holdings):
        label = " ".join(part for part in (str(holding.get("name") or ""), str(holding.get("symbol") or "")) if part)
        if label:
            targets.append(label)
    targets = list(dict.fromkeys(targets))[:5]
    evidence_text = "；".join(f"{item['kind']}（{item['snippet']}）" for item in evidence[:3])
    if rotation:
        reason = (
            f"国际投行重大主题策略规则：{bank}明确配置轮动“{rotation_display}”；"
            f"重大性证据 {evidence_text}；来源层级：{tier}。"
        )
    else:
        reason = (
            f"国际投行重大主题策略规则：{bank}明确“{action}”{(' / ' + '、'.join(themes[:3])) if themes else ''}；"
            f"重大性证据 {evidence_text}；来源层级：{tier}。"
        )
    if tier == "二手转述/待原报告确认":
        reason += " 原报告尚待确认，先按明确署名策略观点提醒。"
    return {
        "matched": True,
        "rule_id": "international_bank_theme_strategy",
        "importance": "high",
        "push_now": True,
        "should_push": True,
        "reason": reason,
        "brief_reason": reason,
        "affected_targets": targets,
        "related_targets": [
            {"name": target, "code": "", "relation": "国际投行主题策略", "direction": "uncertain"} for target in targets
        ],
        "banks": banks,
        "action": action,
        "actions": actions,
        "themes": themes,
        "strategy_type": "rotation" if rotation else "theme_allocation",
        "from_themes": list(rotation["from_themes"]) if rotation else [],
        "to_themes": list(rotation["to_themes"]) if rotation else [],
        "evidence_quotes": list(rotation["evidence_quotes"]) if rotation else [],
        "retrospective": bool(rotation["retrospective"]) if rotation else False,
        "rotation_display": rotation_display,
        "report_title": report_title,
        "source_tier": tier,
        "evidence_score": evidence_score,
        "evidence": evidence,
        "dedup_key": (
            _rotation_dedup_key(bank, rotation["from_themes"], rotation["to_themes"])
            if rotation
            else _theme_dedup_key(bank, report_title, themes, action, item.get("published_at"))
        ),
        "dedup_lookback_days": config["dedup_lookback_days"],
        "source": source,
    }


def investment_bank_research_rule(
    *,
    source: str,
    item: dict[str, Any],
    holdings: list[dict[str, Any]],
    symbols: set[str] | None = None,
) -> dict[str, Any] | None:
    """Force direct-holding international bank rating/target-price news to push."""
    text = compact_text(
        item.get("title"),
        item.get("summary"),
        item.get("content"),
        item.get("full_text"),
        item.get("source_module"),
        item.get("source_display"),
    )
    if not text:
        return None
    if not rule_enabled("investment_bank_rating_target_direct_holding"):
        return None
    allowed_banks = {item.casefold() for item in effective_list("investment_bank_rating_target_direct_holding", "allowed_banks", ())}
    banks = matched_bank_names(text, allowed_banks=allowed_banks or None)
    keywords = effective_list("investment_bank_rating_target_direct_holding", "extra_keywords", RATING_OR_TARGET_KEYWORDS)
    if not banks or not contains_keyword(text, keywords):
        return None
    direct_holdings = matched_holdings(text, holdings, symbols=symbols)
    if not direct_holdings:
        return None
    gap = extract_target_gap(text)
    gap_text = ""
    if gap:
        gap_text = f"；目标价较现价约 {gap['target_gap_pct'] * 100:.1f}%"
    target_labels = []
    for holding in direct_holdings[:5]:
        name = str(holding.get("name") or "").strip()
        code = str(holding.get("symbol") or "").strip()
        target_labels.append(" ".join(part for part in (name, code) if part))
    bank_label = "、".join(banks[:3])
    reason = (
        f"国际投行/头部券商研报硬规则：{bank_label} 对直接持仓或观察标的给出目标价/评级相关观点{gap_text}。"
        "这类估值锚变化必须即时提醒；LLM 的“已有预期/已定价”只能作为备注，不能压制推送。"
    )
    return {
        "matched": True,
        "rule_id": "investment_bank_rating_target_direct_holding",
        "importance": "high",
        "push_now": True,
        "should_push": True,
        "reason": reason,
        "brief_reason": reason,
        "affected_targets": target_labels,
        "related_targets": [
            {
                "name": str(holding.get("name") or "").strip(),
                "code": str(holding.get("symbol") or "").strip(),
                "relation": "直接持仓/观察",
                "direction": "uncertain",
            }
            for holding in direct_holdings[:5]
        ],
        "banks": banks,
        "target_gap": gap,
        "source": source,
    }


def _holding_label(holding: dict[str, Any]) -> str:
    return " ".join(
        part
        for part in (str(holding.get("name") or "").strip(), str(holding.get("symbol") or "").strip())
        if part
    )


def holding_keyword_immediate_alert_rule(
    *,
    source: str,
    item: dict[str, Any],
    holdings: list[dict[str, Any]],
    symbols: set[str] | None = None,
) -> dict[str, Any] | None:
    """Push any monitored article/event tied to a holding or its attention terms."""
    if not rule_enabled("holding_keyword_immediate_alert"):
        return None
    text = compact_text(
        item.get("title"),
        item.get("summary"),
        item.get("content"),
        item.get("full_text"),
        item.get("source_module"),
        item.get("source_display"),
    )
    if not text:
        return None
    direct_holdings = matched_holdings(text, holdings, symbols=symbols)
    keyword_matches = matched_holding_news_keywords(text, holdings)
    if not direct_holdings and not keyword_matches:
        return None

    related_targets: list[dict[str, Any]] = []
    labels: list[str] = []
    reason_parts: list[str] = []
    seen_symbols: set[str] = set()
    if direct_holdings:
        direct_labels = [_holding_label(holding) for holding in direct_holdings]
        direct_labels = [label for label in direct_labels if label]
        if direct_labels:
            reason_parts.append(f"直接持仓命中：{'、'.join(direct_labels[:5])}")
        for holding in direct_holdings:
            symbol = str(holding.get("symbol") or "").strip()
            name = str(holding.get("name") or "").strip()
            label = _holding_label(holding)
            if label:
                labels.append(label)
            if symbol.upper() not in seen_symbols:
                related_targets.append(
                    {"name": name, "code": symbol, "relation": "直接持仓", "direction": "uncertain"}
                )
                seen_symbols.add(symbol.upper())

    keyword_labels: list[str] = []
    for match in keyword_matches:
        holding = match["holding"]
        symbol = str(holding.get("symbol") or "").strip()
        name = str(holding.get("name") or "").strip()
        keywords = [str(keyword).strip() for keyword in match.get("keywords") or [] if str(keyword).strip()]
        if not keywords:
            continue
        label = _holding_label(holding)
        if label:
            labels.append(label)
        keyword_labels.append(f"{'、'.join(keywords[:3])} -> {name or symbol}")
        if symbol.upper() not in seen_symbols:
            related_targets.append(
                {
                    "name": name,
                    "code": symbol,
                    "relation": f"关联关键词：{'、'.join(keywords[:3])}",
                    "direction": "uncertain",
                }
            )
            seen_symbols.add(symbol.upper())
    if keyword_labels:
        reason_parts.append(f"关联关键词命中：{'；'.join(keyword_labels[:5])}")

    reason = (
        "持仓/关联关键词即时提醒："
        + "；".join(reason_parts)
        + "。该规则读取当前运行环境的持仓配置，不由 LLM 决定是否推送。"
    )
    return {
        "matched": True,
        "rule_id": "holding_keyword_immediate_alert",
        "importance": "high",
        "push_now": True,
        "should_push": True,
        "reason": reason,
        "brief_reason": reason,
        "affected_targets": list(dict.fromkeys(labels))[:5],
        "related_targets": related_targets[:5],
        "source": source,
    }


def value_directory_portfolio_relation_rule(
    *,
    source: str,
    item: dict[str, Any],
    holdings: list[dict[str, Any]],
    symbols: set[str] | None = None,
) -> dict[str, Any] | None:
    """Push ValueList bank-stock reports linked to a holding through one-hop maps."""
    del symbols
    if source not in VALUE_DIRECTORY_SOURCES or not rule_enabled("investment_bank_portfolio_relation"):
        return None
    text = compact_text(
        item.get("title"),
        item.get("summary"),
        item.get("content"),
        item.get("full_text"),
        item.get("source_module"),
        item.get("source_display"),
    )
    if not text:
        return None
    allowed_banks = {
        item.casefold()
        for item in effective_list("investment_bank_portfolio_relation", "allowed_banks", ())
    }
    banks = matched_bank_names(text, allowed_banks=allowed_banks or None)
    if not banks:
        return None

    direct_holdings = matched_holdings(text, holdings)
    max_relations = int(rule_settings("investment_bank_portfolio_relation").get("max_relation_matches") or 3)
    relation_matches = portfolio_relation_matches(text, holdings, max_matches=max(1, min(max_relations, 5)))
    if not direct_holdings and not relation_matches:
        return None

    related_targets: list[dict[str, Any]] = []
    labels: list[str] = []
    for holding in direct_holdings:
        label = _holding_label(holding)
        if label:
            labels.append(label)
        related_targets.append(
            {
                "name": str(holding.get("name") or "").strip(),
                "code": str(holding.get("symbol") or "").strip(),
                "relation": "直接持仓/观察",
                "direction": "uncertain",
            }
        )
    paths: list[str] = []
    seen_symbols = {str(item.get("code") or "").upper() for item in related_targets}
    for match in relation_matches:
        holding_name = str(match.get("holding_name") or "").strip()
        holding_symbol = str(match.get("holding_symbol") or "").strip()
        label = " ".join(part for part in (holding_name, holding_symbol) if part)
        if label:
            labels.append(label)
        if holding_symbol.upper() not in seen_symbols:
            related_targets.append(
                {
                    "name": holding_name,
                    "code": holding_symbol,
                    "relation": str(match.get("relation_type") or "持仓关联"),
                    "direction": str(match.get("impact_direction") or "uncertain"),
                }
            )
            seen_symbols.add(holding_symbol.upper())
        trigger = str(match.get("trigger_name") or match.get("matched_term") or "").strip()
        relation_type = str(match.get("relation_type") or "持仓关联").strip()
        if trigger and holding_name:
            paths.append(f"{trigger} -> {relation_type} -> {holding_name}")

    labels = list(dict.fromkeys(label for label in labels if label))[:5]
    paths = list(dict.fromkeys(path for path in paths if path))[:3]
    bank_label = "、".join(banks[:3])
    reason = (
        f"价值目录国际投行持仓关联规则：{bank_label}个股研报命中"
        f"{'直接持仓或' if direct_holdings else ''}已配置的一跳同业/行业关系，必须即时提醒。"
    )
    if paths:
        reason += f" 关联路径：{'；'.join(paths)}。"
    reason += " 该判断只使用用户维护的关系映射，不由 LLM 自行推断。"
    return {
        "matched": True,
        "rule_id": "investment_bank_portfolio_relation",
        "importance": "high",
        "push_now": True,
        "should_push": True,
        "reason": reason,
        "brief_reason": reason,
        "affected_targets": labels,
        "related_targets": related_targets[:5],
        "banks": banks,
        "relation_matches": relation_matches,
        "source": source,
    }


def value_directory_industry_macro_research_rule(
    *,
    source: str,
    item: dict[str, Any],
    holdings: list[dict[str, Any]],
    symbols: set[str] | None = None,
) -> dict[str, Any] | None:
    """Push relevant ValueList international-bank industry/macro reports.

    Unlike the major theme-strategy rule, this does not require an explicit
    action word such as long/overweight. The source itself is a curated
    investment-bank research index, so the guardrails are: ValueList industry
    macro source, recognized bank, and the project's investment universe or
    holding keywords.
    """
    del symbols
    if source != "value_directory_ib_industry_macro" or not rule_enabled("value_directory_industry_macro_research"):
        return None
    text = compact_text(
        item.get("title"),
        item.get("summary"),
        item.get("content"),
        item.get("full_text"),
        item.get("source_module"),
        item.get("source_display"),
    )
    if not text:
        return None
    allowed_banks = {
        item.casefold()
        for item in effective_list("value_directory_industry_macro_research", "allowed_banks", ())
    }
    banks = matched_bank_names(text, allowed_banks=allowed_banks or None)
    if not banks:
        return None
    universe = investment_universe_match(source, item)
    keyword_matches = matched_holding_news_keywords(text, holdings)
    if not universe.get("matched") and not keyword_matches:
        return None
    themes = _strategy_themes(
        text,
        list(effective_list("value_directory_industry_macro_research", "extra_theme_keywords", ())),
    )
    labels = list(dict.fromkeys([*themes, *(str(match.get("holding", {}).get("name") or "") for match in keyword_matches)]))
    labels = [label for label in labels if label][:5]
    if not labels:
        labels = ["半导体/AI 基础设施"]
    keyword_text = ""
    if keyword_matches:
        pairs = []
        for match in keyword_matches[:3]:
            holding = match.get("holding") or {}
            keywords = "、".join(str(keyword) for keyword in match.get("keywords") or [] if str(keyword))[:80]
            holding_name = str(holding.get("name") or holding.get("symbol") or "").strip()
            if keywords and holding_name:
                pairs.append(f"{keywords} -> {holding_name}")
        if pairs:
            keyword_text = f"；持仓关联关键词命中：{'；'.join(pairs)}"
    reason = (
        f"价值目录国际投行行业宏观规则：{banks[0]} 行业/宏观研报命中本项目投资宇宙"
        f"（{universe.get('reason') or '价值目录行业宏观来源'}）{keyword_text}。"
        "先发送标题与可见第一页提取，完整研报不下载。"
    )
    return {
        "matched": True,
        "rule_id": "value_directory_industry_macro_research",
        "importance": "high",
        "push_now": True,
        "should_push": True,
        "reason": reason,
        "brief_reason": reason,
        "affected_targets": labels,
        "related_targets": [
            {"name": label, "code": "", "relation": "国际投行行业/宏观研报", "direction": "uncertain"}
            for label in labels
        ],
        "banks": banks,
        "themes": themes,
        "source": source,
    }


def direct_holding_hard_variable_rule(
    *,
    source: str,
    item: dict[str, Any],
    holdings: list[dict[str, Any]],
    symbols: set[str] | None = None,
) -> dict[str, Any] | None:
    text = compact_text(
        item.get("title"),
        item.get("summary"),
        item.get("content"),
        item.get("full_text"),
        item.get("source_module"),
        item.get("source_display"),
    )
    if not text or not rule_enabled("direct_holding_hard_variable"):
        return None
    keywords = effective_list("direct_holding_hard_variable", "extra_keywords", DIRECT_HOLDING_HARD_VARIABLE_KEYWORDS)
    if not contains_keyword(text, keywords):
        return None
    direct_holdings = matched_holdings(text, holdings, symbols=symbols)
    if not direct_holdings:
        return None
    target_labels = []
    for holding in direct_holdings[:5]:
        name = str(holding.get("name") or "").strip()
        code = str(holding.get("symbol") or "").strip()
        target_labels.append(" ".join(part for part in (name, code) if part))
    reason = (
        "直接持仓硬变量规则：资讯命中持仓/观察标的，并涉及订单、涨价、产能、客户认证、资本开支、"
        "并购、管制或业绩指引等可规则化硬变量；先即时提醒，具体影响方向可由用户和后续复盘确认。"
    )
    return {
        "matched": True,
        "rule_id": "direct_holding_hard_variable",
        "importance": "high",
        "push_now": True,
        "should_push": True,
        "reason": reason,
        "brief_reason": reason,
        "affected_targets": target_labels,
        "related_targets": [
            {
                "name": str(holding.get("name") or "").strip(),
                "code": str(holding.get("symbol") or "").strip(),
                "relation": "直接持仓/观察",
                "direction": "uncertain",
            }
            for holding in direct_holdings[:5]
        ],
        "source": source,
    }


def official_company_hard_variable_rule(
    *,
    source: str,
    item: dict[str, Any],
    holdings: list[dict[str, Any]],
    symbols: set[str] | None = None,
) -> dict[str, Any] | None:
    del holdings, symbols
    if not rule_enabled("official_company_hard_variable"):
        return None
    sources = set(effective_list("official_company_hard_variable", "extra_sources", OFFICIAL_COMPANY_SOURCES))
    if str(source or "") not in sources:
        return None
    text = compact_text(
        item.get("title"),
        item.get("summary"),
        item.get("content"),
        item.get("full_text"),
        item.get("source_module"),
        item.get("source_display"),
    )
    keywords = effective_list("official_company_hard_variable", "extra_keywords", OFFICIAL_COMPANY_HARD_VARIABLE_KEYWORDS)
    if not text or not contains_keyword(text, keywords):
        return None
    reason = (
        "公司官网硬变量规则：核心 AI/半导体公司官网内容命中 HBM/存储、GPU/AI 平台、量产/送样、"
        "产能/资本开支、液冷/先进封装或供应协议等硬变量；先即时提醒，完整影响关系可后续校验。"
    )
    return {
        "matched": True,
        "rule_id": "official_company_hard_variable",
        "importance": "high",
        "push_now": True,
        "should_push": True,
        "reason": reason,
        "brief_reason": reason,
        "affected_targets": [source, "AI/半导体产业链"],
        "related_targets": [
            {
                "name": source,
                "code": "",
                "relation": "核心公司官网",
                "direction": "uncertain",
            }
        ],
        "source": source,
    }


def macro_policy_event_rule(
    *,
    source: str,
    item: dict[str, Any],
    holdings: list[dict[str, Any]],
    symbols: set[str] | None = None,
) -> dict[str, Any] | None:
    del source, holdings, symbols
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    if not rule_enabled("macro_policy_line"):
        return None
    macro = raw.get("macro_policy_line") if isinstance(raw.get("macro_policy_line"), dict) else {}
    if not macro.get("matched"):
        return None
    tier = str(macro.get("tier") or "")
    if tier not in {"primary", "secondary_major", "market_reaction"}:
        return None
    reason = str(macro.get("reason") or "美国核心宏观/Fed 政策线命中。")
    full_reason = f"宏观政策硬规则：{reason} 这类信息可能影响美债、美元、A 股风险偏好和成长股估值，先即时提醒。"
    return {
        "matched": True,
        "rule_id": "macro_policy_line",
        "importance": "high",
        "push_now": True,
        "should_push": True,
        "reason": full_reason,
        "brief_reason": full_reason,
        "affected_targets": list(MACRO_TARGETS),
        "related_targets": [
            {"name": target, "code": "", "relation": "美国宏观/Fed 政策线", "direction": "uncertain"}
            for target in MACRO_TARGETS
        ],
        "macro_policy_line": macro,
    }


def yicai_morning_brief_rule(
    *,
    source: str,
    item: dict[str, Any],
    holdings: list[dict[str, Any]],
    symbols: set[str] | None = None,
) -> dict[str, Any] | None:
    del holdings, symbols
    if source != "yicai_brief" or not rule_enabled("yicai_morning_brief"):
        return None
    text = compact_text(item.get("title"), item.get("summary"), item.get("content"), item.get("full_text"))
    if "券商晨会观点速递" not in text:
        return None
    reason = "强制推送规则：第一财经“券商晨会观点速递”为每日固定必读栏目。"
    return {
        "matched": True,
        "rule_id": "yicai_morning_brief",
        "importance": "high",
        "push_now": True,
        "should_push": True,
        "reason": reason,
        "brief_reason": reason,
        "affected_targets": ["券商晨会观点"],
        "related_targets": [
            {
                "name": "券商晨会观点",
                "code": "",
                "relation": "用户指定每日必读栏目",
                "direction": "uncertain",
            }
        ],
        "source": source,
    }


def first_matching_push_rule(
    *,
    source: str,
    item: dict[str, Any],
    holdings: list[dict[str, Any]],
    symbols: set[str] | None = None,
) -> dict[str, Any] | None:
    matchers = (
        ("investment_bank_rating_target_direct_holding", investment_bank_research_rule),
        ("holding_keyword_immediate_alert", holding_keyword_immediate_alert_rule),
        ("investment_bank_portfolio_relation", value_directory_portfolio_relation_rule),
        ("international_bank_theme_strategy", international_bank_theme_strategy_rule),
        ("value_directory_industry_macro_research", value_directory_industry_macro_research_rule),
        ("yicai_morning_brief", yicai_morning_brief_rule),
        ("direct_holding_hard_variable", direct_holding_hard_variable_rule),
        ("official_company_hard_variable", official_company_hard_variable_rule),
        ("macro_policy_line", macro_policy_event_rule),
    )
    for _rule_id, matcher in sorted(matchers, key=lambda item: rule_priority(item[0]), reverse=True):
        rule = matcher(source=source, item=item, holdings=holdings, symbols=symbols)
        if rule:
            return rule
    return None


def review_from_push_rule(rule: dict[str, Any], item: dict[str, Any], *, push_key: str = "push_now") -> dict[str, Any]:
    title = str(item.get("title") or "").strip()
    summary = str(item.get("summary") or item.get("content") or item.get("full_text") or "").strip()
    review = {
        "importance": "high",
        push_key: True,
        "market_impact": "命中确定性推送规则；模型不参与是否推送的最终裁判。",
        "incremental_classification": "规则命中",
        "affected_targets": list(rule.get("affected_targets") or [])[:5],
        "daily_summary": title or summary[:160],
        "reason": str(rule.get("reason") or ""),
        "brief_reason": str(rule.get("brief_reason") or rule.get("reason") or ""),
        "confidence": "规则",
        "model": "push_rule",
        "rule_forced_push": True,
        "raw": {
            "llm_mode": "rule_only",
            "rule_hits": [rule],
        },
    }
    if push_key != "push_now":
        review["push_now"] = True
    return review


def apply_article_push_rules(
    source: str,
    item: dict[str, Any],
    review: dict[str, Any],
    *,
    holdings: list[dict[str, Any]],
    push_key: str = "push_now",
) -> dict[str, Any]:
    rule = first_matching_push_rule(source=source, item=item, holdings=holdings)
    if not rule:
        return review
    updated = dict(review)
    updated[push_key] = True
    updated["importance"] = "high"
    updated["rule_forced_push"] = True
    updated["brief_reason"] = rule["brief_reason"]
    targets = list(updated.get("affected_targets") or [])
    for target in rule.get("affected_targets") or []:
        if target and target not in targets:
            targets.append(target)
    updated["affected_targets"] = targets[:5]
    reason = str(updated.get("reason") or "").strip()
    if rule["reason"] not in reason:
        updated["reason"] = f"{reason}\n{rule['reason']}".strip()
    raw = dict(updated.get("raw") or {})
    hits = list(raw.get("rule_hits") or [])
    hits.append(rule)
    raw["rule_hits"] = hits
    updated["raw"] = raw
    return updated


def apply_event_push_rules(
    event: dict[str, Any],
    analysis: dict[str, Any],
    *,
    holdings: list[dict[str, Any]],
    symbols: set[str] | None = None,
) -> dict[str, Any]:
    rule = first_matching_push_rule(
        source=str(event.get("source") or ""),
        item=event,
        holdings=holdings,
        symbols=symbols,
    )
    if not rule:
        return analysis
    updated = dict(analysis)
    updated["importance"] = "high"
    updated["rule_forced_push"] = True
    updated["brief_reason"] = rule["brief_reason"]
    updated["push_decision"] = {
        "should_push": True,
        "reason": rule["reason"],
        "source": "rule",
    }
    if not str(updated.get("core_content") or "").strip():
        title = str(event.get("title") or "").strip()
        updated["core_content"] = title or "命中国际投行/头部券商研报硬规则。"
    related = list(updated.get("related_holdings") or [])
    for target in rule.get("related_targets") or []:
        if not any(isinstance(item, dict) and item.get("code") == target.get("code") for item in related):
            related.append(
                {
                    "name": target.get("name", ""),
                    "code": target.get("code", ""),
                    "relation": target.get("relation", "直接持仓/观察"),
                    "impact_direction": target.get("direction", "uncertain"),
                    "impact_magnitude": "无法判断",
                    "reason": rule["reason"],
                }
            )
    updated["related_holdings"] = related[:5]
    hits = list(updated.get("rule_hits") or [])
    hits.append(rule)
    updated["rule_hits"] = hits
    return updated
