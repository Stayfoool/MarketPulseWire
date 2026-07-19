#!/usr/bin/env python3
"""Build one readable report from rule-core shadow comparison reports."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports"
CONTRACT_VERSION = "rule-core-shadow-combined-v1"


def parse_iso(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def source_group(path: Path) -> str:
    name = path.name
    if name.startswith("rule-core-shadow-research-"):
        return "research"
    if name.startswith("rule-core-shadow-official-"):
        return "official"
    if name.startswith("rule-core-shadow-news-"):
        return "news"
    return ""


def load_report(path: Path) -> dict[str, Any] | None:
    group = source_group(path)
    if not group:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    payload["_path"] = str(path)
    payload["_source_group"] = group
    return payload


def iter_reports(report_dir: Path, *, since: datetime) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    if not report_dir.exists():
        return reports
    for path in sorted(report_dir.glob("rule-core-shadow-*.json")):
        if source_group(path) == "":
            continue
        payload = load_report(path)
        if payload is None:
            continue
        generated_at = parse_iso(payload.get("generated_at"))
        if generated_at and generated_at < since:
            continue
        reports.append(payload)
    return reports


def _pair_for(item: dict[str, Any]) -> str:
    comparison = item.get("comparison") if isinstance(item.get("comparison"), dict) else {}
    current = comparison.get("current") if isinstance(comparison.get("current"), dict) else {}
    candidate = comparison.get("candidate") if isinstance(comparison.get("candidate"), dict) else {}
    return f"{current.get('action') or 'none'}->{candidate.get('action') or 'none'}"


def build_combined_report(
    *,
    report_dir: Path = REPORT_DIR,
    hours: int = 24,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=max(1, hours))
    reports = iter_reports(report_dir, since=since)
    skipped = Counter()
    action_changes = Counter()
    source_groups = Counter()
    items: list[dict[str, Any]] = []

    for report in reports:
        source_group_name = str(report.get("_source_group") or "")
        source_groups[source_group_name] += 1
        counts = report.get("counts") if isinstance(report.get("counts"), dict) else {}
        skipped.update(counts.get("skipped") if isinstance(counts.get("skipped"), dict) else {})
        for item in report.get("items") if isinstance(report.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            comparison = item.get("comparison") if isinstance(item.get("comparison"), dict) else {}
            row = {
                "source_group": source_group_name,
                "source": item.get("source") or "",
                "item_id": item.get("item_id") or "",
                "title": item.get("title") or "",
                "url": item.get("url") or "",
                "current_action": (comparison.get("current") or {}).get("action") if isinstance(comparison.get("current"), dict) else None,
                "candidate_action": (comparison.get("candidate") or {}).get("action") if isinstance(comparison.get("candidate"), dict) else None,
                "changed_fields": comparison.get("changed_fields") if isinstance(comparison.get("changed_fields"), list) else [],
                "candidate_admission": (comparison.get("candidate") or {}).get("admission_status") if isinstance(comparison.get("candidate"), dict) else "",
                "candidate_rule_ids": (comparison.get("candidate") or {}).get("rule_ids") if isinstance(comparison.get("candidate"), dict) else [],
                "report_path": report.get("_path") or "",
            }
            items.append(row)
            if row["current_action"] != row["candidate_action"]:
                action_changes.update([_pair_for(item)])

    return {
        "ok": True,
        "contract_version": CONTRACT_VERSION,
        "comparison_only": True,
        "affects_current_decision": False,
        "generated_at": now.isoformat(),
        "window_hours": max(1, hours),
        "report_dir": str(report_dir),
        "counts": {
            "reports": len(reports),
            "reports_by_source_group": dict(source_groups),
            "compared": len(items),
            "action_changes": sum(action_changes.values()),
            "action_changes_by_pair": dict(action_changes),
            "skipped": dict(skipped),
        },
        "items": items,
    }


def markdown_report(payload: dict[str, Any]) -> str:
    counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
    lines = [
        "# Rule Core Shadow Combined Report",
        "",
        f"- Generated at: {payload.get('generated_at')}",
        f"- Window: last {payload.get('window_hours')} hours",
        f"- Reports scanned: {counts.get('reports', 0)}",
        f"- Compared items: {counts.get('compared', 0)}",
        f"- Action changes: {counts.get('action_changes', 0)}",
        f"- Skipped: {json.dumps(counts.get('skipped', {}), ensure_ascii=False, sort_keys=True)}",
        "",
    ]
    pairs = counts.get("action_changes_by_pair") if isinstance(counts.get("action_changes_by_pair"), dict) else {}
    if pairs:
        lines.append("| Action Change | Count |")
        lines.append("|---|---:|")
        for pair, count in sorted(pairs.items()):
            lines.append(f"| `{pair}` | {count} |")
        lines.append("")

    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    if not items:
        lines.append("No comparable items in this window.")
        return "\n".join(lines).rstrip() + "\n"

    lines.append("| Source Group | Source | Current | Candidate | Candidate Rules | Title |")
    lines.append("|---|---|---|---|---|---|")
    for item in items:
        title = str(item.get("title") or "").replace("|", "\\|")[:160]
        rules = ",".join(str(rule) for rule in (item.get("candidate_rule_ids") or [])) or "-"
        lines.append(
            f"| {item.get('source_group') or ''} | {item.get('source') or ''} | "
            f"`{item.get('current_action') or 'none'}` | `{item.get('candidate_action') or 'none'}` | "
            f"{rules} | {title} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def write_combined(payload: dict[str, Any], report_dir: Path = REPORT_DIR) -> dict[str, str]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "rule-core-shadow-combined-latest.json"
    md_path = report_dir / "rule-core-shadow-combined-latest.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(markdown_report(payload), encoding="utf-8")
    return {"json_path": str(json_path), "markdown_path": str(md_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build one combined rule-core shadow comparison report.")
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payload = build_combined_report(report_dir=args.report_dir, hours=args.hours)
    if args.write_report:
        payload["output"] = write_combined(payload, args.report_dir)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(markdown_report(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
