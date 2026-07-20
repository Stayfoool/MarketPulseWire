#!/usr/bin/env python3
"""Regression checks for the production-and-report-refresh wrapper."""

from __future__ import annotations

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
    assert calls[0][1:] == list(batch.collector_command("news"))


def test_enabled_followup_only_refreshes_reports_without_second_collection() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        calls: list[list[str]] = []
        report_dir = root / "reports"

        def runner(command, **kwargs):
            calls.append(list(command))
            return SimpleNamespace(returncode=0)

        assert batch.run_batch(
            "news",
            env={"RULE_CORE_SHADOW_AUTORUN": "1"},
            runner=runner,
            report_dir=report_dir,
        ) == 0
        assert len(calls) == 2
        assert "--production" in calls[0]
        assert "scripts/rule_core_shadow_combined.py" in calls[1]
        assert all("--direct-shadow" not in call for call in calls)
        assert all("scripts/rule_core_shadow_report.py" not in call for call in calls)


def test_enabled_followup_refreshes_combined_report() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        report_dir = root / "reports"
        calls: list[list[str]] = []

        def runner(command, **kwargs):
            calls.append(list(command))
            if "scripts/rule_core_shadow_combined.py" in command:
                report_dir.mkdir(parents=True, exist_ok=True)
                (report_dir / "rule-core-shadow-combined-latest.md").write_text("# combined\n", encoding="utf-8")
            return SimpleNamespace(returncode=0)

        env = {"RULE_CORE_SHADOW_AUTORUN": "1"}
        assert batch.run_batch("news", env=env, runner=runner, report_dir=report_dir) == 0
        assert any("scripts/rule_core_shadow_combined.py" in call for call in calls)


def test_report_refresh_failure_does_not_change_production_status() -> None:
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
    assert calls == 2


def test_production_units_use_the_wrapper_entrypoint() -> None:
    for collector in ("research", "official", "news"):
        path = Path(__file__).resolve().parents[1] / "systemd" / f"surveil-{collector}-collector.service"
        text = path.read_text(encoding="utf-8")
        assert "run_production_with_rule_shadow.py" in text
        assert f"--collector {collector}" in text


def main() -> int:
    test_default_keeps_the_existing_production_command_only()
    test_enabled_followup_only_refreshes_reports_without_second_collection()
    test_enabled_followup_refreshes_combined_report()
    test_report_refresh_failure_does_not_change_production_status()
    test_production_units_use_the_wrapper_entrypoint()
    print("rule core batch checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
