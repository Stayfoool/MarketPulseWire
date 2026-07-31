"""SQLite schema for the unified market monitor."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from db_utils import connect_sqlite


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "surveil.sqlite3"


SCHEMA = """
CREATE TABLE IF NOT EXISTS source_state (
    source TEXT PRIMARY KEY,
    state_json TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_health (
    monitor TEXT NOT NULL,
    source TEXT NOT NULL,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_success_at TEXT,
    last_failure_at TEXT,
    last_error TEXT,
    last_alerted_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (monitor, source)
);

CREATE TABLE IF NOT EXISTS x_stream_health (
    issue_key TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    failure_count INTEGER NOT NULL DEFAULT 0,
    first_failed_at TEXT,
    last_failed_at TEXT,
    last_error TEXT,
    last_alerted_at TEXT,
    last_recovered_at TEXT
);

CREATE TABLE IF NOT EXISTS seen_items (
    source TEXT NOT NULL,
    item_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    published_at TEXT,
    first_seen_at TEXT NOT NULL,
    collection_class TEXT NOT NULL DEFAULT 'live',
    processability_status TEXT NOT NULL DEFAULT 'pending',
    processability_reason TEXT,
    admission_status TEXT NOT NULL DEFAULT 'pending',
    admission_reason TEXT,
    admission_matched_families_json TEXT NOT NULL DEFAULT '[]',
    admission_evidence_json TEXT NOT NULL DEFAULT '[]',
    admission_config_version TEXT,
    admission_rule_contract_version TEXT,
    admission_evaluated_at TEXT,
    result_market_item_id INTEGER,
    processing_status TEXT NOT NULL DEFAULT 'not_applicable',
    processing_error TEXT,
    processed_at TEXT,
    lifecycle_updated_at TEXT,
    PRIMARY KEY (source, item_id)
);

CREATE INDEX IF NOT EXISTS idx_seen_items_first_seen ON seen_items(first_seen_at);

CREATE TABLE IF NOT EXISTS market_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    source_category TEXT,
    publisher_role TEXT,
    collector TEXT,
    content_type TEXT NOT NULL DEFAULT 'unknown',
    title TEXT NOT NULL DEFAULT '',
    summary TEXT,
    full_text TEXT,
    url TEXT,
    published_at TEXT,
    first_seen_at TEXT NOT NULL,
    symbols_json TEXT NOT NULL DEFAULT '[]',
    themes_json TEXT NOT NULL DEFAULT '[]',
    raw_json TEXT NOT NULL DEFAULT '{}',
    access_note TEXT,
    content_hash TEXT NOT NULL,
    collection_class TEXT NOT NULL DEFAULT 'live',
    processability_status TEXT NOT NULL DEFAULT 'pending',
    processability_reason TEXT,
    processing_status TEXT NOT NULL DEFAULT 'pending',
    processing_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source, source_item_id)
);

CREATE INDEX IF NOT EXISTS idx_market_items_seen ON market_items(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_market_items_source ON market_items(source, source_item_id);
CREATE INDEX IF NOT EXISTS idx_market_items_processing ON market_items(processing_status, updated_at);

CREATE TABLE IF NOT EXISTS market_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_item_id INTEGER NOT NULL,
    task TEXT NOT NULL DEFAULT 'production',
    run_key TEXT NOT NULL UNIQUE,
    is_current INTEGER NOT NULL DEFAULT 1,
    review_status TEXT NOT NULL,
    admission_status TEXT NOT NULL,
    admission_reason TEXT,
    admission_matched_families_json TEXT NOT NULL DEFAULT '[]',
    admission_evidence_json TEXT NOT NULL DEFAULT '[]',
    admission_config_version TEXT,
    admission_rule_contract_version TEXT,
    admission_json TEXT NOT NULL DEFAULT '{}',
    decision_action TEXT,
    decision_json TEXT,
    interpretation_json TEXT,
    application_revision TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY(market_item_id) REFERENCES market_items(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_market_reviews_current
    ON market_reviews(market_item_id, task) WHERE is_current = 1;
CREATE INDEX IF NOT EXISTS idx_market_reviews_created ON market_reviews(created_at);
CREATE INDEX IF NOT EXISTS idx_market_reviews_admission ON market_reviews(admission_status, created_at);
CREATE INDEX IF NOT EXISTS idx_market_reviews_action ON market_reviews(decision_action, created_at);

CREATE TABLE IF NOT EXISTS seen_sources (
    source TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seen_posts (
    source TEXT NOT NULL,
    post_id TEXT NOT NULL,
    url TEXT NOT NULL,
    text TEXT NOT NULL,
    published_at TEXT,
    first_seen_at TEXT NOT NULL,
    delivery_status TEXT NOT NULL DEFAULT 'pending',
    delivered_at TEXT,
    delivery_error TEXT,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source, post_id)
);

CREATE TABLE IF NOT EXISTS trendforce_page_seen_items (
    item_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    first_source TEXT NOT NULL,
    first_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stocks (
    symbol TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    full_name TEXT,
    exchange TEXT,
    industry TEXT,
    concepts_json TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_holdings (
    symbol TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    full_name TEXT,
    aliases_json TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    raw_json TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_item_id INTEGER NOT NULL,
    market_review_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    decision_action TEXT NOT NULL,
    attempted_at TEXT,
    sent_at TEXT,
    error TEXT,
    payload_json TEXT,
    FOREIGN KEY(market_item_id) REFERENCES market_items(id),
    FOREIGN KEY(market_review_id) REFERENCES market_reviews(id)
);

CREATE INDEX IF NOT EXISTS idx_deliveries_market_item
ON deliveries(market_item_id, attempted_at);

CREATE INDEX IF NOT EXISTS idx_deliveries_market_review
ON deliveries(market_review_id, attempted_at);

CREATE TABLE IF NOT EXISTS market_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feedback_event_id TEXT NOT NULL UNIQUE,
    market_item_id INTEGER,
    delivery_id INTEGER,
    label TEXT NOT NULL,
    active_labels_json TEXT,
    reason_tags_json TEXT NOT NULL DEFAULT '[]',
    note TEXT,
    operator_id TEXT NOT NULL,
    message_id TEXT,
    chat_id TEXT,
    decision_action TEXT,
    rule_ids_json TEXT NOT NULL DEFAULT '[]',
    delivery_status TEXT,
    decision_version TEXT,
    clicked_at_us INTEGER NOT NULL,
    received_at TEXT NOT NULL,
    supersedes_id INTEGER,
    raw_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(market_item_id) REFERENCES market_items(id),
    FOREIGN KEY(delivery_id) REFERENCES deliveries(id),
    FOREIGN KEY(supersedes_id) REFERENCES market_feedback(id)
);

CREATE INDEX IF NOT EXISTS idx_market_feedback_item
ON market_feedback(market_item_id, operator_id, clicked_at_us, id);

CREATE INDEX IF NOT EXISTS idx_market_feedback_received
ON market_feedback(received_at);

CREATE INDEX IF NOT EXISTS idx_market_feedback_label
ON market_feedback(label, received_at);

CREATE TABLE IF NOT EXISTS rule_alert_dedup (
    dedup_key TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL,
    status TEXT NOT NULL,
    first_source TEXT NOT NULL,
    first_item_id TEXT NOT NULL,
    first_title TEXT NOT NULL,
    first_published_at TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rule_alert_dedup_rule_created
ON rule_alert_dedup(rule_id, created_at);

CREATE TABLE IF NOT EXISTS stock_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    symbol_name TEXT,
    related_symbol TEXT NOT NULL,
    related_name TEXT,
    relation_type TEXT NOT NULL,
    impact_direction TEXT,
    theme TEXT,
    reason TEXT,
    confidence TEXT,
    relation_strength TEXT,
    valid_from TEXT,
    valid_to TEXT,
    last_review_verdict TEXT,
    hit_count INTEGER NOT NULL DEFAULT 0,
    miss_count INTEGER NOT NULL DEFAULT 0,
    source TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    raw_json TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(symbol, related_symbol, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_stock_relations_symbol
ON stock_relations(symbol, enabled);

CREATE INDEX IF NOT EXISTS idx_stock_relations_related
ON stock_relations(related_symbol, enabled);

"""


def init_db(path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_sqlite(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with init_db(args.db) as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    print("initialized tables:")
    for (name,) in tables:
        print(f"- {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
