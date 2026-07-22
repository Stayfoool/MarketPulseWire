"""Shared Web-managed semiconductor/AI admission keyword configuration."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from rule_config_schema import parse_rule_config


RULE_CONFIG_ENV = "RULE_CORE_SHADOW_CONFIG"


def normalize_keywords(values: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        keyword = str(value or "").strip()
        if not keyword:
            continue
        key = keyword.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(keyword)
    return result


def rule_config_path(
    path: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> Path:
    if path is not None:
        return path.expanduser()
    raw = str((env or os.environ).get(RULE_CONFIG_ENV) or "").strip()
    if not raw:
        raise ValueError(f"未配置 {RULE_CONFIG_ENV}，无法读取媒体关键词")
    return Path(raw).expanduser()


def _read_rule_payload(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"媒体关键词配置读取失败：{exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("规则配置必须是 JSON object")
    return raw


def _validate_rule_payload(payload: Mapping[str, object]) -> None:
    parse_rule_config(payload)


def load_media_keyword_config(
    path: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    resolved = rule_config_path(path, env=env)
    raw = _read_rule_payload(resolved)
    _validate_rule_payload(raw)
    return {
        "semiconductor_ai_keywords": normalize_keywords(raw.get("semiconductor_ai_keywords") or []),
        "exclude_keywords": normalize_keywords(raw.get("exclude_keywords") or []),
        "config_version": str(raw.get("config_version") or "").strip(),
    }


def _config_version(keywords: list[str], excludes: list[str]) -> str:
    digest_input = json.dumps(
        {"semiconductor_ai_keywords": keywords, "exclude_keywords": excludes},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:10]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"web-rule-config-{stamp}-{digest}"


def save_media_keyword_config(
    semiconductor_ai_keywords: Iterable[object],
    exclude_keywords: Iterable[object],
    path: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    resolved = rule_config_path(path, env=env)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    lock_path = resolved.with_name(f".{resolved.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        raw = _read_rule_payload(resolved)
        keywords = normalize_keywords(semiconductor_ai_keywords)
        excludes = normalize_keywords(exclude_keywords)
        if not keywords:
            raise ValueError("半导体/AI关键词不能为空")
        current_keywords = normalize_keywords(raw.get("semiconductor_ai_keywords") or [])
        current_excludes = normalize_keywords(raw.get("exclude_keywords") or [])
        if keywords == current_keywords and excludes == current_excludes:
            return {
                "semiconductor_ai_keywords": keywords,
                "exclude_keywords": excludes,
                "config_version": str(raw.get("config_version") or "").strip(),
                "changed": False,
                "backup_path": "",
            }

        updated = dict(raw)
        updated["semiconductor_ai_keywords"] = keywords
        updated["exclude_keywords"] = excludes
        updated["config_version"] = _config_version(keywords, excludes)
        _validate_rule_payload(updated)

        backup_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = resolved.with_name(f"{resolved.name}.bak-{backup_stamp}")
        shutil.copy2(resolved, backup_path)
        os.chmod(backup_path, 0o600)

        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{resolved.name}.", suffix=".tmp", dir=str(resolved.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(updated, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, resolved)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        return {
            "semiconductor_ai_keywords": keywords,
            "exclude_keywords": excludes,
            "config_version": updated["config_version"],
            "changed": True,
            "backup_path": str(backup_path),
        }


def media_keyword_payload(
    path: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    return load_media_keyword_config(path, env=env)


def keyword_matches_text(keyword: str, text: str) -> bool:
    key = str(keyword or "").strip()
    if not key:
        return False
    lowered = str(text or "").casefold()
    folded_key = key.casefold()
    if re.fullmatch(r"[a-z0-9_.+-]+", folded_key):
        return re.search(rf"(?<![a-z0-9]){re.escape(folded_key)}(?![a-z0-9])", lowered) is not None
    return folded_key in lowered


def media_keyword_match(*parts: str) -> dict[str, str | bool]:
    text = " ".join(part for part in parts if part)
    config = load_media_keyword_config()
    for keyword in config["exclude_keywords"]:
        if keyword_matches_text(str(keyword), text):
            return {"matched": False, "blocked": True, "keyword": keyword, "bucket": "exclude"}
    for keyword in config["semiconductor_ai_keywords"]:
        if keyword_matches_text(str(keyword), text):
            return {
                "matched": True,
                "blocked": False,
                "keyword": keyword,
                "bucket": "semiconductor_ai",
            }
    return {"matched": False, "blocked": False, "keyword": "", "bucket": ""}


def is_media_focus_item(*parts: str) -> bool:
    return bool(media_keyword_match(*parts).get("matched"))
