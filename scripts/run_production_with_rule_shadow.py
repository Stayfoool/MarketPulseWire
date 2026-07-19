#!/usr/bin/env python3
"""Run one production collector, then optionally run a report-only comparison.

The production command remains the authority. The optional follow-up is
enabled only by an explicit private environment setting and its failures never
change the production collector exit status.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports"

COLLECTOR_COMMANDS: dict[str, tuple[tuple[str, ...], tuple[str, ...], str]] = {
    "research": (
        ("scripts/research_collector.py", "--production", "--page-min-interval", "900"),
        ("scripts/research_collector.py", "--write-report", "--limit", "0", "--direct-shadow"),
        "research-collector-shadow-",
    ),
    "official": (
        ("scripts/official_collector.py", "--production"),
        ("scripts/official_collector.py", "--write-report", "--limit", "0", "--direct-shadow"),
        "official-collector-shadow-",
    ),
    "news": (
        ("scripts/news_collector.py", "--production"),
        ("scripts/news_collector.py", "--write-report", "--limit", "0", "--direct-shadow"),
        "news-collector-shadow-",
    ),
}


def collector_commands(name: str) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    try:
        return COLLECTOR_COMMANDS[name]
    except KeyError as exc:
        raise ValueError(f"unknown collector: {name}") from exc


def env_flag(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def shadow_autorun_enabled(env: Mapping[str, str]) -> bool:
    return env_flag(env.get("RULE_CORE_SHADOW_AUTORUN"))


def comparison_config_paths(env: Mapping[str, str]) -> tuple[Path, Path] | None:
    config = Path(str(env.get("RULE_CORE_SHADOW_CONFIG") or "").strip()).expanduser()
    portfolio = Path(str(env.get("RULE_CORE_SHADOW_PORTFOLIO") or "").strip()).expanduser()
    if not config.is_file() or not portfolio.is_file():
        return None
    return config, portfolio


def _newest_report(prefix: str, *, report_dir: Path, not_before: float) -> Path | None:
    candidates = [
        path
        for path in report_dir.glob(f"{prefix}*.json")
        if path.is_file() and path.stat().st_mtime >= not_before
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def load_seen_keys(db_path: Path) -> set[tuple[str, str]]:
    if not db_path.is_file():
        return set()
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'seen_items'"
            ).fetchone()
            if not table:
                return set()
            return {
                (str(row[0] or ""), str(row[1] or ""))
                for row in conn.execute("SELECT source, item_id FROM seen_items")
            }
    except sqlite3.Error:
        return set()


def _filter_report_to_keys(payload: dict[str, Any], keys: set[tuple[str, str]]) -> dict[str, Any]:
    filtered = dict(payload)
    for group in ("rss", "pages", "alphabstract", "sources"):
        rows = payload.get(group)
        if not isinstance(rows, list):
            continue
        filtered_rows: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            candidate_rows = row.get("candidates")
            if not isinstance(candidate_rows, list):
                continue
            candidates = [
                candidate
                for candidate in candidate_rows
                if isinstance(candidate, dict)
                and (str(candidate.get("source") or row.get("source") or ""), str(candidate.get("id") or "")) in keys
            ]
            if candidates:
                updated = dict(row)
                updated["candidates"] = candidates
                updated["candidate_count"] = len(candidates)
                filtered_rows.append(updated)
        filtered[group] = filtered_rows
    return filtered


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


def run_shadow_followup(
    collector: str,
    *,
    env: Mapping[str, str],
    runner: Callable[..., Any] = subprocess.run,
    report_dir: Path = REPORT_DIR,
    clock: Callable[[], float] = time.time,
    new_item_keys: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Run the optional shadow collector and comparison without authority."""
    config_paths = comparison_config_paths(env)
    if not shadow_autorun_enabled(env):
        return {"status": "disabled", "reason": "RULE_CORE_SHADOW_AUTORUN is not enabled"}
    if config_paths is None:
        return {"status": "skipped", "reason": "shadow rule/portfolio config is unavailable"}
    if not new_item_keys:
        return {"status": "skipped", "reason": "production batch added no seen_items"}

    _, shadow_command, report_prefix = collector_commands(collector)
    report_dir.mkdir(parents=True, exist_ok=True)
    started = clock()
    shadow_result = _run(shadow_command, env=env, runner=runner, capture_output=True)
    if int(getattr(shadow_result, "returncode", 1)) != 0:
        return {
            "status": "failed",
            "stage": "shadow_collector",
            "returncode": int(getattr(shadow_result, "returncode", 1)),
        }

    raw_report = _newest_report(report_prefix, report_dir=report_dir, not_before=started)
    if raw_report is None:
        return {"status": "failed", "stage": "shadow_collector", "reason": "report was not created"}
    try:
        raw_payload = json.loads(raw_report.read_text(encoding="utf-8"))
        if not isinstance(raw_payload, dict):
            raise ValueError("shadow report must be a JSON object")
        input_payload = _filter_report_to_keys(raw_payload, new_item_keys)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {"status": "failed", "stage": "shadow_collector", "reason": f"invalid report: {exc}"}

    input_report = report_dir / f".rule-core-shadow-input-{collector}-{os.getpid()}.json"
    input_report.write_text(json.dumps(input_payload, ensure_ascii=False), encoding="utf-8")
    output_report = report_dir / f"rule-core-shadow-{collector}-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}.json"
    config, portfolio = config_paths
    compare_command = (
        "scripts/rule_core_shadow_report.py",
        "--input",
        str(input_report),
        "--rule-config",
        str(config),
        "--portfolio",
        str(portfolio),
        "--include-seen",
        "--output",
        str(output_report),
    )
    try:
        compare_result = _run(compare_command, env=env, runner=runner, capture_output=True)
    finally:
        input_report.unlink(missing_ok=True)
    if int(getattr(compare_result, "returncode", 1)) != 0:
        return {
            "status": "failed",
            "stage": "comparison",
            "returncode": int(getattr(compare_result, "returncode", 1)),
            "input_report": str(input_report),
        }
    return {
        "status": "completed",
        "input_report": str(input_report),
        "comparison_report": str(output_report),
    }


def run_batch(
    collector: str,
    *,
    env: Mapping[str, str] | None = None,
    runner: Callable[..., Any] = subprocess.run,
    report_dir: Path = REPORT_DIR,
    db_path: Path = ROOT / "data" / "surveil.sqlite3",
) -> int:
    env = dict(env or os.environ)
    production_command, _, _ = collector_commands(collector)
    before_seen = load_seen_keys(db_path)
    production_result = _run(production_command, env=env, runner=runner)
    production_status = int(getattr(production_result, "returncode", 1))
    if production_status != 0:
        return production_status

    after_seen = load_seen_keys(db_path)
    followup = run_shadow_followup(
        collector,
        env=env,
        runner=runner,
        report_dir=report_dir,
        new_item_keys=after_seen - before_seen,
    )
    if followup.get("status") not in {"disabled", "skipped", "completed"}:
        print(f"rule core shadow follow-up failed: {followup}", file=sys.stderr, flush=True)
    elif followup.get("status") == "completed":
        print(f"rule core shadow comparison: {followup.get('comparison_report')}", flush=True)
    return production_status


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a production collector with an optional read-only rule comparison.")
    parser.add_argument("--collector", choices=sorted(COLLECTOR_COMMANDS), required=True)
    args = parser.parse_args()
    return run_batch(args.collector)


if __name__ == "__main__":
    raise SystemExit(main())
