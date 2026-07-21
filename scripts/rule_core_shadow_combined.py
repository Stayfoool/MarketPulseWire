#!/usr/bin/env python3
"""Build one readable report from rule-core shadow comparison reports."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from llm_rule_catalog import CATALOG_VERSION
from llm_rule_decision import ENGINE_VERSION as LLM_RULE_ENGINE_VERSION
from rule_core_v1 import RULE_CORE_VERSION


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports"
CONTRACT_VERSION = "rule-core-shadow-combined-v1"
# PR #162 is the last rule-changing deployment before explicit rule versions
# were added to comparison records. The completed workflow time is a
# conservative compatibility boundary for already-retained reports.
LEGACY_LATEST_RULE_CORE_SINCE = datetime(2026, 7, 21, 2, 32, 51, tzinfo=timezone.utc)


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


def iter_reports(
    report_dir: Path,
    *,
    since: datetime,
    until: datetime | None = None,
) -> list[dict[str, Any]]:
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
        if generated_at is None:
            continue
        if generated_at < since:
            continue
        if until is not None and generated_at >= until:
            continue
        reports.append(payload)
    return reports


def _pair_for(item: dict[str, Any]) -> str:
    comparison = item.get("comparison") if isinstance(item.get("comparison"), dict) else {}
    current = comparison.get("current") if isinstance(comparison.get("current"), dict) else {}
    candidate = comparison.get("candidate") if isinstance(comparison.get("candidate"), dict) else {}
    return f"{current.get('action') or 'none'}->{candidate.get('action') or 'none'}"


def _reason_for(payload: dict[str, Any]) -> str:
    brief = str(payload.get("brief_reason") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    return brief or reason


def _candidate_reason(payload: dict[str, Any]) -> str:
    reason = _reason_for(payload)
    admission = str(payload.get("admission_reason") or "").strip()
    if reason and admission:
        return f"{admission}; {reason}"
    failure = str(payload.get("failure_reason") or "").strip()
    return reason or failure or admission


def _current_reason(payload: dict[str, Any]) -> str:
    reason = _reason_for(payload)
    admission = str(payload.get("admission_reason") or "").strip()
    if reason and admission:
        return f"{admission}; {reason}"
    return reason or admission


def _rule_core_metadata(report: dict[str, Any]) -> tuple[str, str, bool]:
    recorded = str(report.get("rule_core_version") or "").strip()
    if recorded:
        return recorded, "recorded", recorded == RULE_CORE_VERSION
    generated_at = parse_iso(report.get("generated_at"))
    if generated_at is not None and generated_at >= LEGACY_LATEST_RULE_CORE_SINCE:
        return RULE_CORE_VERSION, "inferred_from_deployment_time", True
    return "", "unconfirmed", False


def _candidate_metadata(report: dict[str, Any]) -> tuple[str, str, str, bool]:
    engine = str(report.get("candidate_engine") or "").strip()
    version = str(report.get("candidate_version") or "").strip()
    if engine:
        latest = (
            engine == LLM_RULE_ENGINE_VERSION and version == CATALOG_VERSION
        ) or (
            engine == "rule_core_v1" and version == RULE_CORE_VERSION
        )
        return engine, version, "recorded", latest
    rule_core_version, source, latest = _rule_core_metadata(report)
    return "rule_core_v1", rule_core_version, source, latest


def _md_cell(value: object, limit: int = 180) -> str:
    text = " ".join(str(value or "").split()).replace("|", "\\|")
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def build_combined_report(
    *,
    report_dir: Path = REPORT_DIR,
    hours: int = 24,
    now: datetime | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    window_end = until or now
    window_start = since or (window_end - timedelta(hours=max(1, hours)))
    reports = iter_reports(report_dir, since=window_start, until=window_end)
    skipped = Counter()
    action_changes = Counter()
    source_groups = Counter()
    items: list[dict[str, Any]] = []
    latest_candidate_items = 0
    comparable_items = 0
    evaluation_statuses = Counter()
    candidate_engines = Counter()
    usage_totals = Counter()
    total_elapsed_seconds = 0.0

    for report in reports:
        source_group_name = str(report.get("_source_group") or "")
        candidate_engine, candidate_version, candidate_version_source, is_latest_candidate_version = (
            _candidate_metadata(report)
        )
        rule_core_version = candidate_version if candidate_engine == "rule_core_v1" else ""
        rule_core_version_source = candidate_version_source if candidate_engine == "rule_core_v1" else ""
        source_groups[source_group_name] += 1
        counts = report.get("counts") if isinstance(report.get("counts"), dict) else {}
        skipped.update(counts.get("skipped") if isinstance(counts.get("skipped"), dict) else {})
        for item in report.get("items") if isinstance(report.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            comparison = item.get("comparison") if isinstance(item.get("comparison"), dict) else {}
            current = comparison.get("current") if isinstance(comparison.get("current"), dict) else {}
            candidate = comparison.get("candidate") if isinstance(comparison.get("candidate"), dict) else {}
            comparable = bool(comparison.get("comparable", True))
            evaluation_status = str(candidate.get("evaluation_status") or ("completed" if comparable else "unknown"))
            usage = candidate.get("usage") if isinstance(candidate.get("usage"), dict) else {}
            if comparable:
                comparable_items += 1
            evaluation_statuses[evaluation_status] += 1
            candidate_engines[candidate_engine] += 1
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = usage.get(key)
                if isinstance(value, (int, float)):
                    usage_totals[key] += int(value)
            elapsed = candidate.get("elapsed_seconds")
            if isinstance(elapsed, (int, float)):
                total_elapsed_seconds += float(elapsed)
            row = {
                "source_group": source_group_name,
                "source": item.get("source") or "",
                "item_id": item.get("item_id") or "",
                "title": item.get("title") or "",
                "url": item.get("url") or "",
                "current_action": current.get("action"),
                "current_importance": current.get("importance"),
                "current_reason": _current_reason(current),
                "current_rule_ids": current.get("rule_ids") if isinstance(current.get("rule_ids"), list) else [],
                "candidate_action": candidate.get("action"),
                "candidate_importance": candidate.get("importance"),
                "candidate_reason": _candidate_reason(candidate),
                "changed_fields": comparison.get("changed_fields") if isinstance(comparison.get("changed_fields"), list) else [],
                "candidate_admission": candidate.get("admission_status") or "",
                "candidate_rule_ids": candidate.get("rule_ids") if isinstance(candidate.get("rule_ids"), list) else [],
                "candidate_rule_evidence": candidate.get("rule_evidence") if isinstance(candidate.get("rule_evidence"), list) else [],
                "candidate_rule_assessments": candidate.get("rule_assessments") if isinstance(candidate.get("rule_assessments"), list) else [],
                "current_rule_evidence": current.get("rule_evidence") if isinstance(current.get("rule_evidence"), list) else [],
                "comparable": comparable,
                "evaluation_status": evaluation_status,
                "failure_reason": candidate.get("failure_reason") or "",
                "candidate_engine": candidate_engine,
                "candidate_version": candidate_version,
                "candidate_version_source": candidate_version_source,
                "is_latest_candidate_version": is_latest_candidate_version,
                "model": candidate.get("model") or "",
                "provider": candidate.get("provider") or "",
                "usage": usage,
                "attempts": _safe_int(candidate.get("attempts")),
                "elapsed_seconds": _safe_float(candidate.get("elapsed_seconds")),
                "input_text_scope": candidate.get("input_text_scope") or "",
                "article_chars": _safe_int(candidate.get("article_chars")),
                "prompt_chars": _safe_int(candidate.get("prompt_chars")),
                "comparison_generated_at": report.get("generated_at") or "",
                "rule_core_version": rule_core_version,
                "rule_core_version_source": rule_core_version_source,
                "rule_config_version": report.get("rule_config_version") or "",
                "application_revision": report.get("application_revision") or "",
                "is_latest_rule_core_version": is_latest_candidate_version if candidate_engine == "rule_core_v1" else False,
                "report_path": report.get("_path") or "",
            }
            items.append(row)
            if is_latest_candidate_version:
                latest_candidate_items += 1
            if comparable and row["current_action"] != row["candidate_action"]:
                action_changes.update([_pair_for(item)])

    engine_names = set(candidate_engines)
    candidate_label = (
        "大模型候选"
        if engine_names == {LLM_RULE_ENGINE_VERSION}
        else "新规则候选"
        if engine_names == {"rule_core_v1"}
        else "对比判断"
    )

    return {
        "ok": True,
        "contract_version": CONTRACT_VERSION,
        "comparison_only": True,
        "affects_current_decision": False,
        "generated_at": now.isoformat(),
        "window_hours": round((window_end - window_start).total_seconds() / 3600, 3),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "latest_rule_core_version": RULE_CORE_VERSION,
        "latest_candidate_versions": {
            "rule_core_v1": RULE_CORE_VERSION,
            LLM_RULE_ENGINE_VERSION: CATALOG_VERSION,
        },
        "candidate_label": candidate_label,
        "legacy_latest_rule_core_since": LEGACY_LATEST_RULE_CORE_SINCE.isoformat(),
        "report_dir": str(report_dir),
        "counts": {
            "reports": len(reports),
            "reports_by_source_group": dict(source_groups),
            "items": len(items),
            "compared": comparable_items,
            "unable_to_compare": len(items) - comparable_items,
            "action_changes": sum(action_changes.values()),
            "action_changes_by_pair": dict(action_changes),
            "skipped": dict(skipped),
            "latest_candidate_items": latest_candidate_items,
            "earlier_or_unconfirmed_candidate_items": len(items) - latest_candidate_items,
            "latest_rule_items": latest_candidate_items,
            "earlier_or_unconfirmed_rule_items": len(items) - latest_candidate_items,
            "evaluation_statuses": dict(evaluation_statuses),
            "candidate_engines": dict(candidate_engines),
            "usage": dict(usage_totals),
            "total_elapsed_seconds": round(total_elapsed_seconds, 3),
        },
        "items": items,
    }


def markdown_report(payload: dict[str, Any]) -> str:
    counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
    lines = [
        f"# {payload.get('report_title') or 'Rule Core Shadow Combined Report'}",
        "",
        f"- Generated at: {payload.get('generated_at')}",
        f"- Window: {payload.get('window_start')} to {payload.get('window_end')}",
        f"- Reports scanned: {counts.get('reports', 0)}",
        f"- Items recorded: {counts.get('items', counts.get('compared', 0))}",
        f"- Compared items: {counts.get('compared', 0)}",
        f"- Unable to compare: {counts.get('unable_to_compare', 0)}",
        f"- Action changes: {counts.get('action_changes', 0)}",
        f"- Candidate: {payload.get('candidate_label') or '对比判断'}",
        f"- Latest-version items: {counts.get('latest_candidate_items', counts.get('latest_rule_items', 0))}",
        f"- Token usage: {json.dumps(counts.get('usage', {}), ensure_ascii=False, sort_keys=True)}",
        f"- Skipped: {json.dumps(counts.get('skipped', {}), ensure_ascii=False, sort_keys=True)}",
        "",
    ]
    if payload.get("review_date"):
        lines.insert(2, f"- Review date: {payload.get('review_date')} (Asia/Shanghai)")
    rebuild = payload.get("rebuild") if isinstance(payload.get("rebuild"), dict) else {}
    if rebuild:
        lines.insert(
            3,
            "- Rebuilt from stored comparison records; candidate rules were not re-evaluated.",
        )
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

    lines.append("| Source Group | Source | Current | Current Reason | Candidate | Status | Candidate Reason | Candidate Rules | Title |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for item in items:
        rules = ",".join(str(rule) for rule in (item.get("candidate_rule_ids") or [])) or "-"
        lines.append(
            f"| {item.get('source_group') or ''} | {item.get('source') or ''} | "
            f"`{item.get('current_action') or 'none'}` | {_md_cell(item.get('current_reason'))} | "
            f"`{item.get('candidate_action') or 'none'}` | `{item.get('evaluation_status') or '-'}` | "
            f"{_md_cell(item.get('candidate_reason'))} | "
            f"{rules} | {_md_cell(item.get('title'), 160)} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def write_combined(payload: dict[str, Any], report_dir: Path = REPORT_DIR) -> dict[str, str]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "rule-core-shadow-combined-latest.json"
    md_path = report_dir / "rule-core-shadow-combined-latest.md"
    for path, content in (
        (json_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"),
        (md_path, markdown_report(payload)),
    ):
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
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
