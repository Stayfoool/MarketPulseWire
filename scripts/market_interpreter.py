"""Shared LLM interpretation prompts and restricted judgement helpers.

The interpretation layer is deliberately downstream of deterministic
decisions. It can summarize and, when explicitly asked, judge an uncertain item
inside rule boundaries, but it must not create a new push standard or override
hard-rule push decisions.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from llm_analysis import call_chat_completion_with_prompts
from market_item import DecisionResult, InterpretationResult, NormalizedMarketItem, normalize_llm_judgement


INTERPRETER_VERSION = "market_interpreter_v1"

ForbiddenFieldMode = Literal["article", "official", "event"]
RelationMode = Literal["targets", "holdings"]

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
}

LLM_JUDGEMENT_ENUM = (
    "not_needed",
    "confirm",
    "weak_confirm",
    "not_match",
    "counter_evidence",
    "possibly_stale_or_priced_in",
    "failed",
)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _json_block(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def relation_schema(mode: RelationMode = "targets") -> dict[str, Any]:
    if mode == "holdings":
        return {
            "related_holdings": [
                {
                    "name": "持仓/标的/环节名称",
                    "code": "可选代码",
                    "relation": "直接相关/同行相关/上下游相关/竞争相关/主题相关/无明确关系",
                    "impact_direction": "positive/negative/neutral/uncertain",
                }
            ]
        }
    return {
        "related_targets": [
            {
                "name": "股票/公司/环节",
                "code": "可选代码",
                "relation": "持仓/观察/上游/下游/竞争/主题/来源提及",
                "direction": "positive/negative/neutral/uncertain",
            }
        ]
    }


def interpretation_schema(mode: RelationMode = "targets") -> dict[str, Any]:
    payload = {
        "core_content": "一句到两句中文核心内容",
        "brief_reason": "一句简短关注原因；不要写长篇门控理由",
    }
    payload.update(relation_schema(mode))
    return payload


def forbidden_field_line(mode: ForbiddenFieldMode = "article") -> str:
    fields = sorted(FORBIDDEN_FIELDS)
    if mode == "official":
        fields = [field for field in fields if field != "should_push_now"]
        fields.insert(0, "should_push_now")
    elif mode == "event":
        fields = [field for field in fields if field not in {"should_push", "incremental_view"}]
        fields[:0] = ["importance", "incremental_view", "should_push"]
    return "不要输出：" + "/".join(dict.fromkeys(fields)) + "。"


def rule_boundary_lines() -> str:
    return "\n".join(
        [
            "当前系统的实时推送开关优先由确定性规则、来源权重、持仓/观察名单、关系映射、Skeptic/Web Evidence 和去重控制；不要把自己当成最终裁判。",
            "只做三件事：用一句到两句中文写清核心内容；用一句短句说明为什么值得关注或为什么待确认；只列原文明确涉及、或输入提示明确给出的股票/公司/产业链环节。",
            "不能自由扩散主题、不能自行新增无规则支撑的股票映射、不能把普通营销稿或泛观点升为 high。",
        ]
    )


def thin_system_prompt(*, task: str, subject_note: str = "") -> str:
    note = f"\n{subject_note.strip()}\n" if subject_note.strip() else "\n"
    return (
        "你是半导体、AI 基础设施和二级市场研究助理。\n"
        f"任务：{task}\n"
        f"{note}"
        f"{rule_boundary_lines()}\n\n"
        "不要给无条件买入/卖出指令，只能输出研究信号、观察建议和待确认点。\n"
        "如果输入 raw.freshness、Skeptic 或规则上下文显示旧闻、二次传播或已反应，只能在 brief_reason 中简短备注；不能用它覆盖规则层强推。\n"
        "只输出 JSON，不要 Markdown，不要输出 JSON 外解释。"
    )


def thin_user_prompt_template(
    *,
    intro: str,
    mode: RelationMode = "targets",
    forbidden_mode: ForbiddenFieldMode = "article",
    extra_notes: list[str] | None = None,
    include_source_module: bool = False,
) -> str:
    source_module = "来源模块：{source_module}\n" if include_source_module else ""
    notes = [
        forbidden_field_line(forbidden_mode),
        "是否即时推送由规则层决定；国际投行目标价/评级、重大主题策略、SemiAnalysis、SEMI/TrendForce/DIGITIMES/The Elec/Nikkei xTECH、持仓硬变量、核心公司官网硬变量和美国核心宏观变量等，都必须围绕规则上下文处理。",
        "对“星际之门/Stargate-like”超大资本开支预告，只需在 core_content/brief_reason 中标注“待确认/预告性质”和涉及环节，如设备、材料、存储、光通信、PCB、先进封装、电力、液冷。",
    ]
    for note in extra_notes or []:
        cleaned = note.strip()
        if cleaned:
            notes.append(cleaned)
    return (
        f"{intro}，输出 JSON：\n"
        f"{_json_block(interpretation_schema(mode))}\n\n"
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
        "importance": decision.importance,
        "reason": decision.reason,
        "brief_reason": decision.brief_reason,
        "rule_hits": decision.rule_hits[:5],
        "candidate_rules": decision.candidate_rules[:5],
        "skeptic": decision.skeptic,
        "dedup": decision.dedup,
        "need_limited_llm_judgement": decision.need_limited_llm_judgement,
    }
    return "规则层上下文（只可按此判读，不能自由扩张）：\n" + _json_block(payload)


def restricted_judgement_instruction(decision: DecisionResult | None = None) -> str:
    context = decision_context(decision)
    lines = [
        "补充判读只允许围绕规则层给定的候选规则、准入条件、排除条件、来源可信度、持仓/关键词/主题白名单和已知关系映射。",
        "不能新增无规则支撑的主题，不能凭概念相似强行映射股票，不能自行把无关内容升为 high，不能覆盖硬规则强推。",
        "llm_judgement 只能使用有限枚举：" + " / ".join(LLM_JUDGEMENT_ENUM) + "。",
    ]
    if context:
        lines.append(context)
    return "\n".join(lines)


def normalize_interpretation_payload(
    payload: dict[str, Any],
    *,
    model: str = "",
    prompt_version: str = INTERPRETER_VERSION,
) -> InterpretationResult:
    related = payload.get("related_targets")
    if not isinstance(related, list):
        related = payload.get("related_holdings") if isinstance(payload.get("related_holdings"), list) else []
    return InterpretationResult(
        core_content=str(payload.get("core_content") or ""),
        brief_reason=str(payload.get("brief_reason") or payload.get("reason") or ""),
        related_targets=[item for item in related if isinstance(item, dict)],
        notes=payload.get("notes") if isinstance(payload.get("notes"), list) else [],
        llm_judgement=normalize_llm_judgement(payload.get("llm_judgement")),
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
        "content_type": _clean_text(item.get("content_type") or item.get("event_type")),
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
    mode: RelationMode = "targets",
    forbidden_mode: ForbiddenFieldMode = "article",
    extra_notes: list[str] | None = None,
    user_agent: str = "surveil-market-interpreter/0.1",
) -> InterpretationResult:
    """Generate a thin interpretation constrained by an existing decision."""
    system_prompt = thin_system_prompt(task=task)
    user_template = thin_user_prompt_template(
        intro=intro,
        mode=mode,
        forbidden_mode=forbidden_mode,
        extra_notes=extra_notes,
    )
    context = item_context(item)
    guarded_content = "\n\n".join(
        part
        for part in (
            restricted_judgement_instruction(decision),
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
