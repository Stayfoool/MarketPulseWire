#!/usr/bin/env python3
"""Run one production collector through the shared service entrypoint."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]

COLLECTOR_COMMANDS: dict[str, tuple[str, ...]] = {
    "research": ("scripts/research_collector.py", "--page-min-interval", "900"),
    "official": ("scripts/official_collector.py",),
    "news": ("scripts/news_collector.py",),
}


def collector_command(name: str) -> tuple[str, ...]:
    try:
        return COLLECTOR_COMMANDS[name]
    except KeyError as exc:
        raise ValueError(f"unknown collector: {name}") from exc


def _run(
    command: Sequence[str],
    *,
    env: Mapping[str, str],
    runner: Callable[..., Any] = subprocess.run,
) -> Any:
    return runner(
        [sys.executable, *command],
        cwd=ROOT,
        env=dict(env),
        check=False,
    )


def run_batch(
    collector: str,
    *,
    env: Mapping[str, str] | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> int:
    env = dict(env or os.environ)
    production_command = collector_command(collector)
    production_result = _run(production_command, env=env, runner=runner)
    return int(getattr(production_result, "returncode", 1))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one production collector.")
    parser.add_argument("--collector", choices=sorted(COLLECTOR_COMMANDS), required=True)
    args = parser.parse_args()
    return run_batch(args.collector)


if __name__ == "__main__":
    raise SystemExit(main())
