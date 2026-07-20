#!/usr/bin/env python3
"""Run one production collector, then refresh its report-only comparison view.

The production runtime records comparisons from its exact NormalizedMarketItem.
This wrapper never collects a second copy of an item. Report refresh failures
never change the production collector exit status.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports"

COLLECTOR_COMMANDS: dict[str, tuple[str, ...]] = {
    "research": ("scripts/research_collector.py", "--production", "--page-min-interval", "900"),
    "official": ("scripts/official_collector.py", "--production"),
    "news": ("scripts/news_collector.py", "--production"),
}


def collector_command(name: str) -> tuple[str, ...]:
    try:
        return COLLECTOR_COMMANDS[name]
    except KeyError as exc:
        raise ValueError(f"unknown collector: {name}") from exc


def env_flag(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def shadow_autorun_enabled(env: Mapping[str, str]) -> bool:
    return env_flag(env.get("RULE_CORE_SHADOW_AUTORUN"))


def _run(
    command: Sequence[str],
    *,
    env: Mapping[str, str],
    runner: Callable[..., Any] = subprocess.run,
    capture_output: bool = False,
) -> Any:
    return runner(
        [sys.executable, *command],
        cwd=ROOT,
        env=dict(env),
        check=False,
        capture_output=capture_output,
        text=capture_output,
    )


def refresh_combined_report(
    *,
    env: Mapping[str, str],
    runner: Callable[..., Any] = subprocess.run,
    report_dir: Path = REPORT_DIR,
) -> dict[str, Any]:
    """Refresh the bounded combined view without evaluating or collecting items."""
    if not shadow_autorun_enabled(env):
        return {"status": "disabled", "reason": "RULE_CORE_SHADOW_AUTORUN is not enabled"}
    combined_command = (
        "scripts/rule_core_shadow_combined.py",
        "--report-dir",
        str(report_dir),
        "--hours",
        str(env.get("RULE_CORE_SHADOW_COMBINED_HOURS") or "24"),
        "--write-report",
    )
    combined_result = _run(combined_command, env=env, runner=runner, capture_output=True)
    if int(getattr(combined_result, "returncode", 1)) != 0:
        return {
            "status": "failed",
            "stage": "combined_report",
            "returncode": int(getattr(combined_result, "returncode", 1)),
        }
    return {
        "status": "completed",
        "combined_report": str(report_dir / "rule-core-shadow-combined-latest.md"),
    }


def run_batch(
    collector: str,
    *,
    env: Mapping[str, str] | None = None,
    runner: Callable[..., Any] = subprocess.run,
    report_dir: Path = REPORT_DIR,
) -> int:
    env = dict(env or os.environ)
    production_command = collector_command(collector)
    production_result = _run(production_command, env=env, runner=runner)
    production_status = int(getattr(production_result, "returncode", 1))
    if production_status != 0:
        return production_status

    followup = refresh_combined_report(
        env=env,
        runner=runner,
        report_dir=report_dir,
    )
    if followup.get("status") not in {"disabled", "skipped", "completed"}:
        print(f"rule core shadow follow-up failed: {followup}", file=sys.stderr, flush=True)
    elif followup.get("status") == "completed":
        print(f"rule core shadow combined: {followup.get('combined_report')}", flush=True)
    return production_status


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a production collector with an optional read-only rule comparison.")
    parser.add_argument("--collector", choices=sorted(COLLECTOR_COMMANDS), required=True)
    args = parser.parse_args()
    return run_batch(args.collector)


if __name__ == "__main__":
    raise SystemExit(main())
