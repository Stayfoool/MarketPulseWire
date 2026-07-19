#!/usr/bin/env python3
"""Regression checks for the production-then-report-only wrapper."""

from __future__ import annotations

import time
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import run_production_with_rule_shadow as batch


def test_default_keeps_the_existing_production_command_only() -> None:
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(list(command))
        return SimpleNamespace(returncode=0)

    assert batch.run_batch("news", env={}, runner=runner) == 0
    assert len(calls) == 1
    assert calls[0][1:] == list(batch.collector_commands("news")[0])


def test_enabled_followup_runs_shadow_then_comparison_without_changing_status() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        config = root / "rule.json"
        portfolio = root / "portfolio.json"
        config.write_text("{}", encoding="utf-8")
        portfolio.write_text("[]", encoding="utf-8")
        calls: list[list[str]] = []
        report_dir = root / "reports"
        db_path = root / "surveil.sqlite3"
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE seen_items (source TEXT, item_id TEXT)")
            conn.commit()

        def runner(command, **kwargs):
            calls.append(list(command))
            if "--production" in command:
                with sqlite3.connect(db_path) as conn:
                    conn.execute("INSERT INTO seen_items(source, item_id) VALUES ('news', 'new-1')")
                    conn.commit()
            if "--direct-shadow" in command:
                report_dir.mkdir(parents=True, exist_ok=True)
                (report_dir / "news-collector-shadow-test.json").write_text(
                    '{"sources": [{"source": "news", "candidates": [{"source": "news", "id": "new-1"}]}]}',
                    encoding="utf-8",
                )
                now = time.time()
                (report_dir / "news-collector-shadow-test.json").touch()
                assert now <= (report_dir / "news-collector-shadow-test.json").stat().st_mtime + 1
            return SimpleNamespace(returncode=0)

        env = {
            "RULE_CORE_SHADOW_AUTORUN": "1",
            "RULE_CORE_SHADOW_CONFIG": str(config),
            "RULE_CORE_SHADOW_PORTFOLIO": str(portfolio),
        }
        assert batch.run_batch("news", env=env, runner=runner, report_dir=report_dir, db_path=db_path) == 0
        assert len(calls) == 3
        assert "--production" in calls[0]
        assert "--direct-shadow" in calls[1]
        assert "--include-seen" in calls[2]


def test_shadow_failure_does_not_change_production_status() -> None:
    calls = 0

    def runner(command, **kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(returncode=0 if calls == 1 else 2)

    env = {
        "RULE_CORE_SHADOW_AUTORUN": "1",
        "RULE_CORE_SHADOW_CONFIG": "/missing/rule.json",
        "RULE_CORE_SHADOW_PORTFOLIO": "/missing/portfolio.json",
    }
    assert batch.run_batch("research", env=env, runner=runner) == 0
    assert calls == 1


def test_production_units_use_the_wrapper_entrypoint() -> None:
    for collector in ("research", "official", "news"):
        path = Path(__file__).resolve().parents[1] / "systemd" / f"surveil-{collector}-collector.service"
        text = path.read_text(encoding="utf-8")
        assert "run_production_with_rule_shadow.py" in text
        assert f"--collector {collector}" in text


def main() -> int:
    test_default_keeps_the_existing_production_command_only()
    test_enabled_followup_runs_shadow_then_comparison_without_changing_status()
    test_shadow_failure_does_not_change_production_status()
    test_production_units_use_the_wrapper_entrypoint()
    print("rule core batch checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
