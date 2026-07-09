"""LLM importance gate for RSS and TrendForce article notifications."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from llm_analysis import call_chat_completion_with_prompts, llm_config
from industry_hardline import apply_hardline_review_override, explain_hardline
from macro_policy import apply_macro_review_override, macro_prompt_note
from push_rules import (
    apply_article_push_rules,
    first_matching_push_rule,
    load_enabled_holdings_for_rules,
    review_from_push_rule,
)
from skeptic_evaluator import skeptic_lines


GATE_SYSTEM_PROMPT = """你是半导体、AI 基础设施和二级市场研究助理。
任务：为一条已通过规则预筛的资讯/报告生成极简实时摘要。

当前系统的实时推送开关优先由确定性规则、来源权重、持仓/观察名单、关系映射和 Web 反馈控制；不要把自己当成最终裁判。

只做三件事：
- 用一句到两句中文写清核心内容。
- 用一句短句说明为什么这条可能值得关注；如果信息不足，就说明“规则命中/来源命中，影响待确认”。
- 只列原文明确涉及、或输入提示明确给出的股票/公司/产业链环节；不要自由扩散。

不要输出：股价方向、影响幅度、持续时间、完整 A 股/美股利好利空列表、tracking points、risks、watchlist、price-in 判断、surprise_level、confidence、长篇 market impact。

只输出 JSON，不要 Markdown。"""


GATE_USER_PROMPT = """请判断以下内容是否需要第一时间推送，输出 JSON：
{
  "core_content": "一句到两句中文核心内容",
  "brief_reason": "一句简短关注原因；不要写长篇门控理由",
  "related_targets": [
    {"name": "股票/公司/环节", "code": "可选代码", "relation": "持仓/观察/上游/下游/竞争/主题/来源提及", "direction": "positive/negative/neutral/uncertain"}
  ]
}

注意：
- 不要输出 importance/push_now/market_impact/price_impact/a_share/global_equity/tracking_points/risks/watchlist_view。
- 国际投行目标价/评级、SemiAnalysis、SEMI/TrendForce/DIGITIMES/The Elec/Nikkei xTECH、持仓硬变量、美国核心宏观变量等是否即时推送，由规则层决定。
- 对“星际之门/Stargate-like”超大资本开支预告，只需在 core_content/brief_reason 中标注“待确认/预告性质”和涉及环节，如设备、材料、存储、光通信、PCB、先进封装、电力、液冷。

来源：{source}
来源模块：{source_module}
标题：{title}
发布时间：{published_at}
正文/摘要：
{content}
"""


def ensure_article_reviews_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS article_reviews (
            source TEXT NOT NULL,
            item_id TEXT NOT NULL,
            url TEXT,
            title TEXT NOT NULL,
            source_module TEXT,
            published_at TEXT,
            importance TEXT NOT NULL,
            push_now INTEGER NOT NULL DEFAULT 0,
            market_impact TEXT,
            incremental_classification TEXT,
            affected_targets_json TEXT NOT NULL,
            reason TEXT,
            daily_summary TEXT,
            confidence TEXT,
            gate_json TEXT NOT NULL,
            skeptic_json TEXT,
            pre_skeptic_importance TEXT,
            pushed_at TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (source, item_id)
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(article_reviews)").fetchall()}
    if "skeptic_json" not in columns:
        conn.execute("ALTER TABLE article_reviews ADD COLUMN skeptic_json TEXT")
    if "pre_skeptic_importance" not in columns:
        conn.execute("ALTER TABLE article_reviews ADD COLUMN pre_skeptic_importance TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_article_reviews_created ON article_reviews(created_at)")
    conn.commit()


def json_loads_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def article_gate_enabled() -> bool:
    if os.getenv("SURVEIL_ARTICLE_GATE", "1").strip() == "0":
        return False
    return llm_config() is not None


def article_item_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("url") or item.get("title") or "")


def normalize_review(parsed: dict[str, Any]) -> dict[str, Any]:
    importance = str(parsed.get("importance") or "low").strip().lower()
    if importance not in {"high", "medium", "low"}:
        importance = "low"
    push_now = bool(parsed.get("push_now")) and importance == "high"
    targets = parsed.get("affected_targets")
    if not isinstance(targets, list):
        targets = []
    related_targets = parsed.get("related_targets")
    if isinstance(related_targets, list):
        for target in related_targets:
            if isinstance(target, dict):
                name = str(target.get("name") or "").strip()
                code = str(target.get("code") or "").strip()
                label = " ".join(part for part in (name, code) if part)
            else:
                label = str(target).strip()
            if label:
                targets.append(label)
    core_content = str(parsed.get("core_content") or "").strip()
    brief_reason = str(parsed.get("brief_reason") or parsed.get("reason") or "").strip()
    return {
        "importance": importance,
        "push_now": push_now,
        "market_impact": str(parsed.get("market_impact") or "").strip(),
        "incremental_classification": str(parsed.get("incremental_classification") or "").strip(),
        "affected_targets": [str(item).strip() for item in targets if str(item).strip()][:5],
        "daily_summary": str(parsed.get("daily_summary") or core_content or "").strip(),
        "reason": brief_reason,
        "brief_reason": brief_reason,
        "confidence": str(parsed.get("confidence") or "").strip(),
        "raw": {**parsed, "llm_mode": "thin"},
    }


def failed_review(item: dict[str, Any], error: Exception) -> dict[str, Any]:
    reason = str(error).strip()
    if len(reason) > 500:
        reason = reason[:497] + "..."
    return {
        "importance": "low",
        "push_now": False,
        "market_impact": "门控模型失败，无法判断是否显著影响股价。",
        "incremental_classification": "无法判断",
        "affected_targets": [],
        "daily_summary": str(item.get("title") or "门控失败条目"),
        "reason": f"门控模型失败：{reason}",
        "confidence": "低",
        "raw": {"error": reason},
        "model": "gate_failed",
    }


def review_article(source: str, item: dict[str, Any]) -> dict[str, Any]:
    text = str(item.get("full_text") or item.get("content") or item.get("summary") or "").strip()
    hardline_note = explain_hardline(source, (item.get("title"), item.get("summary"), text))
    macro_note = macro_prompt_note(item)
    content = text[:6000]
    if macro_note:
        content = f"【宏观政策线提示】{macro_note}\n\n{content}"
    if hardline_note:
        content = f"【产业硬变量线提示】{hardline_note}\n\n{content}"
    user_prompt = (
        GATE_USER_PROMPT.replace("{source}", source)
        .replace("{source_module}", str(item.get("source_module") or item.get("source_display") or ""))
        .replace("{title}", str(item.get("title") or ""))
        .replace("{published_at}", str(item.get("published_at") or ""))
        .replace("{content}", content)
    )
    parsed, model = call_chat_completion_with_prompts(
        GATE_SYSTEM_PROMPT,
        user_prompt,
        user_agent="surveil-article-gate/0.1",
        truncate_user_prompt=False,
        thinking_override=os.getenv("LLM_GATE_THINKING_TYPE", "enabled"),
        max_tokens_override=int(os.getenv("LLM_GATE_MAX_OUTPUT_TOKENS", "1400")),
    )
    review = normalize_review(parsed)
    review["model"] = model
    return review


def save_review(conn: sqlite3.Connection, source: str, item: dict[str, Any], review: dict[str, Any]) -> None:
    ensure_article_reviews_table(conn)
    now = datetime.now(timezone.utc).isoformat()
    item_id = article_item_id(item)
    conn.execute(
        """
        INSERT INTO article_reviews (
            source, item_id, url, title, source_module, published_at,
            importance, push_now, market_impact, incremental_classification,
            affected_targets_json, reason, daily_summary, confidence,
            gate_json, skeptic_json, pre_skeptic_importance, pushed_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, item_id) DO UPDATE SET
            source_module = excluded.source_module,
            published_at = excluded.published_at,
            importance = excluded.importance,
            push_now = excluded.push_now,
            market_impact = excluded.market_impact,
            incremental_classification = excluded.incremental_classification,
            affected_targets_json = excluded.affected_targets_json,
            reason = excluded.reason,
            daily_summary = excluded.daily_summary,
            confidence = excluded.confidence,
            gate_json = excluded.gate_json,
            skeptic_json = excluded.skeptic_json,
            pre_skeptic_importance = excluded.pre_skeptic_importance
        """,
        (
            source,
            item_id,
            str(item.get("url") or ""),
            str(item.get("title") or ""),
            str(item.get("source_module") or item.get("source_display") or ""),
            str(item.get("published_at") or ""),
            str(review.get("importance") or "low"),
            1 if review.get("push_now") else 0,
            str(review.get("market_impact") or ""),
            str(review.get("incremental_classification") or ""),
            json.dumps(review.get("affected_targets") or [], ensure_ascii=False),
            str(review.get("reason") or ""),
            str(review.get("daily_summary") or ""),
            str(review.get("confidence") or ""),
            json.dumps(review, ensure_ascii=False),
            json.dumps(review.get("skeptic") or {}, ensure_ascii=False),
            str(review.get("pre_skeptic_importance") or ""),
            "",
            now,
        ),
    )
    conn.commit()


def apply_hardline_override(source: str, item: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    return apply_hardline_review_override(source, item, review)


def apply_macro_override(item: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    return apply_macro_review_override(review, item)


def rule_first_review(source: str, item: dict[str, Any], *, push_key: str = "push_now") -> dict[str, Any] | None:
    holdings = load_enabled_holdings_for_rules()
    rule = first_matching_push_rule(source=source, item=item, holdings=holdings)
    if not rule:
        return None
    return review_from_push_rule(rule, item, push_key=push_key)


def apply_push_rule_override(
    source: str,
    item: dict[str, Any],
    review: dict[str, Any],
    *,
    push_key: str = "push_now",
) -> dict[str, Any]:
    holdings = load_enabled_holdings_for_rules()
    return apply_article_push_rules(source, item, review, holdings=holdings, push_key=push_key)


def review_exists(conn: sqlite3.Connection, source: str, item_id: str) -> dict[str, Any] | None:
    ensure_article_reviews_table(conn)
    row = conn.execute(
        """
        SELECT importance, push_now, market_impact, incremental_classification,
               affected_targets_json, reason, daily_summary, confidence, gate_json,
               skeptic_json, pre_skeptic_importance, pushed_at
        FROM article_reviews
        WHERE source = ? AND item_id = ?
        """,
        (source, item_id),
    ).fetchone()
    if not row:
        return None
    (
        importance,
        push_now,
        market_impact,
        incremental,
        targets_json,
        reason,
        daily_summary,
        confidence,
        gate_json,
        skeptic_json,
        pre_skeptic_importance,
        pushed_at,
    ) = row
    raw = json_loads_dict(gate_json)
    try:
        targets = json.loads(targets_json or "[]")
    except json.JSONDecodeError:
        targets = []
    return {
        "importance": importance,
        "push_now": bool(push_now),
        "market_impact": market_impact or "",
        "incremental_classification": incremental or "",
        "affected_targets": targets if isinstance(targets, list) else [],
        "reason": reason or "",
        "daily_summary": daily_summary or "",
        "confidence": confidence or "",
        "raw": raw,
        "skeptic": json_loads_dict(skeptic_json) if skeptic_json else raw.get("skeptic", {}),
        "pre_skeptic_importance": pre_skeptic_importance or raw.get("pre_skeptic_importance", ""),
        "pushed_at": pushed_at or "",
    }


def mark_pushed(conn: sqlite3.Connection, source: str, item_id: str) -> None:
    ensure_article_reviews_table(conn)
    conn.execute(
        "UPDATE article_reviews SET pushed_at = ? WHERE source = ? AND item_id = ?",
        (datetime.now(timezone.utc).isoformat(), source, item_id),
    )
    conn.commit()


def gate_lines(review: dict[str, Any]) -> list[str]:
    targets = review.get("affected_targets") or []
    lines = [
        f"重要性门控：{review.get('importance', 'low')}",
        f"是否即时推送：{'是' if review.get('push_now') else '否'}",
    ]
    if review.get("incremental_classification"):
        lines.append(f"门控增量判断：{review['incremental_classification']}")
    if review.get("market_impact"):
        lines.append(f"门控市场影响：{review['market_impact']}")
    if targets:
        lines.append("门控涉及标的/环节：" + "；".join(str(item) for item in targets[:5]))
    if review.get("reason"):
        lines.append(f"门控理由：{review['reason']}")
    lines.extend(skeptic_lines(review))
    return lines
