"""Audited taxonomy for international-bank allocation rotation alerts."""

from __future__ import annotations

from typing import Iterable


ROTATION_THEME_BUCKETS: tuple[dict[str, object], ...] = (
    {
        "id": "semiconductor_equities",
        "label": "芯片股",
        "aliases": ("芯片股", "半导体股", "半导体板块", "chip stocks", "semiconductor stocks", "semis"),
        "style": False,
    },
    {
        "id": "ai_cloud_hyperscalers",
        "label": "AI 云服务商/超大规模云",
        "aliases": (
            "AI云服务商",
            "AI 云服务商",
            "云服务商",
            "云厂商",
            "超大规模云厂商",
            "超大规模云",
            "hyperscaler",
            "hyperscalers",
            "AI cloud providers",
            "cloud service providers",
        ),
        "style": False,
    },
    {
        "id": "ai_hardware",
        "label": "AI 硬件",
        "aliases": ("AI硬件", "AI 硬件", "人工智能硬件", "AI hardware"),
        "style": False,
    },
    {
        "id": "ai_applications",
        "label": "AI 应用",
        "aliases": ("AI应用", "AI 应用", "人工智能应用", "应用软件", "AI applications", "AI software"),
        "style": False,
    },
    {
        "id": "growth_equities",
        "label": "成长股",
        "aliases": ("成长股", "成长风格", "growth stocks", "growth equities"),
        "style": True,
    },
    {
        "id": "value_equities",
        "label": "价值股",
        "aliases": ("价值股", "价值风格", "value stocks", "value equities"),
        "style": True,
    },
    {
        "id": "cyclical_equities",
        "label": "周期股",
        "aliases": ("周期股", "周期风格", "cyclicals", "cyclical stocks"),
        "style": True,
    },
    {
        "id": "defensive_equities",
        "label": "防御股",
        "aliases": ("防御股", "防御风格", "defensives", "defensive stocks"),
        "style": True,
    },
    {
        "id": "large_cap_equities",
        "label": "大盘股",
        "aliases": ("大盘股", "大盘风格", "large caps", "large-cap stocks"),
        "style": True,
    },
    {
        "id": "small_cap_equities",
        "label": "小盘股",
        "aliases": ("小盘股", "小盘风格", "small caps", "small-cap stocks"),
        "style": True,
    },
)

ROTATION_THEME_BY_ID = {str(bucket["id"]): bucket for bucket in ROTATION_THEME_BUCKETS}


def normalize_extra_theme_aliases(values: Iterable[object]) -> list[str]:
    """Keep only additive ``known_theme_id=alias`` mappings."""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        raw = str(value or "").strip()
        theme_id, separator, alias = raw.partition("=")
        theme_id = theme_id.strip()
        alias = alias.strip()
        if not separator or theme_id not in ROTATION_THEME_BY_ID or not alias:
            continue
        normalized = f"{theme_id}={alias}"
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result
