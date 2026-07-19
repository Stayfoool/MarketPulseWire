"""Read-only historical input and comparison for the inactive v1 rule core.

This operator tool reads an explicit local SQLite snapshot only. It never
opens the production database, calls a collector/network/LLM, or writes the
database. Strict mode requires an explicitly stored full body. A separate
summary-proxy mode is available only for screening and must not be treated as
a production-rule comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from market_item import NormalizedMarketItem, decision_result_from_payload
from rule_core_replay import CurrentRuleOutcome, ReplayCase, build_replay_report
from rule_core_v1 import PortfolioRuleConfig, RuleConfig, SourceAdmissionPolicy


DEFAULT_ARTICLE_LIMIT = 12_000
DEFAULT_EVENT_LIMIT = 3_000
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.:-]+")
_RULE_FAMILIES = {
    "holding_keyword_immediate_alert": "holding",
    "holding_keyword": "holding",
    "industry_quantified_hardline": "semiconductor_ai",
    "industry_topic_hard_variable": "semiconductor_ai",
    "ai_compute_supply_demand": "semiconductor_ai",
    "ai_credit_risk": "semiconductor_ai",
    "macro_policy_line": "macro_data",
    "macro_policy": "macro_data",
    "fed_policy": "fed_policy",
    "international_bank_fed_path_revision": "fed_policy",
    "trade_friction_escalation": "trade_policy",
}


@dataclass(frozen=True)
class HistoryInputStats:
    article_rows: int
    event_rows: int
    article_with_decision: int
    article_legacy_unclassified: int
    event_with_decision: int
    event_legacy_unclassified: int
    article_full_text_unavailable: int
    event_full_text_unavailable: int
    event_baseline_skipped: int

    def to_dict(self) -> dict[str, int]:
        return {
            "article_rows": self.article_rows,
            "event_rows": self.event_rows,
            "article_with_decision": self.article_with_decision,
            "article_legacy_unclassified": self.article_legacy_unclassified,
            "event_with_decision": self.event_with_decision,
            "event_legacy_unclassified": self.event_legacy_unclassified,
            "article_full_text_unavailable": self.article_full_text_unavailable,
            "event_full_text_unavailable": self.event_full_text_unavailable,
            "event_baseline_skipped": self.event_baseline_skipped,
        }


def _clean(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _full_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _json_value(value: object) -> Any:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed


def _json_object(value: object) -> dict[str, Any]:
    parsed = _json_value(value)
    return parsed if isinstance(parsed, dict) else {}


def _stable_id(*parts: object) -> str:
    value = ":".join(_clean(part, 500) for part in parts)
    safe = _SAFE_ID.sub("_", value).strip("_")
    return safe[:180] or "history_item"


def _equivalence_group(title: object, summary: object) -> str:
    value = f"{_clean(title, 1000)}\n{_clean(summary, 4000)}"
    return "title-summary:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _decision_info(payload: dict[str, Any]) -> tuple[Any, tuple[str, ...], tuple[str, ...]] | None:
    decision = decision_result_from_payload(payload)
    if decision is None:
        return None
    rule_ids = tuple(
        dict.fromkeys(
            str(hit.get("rule_id") or "")
            for hit in decision.rule_hits
            if isinstance(hit, dict) and hit.get("rule_id")
        )
    )
    families = tuple(dict.fromkeys(_RULE_FAMILIES[rule_id] for rule_id in rule_ids if rule_id in _RULE_FAMILIES))
    return decision, rule_ids, families


def _source_metadata(
    payload: dict[str, Any],
    *,
    default_category: str,
    default_role: str,
    default_content_type: str,
) -> tuple[str, str, str]:
    info = _decision_info(payload)
    audit = info[0].audit_json if info else {}
    return (
        _clean(audit.get("source_category"), 100) or default_category,
        _clean(audit.get("publisher_role"), 100) or default_role,
        _clean(audit.get("content_type"), 100) or default_content_type,
    )


def _explicit_article_body(payload: dict[str, Any]) -> str:
    raw = payload.get("raw")
    if not isinstance(raw, dict):
        return ""
    for key in ("full_text", "article_body", "body", "original_text"):
        value = _full_text(raw.get(key))
        if value:
            return value
    return ""


def _make_case(
    *,
    replay_id: str,
    source: str,
    title: str,
    summary: str,
    full_text: str = "",
    published_at: str,
    first_seen_at: str,
    payload: dict[str, Any],
    default_category: str,
    default_role: str,
    default_content_type: str,
    symbols: Iterable[object] = (),
    baseline: bool = False,
) -> tuple[ReplayCase, bool]:
    info = _decision_info(payload)
    if baseline:
        current = CurrentRuleOutcome(admission_status="not_applicable", admission_reason="baseline")
    elif info:
        decision, rule_ids, families = info
        current = CurrentRuleOutcome(
            admission_status="admitted",
            admission_reason="legacy_decision_result",
            matched_families=families,
            action=decision.action,
            rule_ids=rule_ids,
        )
    else:
        current = CurrentRuleOutcome(
            admission_status="unknown",
            admission_reason="legacy_unclassified",
        )

    category, role, content_type = _source_metadata(
        payload,
        default_category=default_category,
        default_role=default_role,
        default_content_type=default_content_type,
    )
    direct_families = ("trade_policy",) if category == "official_policy" or role == "government_official" else ()
    bounded_title = _clean(title, 1000)
    bounded_summary = _clean(summary, 4000)
    item = NormalizedMarketItem(
        source=_clean(source, 200),
        source_category=category,
        publisher_role=role,
        content_type=content_type,
        title=bounded_title,
        summary=bounded_summary,
        full_text=_full_text(full_text),
        published_at=_clean(published_at, 100),
        first_seen_at=_clean(first_seen_at, 100),
        symbols=[_clean(symbol, 100) for symbol in symbols if _clean(symbol, 100)],
    )
    return (
        ReplayCase(
            replay_id=_stable_id(replay_id),
            equivalence_group=_equivalence_group(bounded_title, bounded_summary),
            item=item,
            source_policy=SourceAdmissionPolicy(direct_families),
            current=current,
        ),
        info is not None,
    )


def read_history_cases(
    db_path: Path,
    *,
    article_limit: int = DEFAULT_ARTICLE_LIMIT,
    event_limit: int = DEFAULT_EVENT_LIMIT,
    content_mode: str = "full_text",
) -> tuple[tuple[ReplayCase, ...], HistoryInputStats]:
    if article_limit <= 0 or event_limit <= 0:
        raise ValueError("history limits must be positive")
    if content_mode not in {"full_text", "summary_proxy"}:
        raise ValueError("content_mode must be full_text or summary_proxy")
    uri = f"file:{db_path}?mode=ro"
    cases: list[ReplayCase] = []
    counts = Counter()
    with sqlite3.connect(uri, uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.row_factory = sqlite3.Row
        article_rows = conn.execute(
            """
            SELECT source, item_id, title, daily_summary, affected_targets_json,
                   published_at, created_at, gate_json
            FROM article_reviews ORDER BY created_at ASC LIMIT ?
            """,
            (article_limit,),
        ).fetchall()
        for row in article_rows:
            payload = _json_object(row["gate_json"])
            full_text = _explicit_article_body(payload)
            if content_mode == "full_text" and not full_text:
                counts["article_full_text_unavailable"] += 1
                continue
            targets = _json_value(row["affected_targets_json"])
            target_names = []
            if isinstance(targets, list):
                target_names = [item.get("name", "") if isinstance(item, dict) else item for item in targets]
            summary = "；".join(
                value for value in (row["daily_summary"], *target_names) if _clean(value, 4000)
            )
            case, has_decision = _make_case(
                replay_id=("article", row["source"], row["item_id"]),
                source=row["source"],
                title=row["title"],
                summary=summary,
                published_at=row["published_at"],
                first_seen_at=row["created_at"],
                payload=payload,
                full_text=full_text,
                default_category="news_media",
                default_role="news_media",
                default_content_type="article",
            )
            cases.append(case)
            counts["article_with_decision" if has_decision else "article_legacy_unclassified"] += 1

        event_rows = conn.execute(
            """
            SELECT e.id, e.source, e.title, e.summary, e.full_text, e.published_at, e.first_seen_at,
                   e.symbols_json, e.baseline_only, a.analysis_json
            FROM events e
            LEFT JOIN event_analyses a ON a.id = (
                SELECT max(a2.id) FROM event_analyses a2 WHERE a2.event_id = e.id
            )
            ORDER BY e.first_seen_at ASC LIMIT ?
            """,
            (event_limit,),
        ).fetchall()
        for row in event_rows:
            if row["baseline_only"]:
                counts["event_baseline_skipped"] += 1
                continue
            full_text = _full_text(row["full_text"])
            if content_mode == "full_text" and not full_text:
                counts["event_full_text_unavailable"] += 1
                continue
            symbols = _json_value(row["symbols_json"])
            if not isinstance(symbols, list):
                symbols = []
            source = str(row["source"] or "")
            is_trade = source.startswith("trade_policy")
            is_company = source == "company_disclosures"
            case, has_decision = _make_case(
                replay_id=("event", source, row["id"]),
                source=source,
                title=row["title"],
                summary=row["summary"],
                full_text=full_text,
                published_at=row["published_at"],
                first_seen_at=row["first_seen_at"],
                payload=_json_object(row["analysis_json"]),
                default_category="company_disclosure" if is_company else "official_policy" if is_trade else "news_media",
                default_role="company_official" if is_company else "government_official" if is_trade else "news_media",
                default_content_type="company_disclosure" if is_company else "article",
                symbols=symbols,
                baseline=bool(row["baseline_only"]),
            )
            cases.append(case)
            counts["event_with_decision" if has_decision else "event_legacy_unclassified"] += 1

    return (
        tuple(cases),
        HistoryInputStats(
            article_rows=len(article_rows),
            event_rows=len(event_rows),
            article_with_decision=counts["article_with_decision"],
            article_legacy_unclassified=counts["article_legacy_unclassified"],
            event_with_decision=counts["event_with_decision"],
            event_legacy_unclassified=counts["event_legacy_unclassified"],
            article_full_text_unavailable=counts["article_full_text_unavailable"],
            event_full_text_unavailable=counts["event_full_text_unavailable"],
            event_baseline_skipped=counts["event_baseline_skipped"],
        ),
    )


def build_history_replay_report(
    db_path: Path,
    *,
    rule_config: RuleConfig,
    portfolio: PortfolioRuleConfig,
    article_limit: int = DEFAULT_ARTICLE_LIMIT,
    event_limit: int = DEFAULT_EVENT_LIMIT,
    content_mode: str = "full_text",
) -> dict[str, Any]:
    cases, stats = read_history_cases(
        db_path,
        article_limit=article_limit,
        event_limit=event_limit,
        content_mode=content_mode,
    )
    report = build_replay_report(cases, rule_config=rule_config, portfolio=portfolio)
    report["input"] = {
        "source": "Alibaba production SQLite read-only snapshot",
        "snapshot_file": db_path.name,
        "query_only": True,
        "content_mode": content_mode,
        "content_scope": (
            "stored full text plus bounded title, summary and structured targets/symbols"
            if content_mode == "full_text"
            else "bounded title, daily summary, event summary, structured targets/symbols (screening only)"
        ),
        "article_limit": article_limit,
        "event_limit": event_limit,
        "stats": stats.to_dict(),
    }
    report["action_change_counts"] = dict(
        Counter(
            f"{row['current']['action'] or 'unknown'} -> {row['candidate']['action'] or 'unknown'}"
            for row in report["changes"]
        )
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the inactive v1 rule core against a local SQLite snapshot.")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--portfolio", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--content-mode", choices=("full_text", "summary_proxy"), default="full_text")
    args = parser.parse_args()
    from rule_core_v1 import parse_portfolio_config, parse_rule_config

    report = build_history_replay_report(
        args.db,
        rule_config=parse_rule_config(json.loads(args.config.read_text(encoding="utf-8"))),
        portfolio=parse_portfolio_config(json.loads(args.portfolio.read_text(encoding="utf-8"))),
        content_mode=args.content_mode,
    )
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.chmod(0o600)
    print(json.dumps({
        "status": report["status"],
        "case_count": report["case_count"],
        "changed_count": report["changed_count"],
        "source_invariance_violations": len(report["source_invariance_violations"]),
        "stats": report["input"]["stats"],
        "action_change_counts": report["action_change_counts"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
