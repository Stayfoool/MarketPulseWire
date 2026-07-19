#!/usr/bin/env python3
"""Compare new rule results with an existing collector shadow report.

The input is an explicit JSON report produced by a shadow collector with
``--direct-shadow``. This tool never collects, persists, delivers or changes
the active decision. It writes only a bounded comparison report.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from market_item import DecisionResult, NormalizedMarketItem
from rule_core_shadow import safe_compare_rule_core
from rule_core_v1 import SourceAdmissionPolicy, parse_portfolio_config, parse_rule_config


CONTRACT_VERSION = "rule-core-shadow-report-v1"
REPORT_GROUPS = ("rss", "pages", "alphabstract", "sources")


def _clean(value: object, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _candidate_rows(payload: dict[str, Any]) -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
    for group in REPORT_GROUPS:
        rows = payload.get(group)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            candidates = row.get("candidates")
            if not isinstance(candidates, list):
                continue
            for candidate in candidates:
                if isinstance(candidate, dict):
                    yield row, candidate


def _normalized_item(candidate: dict[str, Any]) -> NormalizedMarketItem | None:
    shadow = candidate.get("direct_shadow")
    if not isinstance(shadow, dict) or not shadow.get("ok"):
        return None
    raw = shadow.get("normalized_item")
    if not isinstance(raw, dict):
        return None
    full_text = str(raw.get("full_text") or "").strip()
    if not full_text:
        return None
    return NormalizedMarketItem(
        source=str(raw.get("source") or candidate.get("source") or ""),
        source_category=str(raw.get("source_category") or ""),
        publisher_role=str(raw.get("publisher_role") or ""),
        collector=str(raw.get("collector") or ""),
        content_type=str(raw.get("content_type") or "unknown"),
        title=str(raw.get("title") or candidate.get("title") or ""),
        summary=str(raw.get("summary") or candidate.get("summary") or ""),
        full_text=full_text,
        url=str(raw.get("url") or candidate.get("url") or ""),
        published_at=str(raw.get("published_at") or candidate.get("published_at") or ""),
        symbols=list(raw.get("symbols") or []),
        themes=list(raw.get("themes") or []),
        dedupe_key=str(raw.get("dedupe_key") or ""),
        access_note=str(raw.get("access_note") or ""),
    )


def _decision(candidate: dict[str, Any]) -> DecisionResult | None:
    shadow = candidate.get("direct_shadow")
    payload = shadow.get("decision") if isinstance(shadow, dict) else None
    if not isinstance(payload, dict) or not payload.get("action"):
        return None
    return DecisionResult(
        action=str(payload.get("action")),
        importance=str(payload.get("importance") or "unknown"),
        reason=str(payload.get("reason") or ""),
        brief_reason=str(payload.get("brief_reason") or ""),
        rule_hits=list(payload.get("rule_hits") or []),
        candidate_rules=list(payload.get("candidate_rules") or []),
        dedup=dict(payload.get("dedup") or {}),
        need_llm_interpretation=bool(payload.get("need_llm_interpretation")),
        need_limited_llm_judgement=bool(payload.get("need_limited_llm_judgement")),
        audit_json=dict(payload.get("audit") or {}),
    )


def _row_summary(row: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": _clean(candidate.get("source") or row.get("source"), 120),
        "item_id": _clean(candidate.get("id"), 240),
        "title": _clean(candidate.get("title"), 240),
        "url": _clean(candidate.get("url"), 500),
        "already_seen": bool(candidate.get("already_seen")),
        "already_reviewed": bool(candidate.get("already_reviewed")),
    }


def compare_shadow_report(
    payload: dict[str, Any],
    *,
    rule_config: Any,
    portfolio: Any,
    include_seen: bool = False,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    skipped = Counter()
    for source_row, candidate in _candidate_rows(payload):
        if not include_seen and (candidate.get("already_seen") or candidate.get("already_reviewed")):
            skipped["already_seen_or_reviewed"] += 1
            continue
        item = _normalized_item(candidate)
        if item is None:
            skipped["missing_full_text_or_shadow"] += 1
            continue
        current = _decision(candidate)
        if current is None:
            skipped["missing_current_decision"] += 1
            continue
        comparison = safe_compare_rule_core(
            item,
            current_decision=current,
            current_admission_status=str(candidate.get("current_admission_status") or "unknown"),
            current_admission_reason=str(candidate.get("current_admission_reason") or ""),
            current_matched_families=candidate.get("current_matched_families") or (),
            rule_config=rule_config,
            portfolio=portfolio,
            source_policy=SourceAdmissionPolicy(),
        )
        row = _row_summary(source_row, candidate)
        row["comparison"] = comparison
        rows.append(row)

    action_changes = Counter(
        f"{row['comparison']['current'].get('action') or 'none'}->{row['comparison']['candidate'].get('action') or 'none'}"
        for row in rows
        if row["comparison"].get("ok") and "action" in row["comparison"].get("changed_fields", [])
    )
    return {
        "ok": True,
        "contract_version": CONTRACT_VERSION,
        "comparison_only": True,
        "affects_current_decision": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_mode": payload.get("mode") or "unknown",
        "rule_config_version": getattr(rule_config, "config_version", ""),
        "counts": {
            "compared": len(rows),
            "comparison_errors": sum(1 for row in rows if not row["comparison"].get("ok")),
            "action_changes": sum(action_changes.values()),
            "skipped": dict(skipped),
            "action_changes_by_pair": dict(action_changes),
        },
        "items": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare v1 rules with a collector shadow report.")
    parser.add_argument("--input", required=True, type=Path, help="JSON report from a shadow collector.")
    parser.add_argument("--rule-config", required=True, type=Path, help="Explicit rule-config-v1 JSON path.")
    parser.add_argument("--portfolio", required=True, type=Path, help="Explicit portfolio rule-core JSON path.")
    parser.add_argument("--output", type=Path, help="Write the bounded comparison report to this path.")
    parser.add_argument("--include-seen", action="store_true", help="Also compare already-seen/reviewed candidates.")
    args = parser.parse_args()

    payload = _load_json(args.input)
    if not isinstance(payload, dict):
        raise SystemExit("shadow report must be a JSON object")
    rule_config = parse_rule_config(_load_json(args.rule_config))
    portfolio = parse_portfolio_config(_load_json(args.portfolio))
    result = compare_shadow_report(
        payload,
        rule_config=rule_config,
        portfolio=portfolio,
        include_seen=args.include_seen,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
