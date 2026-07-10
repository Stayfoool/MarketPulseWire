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
from investment_universe import investment_universe_match
from media_keyword_config import keyword_matches_text


INTERNATIONAL_BANK_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("高盛", ("高盛", "goldman sachs")),
    ("摩根士丹利", ("摩根士丹利", "morgan stanley")),
    ("摩根大通", ("摩根大通", "jpmorgan", "jp morgan", "j.p. morgan")),
    ("花旗", ("花旗", "citi", "citigroup")),
    ("瑞银", ("瑞银", "ubs")),
    ("美银", ("美银", "bank of america", "bofa")),
    ("伯恩斯坦", ("伯恩斯坦", "bernstein")),
    ("杰富瑞", ("杰富瑞", "jefferies")),
    ("汇丰", ("汇丰", "hsbc")),
    ("野村", ("野村", "nomura")),
    ("麦格理", ("麦格理", "macquarie")),
)

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


def matched_bank_names(text: str, allowed_banks: set[str] | None = None) -> list[str]:
    lowered = text.lower()
    banks: list[str] = []
    for display, aliases in INTERNATIONAL_BANK_ALIASES:
        if allowed_banks and display.casefold() not in allowed_banks and not any(
            alias.casefold() in allowed_banks for alias in aliases
        ):
            continue
        if any(alias.lower() in lowered for alias in aliases):
            banks.append(display)
    return banks


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
    actions = _strategy_actions(text, config["extra_action_keywords"])
    if not actions:
        return None
    themes = _strategy_themes(text, config["extra_theme_keywords"])
    universe = investment_universe_match(source, item)
    if not themes and not universe.get("matched"):
        return None
    evidence = _strategy_evidence(text, themes)
    evidence_score = sum(int(item["score"]) for item in evidence)
    if evidence_score < config["min_evidence_score"]:
        return None
    tier = _source_tier(source, item)
    if tier != "机构公开材料" and not config["allow_secondary_sources"]:
        return None
    report_title = _report_reference(text)
    bank = banks[0]
    action = actions[0]
    targets = themes[:4]
    for holding in matched_holdings(text, holdings):
        label = " ".join(part for part in (str(holding.get("name") or ""), str(holding.get("symbol") or "")) if part)
        if label:
            targets.append(label)
    targets = list(dict.fromkeys(targets))[:5]
    evidence_text = "；".join(f"{item['kind']}（{item['snippet']}）" for item in evidence[:3])
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
        "report_title": report_title,
        "source_tier": tier,
        "evidence_score": evidence_score,
        "evidence": evidence,
        "dedup_key": _theme_dedup_key(bank, report_title, themes, action, item.get("published_at")),
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
    banks = matched_bank_names(text)
    if not banks or not contains_keyword(text, RATING_OR_TARGET_KEYWORDS):
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
    if not text or not contains_keyword(text, DIRECT_HOLDING_HARD_VARIABLE_KEYWORDS):
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
    if str(source or "") not in OFFICIAL_COMPANY_SOURCES:
        return None
    text = compact_text(
        item.get("title"),
        item.get("summary"),
        item.get("content"),
        item.get("full_text"),
        item.get("source_module"),
        item.get("source_display"),
    )
    if not text or not contains_keyword(text, OFFICIAL_COMPANY_HARD_VARIABLE_KEYWORDS):
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


def first_matching_push_rule(
    *,
    source: str,
    item: dict[str, Any],
    holdings: list[dict[str, Any]],
    symbols: set[str] | None = None,
) -> dict[str, Any] | None:
    for matcher in (
        investment_bank_research_rule,
        international_bank_theme_strategy_rule,
        direct_holding_hard_variable_rule,
        official_company_hard_variable_rule,
        macro_policy_event_rule,
    ):
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
