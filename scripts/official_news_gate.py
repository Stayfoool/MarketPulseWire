"""LLM importance gate for official core-company news."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from llm_analysis import call_chat_completion_with_prompts, format_llm_analysis, llm_config
from industry_hardline import apply_hardline_review_override, explain_hardline
from push_rules import (
    apply_article_push_rules,
    first_matching_push_rule,
    load_enabled_holdings_for_rules,
    review_from_push_rule,
)
from skeptic_evaluator import skeptic_lines


OFFICIAL_NEWS_SOURCES = {
    "openai_news",
    "nvidia_blog",
    "nvidia_developer_blog",
    "samsung_semiconductor_news",
    "samsung_global_semiconductor",
    "skhynix_newsroom",
    "micron_news_releases",
}


GATE_SYSTEM_PROMPT = """你是半导体、AI 基础设施和二级市场研究助理。
任务：为一条核心公司官网新闻生成极简实时摘要。
核心公司包括 OpenAI、NVIDIA、Samsung Semiconductor、SK hynix、Micron 等。

实时推送开关优先由确定性规则、来源权重和关系映射控制；不要把自己当成最终裁判。

只做三件事：
- 用一句到两句中文写清核心内容。
- 用一句短句说明为什么值得关注；如果只是来源或规则命中，就写“核心公司官网硬变量，影响待确认”。
- 只列原文明确涉及的公司/股票/产业链环节；不要自由扩散。

不要输出：股价方向、影响幅度、持续时间、完整 A 股/美股利好利空列表、tracking points、risks、watchlist、price-in 判断、surprise_level、confidence、长篇 industry impact。

只输出 JSON，不要 Markdown。"""


GATE_USER_PROMPT = """请分析以下官网新闻，输出 JSON：
{
  "core_content": "一句到两句中文核心内容",
  "brief_reason": "一句简短关注原因；不要写长篇门控理由",
  "related_targets": [
    {"name": "股票/公司/环节", "code": "可选代码", "relation": "核心公司/上游/下游/竞争/主题/来源提及", "direction": "positive/negative/neutral/uncertain"}
  ]
}

注意：
- 不要输出 importance/should_push_now/industry_impact/a_share/global_equity/tracking_points/risks/watchlist_view。
- 是否即时推送由规则层决定。新一代 GPU/ASIC/CPU/互联/液冷/服务器平台、HBM/DRAM/NAND、样品/量产/客户资格认证、大客户采购、资本开支、建厂、先进封装、数据中心扩张、星际之门/Stargate-like 超大资本开支预告等，规则层会优先处理。
- 对超大资本开支“预告/据报/拟宣布/将公布”，只需在 core_content/brief_reason 中标注“待确认/预告性质”和涉及环节，如设备、材料、存储、光通信、PCB、先进封装、电力、液冷。

来源：{source}
标题：{title}
发布时间：{published_at}
正文：
{content}
"""


def ensure_official_news_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS official_news_reviews (
            source TEXT NOT NULL,
            item_id TEXT NOT NULL,
            url TEXT,
            title TEXT NOT NULL,
            published_at TEXT,
            importance TEXT NOT NULL,
            should_push_now INTEGER NOT NULL DEFAULT 0,
            reason TEXT,
            daily_summary TEXT,
            analysis_json TEXT NOT NULL,
            skeptic_json TEXT,
            pre_skeptic_importance TEXT,
            pushed_at TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (source, item_id)
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(official_news_reviews)").fetchall()}
    if "skeptic_json" not in columns:
        conn.execute("ALTER TABLE official_news_reviews ADD COLUMN skeptic_json TEXT")
    if "pre_skeptic_importance" not in columns:
        conn.execute("ALTER TABLE official_news_reviews ADD COLUMN pre_skeptic_importance TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_official_news_created ON official_news_reviews(created_at)")
    conn.commit()


def official_news_enabled() -> bool:
    return llm_config() is not None


def is_official_news_source(source: str) -> bool:
    return source in OFFICIAL_NEWS_SOURCES


def review_exists(conn: sqlite3.Connection, source: str, item_id: str) -> dict[str, Any] | None:
    ensure_official_news_table(conn)
    row = conn.execute(
        """
        SELECT importance, should_push_now, reason, daily_summary, analysis_json,
               skeptic_json, pre_skeptic_importance, pushed_at
        FROM official_news_reviews
        WHERE source = ? AND item_id = ?
        """,
        (source, item_id),
    ).fetchone()
    if not row:
        return None
    importance, should_push_now, reason, daily_summary, analysis_json, skeptic_json, pre_skeptic_importance, pushed_at = row
    parsed = json.loads(analysis_json)
    review = {
        "importance": importance,
        "should_push_now": bool(should_push_now),
        "reason": reason or "",
        "daily_summary": daily_summary or "",
        "analysis": parsed,
        "pushed_at": pushed_at or "",
    }
    try:
        skeptic = json.loads(skeptic_json or "{}")
    except json.JSONDecodeError:
        skeptic = {}
    if isinstance(skeptic, dict) and skeptic:
        review["skeptic"] = skeptic
        review["pre_skeptic_importance"] = pre_skeptic_importance or ""
    elif isinstance(parsed, dict) and isinstance(parsed.get("_skeptic"), dict):
        review["skeptic"] = parsed["_skeptic"]
        review["pre_skeptic_importance"] = parsed.get("_pre_skeptic_importance", "")
    return review


def save_review(conn: sqlite3.Connection, source: str, item: dict[str, Any], review: dict[str, Any]) -> None:
    ensure_official_news_table(conn)
    now = datetime.now(timezone.utc).isoformat()
    analysis_payload = review.get("analysis") if isinstance(review.get("analysis"), dict) else dict(review)
    analysis_payload = dict(analysis_payload)
    if review.get("skeptic"):
        analysis_payload["_skeptic"] = review["skeptic"]
        analysis_payload["_pre_skeptic_importance"] = review.get("pre_skeptic_importance", "")
    conn.execute(
        """
        INSERT INTO official_news_reviews (
            source, item_id, url, title, published_at, importance, should_push_now,
            reason, daily_summary, analysis_json, skeptic_json,
            pre_skeptic_importance, pushed_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, item_id) DO UPDATE SET
            importance = excluded.importance,
            should_push_now = excluded.should_push_now,
            reason = excluded.reason,
            daily_summary = excluded.daily_summary,
            analysis_json = excluded.analysis_json,
            skeptic_json = excluded.skeptic_json,
            pre_skeptic_importance = excluded.pre_skeptic_importance
        """,
        (
            source,
            str(item.get("id") or item.get("url") or item.get("title") or ""),
            str(item.get("url") or ""),
            str(item.get("title") or ""),
            str(item.get("published_at") or ""),
            str(review.get("importance") or "low").lower(),
            1 if review.get("should_push_now") else 0,
            str(review.get("reason") or ""),
            str(review.get("daily_summary") or ""),
            json.dumps(analysis_payload, ensure_ascii=False),
            json.dumps(review.get("skeptic") or {}, ensure_ascii=False),
            str(review.get("pre_skeptic_importance") or ""),
            "",
            now,
        ),
    )
    conn.commit()


def mark_pushed(conn: sqlite3.Connection, source: str, item_id: str) -> None:
    ensure_official_news_table(conn)
    conn.execute(
        "UPDATE official_news_reviews SET pushed_at = ? WHERE source = ? AND item_id = ?",
        (datetime.now(timezone.utc).isoformat(), source, item_id),
    )
    conn.commit()


def normalize_review(parsed: dict[str, Any]) -> dict[str, Any]:
    importance = str(parsed.get("importance") or "low").strip().lower()
    if importance not in {"high", "medium", "low"}:
        importance = "low"
    should_push_now = bool(parsed.get("should_push_now")) and importance == "high"
    analysis = parsed.get("analysis") if isinstance(parsed.get("analysis"), dict) else parsed
    if isinstance(analysis, dict):
        analysis = {**analysis, "llm_mode": "thin"}
    core_content = str(parsed.get("core_content") or (analysis.get("core_content") if isinstance(analysis, dict) else "") or "").strip()
    brief_reason = str(parsed.get("brief_reason") or parsed.get("reason") or "").strip()
    related_targets = parsed.get("related_targets")
    if isinstance(related_targets, list) and isinstance(analysis, dict):
        analysis = {**analysis, "related_targets": related_targets}
    return {
        "importance": importance,
        "should_push_now": should_push_now,
        "reason": brief_reason,
        "brief_reason": brief_reason,
        "industry_impact": str(parsed.get("industry_impact") or "").strip(),
        "a_share_relevance": str(parsed.get("a_share_relevance") or "").strip(),
        "daily_summary": str(parsed.get("daily_summary") or core_content or "").strip(),
        "analysis": analysis,
    }


def review_official_news(source: str, item: dict[str, Any]) -> dict[str, Any]:
    text = str(item.get("full_text") or item.get("content") or item.get("summary") or "").strip()
    title = str(item.get("title") or "").strip()
    hardline_note = explain_hardline(source, (title, text, item.get("source_module")))
    if hardline_note:
        text = f"【产业硬变量线提示】{hardline_note}\n\n{text}"
    user_prompt = (
        GATE_USER_PROMPT.replace("{source}", source)
        .replace("{title}", title)
        .replace("{published_at}", str(item.get("published_at") or ""))
        .replace("{content}", text[:12000])
    )
    parsed, model = call_chat_completion_with_prompts(
        GATE_SYSTEM_PROMPT,
        user_prompt,
        user_agent="surveil-official-news-gate/0.1",
        truncate_user_prompt=False,
        thinking_override=os.getenv("LLM_GATE_THINKING_TYPE", "enabled"),
        max_tokens_override=int(os.getenv("LLM_GATE_MAX_OUTPUT_TOKENS", "1400")),
    )
    review = normalize_review(parsed)
    review["model"] = model
    return review


def apply_official_hardline_override(source: str, item: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    updated = apply_hardline_review_override(source, item, review)
    if updated.get("push_now"):
        updated["should_push_now"] = True
    return updated


def rule_first_official_review(source: str, item: dict[str, Any]) -> dict[str, Any] | None:
    holdings = load_enabled_holdings_for_rules()
    rule = first_matching_push_rule(source=source, item=item, holdings=holdings)
    if not rule:
        return None
    review = review_from_push_rule(rule, item, push_key="should_push_now")
    review["analysis"] = {
        "core_content": str(item.get("summary") or item.get("title") or "").strip(),
        "related_targets": rule.get("related_targets") or [],
        "llm_mode": "rule_only",
    }
    return review


def apply_official_push_rule_override(source: str, item: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    holdings = load_enabled_holdings_for_rules()
    return apply_article_push_rules(source, item, review, holdings=holdings, push_key="should_push_now")


def analysis_lines_from_review(review: dict[str, Any]) -> list[str]:
    parsed = review.get("analysis") if isinstance(review.get("analysis"), dict) else review
    model = str(review.get("model") or "LLM")
    lines = format_llm_analysis(parsed, model)
    prefix = [
        f"官网新闻重要性：{review.get('importance', 'low')}",
        f"是否即时推送：{'是' if review.get('should_push_now') else '否'}",
    ]
    reason = str(review.get("reason") or "").strip()
    if reason:
        prefix.append(f"分流理由：{reason}")
    prefix.extend(skeptic_lines(review))
    return [lines[0], *prefix, *lines[1:]]
