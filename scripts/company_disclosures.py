#!/usr/bin/env python3
"""Collect portfolio company disclosures through a switchable provider adapter."""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from collector_runtime import load_source_state, save_source_state
from db_utils import connect_sqlite
from disclosure_document import parse_disclosure_pdf
from disclosure_providers import DisclosureProvider, DisclosureRecord, DisclosureSecurity, disclosure_identity, provider_factory
from env_utils import load_env
from market_db import DEFAULT_DB_PATH, init_db
from market_flow import normalize_market_item, process_market_item
from market_review_store import event_content_hash, load_enabled_holdings
from portfolio_import import import_holdings
from production_admission import persist_production_admission_context, production_admission_context
from source_health import record_source_failure, record_source_success
from source_profiles import SOURCE_PROFILE_CONFIG_PATH, runtime_source_profile, source_profile_enabled


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config" / "portfolio.json"
DEFAULT_DOCUMENT_DIR = ROOT / "data" / "company_disclosures"
SOURCE_ID = "company_disclosures"
STATE_PREFIX = "collector"
HEALTH_MONITOR = "company_disclosures"
DOCUMENT_HEALTH_MONITOR = "company_disclosure_document"
VALID_MODES = {"report_only", "live"}
CONTENT_KINDS = ("fulltext", "relation")
BJ = ZoneInfo("Asia/Shanghai")


def env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on", "启用"}


def env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def query_date_range(days: int) -> tuple[str, str]:
    today = datetime.now(BJ).date()
    start = today - timedelta(days=max(0, days - 1))
    return start.isoformat(), today.isoformat()


def document_dir() -> Path:
    raw = os.getenv("COMPANY_DISCLOSURE_PDF_DIR", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_DOCUMENT_DIR


def parse_record_document(record: DisclosureRecord) -> tuple[str, dict[str, Any]]:
    return parse_disclosure_pdf(
        record.document_url,
        target_dir=document_dir(),
        identity_parts=[record.symbol, disclosure_identity(record), record.title],
        enabled=env_bool("COMPANY_DISCLOSURE_PDF_PARSE", True),
        max_bytes=env_int("COMPANY_DISCLOSURE_PDF_MAX_BYTES", 30 * 1024 * 1024, minimum=1024 * 1024),
        max_pages=env_int("COMPANY_DISCLOSURE_PDF_MAX_PAGES", 80, minimum=1),
        max_chars=env_int("COMPANY_DISCLOSURE_TEXT_MAX_CHARS", 20_000, minimum=1000),
        min_chars=env_int("COMPANY_DISCLOSURE_TEXT_MIN_CHARS", 200, minimum=0),
    )


def event_from_disclosure(record: DisclosureRecord, full_text: str, document_meta: dict[str, Any]) -> dict[str, Any]:
    identity = disclosure_identity(record)
    event_type = "investor_relations_record" if record.content_kind == "relation" else "announcement"
    summary_parts = [
        f"股票：{record.company_name} {record.symbol}".strip(),
        f"标题：{record.title}",
        f"发布时间：{record.published_at}",
        f"披露类型：{'投资者关系活动记录' if record.content_kind == 'relation' else '公司公告'}",
    ]
    raw = {
        "transport_provider": record.provider,
        "provider_record_id": record.provider_record_id,
        "official_record_id": record.official_record_id,
        "official_document_url": record.document_url,
        "document_type": record.document_type,
        "content_kind": record.content_kind,
        "category": record.category,
        "provider_metadata": dict(record.raw_metadata),
        "_document_parse": dict(document_meta),
    }
    if not full_text:
        raw["_text_quality"] = "公告 PDF 正文未抽取成功，系统仅基于标题和元数据保守判断。"
    return {
        "source": SOURCE_ID,
        "source_event_id": identity,
        "source_category": "company_disclosures",
        "publisher_role": "company_official",
        "collector": "company_disclosures",
        "event_type": event_type,
        "content_type": event_type,
        "title": record.title,
        "summary": "；".join(summary_parts),
        "full_text": full_text,
        "url": record.document_url,
        "published_at": record.published_at,
        "symbols": [record.symbol],
        "themes": ["投资者关系活动记录"] if record.content_kind == "relation" else ["公司公告"],
        "raw": raw,
        "content_hash": event_content_hash(identity, record.title, record.published_at, full_text[:2000]),
    }


def _cached_securities(
    holdings: list[dict[str, Any]],
    provider_name: str,
    state: dict[str, Any],
) -> tuple[dict[str, DisclosureSecurity], list[str]]:
    provider_states = state.get("providers") if isinstance(state.get("providers"), dict) else {}
    provider_state = provider_states.get(provider_name) if isinstance(provider_states.get(provider_name), dict) else {}
    raw_refs = provider_state.get("securities") if isinstance(provider_state.get("securities"), dict) else {}
    cached: dict[str, DisclosureSecurity] = {}
    missing: list[str] = []
    names = {str(item.get("symbol") or "").upper(): str(item.get("name") or "") for item in holdings}
    for symbol in names:
        item = raw_refs.get(symbol)
        if not isinstance(item, dict) or not str(item.get("provider_security_id") or "").strip():
            missing.append(symbol)
            continue
        cached[symbol] = DisclosureSecurity(
            symbol=symbol,
            code=str(item.get("code") or symbol.split(".", 1)[0]),
            provider_security_id=str(item["provider_security_id"]),
            company_name=str(item.get("company_name") or names[symbol]),
            raw_metadata=dict(item.get("raw_metadata") or {}),
        )
    return cached, missing


def _serialized_securities(securities: dict[str, DisclosureSecurity]) -> dict[str, dict[str, Any]]:
    return {
        symbol: {
            "code": item.code,
            "provider_security_id": item.provider_security_id,
            "company_name": item.company_name,
            "raw_metadata": dict(item.raw_metadata),
        }
        for symbol, item in securities.items()
    }


def fetch_records(
    provider: DisclosureProvider,
    securities: list[DisclosureSecurity],
    start_date: str,
    end_date: str,
    *,
    max_pages: int = 100,
) -> list[DisclosureRecord]:
    records: dict[str, DisclosureRecord] = {}
    for content_kind in CONTENT_KINDS:
        page_number = 1
        while True:
            page = provider.list_disclosures(securities, start_date, end_date, content_kind, page_number)
            for record in page.records:
                records.setdefault(disclosure_identity(record), record)
            if not page.has_more:
                break
            page_number += 1
            if page_number > max_pages:
                raise RuntimeError(f"{provider.name} pagination exceeded {max_pages} pages for {content_kind}")
    return sorted(records.values(), key=lambda item: (item.published_at, disclosure_identity(item)))


def _record_health(db_path: Path, monitor: str, source: str, error: Exception | str | None) -> None:
    with connect_sqlite(db_path) as conn:
        if error is None:
            record_source_success(conn, monitor, source)
        else:
            record_source_failure(conn, monitor, source, error)
        conn.commit()


def collect_disclosures(
    *,
    provider: DisclosureProvider,
    mode: str,
    days: int,
    db_path: Path = DEFAULT_DB_PATH,
    holdings: list[dict[str, Any]] | None = None,
    analyze: bool = True,
    deliver: bool = True,
    dry_run: bool = False,
    parse_documents: bool = True,
    backfill_baselines: bool = False,
    backfill_first_seen_at: str = "",
) -> dict[str, Any]:
    if mode not in VALID_MODES:
        raise ValueError(f"unsupported company disclosure mode: {mode}")
    if backfill_first_seen_at and not backfill_baselines:
        raise ValueError("backfill_first_seen_at requires backfill_baselines")
    init_db(db_path).close()
    holdings = list(holdings if holdings is not None else load_enabled_holdings(db_path))
    symbols = [str(item.get("symbol") or "").strip().upper() for item in holdings if item.get("symbol")]
    if not symbols:
        return {
            "fetched": 0,
            "new": 0,
            "existing": 0,
            "baseline": 0,
            "backfilled": 0,
            "processed": 0,
            "document_failures": 0,
        }

    with connect_sqlite(db_path) as conn:
        state = load_source_state(conn, SOURCE_ID, prefix=STATE_PREFIX)
    cached, missing = _cached_securities(holdings, provider.name, state)
    used_cached_mapping = bool(cached)
    if missing:
        resolved = provider.resolve_securities(missing)
        unresolved = sorted(set(missing) - set(resolved))
        if unresolved:
            raise RuntimeError(f"{provider.name} did not resolve: {', '.join(unresolved)}")
        cached.update(resolved)
    securities = [cached[symbol] for symbol in symbols]
    start_date, end_date = query_date_range(days)
    try:
        records = fetch_records(provider, securities, start_date, end_date)
    except Exception:
        if not used_cached_mapping:
            raise
        refreshed = provider.resolve_securities(symbols)
        unresolved = sorted(set(symbols) - set(refreshed))
        if unresolved:
            raise RuntimeError(f"{provider.name} did not refresh: {', '.join(unresolved)}")
        cached = refreshed
        securities = [cached[symbol] for symbol in symbols]
        records = fetch_records(provider, securities, start_date, end_date)

    raw_known = state.get("known_identities") if isinstance(state.get("known_identities"), list) else []
    known_order = [str(value) for value in raw_known if str(value)]
    known = set(known_order)
    raw_initialized = state.get("initialized_providers") if isinstance(state.get("initialized_providers"), list) else []
    initialized_providers = {str(value) for value in raw_initialized if str(value)}
    provider_baseline = provider.name not in initialized_providers
    stats: dict[str, Any] = {
        "provider": provider.name,
        "mode": mode,
        "range": f"{start_date}..{end_date}",
        "fetched": len(records),
        "new": 0,
        "existing": 0,
        "baseline": 0,
        "backfilled": 0,
        "processed": 0,
        "excluded": 0,
        "document_failures": 0,
    }
    backfill_seen_at = ""
    if backfill_first_seen_at:
        parsed_seen_at = datetime.fromisoformat(backfill_first_seen_at.replace("Z", "+00:00"))
        if parsed_seen_at.tzinfo is None:
            raise ValueError("backfill_first_seen_at must include a timezone")
        backfill_seen_at = parsed_seen_at.astimezone(timezone.utc).isoformat()
    for record in records:
        identity = disclosure_identity(record)
        identity_known = identity in known
        if identity_known:
            stats["existing"] += 1
            if not backfill_baselines:
                continue
        else:
            stats["new"] += 1
        if parse_documents:
            full_text, document_meta = parse_record_document(record)
        else:
            full_text, document_meta = "", {"enabled": False, "status": "skipped", "source": "disclosure_pdf"}
        if document_meta.get("status") == "failed":
            stats["document_failures"] += 1
        event = event_from_disclosure(record, full_text, document_meta)
        baseline_only = identity_known or provider_baseline or mode == "report_only"
        if baseline_only:
            event["baseline_only"] = True
            if identity_known and backfill_seen_at:
                event["first_seen_at"] = backfill_seen_at
        if dry_run:
            label = "backfill" if identity_known else ("baseline" if provider_baseline else "report-only")
            print(f"[{label}] {identity} {record.symbol} {record.title} pdf={document_meta.get('status')}", flush=True)
            if not identity_known:
                stats["baseline"] += 1
        elif baseline_only:
            normalized = normalize_market_item(SOURCE_ID, event, store_kind="event")
            outcome = process_market_item(
                normalized,
                event,
                store_kind="event",
                source_profile_id=SOURCE_ID,
                db_path=db_path,
                baseline_only=True,
                analyze=False,
                deliver=False,
                current_admission_status="not_applicable",
                current_admission_reason="baseline_only",
            )
            if identity_known:
                stats["backfilled"] += 1 if outcome.inserted else 0
                print(
                    f"[backfill] {identity} {record.symbol} {record.title} "
                    f"inserted={outcome.inserted} pdf={document_meta.get('status')}",
                    flush=True,
                )
            else:
                stats["baseline"] += 1
                print(f"[baseline] {identity} {record.symbol} {record.title} pdf={document_meta.get('status')}", flush=True)
        else:
            normalized = normalize_market_item(SOURCE_ID, event, store_kind="event")
            admission_context = persist_production_admission_context(normalized, production_admission_context(normalized, db_path=db_path), db_path=db_path)
            admission = admission_context.result
            if admission.status != "admitted":
                stats["excluded"] += 1
                print(
                    f"[excluded] {identity} {record.symbol} {record.title} reason={admission.reason_code}",
                    flush=True,
                )
            else:
                outcome = process_market_item(
                    normalized,
                    event,
                    store_kind="event",
                    source_profile_id=SOURCE_ID,
                    db_path=db_path,
                    analyze=analyze,
                    deliver=deliver,
                    production_admission=admission,
                    production_portfolio=admission_context.portfolio,
                    market_item_id=admission_context.market_item_id,
                    market_review_id=admission_context.market_review_id,
                )
                stats["processed"] += 1 if outcome.inserted else 0
        if not dry_run and not identity_known:
            known.add(identity)
            known_order.append(identity)

    if not dry_run:
        initialized_providers.add(provider.name)
        providers = dict(state.get("providers")) if isinstance(state.get("providers"), dict) else {}
        providers[provider.name] = {
            "securities": _serialized_securities(cached),
            "last_range": stats["range"],
            "last_fetched": stats["fetched"],
        }
        next_state = {
            "schema_version": 1,
            "known_identities": known_order[-5000:],
            "initialized_providers": sorted(initialized_providers),
            "providers": providers,
            "last_mode": mode,
            "last_run_at": datetime.now(BJ).isoformat(),
            "last_stats": stats,
        }
        with connect_sqlite(db_path) as conn:
            save_source_state(conn, SOURCE_ID, next_state, prefix=STATE_PREFIX)
            conn.commit()
        _record_health(db_path, HEALTH_MONITOR, provider.name, None)
        document_error = f"{stats['document_failures']} document parse failures" if stats["document_failures"] else None
        _record_health(db_path, DOCUMENT_HEALTH_MONITOR, provider.name, document_error)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="公司公告采集")
    parser.add_argument("--days", type=int, default=2)
    parser.add_argument("--provider")
    parser.add_argument("--mode", choices=sorted(VALID_MODES))
    parser.add_argument("--no-analyze", action="store_true")
    parser.add_argument("--no-deliver", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-pdf", action="store_true")
    parser.add_argument("--backfill-baselines", action="store_true")
    parser.add_argument("--backfill-first-seen-at")
    args = parser.parse_args()

    if args.backfill_first_seen_at and not args.backfill_baselines:
        parser.error("--backfill-first-seen-at requires --backfill-baselines")

    load_env(ROOT / ".env")
    if not source_profile_enabled(SOURCE_ID):
        print(f"source profile: {SOURCE_ID} 已停用，跳过本轮。", flush=True)
        return 0
    profile = runtime_source_profile(SOURCE_ID, config_path=SOURCE_PROFILE_CONFIG_PATH) or {}
    provider_name = str(args.provider or profile.get("provider") or "cninfo_public").strip()
    mode = str(args.mode or profile.get("operation_mode") or "report_only").strip()
    init_db(DEFAULT_DB_PATH).close()
    import_holdings(DEFAULT_CONFIG_PATH, DEFAULT_DB_PATH)
    try:
        stats = collect_disclosures(
            provider=provider_factory(provider_name),
            mode=mode,
            days=args.days,
            analyze=not args.no_analyze,
            deliver=not args.no_deliver,
            dry_run=args.dry_run,
            parse_documents=not args.no_pdf,
            backfill_baselines=args.backfill_baselines,
            backfill_first_seen_at=str(args.backfill_first_seen_at or ""),
        )
    except Exception as exc:  # noqa: BLE001 - scheduled run records provider failure
        if not args.dry_run:
            _record_health(DEFAULT_DB_PATH, HEALTH_MONITOR, provider_name, exc)
        print(f"company disclosures failed: {type(exc).__name__}: {exc}", flush=True)
        return 1
    print(
        "company disclosures finished: "
        + " ".join(f"{key}={value}" for key, value in stats.items()),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
