#!/usr/bin/env python3
"""Preview or apply the approved admission-rule simplification."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from env_utils import load_env
from media_keyword_config import normalize_keywords, rule_config_path
from rule_config_schema import parse_rule_config


ROOT = Path(__file__).resolve().parents[1]
REMOVED_GENERIC_TERMS = frozenset({"ai", "人工智能"})


def _term_id(value: str) -> str:
    return "term:" + hashlib.sha256(value.casefold().encode("utf-8")).hexdigest()[:12]


def _read_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"配置读取失败：{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("规则配置必须是 JSON object")
    return payload


def _read_title_keywords(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"标题限定关键词文件读取失败：{exc}") from exc
    if not isinstance(payload, list):
        raise ValueError("标题限定关键词文件必须是 JSON array")
    return normalize_keywords(payload)


def build_migration(
    payload: Mapping[str, Any],
    *,
    title_keywords: list[str],
) -> dict[str, Any]:
    master = normalize_keywords(payload.get("semiconductor_ai_keywords") or [])
    retained = [term for term in master if term.casefold() not in REMOVED_GENERIC_TERMS]
    retained_keys = {term.casefold() for term in retained}
    invalid_title = [term for term in title_keywords if term.casefold() not in retained_keys]
    if invalid_title:
        raise ValueError(
            "标题限定关键词必须来自删除通用词后的 semiconductor_ai_keywords："
            + ", ".join(_term_id(term) for term in invalid_title)
        )

    raw_macro = payload.get("macro_data")
    if not isinstance(raw_macro, dict):
        raise ValueError("macro_data 必须是 JSON object")
    tiers = raw_macro.get("tiers")
    if tiers is not None:
        if not isinstance(tiers, dict) or not isinstance(tiers.get("primary"), list):
            raise ValueError("旧 macro_data.tiers.primary 必须是 JSON array")
        indicators = normalize_keywords(tiers["primary"])
    else:
        indicators = normalize_keywords(raw_macro.get("indicators") or [])

    updated = dict(payload)
    updated["semiconductor_ai_keywords"] = retained
    updated["semiconductor_ai_title_keywords"] = title_keywords
    updated["macro_data"] = {
        "indicators": indicators,
        "context_aliases": list(raw_macro.get("context_aliases") or []),
    }
    changed = (
        master != retained
        or normalize_keywords(payload.get("semiconductor_ai_title_keywords") or [])
        != title_keywords
        or raw_macro != updated["macro_data"]
    )
    if changed:
        digest = hashlib.sha256(
            json.dumps(
                {
                    "semiconductor_ai_keywords": retained,
                    "semiconductor_ai_title_keywords": title_keywords,
                    "macro_data": updated["macro_data"],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:10]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        updated["config_version"] = f"admission-simplification-{stamp}-{digest}"
    parse_rule_config(updated)

    old_indicators = normalize_keywords(raw_macro.get("indicators") or [])
    removed_terms = [term for term in master if term.casefold() in REMOVED_GENERIC_TERMS]
    return {
        "updated": updated,
        "preview": {
            "current_keyword_count": len(master),
            "target_keyword_count": len(retained),
            "removed_generic_count": len(removed_terms),
            "removed_generic_term_ids": [_term_id(term) for term in removed_terms],
            "title_keyword_count": len(title_keywords),
            "current_macro_indicator_count": len(old_indicators),
            "target_macro_indicator_count": len(indicators),
            "removed_macro_indicator_count": len(old_indicators) - len(indicators),
            "legacy_macro_tiers_removed": tiers is not None,
            "changed": changed,
        },
    }


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> Path:
    backup_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = path.with_name(f"{path.name}.bak-{backup_stamp}")
    shutil.copy2(path, backup_path)
    os.chmod(backup_path, 0o600)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return backup_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--rule-config", type=Path)
    parser.add_argument("--title-keywords-file", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env(args.env_file)
    target = rule_config_path(args.rule_config, env=os.environ)
    lock_path = target.with_name(f".{target.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        result = build_migration(
            _read_object(target),
            title_keywords=_read_title_keywords(args.title_keywords_file),
        )
        public = dict(result["preview"])
        public["applied"] = False
        if args.apply and public["changed"]:
            backup = _atomic_write(target, result["updated"])
            public.update(
                {
                    "applied": True,
                    "config_version": result["updated"]["config_version"],
                    "backup_path": str(backup),
                }
            )
    print(json.dumps(public, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
