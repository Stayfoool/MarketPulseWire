"""Legacy event API selector backed by the global market-flow route."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType
from typing import Any

from market_db import DEFAULT_DB_PATH
from market_runtime import DIRECT_PATH_ENV, market_flow_direct_path_enabled


def event_direct_path_enabled() -> bool:
    return market_flow_direct_path_enabled()


def selected_event_module() -> ModuleType:
    if event_direct_path_enabled():
        import market_event_adapter

        return market_event_adapter
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
