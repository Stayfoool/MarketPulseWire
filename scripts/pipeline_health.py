"""Health helpers for internal scheduled pipelines."""

from __future__ import annotations

from pathlib import Path

from db_utils import connect_sqlite
from market_db import DEFAULT_DB_PATH, init_db
from source_health import record_source_failure, record_source_success


MONITOR_NAME = "signal_pipeline"


def record_pipeline_success(name: str, *, db_path: Path = DEFAULT_DB_PATH) -> None:
    init_db(db_path).close()
    with connect_sqlite(db_path) as conn:
        record_source_success(conn, MONITOR_NAME, name)
        conn.commit()


def record_pipeline_failure(name: str, error: Exception | str, *, db_path: Path = DEFAULT_DB_PATH) -> None:
    init_db(db_path).close()
    with connect_sqlite(db_path) as conn:
        record_source_failure(conn, MONITOR_NAME, name, error)
        conn.commit()
