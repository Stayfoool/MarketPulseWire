"""Deterministic prefilter for the user's market-intelligence universe."""

from __future__ import annotations

import json
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

from db_utils import connect_sqlite
from macro_policy import macro_policy_match
from market_db import DEFAULT_DB_PATH
from media_keyword_config import keyword_matches_text, media_keyword_match


SEMICONDUCTOR_AI_KEYWORDS = (
    "半导体",
    "芯片",
    "晶圆",
    "先进封装",
    "封装基板",
    "玻璃基板",
    "gpu",
    "asic",
    "hbm",
    "dram",
    "nand",
    "nor flash",
    "ssd",
    "存储",
    "内存",
    "mlcc",
    "pcb",
    "cpo",
    "光模块",
    "光通信",
    "硅光",
    "光互联",
    "光电",
    "服务器",
    "ai服务器",
    "ai server",
    "数据中心",
    "data center",
    "datacenter",
    "算力",
    "大模型",
    "llm",
    "人工智能",
    "英伟达",
    "nvidia",
    "blackwell",
    "rubin",
    "gb200",
    "gb300",
    "nvlink",
    "kyber",
    "nvl144",
    "液冷",
    "散热",
    "金刚石",
    "diamond",
    "碳化硅",
    "sic",
    "氮化镓",
    "gan",
    "电子特气",
    "光刻胶",
    "半导体设备",
    "刻蚀",
    "薄膜",
    "量测",
    "测试机",
    "探针卡",
)

GENERIC_POWER_KEYWORDS = (
    "电力",
    "电网",
    "变压器",
    "输变电",
    "配电",
    "电源",
    "储能",
    "power",
    "electricity",
    "grid",
    "transformer",
    "energy storage",
)

AI_INFRA_CONTEXT_KEYWORDS = (
    "ai",
    "人工智能",
    "算力",
    "数据中心",
    "data center",
    "datacenter",
    "服务器",
    "server",
    "gpu",
    "英伟达",
    "nvidia",
    "blackwell",
    "rubin",
    "gb200",
    "gb300",
    "云厂商",
    "hyperscaler",
    "cloud",
    "csp",
)


def item_text(item: dict[str, Any]) -> str:
    return "\n".join(
        str(item.get(key) or "")
        for key in ("title", "summary", "content", "full_text", "source_module", "source_display")
        if str(item.get(key) or "").strip()
    )


def contains_any_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword_matches_text(keyword, text) for keyword in keywords)


@lru_cache(maxsize=4)
def holding_terms(db_path: str = str(DEFAULT_DB_PATH)) -> tuple[str, ...]:
    path = Path(db_path)
    if not path.exists():
        return ()
    try:
        with connect_sqlite(path) as conn:
            rows = conn.execute(
                """
                SELECT symbol, name, full_name, aliases_json, raw_json
                FROM portfolio_holdings
                WHERE enabled = 1
                """
            ).fetchall()
    except sqlite3.Error:
        return ()
    terms: list[str] = []
    for symbol, name, full_name, aliases_json, raw_json in rows:
        for value in (symbol, name, full_name):
            if str(value or "").strip():
                terms.append(str(value).strip())
        for raw_json_text in (aliases_json, raw_json):
            try:
                parsed = json.loads(raw_json_text or "[]" if raw_json_text == aliases_json else raw_json_text or "{}")
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list):
                terms.extend(str(item).strip() for item in parsed if str(item).strip())
            elif isinstance(parsed, dict):
                for key in ("news_keywords", "aliases"):
                    values = parsed.get(key)
                    if isinstance(values, list):
                        terms.extend(str(item).strip() for item in values if str(item).strip())
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        key = term.casefold()
        if key in seen or len(term) < 2:
            continue
        seen.add(key)
        result.append(term)
    return tuple(result)


def matched_holding(text: str, db_path: Path = DEFAULT_DB_PATH) -> str:
    for term in holding_terms(str(db_path)):
        if keyword_matches_text(term, text):
            return term
    return ""


def investment_universe_match(
    source: str,
    item: dict[str, Any],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    text = item_text(item)
    if not text.strip():
        return {"matched": False, "reason": "内容为空", "tags": []}

    macro = macro_policy_match(item)
    if macro.get("matched"):
        return {
            "matched": True,
            "reason": str(macro.get("reason") or "命中美国宏观/美联储政策线"),
            "tags": ["macro_policy", str(macro.get("tier") or "")],
            "macro_policy_line": macro,
        }

    keyword = media_keyword_match(
        str(item.get("title") or ""),
        str(item.get("summary") or ""),
        str(item.get("content") or ""),
        str(item.get("full_text") or ""),
    )
    if keyword.get("blocked"):
        return {
            "matched": False,
            "reason": f"命中媒体排除关键词：{keyword.get('keyword')}",
            "tags": ["media_keyword_excluded"],
        }
    if keyword.get("bucket") == "include":
        return {
            "matched": True,
            "reason": f"命中用户显式包含关键词：{keyword.get('keyword')}",
            "tags": ["user_include_keyword"],
            "keyword": keyword.get("keyword"),
        }

    holding = matched_holding(text, db_path=db_path)
    if holding:
        return {
            "matched": True,
            "reason": f"命中持仓/持仓关键词：{holding}",
            "tags": ["holding_match"],
            "holding_keyword": holding,
        }

    if contains_any_keyword(text, SEMICONDUCTOR_AI_KEYWORDS):
        return {
            "matched": True,
            "reason": "命中半导体/AI 基础设施投资宇宙关键词",
            "tags": ["semiconductor_ai"],
            "keyword": keyword.get("keyword") or "",
        }

    if contains_any_keyword(text, GENERIC_POWER_KEYWORDS):
        if contains_any_keyword(text, AI_INFRA_CONTEXT_KEYWORDS):
            return {
                "matched": True,
                "reason": "命中电力/电网相关内容，且同时具备 AI 数据中心上下文",
                "tags": ["ai_power_infra"],
                "keyword": keyword.get("keyword") or "",
            }
        return {
            "matched": False,
            "reason": "仅命中泛电力/变压器/电网关键词，未见 AI 数据中心、半导体或持仓上下文",
            "tags": ["generic_power_filtered"],
            "keyword": keyword.get("keyword") or "",
        }

    if keyword.get("matched"):
        return {
            "matched": False,
            "reason": f"命中媒体关键词 {keyword.get('keyword')}，但未落入当前半导体/AI/持仓/美国核心宏观投资宇宙",
            "tags": ["keyword_outside_universe"],
            "keyword": keyword.get("keyword"),
        }
    return {
        "matched": False,
        "reason": "未命中持仓、半导体/AI 主线、用户显式关键词或美国核心宏观变量",
        "tags": ["outside_universe"],
    }


def split_candidate_lines(text: str) -> list[str]:
    raw_lines = []
    for line in str(text or "").replace("\r", "\n").split("\n"):
        cleaned = line.strip()
        if not cleaned:
            continue
        if cleaned in {"国内", "国外"}:
            continue
        raw_lines.append(cleaned)
    return raw_lines


def relevant_digest_for_mixed_item(source: str, item: dict[str, Any], *, max_lines: int = 6) -> str:
    lines = split_candidate_lines(item_text(item))
    relevant: list[str] = []
    seen: set[str] = set()
    for line in lines:
        candidate = {"title": line, "summary": line, "full_text": ""}
        match = investment_universe_match(source, candidate)
        if not match.get("matched"):
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        relevant.append(line)
        if len(relevant) >= max_lines:
            break
    return "\n".join(relevant)
