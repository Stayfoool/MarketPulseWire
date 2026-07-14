"""Private runtime configuration for international-bank theme strategy alerts."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from investment_bank_theme_taxonomy import normalize_extra_theme_aliases


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "investment_bank_theme_rules.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    # Empty means use the audited aliases built into push_rules.py.
    "allowed_banks": [],
    "extra_theme_keywords": [],
    "extra_action_keywords": [],
    "extra_rotation_theme_aliases": [],
    "allow_broad_style_rotation": True,
    "require_investment_universe_leg": True,
    "min_evidence_score": 2,
    "allow_secondary_sources": True,
    "dedup_lookback_days": 14,
}


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


def _positive_int(value: object, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def normalize_config(raw: object) -> dict[str, Any]:
    values = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(values.get("enabled", DEFAULT_CONFIG["enabled"])),
        "allowed_banks": normalize_list(values.get("allowed_banks") or []),
        "extra_theme_keywords": normalize_list(values.get("extra_theme_keywords") or []),
        "extra_action_keywords": normalize_list(values.get("extra_action_keywords") or []),
        "extra_rotation_theme_aliases": normalize_extra_theme_aliases(
            values.get("extra_rotation_theme_aliases") or []
        ),
        "allow_broad_style_rotation": bool(
            values.get("allow_broad_style_rotation", DEFAULT_CONFIG["allow_broad_style_rotation"])
        ),
        "require_investment_universe_leg": bool(
            values.get("require_investment_universe_leg", DEFAULT_CONFIG["require_investment_universe_leg"])
        ),
        "min_evidence_score": _positive_int(
            values.get("min_evidence_score"),
            int(DEFAULT_CONFIG["min_evidence_score"]),
            minimum=1,
            maximum=8,
        ),
        "allow_secondary_sources": bool(values.get("allow_secondary_sources", DEFAULT_CONFIG["allow_secondary_sources"])),
        "dedup_lookback_days": _positive_int(
            values.get("dedup_lookback_days"),
            int(DEFAULT_CONFIG["dedup_lookback_days"]),
            minimum=1,
            maximum=90,
        ),
    }


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        config = dict(DEFAULT_CONFIG)
    else:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"国际投行主题策略配置读取失败：{exc}") from exc
        config = normalize_config(raw)
    try:
        from rule_center import configured_rule_settings

        override = configured_rule_settings("international_bank_theme_strategy")
    except Exception:  # noqa: BLE001 - rule-center config must not break alerts.
        override = {}
    for key in DEFAULT_CONFIG:
        if key in override:
            config[key] = normalize_config({**config, key: override[key]})[key]
    return config


def save_config(raw: object, path: Path = CONFIG_PATH) -> dict[str, Any]:
    payload = normalize_config(raw)
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


def config_payload(path: Path = CONFIG_PATH) -> dict[str, Any]:
    payload = load_config(path)
    return {
        **payload,
        "default_config": DEFAULT_CONFIG,
        "path": str(path),
        "has_local_override": path.exists(),
    }
