"""SQLite helpers shared by local monitor processes."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar


T = TypeVar("T")


SEEN_ITEM_LIFECYCLE_FIELDS = {
    "collection_class",
    "processability_status",
    "processability_reason",
    "admission_status",
    "admission_reason",
    "admission_matched_families_json",
    "admission_evidence_json",
    "admission_config_version",
    "admission_rule_contract_version",
    "admission_evaluated_at",
    "result_event_id",
    "processing_status",
    "processing_error",
    "processed_at",
    "lifecycle_updated_at",
}
SEEN_ITEM_LIFECYCLE_VALUES = {
    "collection_class": {"baseline", "live"},
    "processability_status": {
        "not_required", "pending", "succeeded", "fallback",
        "failed_retryable", "failed_terminal",
    },
    "admission_status": {"pending", "admitted", "excluded", "not_applicable"},
    "processing_status": {
        "not_applicable", "pending", "succeeded",
        "failed_retryable", "failed_terminal", "insufficient_evidence",
    },
}


def is_locked_error(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


def connect_sqlite(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(exist_ok=True)
    last_error: sqlite3.OperationalError | None = None
    for attempt in range(5):
        conn = sqlite3.connect(path, timeout=60, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout = 60000")
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.OperationalError as exc:
                if not is_locked_error(exc):
                    raise
            conn.execute("PRAGMA synchronous = NORMAL")
            return conn
        except sqlite3.OperationalError as exc:
            conn.close()
            last_error = exc
            if not is_locked_error(exc) or attempt == 4:
                raise
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"SQLite 连接失败：{last_error}")


def retry_on_locked(operation: Callable[[], T], attempts: int = 6) -> T:
    last_error: sqlite3.OperationalError | None = None
    for attempt in range(attempts):
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            if not is_locked_error(exc):
                raise
            last_error = exc
            if attempt == attempts - 1:
                break
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"SQLite 数据库繁忙，重试后仍失败：{last_error}")


def update_seen_item_lifecycle(
    conn: sqlite3.Connection,
    source: str,
    item_id: str,
    **values: Any,
) -> None:
    unknown = set(values) - SEEN_ITEM_LIFECYCLE_FIELDS
    if unknown:
        raise ValueError(f"unsupported seen_items lifecycle fields: {sorted(unknown)}")
    if not values:
        return
    for field, allowed in SEEN_ITEM_LIFECYCLE_VALUES.items():
        if field in values and values[field] not in allowed:
            raise ValueError(f"invalid {field}: {values[field]}")
    assignments = ", ".join(f"{column} = ?" for column in values)
    params = [values[column] for column in values]
    cursor = conn.execute(
        f"UPDATE seen_items SET {assignments} WHERE source = ? AND item_id = ?",
        (*params, source, item_id),
    )
    if cursor.rowcount != 1:
        raise LookupError(f"seen item not found: {source}/{item_id}")
