#!/usr/bin/env python3
"""Preview or apply the one-time media-keyword unification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from env_utils import load_env  # noqa: E402
from media_keyword_config import (  # noqa: E402
    load_media_keyword_config,
    normalize_keywords,
    rule_config_path,
    save_media_keyword_config,
)


LEGACY_CONFIG_PATH = ROOT / "config" / "media_keywords.json"
APPROVED_SAFE_ALIASES = (
    "SMIC",
    "中芯国际",
    "Kirin",
    "华为麒麟",
    "麒麟芯片",
    "JCET",
    "长电科技",
)
APPROVED_OMITTED_LEGACY_CODE_TERMS = (
    "power",
    "electricity",
    "energy storage",
    "grid",
    "电力",
)


def _term_id(value: str) -> str:
    return "term:" + hashlib.sha256(value.casefold().encode("utf-8")).hexdigest()[:12]


def _legacy_payload(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {"base_keywords": [], "include_keywords": [], "exclude_keywords": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"旧媒体关键词配置读取失败：{exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("旧媒体关键词配置必须是 JSON object")
    return {
        "base_keywords": normalize_keywords(raw.get("base_keywords") or []),
        "include_keywords": normalize_keywords(raw.get("include_keywords") or []),
        "exclude_keywords": normalize_keywords(raw.get("exclude_keywords") or []),
    }


def _ordered_union(*groups: Iterable[object]) -> list[str]:
    return normalize_keywords(value for group in groups for value in group)


def build_migration(
    *,
    rule_path: Path,
    legacy_path: Path = LEGACY_CONFIG_PATH,
) -> dict[str, object]:
    current = load_media_keyword_config(rule_path)
    legacy = _legacy_payload(legacy_path)
    current_keywords = list(current["semiconductor_ai_keywords"])
    legacy_user_keywords = _ordered_union(
        legacy["base_keywords"], legacy["include_keywords"]
    )
    merged_keywords = _ordered_union(
        current_keywords,
        legacy_user_keywords,
        APPROVED_SAFE_ALIASES,
    )
    merged_excludes = _ordered_union(
        current["exclude_keywords"], legacy["exclude_keywords"]
    )
    current_keys = {value.casefold() for value in current_keywords}
    merged_keys = {value.casefold() for value in merged_keywords}
    omitted_code_terms = [
        value
        for value in APPROVED_OMITTED_LEGACY_CODE_TERMS
        if value.casefold() not in merged_keys
    ]
    added = [value for value in merged_keywords if value.casefold() not in current_keys]
    return {
        "migration_version": "media-keywords-to-semiconductor-ai-v1",
        "current_config_version": current["config_version"],
        "current_keyword_count": len(current_keywords),
        "legacy_user_base_count": len(legacy["base_keywords"]),
        "legacy_user_include_count": len(legacy["include_keywords"]),
        "legacy_user_exclude_count": len(legacy["exclude_keywords"]),
        "approved_alias_count": len(APPROVED_SAFE_ALIASES),
        "added_count": len(added),
        "added_term_ids": [_term_id(value) for value in added],
        "omitted_legacy_code_count": len(omitted_code_terms),
        "omitted_legacy_code_term_ids": [_term_id(value) for value in omitted_code_terms],
        "target_keyword_count": len(merged_keywords),
        "target_exclude_count": len(merged_excludes),
        "changed": merged_keywords != current_keywords
        or merged_excludes != list(current["exclude_keywords"]),
        "keywords": merged_keywords,
        "exclude_keywords": merged_excludes,
    }


def public_preview(migration: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in migration.items()
        if key not in {"keywords", "exclude_keywords"}
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--rule-config", type=Path)
    parser.add_argument("--legacy-config", type=Path, default=LEGACY_CONFIG_PATH)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env(args.env_file)
    target = rule_config_path(args.rule_config, env=os.environ)
    migration = build_migration(rule_path=target, legacy_path=args.legacy_config)
    result = public_preview(migration)
    if args.apply:
        saved = save_media_keyword_config(
            migration["keywords"],
            migration["exclude_keywords"],
            target,
        )
        result.update(
            {
                "applied": True,
                "changed": saved["changed"],
                "config_version": saved["config_version"],
                "backup_path": saved["backup_path"],
            }
        )
    else:
        result["applied"] = False
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
