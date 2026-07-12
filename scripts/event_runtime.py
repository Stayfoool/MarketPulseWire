"""Runtime selector for direct versus compatibility event flow entrypoints."""

from __future__ import annotations

import os
from pathlib import Path
from types import ModuleType
from typing import Any

from market_db import DEFAULT_DB_PATH


DIRECT_PATH_ENV = "SURVEIL_EVENT_DIRECT_PATH"


def event_direct_path_enabled() -> bool:
    return os.getenv(DIRECT_PATH_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


def selected_event_module() -> ModuleType:
    if event_direct_path_enabled():
        import market_event_flow

        return market_event_flow
    import event_pipeline

    return event_pipeline


def runtime_path_name() -> str:
    return "direct" if event_direct_path_enabled() else "compat"


def content_hash(*parts: str) -> str:
    return selected_event_module().content_hash(*parts)


def load_enabled_holdings(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    return selected_event_module().load_enabled_holdings(db_path)


def upsert_event(event: dict[str, Any], db_path: Path = DEFAULT_DB_PATH) -> tuple[int, bool]:
    return selected_event_module().upsert_event(event, db_path)


def analyze_event(event_id: int, task: str = "portfolio_event", db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    return selected_event_module().analyze_event(event_id, task=task, db_path=db_path)


def maybe_deliver_event(event_id: int, analysis: dict[str, Any], db_path: Path = DEFAULT_DB_PATH) -> str:
    return selected_event_module().maybe_deliver_event(event_id, analysis, db_path=db_path)
