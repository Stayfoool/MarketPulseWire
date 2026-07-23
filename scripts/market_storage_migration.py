#!/usr/bin/env python3
"""Preview or apply the idempotent legacy-result migration to canonical storage."""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from db_utils import connect_sqlite
from market_db import DEFAULT_DB_PATH, MARKET_RESULTS_MIGRATION_VERSION, init_db
from market_item import NormalizedMarketItem, decision_result_from_payload, item_from_event_mapping
from market_store import ensure_market_item_alias, json_dumps, upsert_market_item, utc_now


MIGRATION_VERSION = MARKET_RESULTS_MIGRATION_VERSION


@dataclass
class MigrationStats:
    items: int = 0
    aliases: int = 0
    reviews: int = 0
    reviews_with_decision: int = 0
    reviews_without_decision: int = 0
    event_deliveries_linked: int = 0
    reconciled_event_reviews: int = 0
    skipped_existing: int = 0
    by_store: dict[str, int] = field(default_factory=dict)

    def add_store(self, store: str) -> None:
        self.by_store[store] = self.by_store.get(store, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "migration_version": MIGRATION_VERSION,
            "items": self.items,
            "aliases": self.aliases,
            "reviews": self.reviews,
            "reviews_with_decision": self.reviews_with_decision,
            "reviews_without_decision": self.reviews_without_decision,
            "event_deliveries_linked": self.event_deliveries_linked,
            "reconciled_event_reviews": self.reconciled_event_reviews,
            "skipped_existing": self.skipped_existing,
            "by_store": dict(sorted(self.by_store.items())),
        }


def _json_dict(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _interpretation(payload: dict[str, Any]) -> dict[str, Any]:
    queue = [payload]
    seen: set[int] = set()
    while queue:
        current = queue.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        candidate = current.get("_interpretation_result")
        if isinstance(candidate, dict):
            return candidate
        for key in ("raw", "analysis"):
            nested = current.get(key)
            if isinstance(nested, dict):
                queue.append(nested)
    return {}


def _seen_admission(conn: sqlite3.Connection, source: str, source_item_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT admission_status, admission_reason, admission_matched_families_json,
               admission_evidence_json, admission_config_version,
               admission_rule_contract_version, admission_evaluated_at
        FROM seen_items WHERE source = ? AND item_id = ?
        """,
        (source, source_item_id),
    ).fetchone()
    if not row:
        return {"status": "legacy_unclassified"}
    return {
        "status": str(row[0] or "legacy_unclassified"),
        "reason_code": str(row[1] or ""),
        "matched_families": _json_list(row[2]),
        "evidence": _json_list(row[3]),
        "config_version": str(row[4] or ""),
        "rule_contract_version": str(row[5] or ""),
        "evaluated_at": str(row[6] or ""),
    }


def _ensure_article_item(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    item_kind: str,
    store_kind: str,
) -> int:
    source = str(row["source"] or "")
    source_item_id = str(row["item_id"] or "")
    existing = conn.execute(
        "SELECT id FROM market_items WHERE source = ? AND source_item_id = ?",
        (source, source_item_id),
    ).fetchone()
    if existing:
        market_item_id = int(existing[0])
    else:
        item = NormalizedMarketItem(
            source=source,
            content_type="official_news" if item_kind == "official" else "article",
            title=str(row["title"] or ""),
            url=str(row["url"] or ""),
            published_at=str(row["published_at"] or ""),
            first_seen_at=str(row["created_at"] or ""),
            raw={"id": source_item_id},
        )
        market_item_id = upsert_market_item(
            conn,
            item,
            collection_class="legacy_unclassified",
            processability_status="legacy_unclassified",
            processing_status="succeeded",
            legacy_store_kind=store_kind,
            legacy_store_id=f"{source}:{source_item_id}",
        )
    ensure_market_item_alias(
        conn,
        market_item_id,
        item_kind=item_kind,
        source=source,
        legacy_item_id=source_item_id,
        legacy_store_kind=store_kind,
    )
    return market_item_id


def _insert_review(
    conn: sqlite3.Connection,
    *,
    market_item_id: int,
    task: str,
    source: str,
    source_item_id: str,
    store_kind: str,
    legacy_result_id: str,
    payload: dict[str, Any],
    created_at: str,
    admission: dict[str, Any],
    make_current: bool = True,
) -> tuple[bool, bool]:
    existing = conn.execute(
        "SELECT 1 FROM market_reviews WHERE legacy_store_kind = ? AND legacy_store_id = ?",
        (store_kind, legacy_result_id),
    ).fetchone()
    if existing:
        return False, False
    decision = decision_result_from_payload(payload)
    interpretation = _interpretation(payload)
    if make_current:
        conn.execute(
            "UPDATE market_reviews SET is_current = 0 WHERE market_item_id = ? AND task = ? AND is_current = 1",
            (market_item_id, task),
        )
    status = str(admission.get("status") or "legacy_unclassified")
    conn.execute(
        """
        INSERT INTO market_reviews (
            market_item_id, task, run_key, is_current, review_status,
            admission_status, admission_reason, admission_matched_families_json,
            admission_evidence_json, admission_config_version,
            admission_rule_contract_version, admission_json, decision_action,
            importance, decision_json, interpretation_json, legacy_payload_json,
            application_revision, legacy_store_kind, legacy_store_id,
            created_at, completed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?)
        """,
        (
            market_item_id,
            task,
            uuid.uuid4().hex,
            int(make_current),
            "succeeded" if decision is not None else "legacy_unclassified",
            status,
            str(admission.get("reason_code") or ""),
            json_dumps(admission.get("matched_families") or []),
            json_dumps(admission.get("evidence") or []),
            str(admission.get("config_version") or ""),
            str(admission.get("rule_contract_version") or ""),
            json_dumps(admission),
            decision.action if decision else None,
            decision.importance if decision else None,
            json_dumps(decision.to_dict()) if decision else None,
            json_dumps(interpretation) if interpretation else None,
            json_dumps(payload),
            store_kind,
            legacy_result_id,
            created_at or utc_now(),
            created_at or utc_now(),
        ),
    )
    return True, decision is not None


def _event_review_placeholders(conn: sqlite3.Connection, *, apply: bool) -> dict[str, int]:
    """Map first-stage ``event_id:task`` links to their exact latest legacy row."""
    mapped: dict[str, int] = {}
    rows = list(
        conn.execute(
            """
            SELECT id,legacy_store_id FROM market_reviews
            WHERE legacy_store_kind='event_analyses'
              AND instr(COALESCE(legacy_store_id,''), ':') > 0
            ORDER BY id
            """
        )
    )
    for review in rows:
        event_id_text, task = str(review["legacy_store_id"]).split(":", 1)
        if not event_id_text.isdigit() or not task:
            continue
        analysis = conn.execute(
            """
            SELECT id FROM event_analyses
            WHERE event_id=? AND task=?
            ORDER BY id DESC LIMIT 1
            """,
            (int(event_id_text), task),
        ).fetchone()
        if not analysis:
            continue
        analysis_id = str(analysis[0])
        conflict = conn.execute(
            """
            SELECT id FROM market_reviews
            WHERE legacy_store_kind='event_analyses' AND legacy_store_id=? AND id<>?
            """,
            (analysis_id, int(review["id"])),
        ).fetchone()
        if conflict:
            continue
        mapped[analysis_id] = int(review["id"])
        if apply:
            conn.execute(
                "UPDATE market_reviews SET legacy_store_id=? WHERE id=?",
                (analysis_id, int(review["id"])),
            )
    return mapped


def _migrate_legacy_results(conn: sqlite3.Connection, *, apply: bool) -> MigrationStats:
    conn.row_factory = sqlite3.Row
    stats = MigrationStats()
    placeholder_reviews = _event_review_placeholders(conn, apply=apply)
    stats.reconciled_event_reviews = len(placeholder_reviews)
    stats.event_deliveries_linked = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM deliveries d
            JOIN events e ON e.id=d.event_id
            WHERE d.event_id IS NOT NULL AND d.market_item_id IS NULL
            """
        ).fetchone()[0]
    )
    article_specs = (
        ("article_reviews", "article", "gate_json"),
        ("official_news_reviews", "official", "analysis_json"),
    )
    for store_kind, item_kind, payload_column in article_specs:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (store_kind,)
        ).fetchone() is None:
            continue
        rows = list(conn.execute(f"SELECT * FROM {store_kind} ORDER BY created_at, rowid"))
        for row in rows:
            legacy_result_id = f"{row['source']}:{row['item_id']}"
            review_exists = conn.execute(
                "SELECT 1 FROM market_reviews WHERE legacy_store_kind=? AND legacy_store_id=?",
                (store_kind, legacy_result_id),
            ).fetchone()
            alias_exists = conn.execute(
                """
                SELECT 1 FROM market_item_aliases
                WHERE item_kind=? AND source=? AND legacy_item_id=?
                """,
                (item_kind, str(row["source"]), str(row["item_id"])),
            ).fetchone()
            item_exists = conn.execute(
                "SELECT 1 FROM market_items WHERE source=? AND source_item_id=?",
                (str(row["source"]), str(row["item_id"])),
            ).fetchone()
            if not item_exists:
                stats.items += 1
            if not alias_exists:
                stats.aliases += 1
            if review_exists:
                stats.skipped_existing += 1
            else:
                stats.reviews += 1
                stats.add_store(store_kind)
            payload = _json_dict(row[payload_column])
            payload["_legacy_row"] = {
                key: row[key] for key in row.keys() if key != payload_column
            }
            has_decision = decision_result_from_payload(payload) is not None
            if not review_exists:
                stats.reviews_with_decision += int(has_decision)
                stats.reviews_without_decision += int(not has_decision)
            if not apply:
                continue
            market_item_id = _ensure_article_item(
                conn, row, item_kind=item_kind, store_kind=store_kind
            )
            if not review_exists:
                _insert_review(
                    conn,
                    market_item_id=market_item_id,
                    task="production",
                    source=str(row["source"]),
                    source_item_id=str(row["item_id"]),
                    store_kind=store_kind,
                    legacy_result_id=legacy_result_id,
                    payload=payload,
                    created_at=str(row["created_at"] or ""),
                    admission=_seen_admission(conn, str(row["source"]), str(row["item_id"])),
                )

    events = list(conn.execute("SELECT * FROM events ORDER BY id"))
    event_items: dict[int, int] = {}
    for event in events:
        event_id = int(event["id"])
        alias_exists = conn.execute(
            """
            SELECT market_item_id FROM market_item_aliases
            WHERE item_kind='event' AND source=? AND legacy_item_id=?
            """,
            (str(event["source"]), str(event_id)),
        ).fetchone()
        item_exists = conn.execute(
            """
            SELECT id,collection_class,processability_status,processability_reason,
                   processing_status,processing_error
            FROM market_items WHERE source=? AND source_item_id=?
            """,
            (str(event["source"]), str(event["source_event_id"])),
        ).fetchone()
        if not item_exists:
            stats.items += 1
        if not alias_exists:
            stats.aliases += 1
        if not apply:
            continue
        raw = _json_dict(event["raw_json"])
        item = item_from_event_mapping(
            {
                "source": event["source"],
                "source_event_id": event["source_event_id"],
                "event_type": event["event_type"],
                "title": event["title"],
                "summary": event["summary"],
                "full_text": event["full_text"],
                "url": event["url"],
                "published_at": event["published_at"],
                "first_seen_at": event["first_seen_at"],
                "symbols": _json_list(event["symbols_json"]),
                "themes": _json_list(event["themes_json"]),
                "raw": raw,
            }
        )
        market_item_id = upsert_market_item(
            conn,
            item,
            collection_class="baseline" if event["baseline_only"] else "live",
            processability_status="succeeded",
            processing_status="succeeded",
            legacy_store_kind="events",
            legacy_store_id=str(event_id),
        )
        if item_exists:
            conn.execute(
                """
                UPDATE market_items
                SET collection_class=?, processability_status=?, processability_reason=?,
                    processing_status=?, processing_error=?
                WHERE id=?
                """,
                (
                    item_exists["collection_class"],
                    item_exists["processability_status"],
                    item_exists["processability_reason"],
                    item_exists["processing_status"],
                    item_exists["processing_error"],
                    market_item_id,
                ),
            )
        ensure_market_item_alias(
            conn,
            market_item_id,
            item_kind="event",
            source=str(event["source"]),
            legacy_item_id=str(event_id),
            legacy_store_kind="events",
        )
        event_items[event_id] = market_item_id

    event_analysis_rows = list(
        conn.execute(
            """
            SELECT a.*, e.source, e.source_event_id, e.event_type, e.title,
                   e.summary, e.full_text, e.url, e.published_at, e.first_seen_at,
                   e.symbols_json, e.themes_json, e.raw_json, e.baseline_only
            FROM event_analyses a JOIN events e ON e.id = a.event_id
            ORDER BY a.id
            """
        )
    )
    for row in event_analysis_rows:
        legacy_result_id = str(row["id"])
        review_exists = conn.execute(
            "SELECT 1 FROM market_reviews WHERE legacy_store_kind='event_analyses' AND legacy_store_id=?",
            (legacy_result_id,),
        ).fetchone()
        if not review_exists and legacy_result_id in placeholder_reviews:
            review_exists = (1,)
        if review_exists:
            stats.skipped_existing += 1
            continue
        stats.reviews += 1
        stats.add_store("event_analyses")
        payload = _json_dict(row["analysis_json"])
        payload["_legacy_row"] = {
            key: row[key]
            for key in row.keys()
            if key not in {"analysis_json", "raw_json", "full_text"}
        }
        has_decision = decision_result_from_payload(payload) is not None
        stats.reviews_with_decision += int(has_decision)
        stats.reviews_without_decision += int(not has_decision)
        if not apply:
            continue
        event_id = int(row["event_id"])
        market_item_id = event_items.get(event_id)
        if market_item_id is None:
            mapped = conn.execute(
                """
                SELECT market_item_id FROM market_item_aliases
                WHERE item_kind='event' AND source=? AND legacy_item_id=?
                """,
                (str(row["source"]), str(event_id)),
            ).fetchone()
            if not mapped:
                raise RuntimeError(f"event alias missing during migration: {event_id}")
            market_item_id = int(mapped[0])
        _insert_review(
            conn,
            market_item_id=market_item_id,
            task=str(row["task"] or "portfolio_event"),
            source=str(row["source"]),
            source_item_id=str(row["source_event_id"]),
            store_kind="event_analyses",
            legacy_result_id=legacy_result_id,
            payload=payload,
            created_at=str(row["created_at"] or ""),
            admission=_seen_admission(conn, str(row["source"]), str(row["source_event_id"])),
            make_current=False,
        )

    if apply:
        for event_id, task, latest_id in conn.execute(
            """
            SELECT event_id,task,MAX(id)
            FROM event_analyses
            GROUP BY event_id,task
            """
        ):
            mapped = event_items.get(int(event_id))
            if mapped is None:
                alias = conn.execute(
                    """
                    SELECT market_item_id FROM market_item_aliases
                    WHERE item_kind='event' AND legacy_item_id=?
                    LIMIT 1
                    """,
                    (str(event_id),),
                ).fetchone()
                mapped = int(alias[0]) if alias else None
            if mapped is None:
                continue
            conn.execute(
                """
                UPDATE market_reviews SET is_current=0
                WHERE market_item_id=? AND task=? AND legacy_store_kind='event_analyses'
                """,
                (mapped, str(task)),
            )
            made_current = conn.execute(
                """
                UPDATE market_reviews SET is_current=1
                WHERE market_item_id=? AND task=?
                  AND legacy_store_kind='event_analyses' AND legacy_store_id=?
                """,
                (mapped, str(task), str(latest_id)),
            ).rowcount
            if made_current != 1:
                raise RuntimeError(
                    f"latest event analysis review missing: event={event_id} task={task} analysis={latest_id}"
                )
        linked = conn.execute(
            """
            UPDATE deliveries
            SET market_item_id = (
                SELECT a.market_item_id FROM market_item_aliases a
                WHERE a.item_kind='event' AND a.legacy_item_id=CAST(deliveries.event_id AS TEXT)
                LIMIT 1
            ), attempted_at = COALESCE(NULLIF(attempted_at, ''), sent_at)
            WHERE event_id IS NOT NULL AND market_item_id IS NULL
              AND EXISTS (
                SELECT 1 FROM market_item_aliases a
                WHERE a.item_kind='event' AND a.legacy_item_id=CAST(deliveries.event_id AS TEXT)
              )
            """
        ).rowcount
        stats.event_deliveries_linked = max(0, int(linked))
        conn.execute(
            """
            INSERT INTO source_state(source, state_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at
            """,
            (MIGRATION_VERSION, json_dumps(stats.to_dict()), utc_now()),
        )
    return stats


def migrate_legacy_results(conn: sqlite3.Connection, *, apply: bool) -> MigrationStats:
    if not apply:
        return _migrate_legacy_results(conn, apply=False)
    conn.execute("BEGIN IMMEDIATE")
    try:
        stats = _migrate_legacy_results(conn, apply=True)
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        init_db(args.db).close()
    elif not args.db.exists():
        parser.error(f"database does not exist: {args.db}")
    with connect_sqlite(args.db) as conn:
        stats = migrate_legacy_results(conn, apply=args.apply)
        if not args.apply:
            conn.rollback()
    print(json.dumps({"mode": "apply" if args.apply else "preview", **stats.to_dict()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
