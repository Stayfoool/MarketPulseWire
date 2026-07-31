"""Generate core-content summaries downstream of authoritative decisions."""

from __future__ import annotations

import json
from typing import Any

from llm_analysis import call_chat_completion_with_prompts
from market_item import DecisionResult, InterpretationResult, NormalizedMarketItem


INTERPRETER_VERSION = "market_interpreter_v2"

FORBIDDEN_FIELDS = {
    "importance",
    "push_now",
    "should_push_now",
    "should_push",
    "market_impact",
    "industry_impact",
    "price_impact",
    "a_share",
    "global_equity",
    "tracking_points",
    "risks",
    "watchlist_view",
    "incremental_view",
    "surprise_level",
    "confidence",
    "brief_reason",
    "related_targets",
    "related_holdings",
}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _json_block(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def interpretation_schema() -> dict[str, Any]:
    return {"core_content": "一句到两句中文核心内容"}


def forbidden_field_line() -> str:
    return "不要输出：" + "/".join(sorted(FORBIDDEN_FIELDS)) + "。"


def rule_boundary_lines() -> str:
    return "\n".join(
        [
            "当前系统的实时推送资格只由输入中的 DecisionResult 决定；不要评价、解释或改写该决定。",
            "只做一件事：用一句到两句中文写清与 DecisionResult 命中事实直接相关的核心内容。",
            "不要输出推送原因、风险提示、投资建议、相关股票、公司映射或产业链环节。",
        ]
    )


def thin_system_prompt(*, task: str, subject_note: str = "") -> str:
    note = f"\n{subject_note.strip()}\n" if subject_note.strip() else "\n"
    return (
        "你是半导体、AI 基础设施和二级市场研究助理。\n"
        f"任务：{task}\n"
        f"{note}"
        f"{rule_boundary_lines()}\n\n"
        "不要给买入/卖出指令，不要补充风险提示或待确认点。\n"
        "只输出 JSON，不要 Markdown，不要输出 JSON 外解释。"
    )


def thin_user_prompt_template(
    *,
    intro: str,
    extra_notes: list[str] | None = None,
    include_source_module: bool = False,
) -> str:
    source_module = "来源模块：{source_module}\n" if include_source_module else ""
    notes = [
        forbidden_field_line(),
        "只根据原文和 DecisionResult 上下文提炼核心事实；不要总结规则、风险、估值或相关标的。",
    ]
    for note in extra_notes or []:
        cleaned = note.strip()
        if cleaned:
            notes.append(cleaned)
    return (
        f"{intro}，输出 JSON：\n"
        f"{_json_block(interpretation_schema())}\n\n"
        "注意：\n- "
        + "\n- ".join(notes)
        + "\n\n"
        "来源：{source}\n"
        f"{source_module}"
        "标题：{title}\n"
        "发布时间：{published_at}\n"
        "正文/摘要：\n"
        "{content}\n"
    )


def decision_context(decision: DecisionResult | None) -> str:
    if decision is None:
        return ""
    payload = {
        "action": decision.action,
        "reason": decision.reason,
        "rule_hits": decision.rule_hits[:5],
    }
    return "DecisionResult 上下文（只用于选择核心事实，不能改写或解释）：\n" + _json_block(payload)


def normalize_interpretation_payload(
    payload: dict[str, Any],
    *,
    model: str = "",
    prompt_version: str = INTERPRETER_VERSION,
) -> InterpretationResult:
    return InterpretationResult(
        core_content=str(payload.get("core_content") or ""),
        model=model,
        prompt_version=prompt_version,
    )


def item_context(item: NormalizedMarketItem | dict[str, Any]) -> dict[str, Any]:
    if isinstance(item, NormalizedMarketItem):
        return {
            "source": item.source,
            "source_category": item.source_category,
            "collector": item.collector,
            "content_type": item.content_type,
            "title": item.title,
            "summary": item.summary,
            "published_at": item.published_at,
            "symbols": item.symbols,
            "themes": item.themes,
            "dedupe_key": item.dedupe_key,
            "access_note": item.access_note,
        }
    return {
        "source": _clean_text(item.get("source")),
        "content_type": _clean_text(item.get("content_type")),
        "title": _clean_text(item.get("title")),
        "summary": _clean_text(item.get("summary") or item.get("content")),
        "published_at": _clean_text(item.get("published_at")),
        "symbols": item.get("symbols") if isinstance(item.get("symbols"), list) else [],
        "themes": item.get("themes") if isinstance(item.get("themes"), list) else [],
        "dedupe_key": _clean_text(item.get("dedupe_key")),
        "access_note": _clean_text(item.get("access_note")),
    }


def interpret_market_item(
    item: NormalizedMarketItem | dict[str, Any],
    decision: DecisionResult,
    *,
    content: str = "",
    task: str = "为一条已完成规则决策的市场信息生成极简实时摘要。",
    intro: str = "请解读以下市场信息",
    extra_notes: list[str] | None = None,
    user_agent: str = "surveil-market-interpreter/0.1",
) -> InterpretationResult:
    """Generate a thin interpretation constrained by an existing decision."""
    system_prompt = thin_system_prompt(task=task)
    user_template = thin_user_prompt_template(
        intro=intro,
        extra_notes=extra_notes,
    )
    context = item_context(item)
    guarded_content = "\n\n".join(
        part
        for part in (
            decision_context(decision),
            "标准化信息：\n" + _json_block(context),
            str(content or "").strip(),
        )
        if part
    )
    parsed, model = call_chat_completion_with_prompts(
        system_prompt,
        user_template.replace("{source}", str(context.get("source") or ""))
        .replace("{title}", str(context.get("title") or ""))
        .replace("{published_at}", str(context.get("published_at") or ""))
        .replace("{content}", guarded_content),
        user_agent=user_agent,
    )
    return normalize_interpretation_payload(parsed, model=model)
