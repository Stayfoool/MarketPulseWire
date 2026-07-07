"""Shared runtime helpers for MarketPulseWire collectors.

This module is deliberately small and behavior-preserving. It centralizes the
collector plumbing that every source family needs before we merge the larger
research/official/news collectors.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from db_utils import ensure_source_state_table
from source_backoff import should_skip_by_backoff
from source_profiles import (
    SOURCE_PROFILE_CONFIG_PATH,
    filter_enabled_named_sources,
    filter_enabled_source_mapping,
)


T = TypeVar("T")


def source_id_for(source: Any) -> str:
    """Return a stable source id from a string or object with a ``name`` field."""
    return str(getattr(source, "name", source) or "").strip()


def filter_enabled_mapping_for_run(
    sources: dict[str, T],
    *,
    label: str,
    config_path: Path = SOURCE_PROFILE_CONFIG_PATH,
) -> dict[str, T]:
    enabled = filter_enabled_source_mapping(sources, config_path=config_path)
    disabled_count = len(sources) - len(enabled)
    if disabled_count:
        print(f"source profile: {label} 跳过 {disabled_count} 个已停用 source。", flush=True)
    if not enabled:
        print(f"source profile: {label} 没有启用的 source，跳过本轮。", flush=True)
    return enabled


def filter_enabled_named_for_run(
    sources: Iterable[T],
    *,
    label: str,
    config_path: Path = SOURCE_PROFILE_CONFIG_PATH,
) -> list[T]:
    source_list = list(sources)
    enabled = filter_enabled_named_sources(source_list, config_path=config_path)
    disabled_count = len(source_list) - len(enabled)
    if disabled_count:
        print(f"source profile: {label} 跳过 {disabled_count} 个已停用 source。", flush=True)
    if not enabled:
        print(f"source profile: {label} 没有启用的 source，跳过本轮。", flush=True)
    return enabled


def source_state_key(source: str, *, prefix: str = "") -> str:
    source = str(source or "").strip()
    return f"{prefix}:{source}" if prefix else source


def load_source_state(
    conn: sqlite3.Connection,
    source: str,
    *,
    prefix: str = "",
) -> dict[str, Any]:
    ensure_source_state_table(conn)
    row = conn.execute(
        "SELECT state_json FROM source_state WHERE source = ?",
        (source_state_key(source, prefix=prefix),),
    ).fetchone()
    if not row or not row[0]:
        return {}
    try:
        parsed = json.loads(str(row[0]))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def save_source_state(
    conn: sqlite3.Connection,
    source: str,
    state: dict[str, Any],
    *,
    prefix: str = "",
) -> None:
    ensure_source_state_table(conn)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO source_state (source, state_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(source) DO UPDATE SET
            state_json = excluded.state_json,
            updated_at = excluded.updated_at
        """,
        (
            source_state_key(source, prefix=prefix),
            json.dumps(state, ensure_ascii=False, sort_keys=True),
            now,
        ),
    )


def load_source_states(
    conn: sqlite3.Connection,
    sources: Iterable[str],
    *,
    prefix: str = "",
) -> dict[str, dict[str, Any]]:
    return {source: load_source_state(conn, source, prefix=prefix) for source in sources}


def split_sources_by_backoff(
    sources: Iterable[str],
    states: dict[str, dict[str, Any]],
    *,
    label_for_source: Callable[[str], str] | None = None,
) -> tuple[list[str], set[str]]:
    runnable: list[str] = []
    skipped: set[str] = set()
    label_for_source = label_for_source or (lambda source: source)
    for source in sources:
        skip, until = should_skip_by_backoff(states.get(source, {}))
        if skip:
            skipped.add(source)
            print(f"{label_for_source(source)}：源级退避中，跳过抓取直到 {until}。", flush=True)
            continue
        runnable.append(source)
    return runnable, skipped
