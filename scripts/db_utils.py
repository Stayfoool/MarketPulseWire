"""SQLite helpers shared by local monitor processes."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar


T = TypeVar("T")


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


def ensure_seen_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_items (
            source TEXT NOT NULL,
            item_id TEXT NOT NULL,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT,
            published_at TEXT,
            first_seen_at TEXT NOT NULL,
            PRIMARY KEY (source, item_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_sources (
            source TEXT PRIMARY KEY,
            first_seen_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_seen_items_first_seen ON seen_items(first_seen_at)")


def ensure_source_state_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_state (
            source TEXT PRIMARY KEY,
            state_json TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )


def ensure_trendforce_page_seen_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trendforce_page_seen_items (
            item_id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            first_source TEXT NOT NULL,
            first_seen_at TEXT NOT NULL
        )
        """
    )
