#!/usr/bin/env python3
"""Summarize shadow collector reports without sending notifications."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports"
COLLECTOR_PREFIXES = {
    "research": "research-collector-shadow-",
    "official": "official-collector-shadow-",
    "news": "news-collector-shadow-",
}


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def family_from_path(path: Path) -> str:
    name = path.name
    for family, prefix in COLLECTOR_PREFIXES.items():
        if name.startswith(prefix):
            return family
    return ""


def load_report(path: Path) -> dict[str, Any] | None:
    family = family_from_path(path)
    if not family:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    payload["_family"] = family
    payload["_path"] = str(path)
    return payload


def iter_reports(report_dir: Path, *, since: datetime) -> list[dict[str, Any]]:
    if not report_dir.exists():
        return []
    reports: list[dict[str, Any]] = []
    for path in sorted(report_dir.glob("*-collector-shadow-*.json")):
        payload = load_report(path)
        if not payload:
            continue
        finished_at = parse_iso(str(payload.get("finished_at") or "")) or parse_iso(str(payload.get("started_at") or ""))
        if finished_at and finished_at < since:
            continue
        payload["_finished_at"] = finished_at.isoformat() if finished_at else ""
        reports.append(payload)
    return reports


def report_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("sources"), list):
        return [row for row in payload["sources"] if isinstance(row, dict)]
    rows: list[dict[str, Any]] = []
    for key in ("rss", "pages"):
        if isinstance(payload.get(key), list):
            rows.extend(row for row in payload[key] if isinstance(row, dict))
    return rows


def sample_candidates(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for row in rows:
        source = str(row.get("source") or "")
        candidates = row.get("candidates") if isinstance(row.get("candidates"), list) else []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            if item.get("already_seen") or item.get("already_reviewed"):
                continue
            direct = item.get("direct_shadow") if isinstance(item.get("direct_shadow"), dict) else {}
            decision = direct.get("decision") if isinstance(direct.get("decision"), dict) else {}
            samples.append(
                {
                    "source": source,
                    "title": str(item.get("title") or "")[:220],
                    "url": str(item.get("url") or ""),
                    "published_at": str(item.get("published_at") or ""),
                    "would_focus": bool(item.get("would_focus")),
                    "mandatory_push": str(item.get("mandatory_push") or ""),
                    "direct_action": str(decision.get("action") or ""),
                    "direct_importance": str(decision.get("importance") or ""),
                    "direct_rule_ids": list(decision.get("rule_hit_ids") or []),
                }
            )
            if len(samples) >= limit:
                return samples
    return samples


def build_digest(
    *,
    report_dir: Path = REPORT_DIR,
    hours: int = 24,
    sample_limit: int = 30,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    reports = iter_reports(report_dir, since=since)
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for payload in reports:
        by_family[str(payload.get("_family") or "")].append(payload)

    families: dict[str, Any] = {}
    for family in sorted(COLLECTOR_PREFIXES):
        family_reports = by_family.get(family, [])
        rows = [row for payload in family_reports for row in report_rows(payload)]
        source_stats: dict[str, dict[str, Any]] = {}
        for row in rows:
            source = str(row.get("source") or "")
            if not source:
                continue
            stats = source_stats.setdefault(
                source,
                {
                    "source": source,
                    "runs": 0,
                    "failed_runs": 0,
                    "raw_items": 0,
                    "candidates": 0,
                    "focus_candidates": 0,
                    "sample_new_candidates": 0,
                    "last_error": "",
                },
            )
            stats["runs"] += 1
            if not row.get("ok"):
                stats["failed_runs"] += 1
                stats["last_error"] = str(row.get("error") or "")
            stats["raw_items"] += int(row.get("raw_count") or 0)
            stats["candidates"] += int(row.get("candidate_count") or 0)
            stats["focus_candidates"] += int(row.get("focus_count") or 0)
            candidates = row.get("candidates") if isinstance(row.get("candidates"), list) else []
            stats["sample_new_candidates"] += sum(
                1
                for item in candidates
                if isinstance(item, dict)
                and not item.get("already_seen")
                and not item.get("already_reviewed")
            )

        samples = sample_candidates(rows, sample_limit)
        families[family] = {
            "reports": len(family_reports),
            "ok_reports": sum(1 for payload in family_reports if payload.get("ok")),
            "failed_reports": sum(1 for payload in family_reports if not payload.get("ok")),
            "sources": sorted(source_stats.values(), key=lambda item: item["source"]),
            "sample_new_candidates": samples,
        }

    return {
        "ok": True,
        "mode": "collector_shadow_digest",
        "generated_at": now.isoformat(),
        "window_hours": hours,
        "report_dir": str(report_dir),
        "report_count": len(reports),
        "families": families,
    }


def markdown_digest(payload: dict[str, Any]) -> str:
    lines = [
        "# MarketPulseWire Shadow Collector Digest",
        "",
        f"- Generated at: {payload.get('generated_at')}",
        f"- Window: last {payload.get('window_hours')} hours",
        f"- Reports scanned: {payload.get('report_count')}",
        "",
    ]
    labels = {
        "research": "Research / Industry Media",
        "official": "Official Company",
        "news": "News Media",
    }
    for family, label in labels.items():
        data = payload.get("families", {}).get(family, {})
        lines.extend(
            [
                f"## {label}",
                "",
                f"- Reports: {data.get('reports', 0)}",
                f"- Failed reports: {data.get('failed_reports', 0)}",
                "",
            ]
        )
        sources = data.get("sources") if isinstance(data.get("sources"), list) else []
        if sources:
            lines.append("| Source | Runs | Failed | Raw | Candidates | Focus | Sample New | Last Error |")
            lines.append("|---|---:|---:|---:|---:|---:|---:|---|")
            for item in sources:
                error = str(item.get("last_error") or "").replace("\n", " ")[:120]
                lines.append(
                    f"| {item.get('source')} | {item.get('runs', 0)} | {item.get('failed_runs', 0)} | "
                    f"{item.get('raw_items', 0)} | {item.get('candidates', 0)} | "
                    f"{item.get('focus_candidates', 0)} | {item.get('sample_new_candidates', 0)} | {error} |"
                )
            lines.append("")
        samples = data.get("sample_new_candidates") if isinstance(data.get("sample_new_candidates"), list) else []
        if samples:
            lines.append("Sample new/unreviewed candidates:")
            for sample in samples[:10]:
                title = str(sample.get("title") or "").replace("\n", " ")
                source = sample.get("source")
                url = sample.get("url")
                focus = " focus" if sample.get("would_focus") else ""
                direct_action = str(sample.get("direct_action") or "")
                direct_rules = sample.get("direct_rule_ids") if isinstance(sample.get("direct_rule_ids"), list) else []
                direct = ""
                if direct_action:
                    direct = f" direct={direct_action}"
                    if direct_rules:
                        direct += f" rules={','.join(str(rule) for rule in direct_rules[:3])}"
                if url:
                    lines.append(f"- [{source}]{focus}{direct} [{title}]({url})")
                else:
                    lines.append(f"- [{source}]{focus}{direct} {title}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_digest(payload: dict[str, Any], report_dir: Path = REPORT_DIR) -> dict[str, str]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    json_path = report_dir / f"collector-shadow-digest-{stamp}.json"
    md_path = report_dir / f"collector-shadow-digest-{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(markdown_digest(payload), encoding="utf-8")
    return {"json_path": str(json_path), "markdown_path": str(md_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize shadow collector reports.")
    parser.add_argument("--hours", type=int, default=24, help="Lookback window in hours.")
    parser.add_argument("--sample-limit", type=int, default=30, help="Maximum sample candidates per family.")
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR, help="Directory containing shadow JSON reports.")
    parser.add_argument("--write-report", action="store_true", help="Write JSON and Markdown digest files.")
    parser.add_argument("--json", action="store_true", help="Print JSON payload.")
    args = parser.parse_args()

    payload = build_digest(
        report_dir=args.report_dir,
        hours=max(1, args.hours),
        sample_limit=max(0, args.sample_limit),
    )
    if args.write_report:
        payload["output"] = write_digest(payload, args.report_dir)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(markdown_digest(payload), end="")
        if payload.get("output"):
            print(f"output: {payload['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
