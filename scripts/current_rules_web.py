"""Read-only Web projection of the current admission and LLM decision rules."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from admission_rules import (
    CONTRACT_VERSION,
    FAMILY_ORDER,
    HOLDING_ONLY_SOURCE_CATEGORIES,
    HOLDING_ONLY_SOURCES,
)
from llm_rule_catalog import configured_rule_path, load_rule_catalog
from market_db import DEFAULT_DB_PATH
from production_admission import load_production_portfolio, load_production_rule_config


MAX_DISPLAY_VALUES = 500

FAMILY_LABELS = {
    "holding": "持仓",
    "semiconductor_ai": "半导体/AI",
    "macro_data": "宏观数据",
    "fed_policy": "美联储政策",
    "trade_policy": "贸易政策",
}


def _field(label: str, values: object) -> dict[str, Any]:
    normalized = [str(value).strip() for value in values if str(value).strip()]
    return {
        "label": label,
        "values": normalized[:MAX_DISPLAY_VALUES],
        "count": len(normalized),
        "truncated": len(normalized) > MAX_DISPLAY_VALUES,
    }


def _holding_lines(portfolio: object, attribute: str) -> list[str]:
    lines: list[str] = []
    for holding in portfolio.holdings:
        values = tuple(getattr(holding, attribute))
        if values:
            lines.append(f"{holding.symbol} · {'、'.join(holding.names[:1])}：{'、'.join(values)}")
    return lines


def range_admission_rules_payload(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    config = load_production_rule_config(env)
    portfolio = load_production_portfolio(db_path, read_only=True)
    groups = [
        {
            "family": "holding",
            "title": FAMILY_LABELS["holding"],
            "summary": "名称、别名或代码直接命中，或者命中该持仓配置的关联新闻关键词。",
            "count": len(portfolio.holdings),
            "fields": [
                _field(
                    "当前启用持仓",
                    (
                        f"{holding.symbol} · {'、'.join(holding.names)}"
                        for holding in portfolio.holdings
                    ),
                ),
                _field("关联新闻关键词", _holding_lines(portfolio, "related_news_keywords")),
                _field("持仓排除关键词", _holding_lines(portfolio, "exclude_keywords")),
            ],
        },
        {
            "family": "semiconductor_ai",
            "title": FAMILY_LABELS["semiconductor_ai"],
            "summary": "主关键词匹配规则正文；标题限定关键词只匹配标题。",
            "count": len(config.semiconductor_ai_keywords),
            "fields": [
                _field("主关键词", config.semiconductor_ai_keywords),
                _field("标题限定关键词", config.semiconductor_ai_title_keywords),
            ],
        },
        {
            "family": "macro_data",
            "title": FAMILY_LABELS["macro_data"],
            "summary": "宏观指标和宏观语境必须同时命中。",
            "count": len(config.macro_indicators),
            "fields": [
                _field("宏观指标", config.macro_indicators),
                _field("宏观语境", config.macro_context_aliases),
            ],
        },
        {
            "family": "fed_policy",
            "title": FAMILY_LABELS["fed_policy"],
            "summary": "美联储事件、人物或路径词命中，或者命中受信任大型金融机构负责人判断。",
            "count": len(config.fed_event_aliases)
            + len(config.fed_actor_aliases)
            + len(config.fed_path_aliases)
            + len(config.trusted_institutions),
            "fields": [
                _field("事件词", config.fed_event_aliases),
                _field("人物词", config.fed_actor_aliases),
                _field("路径词", config.fed_path_aliases),
                _field(
                    "受信任大型金融机构",
                    (
                        f"{institution.institution_id} · {'、'.join(institution.aliases)}"
                        for institution in config.trusted_institutions
                    ),
                ),
            ],
        },
        {
            "family": "trade_policy",
            "title": FAMILY_LABELS["trade_policy"],
            "summary": "同一句内命中贸易走廊和政策工具/阶段，或者命中现有贸易摩擦分类；官方政策来源适用直接准入边界。",
            "count": len(config.trade_corridors),
            "fields": [
                _field(
                    "贸易走廊",
                    (
                        f"{corridor.corridor_id} · 中国侧：{'、'.join(corridor.china_terms) or '-'}；"
                        f"对手侧：{'、'.join(corridor.counterparty_terms) or '-'}；"
                        f"联合词：{'、'.join(corridor.joint_terms) or '-'}"
                        for corridor in config.trade_corridors
                    ),
                ),
                _field("政策工具", config.trade_instruments),
                _field("政策阶段", config.trade_stages),
            ],
        },
    ]
    order = {family: index for index, family in enumerate(FAMILY_ORDER)}
    groups.sort(key=lambda group: order[str(group["family"])])
    return {
        "status": "loaded",
        "config_version": config.config_version,
        "contract_version": CONTRACT_VERSION,
        "relation": "or",
        "group_count": len(groups),
        "groups": groups,
        "global_exclusions": _field("全局排除关键词", config.exclude_keywords),
        "source_boundaries": [
            {
                "title": "普通来源",
                "description": "五个范围准入组按“或”关系判断，任一组命中即可通过范围准入。",
            },
            {
                "title": "只允许持仓准入的来源",
                "description": "来源或来源分类命中下列代码值时，只保留持仓组的准入结果。",
                "values": sorted(HOLDING_ONLY_SOURCES | HOLDING_ONLY_SOURCE_CATEGORIES),
            },
            {
                "title": "官方贸易政策来源",
                "description": "source_category=official_policy 或 publisher_role=government_official 时，规范化后直接适用 trade_policy 准入。",
            },
        ],
    }


def llm_decision_rules_payload(*, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    effective_env = os.environ if env is None else env
    version, rules = load_rule_catalog(configured_rule_path(effective_env))
    return {
        "status": "loaded",
        "version": version,
        "rule_count": len(rules),
        "families": [
            family
            for family in FAMILY_ORDER
            if any(family in rule.applicable_families for rule in rules)
        ],
        "rules": [
            {
                "rule_id": rule.rule_id,
                "family": rule.family,
                "family_label": FAMILY_LABELS[rule.family],
                "applicable_families": list(rule.applicable_families),
                "applicable_family_labels": [
                    FAMILY_LABELS[family] for family in rule.applicable_families
                ],
                "title": rule.title,
                "allowed_actions": list(rule.allowed_actions),
                "action_conditions": dict(rule.action_conditions),
                "required_facts": list(rule.required_facts),
                "exclusions": list(rule.exclusions),
                "version": rule.version,
            }
            for rule in rules
        ],
    }


def current_rules_payload(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    try:
        range_admission = range_admission_rules_payload(db_path=db_path, env=env)
    except Exception:  # noqa: BLE001 - expose a bounded error without private paths or content
        range_admission = {
            "status": "error",
            "error": "范围准入规则加载失败，请检查生产私有配置和当前持仓数据。",
        }
    try:
        llm_decision = llm_decision_rules_payload(env=env)
    except Exception:  # noqa: BLE001 - expose a bounded error without private paths or content
        llm_decision = {
            "status": "error",
            "error": "大模型决策规则加载失败，请检查私有规则文件及其严格校验结果。",
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "range_admission": range_admission,
        "llm_decision": llm_decision,
    }


__all__ = [
    "FAMILY_LABELS",
    "current_rules_payload",
    "llm_decision_rules_payload",
    "range_admission_rules_payload",
]
