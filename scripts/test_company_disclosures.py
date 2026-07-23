#!/usr/bin/env python3
"""Regression checks for provider-neutral company disclosure collection."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from company_disclosures import collect_disclosures, event_from_disclosure, fetch_records
from decision_engine import decide_market_item
from disclosure_document import parse_disclosure_pdf
from disclosure_providers import DisclosurePage, DisclosureRecord, DisclosureSecurity
from market_db import init_db
from market_runtime import normalize_market_item


def record(record_id: str, *, kind: str = "fulltext", provider: str = "cninfo_public") -> DisclosureRecord:
    return DisclosureRecord(
        provider=provider,
        provider_record_id=f"{provider}-{record_id}",
        official_record_id=record_id,
        symbol="301308.SZ",
        company_name="江波龙",
        title=f"公告 {record_id}",
        published_at="2026-07-16T08:00:00+08:00",
        document_url=f"https://static.cninfo.com.cn/finalpage/2026-07-16/{record_id}.PDF",
        document_type="PDF",
        content_kind=kind,
        category="test",
    )


class FakeProvider:
    def __init__(self, records: list[DisclosureRecord], name: str = "cninfo_public") -> None:
        self.name = name
        self.records = records

    def resolve_securities(self, symbols: list[str]) -> dict[str, DisclosureSecurity]:
        return {
            symbol: DisclosureSecurity(symbol, symbol.split(".", 1)[0], f"org-{symbol}", "江波龙")
            for symbol in symbols
        }

    def list_disclosures(
        self,
        securities: list[DisclosureSecurity],
        start_date: str,
        end_date: str,
        content_kind: str,
        page: int,
    ) -> DisclosurePage:
        rows = tuple(item for item in self.records if item.content_kind == content_kind)
        return DisclosurePage(records=rows, has_more=False, total=len(rows))


def seed_production_holding(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO portfolio_holdings
                (symbol, name, full_name, aliases_json, enabled, raw_json, updated_at)
            VALUES ('301308.SZ', '江波龙', '', '[]', 1, '{}', '2026-07-23T00:00:00+00:00')
            """
        )


def test_report_only_baselines_then_live_processes_only_new_record() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        seed_production_holding(db_path)
        holdings = [{"symbol": "301308.SZ", "name": "江波龙"}]
        provider = FakeProvider([record("1225409631"), record("1225286505", kind="relation")])
        first = collect_disclosures(
            provider=provider,
            mode="report_only",
            days=2,
            db_path=db_path,
            holdings=holdings,
            deliver=False,
            parse_documents=False,
        )
        with sqlite3.connect(db_path) as conn:
            baseline_rows = conn.execute(
                "SELECT source, source_event_id, baseline_only FROM events ORDER BY source_event_id"
            ).fetchall()
            assert conn.execute("SELECT COUNT(*) FROM event_analyses").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 0
        assert baseline_rows == [
            ("company_disclosures", "announcement:1225286505", 1),
            ("company_disclosures", "announcement:1225409631", 1),
        ]
        assert first["baseline"] == 2 and first["processed"] == 0

        provider.records.append(record("1226000000"))
        second = collect_disclosures(
            provider=provider,
            mode="live",
            days=2,
            db_path=db_path,
            holdings=holdings,
            analyze=False,
            deliver=False,
            parse_documents=False,
        )
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute("SELECT source, source_event_id, baseline_only FROM events").fetchall()
            state_raw = conn.execute(
                "SELECT state_json FROM source_state WHERE source = ?",
                ("collector:company_disclosures",),
            ).fetchone()[0]
        assert rows == [
            ("company_disclosures", "announcement:1225286505", 1),
            ("company_disclosures", "announcement:1225409631", 1),
            ("company_disclosures", "announcement:1226000000", 0),
        ]
        assert second["processed"] == 1 and second["existing"] == 2
        assert "cninfo_public" in json.loads(state_raw)["initialized_providers"]


def test_new_provider_is_baselined_before_live_processing() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        holdings = [{"symbol": "301308.SZ", "name": "江波龙"}]
        collect_disclosures(
            provider=FakeProvider([record("1225409631")]),
            mode="report_only",
            days=2,
            db_path=db_path,
            holdings=holdings,
            deliver=False,
            parse_documents=False,
        )
        alternate_record = record("alternate-only", provider="tushare")
        result = collect_disclosures(
            provider=FakeProvider([alternate_record], name="tushare"),
            mode="live",
            days=2,
            db_path=db_path,
            holdings=holdings,
            analyze=False,
            deliver=False,
            parse_documents=False,
        )
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT source_event_id, baseline_only FROM events ORDER BY source_event_id"
            ).fetchall()
            assert conn.execute("SELECT COUNT(*) FROM event_analyses").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 0
    assert rows == [("announcement:1225409631", 1), ("announcement:alternate-only", 1)]
    assert result["baseline"] == 1 and result["processed"] == 0


def test_excluded_live_record_advances_source_identity_without_creating_event() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        provider = FakeProvider([record("excluded")])
        state = {
            "schema_version": 1,
            "known_identities": [],
            "initialized_providers": [provider.name],
        }
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO source_state (source, state_json, updated_at) VALUES (?, ?, ?)",
                (
                    "collector:company_disclosures",
                    json.dumps(state),
                    "2026-07-23T00:00:00+00:00",
                ),
            )

        first = collect_disclosures(
            provider=provider,
            mode="live",
            days=2,
            db_path=db_path,
            holdings=[{"symbol": "301308.SZ", "name": "江波龙"}],
            analyze=False,
            deliver=False,
            parse_documents=False,
        )
        second = collect_disclosures(
            provider=provider,
            mode="live",
            days=2,
            db_path=db_path,
            holdings=[{"symbol": "301308.SZ", "name": "江波龙"}],
            analyze=False,
            deliver=False,
            parse_documents=False,
        )
        with sqlite3.connect(db_path) as conn:
            saved = json.loads(
                conn.execute(
                    "SELECT state_json FROM source_state WHERE source = ?",
                    ("collector:company_disclosures",),
                ).fetchone()[0]
            )
            event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    assert first["excluded"] == 1 and first["processed"] == 0
    assert second["existing"] == 1 and second["excluded"] == 0
    assert saved["known_identities"] == ["announcement:excluded"]
    assert event_count == 0


def test_known_identity_backfill_is_idempotent_and_never_analyzed_or_delivered() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        state = {
            "schema_version": 1,
            "known_identities": ["announcement:1225409631"],
            "initialized_providers": ["cninfo_public"],
        }
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO source_state (source, state_json, updated_at) VALUES (?, ?, ?)",
                ("collector:company_disclosures", json.dumps(state), "2026-07-16T11:41:48+00:00"),
            )
        kwargs = {
            "provider": FakeProvider([record("1225409631")]),
            "mode": "live",
            "days": 2,
            "db_path": db_path,
            "holdings": [{"symbol": "301308.SZ", "name": "江波龙"}],
            "deliver": True,
            "parse_documents": False,
            "backfill_baselines": True,
            "backfill_first_seen_at": "2026-07-16T19:41:48+08:00",
        }
        first = collect_disclosures(**kwargs)
        second = collect_disclosures(**kwargs)
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT source_event_id, first_seen_at, baseline_only FROM events"
            ).fetchall()
            analysis_count = conn.execute("SELECT COUNT(*) FROM event_analyses").fetchone()[0]
            delivery_count = conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]

    assert rows == [("announcement:1225409631", "2026-07-16T11:41:48+00:00", 1)]
    assert analysis_count == 0
    assert delivery_count == 0
    assert first["existing"] == 1 and first["backfilled"] == 1 and first["processed"] == 0
    assert second["existing"] == 1 and second["backfilled"] == 0 and second["processed"] == 0


def test_pagination_runs_to_completion_for_each_content_kind() -> None:
    class PagedProvider(FakeProvider):
        def list_disclosures(self, securities, start_date, end_date, content_kind, page):
            row = record(f"{content_kind}-{page}", kind=content_kind)
            return DisclosurePage(records=(row,), has_more=page == 1, total=2)

    security = DisclosureSecurity("301308.SZ", "301308", "org", "江波龙")
    records = fetch_records(PagedProvider([]), [security], "2026-07-15", "2026-07-16")
    assert {item.official_record_id for item in records} == {
        "fulltext-1",
        "fulltext-2",
        "relation-1",
        "relation-2",
    }


def test_stale_cached_security_mapping_is_refreshed_once() -> None:
    class RefreshingProvider(FakeProvider):
        resolve_calls = 0

        def resolve_securities(self, symbols):
            self.resolve_calls += 1
            return super().resolve_securities(symbols)

        def list_disclosures(self, securities, start_date, end_date, content_kind, page):
            if securities[0].provider_security_id == "stale-org":
                raise RuntimeError("stale orgId")
            return super().list_disclosures(securities, start_date, end_date, content_kind, page)

    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        init_db(db_path).close()
        state = {
            "providers": {
                "cninfo_public": {
                    "securities": {
                        "301308.SZ": {
                            "code": "301308",
                            "provider_security_id": "stale-org",
                            "company_name": "江波龙",
                        }
                    }
                }
            }
        }
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO source_state (source, state_json, updated_at) VALUES (?, ?, ?)",
                ("collector:company_disclosures", json.dumps(state), "2026-07-16T00:00:00+00:00"),
            )
        provider = RefreshingProvider([record("1225409631")])
        result = collect_disclosures(
            provider=provider,
            mode="report_only",
            days=2,
            db_path=db_path,
            holdings=[{"symbol": "301308.SZ", "name": "江波龙"}],
            deliver=False,
            parse_documents=False,
        )
    assert provider.resolve_calls == 1
    assert result["fetched"] == 1


def test_event_preserves_provider_audit_but_uses_logical_source() -> None:
    item = record("1225409631")
    event = event_from_disclosure(item, "公告正文", {"status": "ok", "file_sha256": "abc"})
    assert event["source"] == "company_disclosures"
    assert event["source_event_id"] == "announcement:1225409631"
    assert event["raw"]["transport_provider"] == "cninfo_public"
    assert event["raw"]["official_document_url"].endswith("1225409631.PDF")
    assert event["full_text"] == "公告正文"


def test_transport_provider_does_not_change_identity_or_decision() -> None:
    text = "HBM supply shortage will persist until 2028 and prices are projected to double."
    records = [record("1225409631", provider=name) for name in ("cninfo_public", "tushare")]
    items = []
    decisions = []
    for disclosure in records:
        disclosure = replace(disclosure, title=text)
        event = event_from_disclosure(disclosure, text, {"status": "ok"})
        item = normalize_market_item("company_disclosures", event, store_kind="event")
        items.append(item)
        decisions.append(decide_market_item(item, holdings=[]))
    assert {item.dedupe_key for item in items} == {"company_disclosures:announcement:1225409631"}
    assert {decision.action for decision in decisions} == {"push"}
    assert {decision.rule_hits[0]["rule_id"] for decision in decisions} == {"industry_quantified_hardline"}


def test_malformed_pdf_is_a_bounded_metadata_failure() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        invalid = root / "not-a-pdf.txt"
        invalid.write_bytes(b"not a PDF")
        text, metadata = parse_disclosure_pdf(
            invalid.as_uri(),
            target_dir=root / "cache",
            identity_parts=["301308.SZ", "bad"],
        )
    assert text == ""
    assert metadata["status"] == "failed"
    assert "not a PDF" in metadata["error"]


def main() -> int:
    test_report_only_baselines_then_live_processes_only_new_record()
    test_new_provider_is_baselined_before_live_processing()
    test_excluded_live_record_advances_source_identity_without_creating_event()
    test_known_identity_backfill_is_idempotent_and_never_analyzed_or_delivered()
    test_pagination_runs_to_completion_for_each_content_kind()
    test_stale_cached_security_mapping_is_refreshed_once()
    test_event_preserves_provider_audit_but_uses_logical_source()
    test_transport_provider_does_not_change_identity_or_decision()
    test_malformed_pdf_is_a_bounded_metadata_failure()
    print("company disclosure checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
