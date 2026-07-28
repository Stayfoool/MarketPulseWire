#!/usr/bin/env python3
"""Regression checks for the shared production collector wrapper."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import run_production_collector as batch


def test_default_keeps_the_existing_production_command_only() -> None:
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(list(command))
        return SimpleNamespace(returncode=0)

    assert batch.run_batch("news", env={}, runner=runner) == 0
    assert len(calls) == 1
    assert calls[0][1:] == list(batch.collector_command("news"))


def test_production_units_use_the_wrapper_entrypoint() -> None:
    for collector in ("research", "official", "news"):
        path = Path(__file__).resolve().parents[1] / "systemd" / f"surveil-{collector}-collector.service"
        text = path.read_text(encoding="utf-8")
        assert "run_production_collector.py" in text
        assert f"--collector {collector}" in text


def test_retired_shadow_settings_do_not_return() -> None:
    root = Path(__file__).resolve().parents[1]
    retired = (
        "RULE_CORE_SHADOW_AUTORUN",
        "RULE_CORE_SHADOW_CONFIG",
        "RULE_COMPARISON_CANDIDATE",
    )
    for relative_path in ("README.md", ".env.example", "scripts/run_production_collector.py"):
        text = (root / relative_path).read_text(encoding="utf-8")
        for name in retired:
            assert name not in text, f"{relative_path}: retired setting returned: {name}"


def main() -> int:
    test_default_keeps_the_existing_production_command_only()
    test_production_units_use_the_wrapper_entrypoint()
    test_retired_shadow_settings_do_not_return()
    print("production collector batch checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
