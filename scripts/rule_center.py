"""Rule registry, private overrides, audit, and dry-run helpers for the Web workbench."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from db_utils import connect_sqlite
from market_db import DEFAULT_DB_PATH, db_table_exists, init_db


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "push_rules.local.json"
ORDERED_FIRST_MATCH = "ordered_first_match"
PARALLEL_MERGE = "parallel_merge"
EXECUTION_MODE_LABELS = {
    ORDERED_FIRST_MATCH: "顺序首命中",
    PARALLEL_MERGE: "并行合并",
}


RULE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "investment_bank_rating_target_direct_holding",
        "name": "国际投行单股评级/目标价",
        "group": "投行研究",
        "description": "同一局部证据窗口内，认可国际投行对直接持仓/观察标的给出评级、目标价或覆盖变化时即时提醒。",
        "runtime": "push_rules / article + event",
        "execution_mode": ORDERED_FIRST_MATCH,
        "hit_markers": ("investment_bank_rating_target_direct_holding",),
        "priority": 100,
        "fields": (
            {"key": "enabled", "label": "启用", "type": "bool", "default": True},
            {"key": "priority", "label": "规则顺序", "type": "int", "default": 100, "min": 1, "max": 999},
            {
                "key": "allowed_banks",
                "label": "机构白名单",
                "type": "list",
                "default": [],
                "help": "留空使用代码内置国际投行名单；填写后仅允许列表中的中文名或英文别名。",
            },
            {
                "key": "extra_keywords",
                "label": "额外评级/目标价触发词",
                "type": "list",
                "default": [],
                "help": "叠加到内置评级、目标价、上调/下调等词表。",
            },
        ),
    },
    {
        "id": "investment_bank_portfolio_relation",
        "name": "国际投行高级关系映射（暂不实时启用）",
        "group": "投行研究",
        "description": "保留给未来的上下游、多对多和方向传导关系映射；当前日常实时提醒优先使用持仓管理中的“关联新闻关键词”。",
        "runtime": "push_rules / value directory article",
        "execution_mode": ORDERED_FIRST_MATCH,
        "hit_markers": ("investment_bank_portfolio_relation",),
        "priority": 95,
        "fields": (
            {"key": "enabled", "label": "启用", "type": "bool", "default": False},
            {"key": "priority", "label": "规则顺序", "type": "int", "default": 95, "min": 1, "max": 999},
            {
                "key": "allowed_banks",
                "label": "机构白名单",
                "type": "list",
                "default": [],
                "help": "留空使用代码内置国际投行名单；填写后仅允许列表中的中文名或英文别名。",
            },
            {
                "key": "max_relation_matches",
                "label": "单条最多关联路径",
                "type": "int",
                "default": 3,
                "min": 1,
                "max": 5,
                "help": "限制单条研报展示的持仓关系数量，避免同一行业报告推送过长。",
            },
        ),
    },
    {
        "id": "holding_keyword_immediate_alert",
        "name": "持仓/关联关键词即时提醒",
        "group": "持仓与公司",
        "description": "任一已接入文章或事件命中直接持仓名称/别名，或该持仓配置的“关联新闻关键词”时即时薄推送；不由 LLM 决定。",
        "runtime": "push_rules / article + event",
        "execution_mode": ORDERED_FIRST_MATCH,
        "hit_markers": ("holding_keyword_immediate_alert",),
        "priority": 98,
        "fields": (
            {"key": "enabled", "label": "启用", "type": "bool", "default": True},
            {"key": "priority", "label": "规则顺序", "type": "int", "default": 98, "min": 1, "max": 999},
        ),
    },
    {
        "id": "international_bank_fed_rate_path_revision",
        "name": "国际大行美联储利率路径修正",
        "group": "宏观与政策",
        "description": "认可国际大行明确修正美联储加息/降息次数、时点、累计基点或终端利率时即时提醒；具体但未证明发生修正的预测进入 daily。",
        "runtime": "international_bank_fed / decision_engine / all normalized sources",
        "execution_mode": PARALLEL_MERGE,
        "hit_markers": ("international_bank_fed_rate_path_revision",),
        "fields": (
            {"key": "enabled", "label": "启用", "type": "bool", "default": True},
            {
                "key": "allowed_banks",
                "label": "主要银行白名单",
                "type": "list",
                "default": [],
                "help": "留空使用代码审计的主要国际银行集合；只接受已审计中文名或英文别名。",
            },
        ),
    },
    {
        "id": "international_bank_theme_strategy",
        "name": "国际投行重大主题策略",
        "group": "投行研究",
        "description": "明确做多/做空/超配等重大主题策略，或给出完整 from -> to 配置轮动时即时提醒；轮动关系由代码内审计语法判定。",
        "runtime": "push_rules / article + event",
        "execution_mode": ORDERED_FIRST_MATCH,
        "hit_markers": ("international_bank_theme_strategy",),
        "priority": 90,
        "external_config": "investment_bank_theme",
        "fields": (
            {"key": "enabled", "label": "启用", "type": "bool", "default": True},
            {"key": "priority", "label": "规则顺序", "type": "int", "default": 90, "min": 1, "max": 999},
            {"key": "allowed_banks", "label": "机构白名单", "type": "list", "default": []},
            {
                "key": "extra_theme_keywords",
                "label": "额外单主题关键词",
                "type": "list",
                "default": [],
                "help": "仅叠加到原有做多/超配等单主题策略，不参与 from -> to 轮动解析。",
            },
            {
                "key": "extra_action_keywords",
                "label": "额外单主题配置动作词",
                "type": "list",
                "default": [],
                "help": "仅叠加到原有单主题策略；轮动关系动词和排除语法固定在代码中。",
            },
            {
                "key": "extra_rotation_theme_aliases",
                "label": "额外轮动主题别名",
                "type": "list",
                "default": [],
                "help": "每行 theme_id=别名，例如 ai_cloud_hyperscalers=超大规模云；只接受代码内已知主题 ID。",
            },
            {
                "key": "allow_broad_style_rotation",
                "label": "允许成长/价值等风格轮动",
                "type": "bool",
                "default": True,
            },
            {
                "key": "require_investment_universe_leg",
                "label": "至少一腿属于认可投资范围",
                "type": "bool",
                "default": True,
                "help": "认可范围包括内置半导体/AI 主题及启用的宽基风格桶。",
            },
            {"key": "min_evidence_score", "label": "最低重大性证据分", "type": "int", "default": 2, "min": 1, "max": 8},
            {"key": "allow_secondary_sources", "label": "允许媒体明确署名转述", "type": "bool", "default": True},
            {"key": "dedup_lookback_days", "label": "跨来源去重天数", "type": "int", "default": 14, "min": 1, "max": 90},
        ),
    },
    {
        "id": "value_directory_industry_macro_research",
        "name": "价值目录投行行业宏观研报",
        "group": "投行研究",
        "description": "价值目录国际投行-行业宏观来源中，认可机构且命中半导体/AI 基础设施、持仓关联关键词或配置主题的研报即时提醒。",
        "runtime": "push_rules / value_directory article",
        "execution_mode": ORDERED_FIRST_MATCH,
        "hit_markers": ("value_directory_industry_macro_research",),
        "priority": 88,
        "fields": (
            {"key": "enabled", "label": "启用", "type": "bool", "default": True},
            {"key": "priority", "label": "规则顺序", "type": "int", "default": 88, "min": 1, "max": 999},
            {
                "key": "allowed_banks",
                "label": "机构白名单",
                "type": "list",
                "default": [],
                "help": "留空使用代码内置国际投行名单；填写后仅允许列表中的中文名或英文别名。",
            },
            {
                "key": "extra_theme_keywords",
                "label": "额外主题关键词",
                "type": "list",
                "default": [],
                "help": "叠加到本项目投资宇宙之外、但你希望价值目录行业宏观研报即时提醒的主题。",
            },
        ),
    },
    {
        "id": "direct_holding_hard_variable",
        "name": "直接持仓硬变量",
        "group": "持仓与公司",
        "description": "持仓/观察标的命中订单、涨价、产能、客户认证、资本开支、并购、管制或业绩指引等硬变量时即时提醒。",
        "runtime": "push_rules / article + event",
        "execution_mode": ORDERED_FIRST_MATCH,
        "hit_markers": ("direct_holding_hard_variable",),
        "priority": 80,
        "fields": (
            {"key": "enabled", "label": "启用", "type": "bool", "default": True},
            {"key": "priority", "label": "规则顺序", "type": "int", "default": 80, "min": 1, "max": 999},
            {"key": "extra_keywords", "label": "额外硬变量触发词", "type": "list", "default": []},
        ),
    },
    {
        "id": "official_company_hard_variable",
        "name": "核心公司官网硬变量",
        "group": "持仓与公司",
        "description": "核心公司官网的 HBM/存储、GPU/AI 平台、量产、客户认证、产能、资本开支、液冷和先进封装等硬变量即时提醒。",
        "runtime": "push_rules / official article",
        "execution_mode": ORDERED_FIRST_MATCH,
        "hit_markers": ("official_company_hard_variable",),
        "priority": 70,
        "fields": (
            {"key": "enabled", "label": "启用", "type": "bool", "default": True},
            {"key": "priority", "label": "规则顺序", "type": "int", "default": 70, "min": 1, "max": 999},
            {"key": "extra_keywords", "label": "额外硬变量触发词", "type": "list", "default": []},
            {
                "key": "extra_sources",
                "label": "额外官网来源 ID",
                "type": "list",
                "default": [],
                "help": "叠加到 OpenAI、NVIDIA、Samsung、SK hynix、Micron 等内置官网来源。",
            },
        ),
    },
    {
        "id": "macro_policy_line",
        "name": "美国宏观/Fed 政策线",
        "group": "宏观流动性",
        "description": "非农、CPI、PCE、FOMC/主席讲话等核心宏观事件即时提醒。",
        "runtime": "macro_policy + push_rules / article + event",
        "execution_mode": ORDERED_FIRST_MATCH,
        "hit_markers": ("macro_policy_line",),
        "priority": 60,
        "fields": (
            {"key": "enabled", "label": "启用", "type": "bool", "default": True},
            {"key": "priority", "label": "规则顺序", "type": "int", "default": 60, "min": 1, "max": 999},
            {"key": "extra_primary_keywords", "label": "额外核心宏观关键词", "type": "list", "default": []},
        ),
    },
    {
        "id": "trade_friction_escalation",
        "name": "中美/中欧贸易摩擦早期预警",
        "group": "宏观与政策",
        "description": "任一通用来源出现中美或中欧贸易政策工具、前置程序、报复威胁或明确关系升级时提前提醒；弱但明确的紧张信号进入 daily。",
        "runtime": "trade_friction / decision_engine / all normalized sources",
        "execution_mode": PARALLEL_MERGE,
        "hit_markers": ("trade_friction_escalation",),
        "fields": (
            {"key": "enabled", "label": "启用", "type": "bool", "default": True},
        ),
    },
    {
        "id": "attributed_research_hard_variable",
        "name": "明确署名的行业研究硬变量",
        "group": "研究机构/行业媒体",
        "description": "任一通用来源明确署名引用受信任行业研究源，并包含半导体/AI 产业硬变量或重大预期变化时即时提醒；来源分类不参与重要性判断。",
        "runtime": "attributed_research / market_flow + decision_engine",
        "execution_mode": PARALLEL_MERGE,
        "hit_markers": ("attributed_research_hard_variable",),
        "fields": (
            {"key": "enabled", "label": "启用", "type": "bool", "default": True},
            {
                "key": "trusted_institutions",
                "label": "受信任机构 ID",
                "type": "list",
                "default": ["semianalysis", "trendforce", "semi", "digitimes", "the_elec", "nikkei_xtech"],
                "help": "填写后替换默认机构列表；机构 ID 使用规则定义中的稳定 ID。",
            },
            {
                "key": "extra_aliases",
                "label": "额外机构/人物别名",
                "type": "list",
                "default": [],
                "help": "格式为 institution_id=alias，例如 trendforce=集邦。",
            },
        ),
    },
    {
        "id": "ai_compute_supply_demand",
        "name": "AI算力供需变化",
        "group": "通用内容规则",
        "description": "监测AI算力的外部供给、过剩/闲置、容量约束、利用率、合同与取消、价格、容量增减及供电/选址约束；具体事实即时提醒，普通观点进入日报。",
        "runtime": "ai_compute_supply_demand / market_flow + decision_engine",
        "execution_mode": PARALLEL_MERGE,
        "hit_markers": ("ai_compute_supply_demand",),
        "fields": (
            {"key": "enabled", "label": "启用", "type": "bool", "default": True},
        ),
    },
    {
        "id": "ai_hyperscaler_credit_stress",
        "name": "AI 信用与融资风险",
        "group": "通用内容规则",
        "description": "监测重点 AI 基础设施融资主体的发债、承接、二级表现、利差、融资成本、杠杆/现金流和资本开支融资约束；普通融资进入日报，明确硬结果或多个独立压力信号即时提醒。",
        "runtime": "ai_credit_risk / market_flow + decision_engine",
        "execution_mode": PARALLEL_MERGE,
        "hit_markers": ("ai_hyperscaler_credit_stress",),
        "fields": (
            {"key": "enabled", "label": "启用", "type": "bool", "default": True},
        ),
    },
    {
        "id": "industry_quantified_hardline",
        "name": "重点主题与产业硬变量",
        "group": "通用内容规则",
        "description": "任一通用来源同时命中重点主题，以及价格、产能、资本开支、订单、交付、供需、监管、技术路线或业绩规模等实质变量时即时提醒。",
        "runtime": "industry_hardline / market_flow + decision_engine",
        "execution_mode": PARALLEL_MERGE,
        "hit_markers": ("industry_hardline_override", "industry_quantified_hardline"),
        "fields": (
            {"key": "enabled", "label": "启用", "type": "bool", "default": True},
            {"key": "extra_keywords", "label": "额外硬变量触发词", "type": "list", "default": []},
        ),
    },
)

_ACTIVE_EVIDENCE_RULES = {
    "macro_policy_line": {
        "name": "宏观/Fed 证据抽取",
        "description": "为范围识别和来源元数据提取核心宏观/Fed 证据；不决定 action。",
        "runtime": "macro_policy / range and source evidence",
    },
    "attributed_research_hard_variable": {
        "name": "受信研究机构归因抽取",
        "description": "维护受信研究机构及别名，用于决策前归因证据抽取；不决定 action。",
        "runtime": "attributed_research / pre-decision evidence",
    },
}
RULE_DEFINITIONS = tuple(
    {**rule, **_ACTIVE_EVIDENCE_RULES[str(rule["id"])]}
    for rule in RULE_DEFINITIONS
    if str(rule["id"]) in _ACTIVE_EVIDENCE_RULES
)
RULE_BY_ID = {str(rule["id"]): rule for rule in RULE_DEFINITIONS}


def normalize_list(values: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _bounded_int(value: object, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def _default_settings(rule: dict[str, Any]) -> dict[str, Any]:
    return {str(field["key"]): field.get("default") for field in rule.get("fields") or ()}


def _normalize_settings(rule: dict[str, Any], raw: object) -> dict[str, Any]:
    values = raw if isinstance(raw, dict) else {}
    normalized = _default_settings(rule)
    for field in rule.get("fields") or ():
        key = str(field["key"])
        if key not in values:
            continue
        kind = str(field.get("type") or "")
        if kind == "bool":
            normalized[key] = bool(values[key])
        elif kind == "list":
            normalized[key] = normalize_list(values[key] if isinstance(values[key], list) else [])
        elif kind == "int":
            normalized[key] = _bounded_int(
                values[key],
                int(field.get("default") or 0),
                minimum=int(field.get("min") or 0),
                maximum=int(field.get("max") or 999),
            )
    return normalized


def default_config() -> dict[str, Any]:
    return {"version": 1, "rules": {rule_id: _default_settings(rule) for rule_id, rule in RULE_BY_ID.items()}}


def load_rule_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        return default_config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"规则中心配置读取失败：{exc}") from exc
    rules_raw = raw.get("rules") if isinstance(raw, dict) else {}
    rules_raw = rules_raw if isinstance(rules_raw, dict) else {}
    return {
        "version": 1,
        "rules": {rule_id: _normalize_settings(rule, rules_raw.get(rule_id)) for rule_id, rule in RULE_BY_ID.items()},
    }


def configured_rule_settings(rule_id: str, path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Return only fields explicitly present in the private override file."""
    rule = RULE_BY_ID.get(str(rule_id))
    if not rule or not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rules_raw = raw.get("rules") if isinstance(raw, dict) and isinstance(raw.get("rules"), dict) else {}
    values = rules_raw.get(str(rule_id))
    if not isinstance(values, dict):
        return {}
    normalized = _normalize_settings(rule, values)
    return {key: normalized[key] for key in values if key in normalized}


def save_rule_config(raw: object, path: Path = CONFIG_PATH) -> dict[str, Any]:
    rules_raw = raw.get("rules") if isinstance(raw, dict) and isinstance(raw.get("rules"), dict) else {}
    payload = {
        "version": 1,
        "rules": {rule_id: _normalize_settings(rule, rules_raw.get(rule_id)) for rule_id, rule in RULE_BY_ID.items()},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return payload


def rule_settings(rule_id: str) -> dict[str, Any]:
    rule = RULE_BY_ID.get(str(rule_id))
    if not rule:
        return {}
    return load_rule_config()["rules"][str(rule_id)]


def rule_enabled(rule_id: str) -> bool:
    settings = rule_settings(rule_id)
    return bool(settings.get("enabled", True))


def rule_priority(rule_id: str) -> int:
    rule = RULE_BY_ID.get(str(rule_id))
    if rule and rule.get("execution_mode") != ORDERED_FIRST_MATCH:
        raise ValueError(f"并行合并规则没有执行顺序：{rule_id}")
    default = int((rule or {}).get("priority") or 0)
    return int(rule_settings(rule_id).get("priority") or default)


def effective_list(rule_id: str, key: str, default: Iterable[object], *, replace_when_set: bool = False) -> tuple[str, ...]:
    configured = rule_settings(rule_id).get(key)
    values = normalize_list(configured if isinstance(configured, list) else [])
    if replace_when_set and values:
        return tuple(values)
    return tuple(normalize_list([*default, *values]))


def _rule_view(rule: dict[str, Any], settings: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
    fields = []
    for field in rule.get("fields") or ():
        key = str(field["key"])
        fields.append({**field, "value": settings.get(key, field.get("default"))})
    execution_mode = str(rule.get("execution_mode") or "")
    return {
        "id": rule["id"],
        "name": rule["name"],
        "group": rule["group"],
        "description": rule["description"],
        "runtime": rule["runtime"],
        "execution_mode": execution_mode,
        "execution_mode_label": EXECUTION_MODE_LABELS.get(execution_mode, execution_mode),
        "fields": fields,
        "stats": stats,
    }


def _marker_stats(conn, markers: tuple[str, ...], cutoff: str) -> dict[str, Any]:
    clauses = " OR ".join(
        "COALESCE(r.decision_json,'') || COALESCE(r.legacy_payload_json,'') LIKE ?"
        for _ in markers
    )
    params = [f"%{marker}%" for marker in markers]
    total = int(
        conn.execute(
            f"""
            SELECT COUNT(*)
            FROM market_reviews r
            WHERE r.created_at>=? AND r.is_current=1 AND ({clauses})
            """,
            [cutoff, *params],
        ).fetchone()[0]
    )
    row = conn.execute(
        f"""
        SELECT m.source,m.source_item_id,m.title,m.published_at,r.created_at
        FROM market_reviews r
        JOIN market_items m ON m.id=r.market_item_id
        WHERE r.created_at>=? AND r.is_current=1 AND ({clauses})
        ORDER BY r.created_at DESC LIMIT 1
        """,
        [cutoff, *params],
    ).fetchone()
    latest = (
        {
            "source": row[0] or "",
            "item_id": row[1] or "",
            "title": row[2] or "",
            "published_at": row[3] or "",
            "created_at": row[4] or "",
        }
        if row
        else {}
    )
    return {"matches_30d": total, "last_match": latest}


def rule_center_payload(db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    generic = load_rule_config()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    init_db(db_path).close()
    with connect_sqlite(db_path) as conn:
        rules = []
        for definition in RULE_DEFINITIONS:
            rule_id = str(definition["id"])
            settings = dict(generic["rules"][rule_id])
            rules.append(_rule_view(definition, settings, _marker_stats(conn, tuple(definition["hit_markers"]), cutoff)))
    return {
        "rules": rules,
        "config_path": str(CONFIG_PATH),
        "has_local_override": CONFIG_PATH.exists(),
        "runtime_note": "保存后新资讯会动态读取私有配置，无需重启；只有同时更新代码或环境变量时才需要重启对应常驻服务。",
    }


def _changes(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    before_rules = before.get("rules") if isinstance(before.get("rules"), dict) else {}
    after_rules = after.get("rules") if isinstance(after.get("rules"), dict) else {}
    for rule_id in RULE_BY_ID:
        left = before_rules.get(rule_id, {})
        right = after_rules.get(rule_id, {})
        if left != right:
            result.append({"rule_id": rule_id, "before": left, "after": right})
    return result


def _write_audit(before: dict[str, Any], after: dict[str, Any], db_path: Path) -> None:
    changes = _changes(before, after)
    if not changes:
        return
    init_db(db_path).close()
    with connect_sqlite(db_path) as conn:
        conn.execute(
            """
            INSERT INTO rule_config_audit (changed_at, actor, before_json, after_json, changes_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                "web_workbench",
                json.dumps(before, ensure_ascii=False),
                json.dumps(after, ensure_ascii=False),
                json.dumps(changes, ensure_ascii=False),
            ),
        )
        conn.commit()


def save_rule_center_config(raw: object, *, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    incoming_rules = raw.get("rules") if isinstance(raw, dict) and isinstance(raw.get("rules"), dict) else {}
    before_generic = load_rule_config()
    generic_input = {"rules": {rule_id: incoming_rules.get(rule_id) for rule_id in RULE_BY_ID}}
    saved_generic = save_rule_config(generic_input)
    before = {"rules": before_generic["rules"]}
    after = {"rules": saved_generic["rules"]}
    _write_audit(before, after, db_path)
    return rule_center_payload(db_path)


def list_rule_audit(*, db_path: Path = DEFAULT_DB_PATH, limit: int = 30) -> list[dict[str, Any]]:
    init_db(db_path).close()
    with connect_sqlite(db_path) as conn:
        if not db_table_exists(conn, "rule_config_audit"):
            return []
        rows = conn.execute(
            """
            SELECT id, changed_at, actor, changes_json
            FROM rule_config_audit
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    result = []
    for row in rows:
        try:
            changes = json.loads(row[3] or "[]")
        except json.JSONDecodeError:
            changes = []
        result.append({"id": row[0], "changed_at": row[1], "actor": row[2], "changes": changes if isinstance(changes, list) else []})
    return result


def _article_candidates(conn, cutoff: str, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT m.source,m.source_item_id,m.title,m.summary,m.full_text,m.published_at,
               m.raw_json,r.legacy_payload_json
        FROM market_items m
        JOIN market_item_aliases a ON a.market_item_id=m.id AND a.item_kind='article'
        JOIN market_reviews r ON r.market_item_id=m.id AND r.is_current=1
        WHERE r.created_at >= ?
        ORDER BY r.created_at DESC
        LIMIT ?
        """,
        (cutoff, limit),
    ).fetchall()
    candidates = []
    for source, item_id, title, summary, full_text, published_at, raw_json, payload_json in rows:
        try:
            raw = json.loads(raw_json or "{}")
        except json.JSONDecodeError:
            raw = {}
        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        candidates.append(
            {
                "kind": "article",
                "source": source,
                "item_id": item_id,
                "title": title,
                "summary": summary or payload.get("daily_summary") or "",
                "published_at": published_at or "",
                "full_text": full_text or summary or "",
                "raw": {**raw, "review": payload},
            }
        )
    return candidates


def _event_candidates(conn, cutoff: str, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT m.source_item_id,m.source,m.title,m.summary,m.full_text,m.published_at,m.raw_json
        FROM market_items m
        JOIN market_item_aliases a ON a.market_item_id=m.id AND a.item_kind='event'
        WHERE m.first_seen_at >= ?
        ORDER BY m.first_seen_at DESC
        LIMIT ?
        """,
        (cutoff, limit),
    ).fetchall()
    candidates = []
    for item_id, source, title, summary, full_text, published_at, raw_json in rows:
        try:
            raw = json.loads(raw_json or "{}")
        except json.JSONDecodeError:
            raw = {}
        candidates.append(
            {
                "kind": "event",
                "source": source,
                "item_id": str(item_id),
                "title": title,
                "summary": summary or "",
                "full_text": full_text or "",
                "published_at": published_at or "",
                "raw": raw,
            }
        )
    return candidates
