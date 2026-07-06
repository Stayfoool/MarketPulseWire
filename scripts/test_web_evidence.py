#!/usr/bin/env python3
"""Regression checks for controlled web evidence retrieval."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from market_db import init_db
from settings_store import settings_payload
import web_evidence
from web_evidence import build_queries, collect_web_evidence, prompt_pack


def restore_env(snapshot: dict[str, str | None]) -> None:
    for key, value in snapshot.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_settings_payload_exposes_web_evidence_group() -> None:
    payload = settings_payload(Path("/tmp/nonexistent-marketpulsewire-env"))
    group_ids = [group["id"] for group in payload["groups"]]
    assert "web_evidence" in group_ids
    fields = {
        field["key"]
        for group in payload["groups"]
        if group["id"] == "web_evidence"
        for field in group["fields"]
    }
    assert "WEB_EVIDENCE_PROVIDER" in fields
    assert "WEB_EVIDENCE_API_KEY" in fields


def test_build_queries_include_core_tasks() -> None:
    item = {
        "title": "TrendForce称NAND涨价幅度放缓 可能进入周期拐点",
        "summary": "美联储讲话后美债收益率下行，市场担心成长股估值。",
    }
    review = {"affected_targets": ["存储", "江波龙"], "reason": "高重要性"}
    queries = build_queries("trendforce_semiconductors", item, review, mode="realtime")
    query_types = {query.query_type for query in queries}
    assert {"prior_coverage", "primary_source", "counter_evidence", "market_pricing"}.issubset(query_types)
    assert "macro_background" in query_types
    assert any("价格回落" in query.query or "扩产" in query.query for query in queries)


def test_collect_web_evidence_saves_docs_and_pack() -> None:
    keys = [
        "WEB_EVIDENCE_ENABLED",
        "WEB_EVIDENCE_PROVIDER",
        "WEB_EVIDENCE_API_KEY",
        "WEB_EVIDENCE_MAX_QUERIES",
        "WEB_EVIDENCE_MAX_RESULTS",
    ]
    snapshot = {key: os.environ.get(key) for key in keys}
    original_search = web_evidence.provider_search
    try:
        os.environ["WEB_EVIDENCE_ENABLED"] = "1"
        os.environ["WEB_EVIDENCE_PROVIDER"] = "tavily"
        os.environ["WEB_EVIDENCE_API_KEY"] = "test-key"
        os.environ["WEB_EVIDENCE_MAX_QUERIES"] = "2"
        os.environ["WEB_EVIDENCE_MAX_RESULTS"] = "2"

        def fake_search(provider, query):  # noqa: ANN001
            assert provider == "tavily"
            return [
                {
                    "url": f"https://www.trendforce.com/presscenter/news/test-{query.query_type}.html",
                    "title": f"{query.query_type} title",
                    "content": f"{query.query_type} claim about NAND pricing.",
                    "published_date": "2026-07-03",
                    "score": 0.91,
                }
            ]

        web_evidence.provider_search = fake_search
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = init_db(Path(tmpdir) / "surveil.sqlite3")
            pack = collect_web_evidence(
                conn,
                trigger_module="skeptic_evaluator",
                source="trendforce_semiconductors",
                item={
                    "id": "tf-nand",
                    "title": "TrendForce称NAND涨价幅度放缓",
                    "published_at": datetime.now(timezone.utc).isoformat(),
                    "summary": "NAND涨价幅度放缓。",
                },
                review={"importance": "high", "push_now": True, "affected_targets": ["存储"]},
            )
            assert pack is not None
            assert pack["provider"] == "tavily"
            assert pack["summary"]["doc_count"] == 2
            assert "documents" in prompt_pack(pack)
            run_count = conn.execute("SELECT COUNT(*) FROM web_evidence_runs").fetchone()[0]
            doc_count = conn.execute("SELECT COUNT(*) FROM web_evidence_docs").fetchone()[0]
            health = conn.execute(
                "SELECT consecutive_failures FROM source_health WHERE monitor='web_evidence' AND source='tavily'"
            ).fetchone()
            assert run_count == 1
            assert doc_count == 2
            assert health[0] == 0
            conn.close()
    finally:
        web_evidence.provider_search = original_search
        restore_env(snapshot)


def test_disabled_web_evidence_is_noop() -> None:
    snapshot = {
        "WEB_EVIDENCE_ENABLED": os.environ.get("WEB_EVIDENCE_ENABLED"),
        "WEB_EVIDENCE_API_KEY": os.environ.get("WEB_EVIDENCE_API_KEY"),
    }
    try:
        os.environ["WEB_EVIDENCE_ENABLED"] = "0"
        os.environ["WEB_EVIDENCE_API_KEY"] = "test-key"
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = init_db(Path(tmpdir) / "surveil.sqlite3")
            pack = collect_web_evidence(
                conn,
                trigger_module="skeptic_evaluator",
                source="test",
                item={"id": "1", "title": "测试"},
                review={"importance": "high", "push_now": True},
            )
            assert pack is None
            assert conn.execute("SELECT COUNT(*) FROM web_evidence_runs").fetchone()[0] == 0
            conn.close()
    finally:
        restore_env(snapshot)


def main() -> int:
    test_settings_payload_exposes_web_evidence_group()
    test_build_queries_include_core_tasks()
    test_collect_web_evidence_saves_docs_and_pack()
    test_disabled_web_evidence_is_noop()
    print("web evidence checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

