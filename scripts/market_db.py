"""SQLite schema for the unified market monitor."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from db_utils import SEEN_ITEM_LIFECYCLE_COLUMNS, connect_sqlite


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

CREATE TABLE IF NOT EXISTS seen_items (
    source TEXT NOT NULL,
    item_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    published_at TEXT,
    first_seen_at TEXT NOT NULL,
    collection_class TEXT NOT NULL DEFAULT 'legacy_unclassified',
    processability_status TEXT NOT NULL DEFAULT 'legacy_unclassified',
    processability_reason TEXT,
    admission_status TEXT NOT NULL DEFAULT 'legacy_unclassified',
    admission_reason TEXT,
    admission_matched_families_json TEXT NOT NULL DEFAULT '[]',
    admission_evidence_json TEXT NOT NULL DEFAULT '[]',
    admission_config_version TEXT,
    admission_rule_contract_version TEXT,
    admission_evaluated_at TEXT,
    result_event_id INTEGER,
    processing_status TEXT NOT NULL DEFAULT 'legacy_unclassified',
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
    legacy_store_kind TEXT,
    legacy_store_id TEXT,
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
    importance TEXT,
    decision_json TEXT,
    interpretation_json TEXT,
    application_revision TEXT,
    legacy_store_kind TEXT,
    legacy_store_id TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY(market_item_id) REFERENCES market_items(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_market_reviews_current
    ON market_reviews(market_item_id, task) WHERE is_current = 1;
CREATE UNIQUE INDEX IF NOT EXISTS idx_market_reviews_legacy
    ON market_reviews(legacy_store_kind, legacy_store_id)
    WHERE legacy_store_kind IS NOT NULL AND legacy_store_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_market_reviews_created ON market_reviews(created_at);
CREATE INDEX IF NOT EXISTS idx_market_reviews_admission ON market_reviews(admission_status, created_at);
CREATE INDEX IF NOT EXISTS idx_market_reviews_action ON market_reviews(decision_action, created_at);

CREATE TRIGGER IF NOT EXISTS trg_seen_items_market_insert
AFTER INSERT ON seen_items
BEGIN
    INSERT INTO market_items (
        source, source_item_id, dedupe_key, content_type, title, summary, full_text,
        url, published_at, first_seen_at, content_hash, collection_class,
        processability_status, processability_reason, processing_status,
        processing_error, legacy_store_kind, legacy_store_id, created_at, updated_at
    ) VALUES (
        NEW.source, NEW.item_id, NEW.source || ':' || NEW.item_id, 'unknown',
        NEW.title, NEW.summary, '', NEW.url, NEW.published_at, NEW.first_seen_at,
        'seen:' || NEW.source || ':' || NEW.item_id, NEW.collection_class,
        NEW.processability_status, NEW.processability_reason, NEW.processing_status,
        NEW.processing_error, 'seen_items', NEW.source || ':' || NEW.item_id,
        NEW.first_seen_at, COALESCE(NEW.lifecycle_updated_at, NEW.first_seen_at)
    )
    ON CONFLICT(source, source_item_id) DO UPDATE SET
        title = excluded.title, summary = excluded.summary, url = excluded.url,
        published_at = excluded.published_at,
        collection_class = excluded.collection_class,
        processability_status = excluded.processability_status,
        processability_reason = excluded.processability_reason,
        processing_status = excluded.processing_status,
        processing_error = excluded.processing_error,
        updated_at = excluded.updated_at;
END;

CREATE TRIGGER IF NOT EXISTS trg_seen_items_market_update
AFTER UPDATE ON seen_items
BEGIN
    UPDATE market_items SET
        title = NEW.title,
        summary = NEW.summary,
        url = NEW.url,
        published_at = NEW.published_at,
        collection_class = NEW.collection_class,
        processability_status = NEW.processability_status,
        processability_reason = NEW.processability_reason,
        processing_status = NEW.processing_status,
        processing_error = NEW.processing_error,
        updated_at = COALESCE(NEW.lifecycle_updated_at, NEW.first_seen_at)
    WHERE source = NEW.source AND source_item_id = NEW.item_id;
END;

CREATE TABLE IF NOT EXISTS seen_sources (
    source TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL
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

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    full_text TEXT,
    url TEXT,
    published_at TEXT,
    first_seen_at TEXT NOT NULL,
    symbols_json TEXT,
    themes_json TEXT,
    raw_json TEXT,
    content_hash TEXT NOT NULL,
    baseline_only INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source, source_event_id)
);

CREATE INDEX IF NOT EXISTS idx_events_seen ON events(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_events_source_type ON events(source, event_type);

CREATE TABLE IF NOT EXISTS event_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    task TEXT NOT NULL,
    model TEXT,
    importance TEXT,
    classification TEXT,
    direction TEXT,
    impact_duration TEXT,
    should_push INTEGER NOT NULL DEFAULT 0,
    analysis_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(event_id) REFERENCES events(id)
);

CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    market_item_id INTEGER,
    market_review_id INTEGER,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    decision_action TEXT,
    attempted_at TEXT,
    sent_at TEXT,
    error TEXT,
    payload_json TEXT,
    FOREIGN KEY(event_id) REFERENCES events(id),
    FOREIGN KEY(market_item_id) REFERENCES market_items(id),
    FOREIGN KEY(market_review_id) REFERENCES market_reviews(id)
);

CREATE TABLE IF NOT EXISTS market_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feedback_event_id TEXT NOT NULL UNIQUE,
    item_kind TEXT NOT NULL,
    source TEXT NOT NULL,
    item_id TEXT NOT NULL,
    delivery_id INTEGER,
    label TEXT NOT NULL,
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
    FOREIGN KEY(delivery_id) REFERENCES deliveries(id),
    FOREIGN KEY(supersedes_id) REFERENCES market_feedback(id)
);

CREATE INDEX IF NOT EXISTS idx_market_feedback_item
ON market_feedback(item_kind, source, item_id, operator_id, clicked_at_us, id);

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

CREATE TABLE IF NOT EXISTS rule_config_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    changed_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    changes_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rule_config_audit_changed
ON rule_config_audit(changed_at);

CREATE TABLE IF NOT EXISTS jygs_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    run_slot TEXT NOT NULL,
    symbol TEXT,
    name TEXT NOT NULL,
    latest_price TEXT,
    change_pct TEXT,
    board_status TEXT,
    limit_up_time TEXT,
    themes TEXT,
    reason TEXT,
    full_text TEXT,
    url TEXT,
    raw_json TEXT,
    content_hash TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    UNIQUE(trade_date, run_slot, symbol, content_hash)
);

CREATE TABLE IF NOT EXISTS stock_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    symbol TEXT,
    name TEXT NOT NULL,
    trade_date TEXT,
    prediction_direction TEXT,
    duration_bucket TEXT,
    confidence TEXT,
    thesis TEXT,
    invalidation TEXT,
    model TEXT,
    prompt_version TEXT,
    analysis_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER NOT NULL,
    as_of_date TEXT NOT NULL,
    return_1d REAL,
    return_3d REAL,
    return_5d REAL,
    return_10d REAL,
    max_drawdown REAL,
    limit_up_days INTEGER,
    outcome_json TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(prediction_id, as_of_date),
    FOREIGN KEY(prediction_id) REFERENCES stock_predictions(id)
);

CREATE TABLE IF NOT EXISTS prediction_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER NOT NULL,
    review_type TEXT NOT NULL,
    review_text TEXT NOT NULL,
    model TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(prediction_id) REFERENCES stock_predictions(id)
);

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

CREATE TABLE IF NOT EXISTS relation_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_table TEXT,
    source_id TEXT,
    symbol TEXT NOT NULL,
    symbol_name TEXT,
    related_symbol TEXT NOT NULL,
    related_name TEXT,
    relation_type TEXT NOT NULL,
    impact_direction TEXT,
    theme TEXT,
    reason TEXT,
    confidence TEXT,
    source TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    raw_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS market_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id TEXT NOT NULL UNIQUE,
    skill_name TEXT NOT NULL,
    source_name TEXT,
    source_path TEXT,
    kind TEXT NOT NULL DEFAULT 'relevance_map',
    date TEXT,
    topic TEXT,
    themes_json TEXT,
    trigger_text TEXT,
    chain_text TEXT,
    affected_text TEXT,
    strength TEXT,
    nature TEXT,
    key_insight TEXT,
    constraints_json TEXT,
    hard_evidence_json TEXT,
    staleness TEXT,
    verified_outcome TEXT,
    match_text TEXT,
    raw_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_table TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source TEXT NOT NULL,
    source_item_id TEXT,
    title TEXT NOT NULL,
    url TEXT,
    published_at TEXT,
    first_seen_at TEXT,
    pushed_at TEXT,
    importance TEXT,
    incremental_classification TEXT,
    direction TEXT,
    confidence TEXT,
    thesis TEXT,
    invalidation TEXT,
    model TEXT,
    prompt_version TEXT,
    raw_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_table, source_id)
);

CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);
CREATE INDEX IF NOT EXISTS idx_signals_source ON signals(source, importance);

CREATE TABLE IF NOT EXISTS signal_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    target_key TEXT NOT NULL,
    symbol TEXT,
    name TEXT,
    market TEXT,
    target_role TEXT,
    expected_direction TEXT,
    expected_horizon TEXT,
    relation_type TEXT,
    relation_reason TEXT,
    confidence TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(signal_id, target_key, target_role),
    FOREIGN KEY(signal_id) REFERENCES signals(id)
);

CREATE INDEX IF NOT EXISTS idx_signal_targets_symbol ON signal_targets(symbol);

CREATE TABLE IF NOT EXISTS signal_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    evidence_type TEXT NOT NULL,
    text TEXT NOT NULL,
    url TEXT,
    source TEXT,
    observed_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(signal_id, evidence_type, text),
    FOREIGN KEY(signal_id) REFERENCES signals(id)
);

CREATE TABLE IF NOT EXISTS signal_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    target_id INTEGER,
    symbol TEXT,
    as_of_date TEXT NOT NULL,
    return_1d REAL,
    return_3d REAL,
    return_5d REAL,
    return_10d REAL,
    return_20d REAL,
    excess_return_1d REAL,
    excess_return_3d REAL,
    excess_return_5d REAL,
    excess_return_10d REAL,
    excess_return_20d REAL,
    max_drawdown REAL,
    max_runup REAL,
    volume_change REAL,
    limit_up_days INTEGER,
    matched_direction TEXT,
    outcome_status TEXT NOT NULL,
    outcome_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(signal_id, symbol, as_of_date),
    FOREIGN KEY(signal_id) REFERENCES signals(id),
    FOREIGN KEY(target_id) REFERENCES signal_targets(id)
);

CREATE INDEX IF NOT EXISTS idx_signal_outcomes_symbol ON signal_outcomes(symbol, as_of_date);

CREATE TABLE IF NOT EXISTS signal_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    target_id INTEGER,
    symbol TEXT,
    review_type TEXT NOT NULL,
    verdict TEXT,
    error_type TEXT,
    review_text TEXT NOT NULL,
    lessons_json TEXT,
    model TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(signal_id) REFERENCES signals(id),
    FOREIGN KEY(target_id) REFERENCES signal_targets(id)
);

CREATE INDEX IF NOT EXISTS idx_signal_reviews_signal ON signal_reviews(signal_id, review_type);
CREATE INDEX IF NOT EXISTS idx_signal_reviews_created ON signal_reviews(created_at);

CREATE TABLE IF NOT EXISTS source_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    window_days INTEGER NOT NULL,
    signal_count INTEGER NOT NULL DEFAULT 0,
    hit_rate REAL,
    avg_excess_return REAL,
    median_reaction_lag REAL,
    false_positive_rate REAL,
    stale_news_rate REAL,
    score_json TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(source, window_days)
);

CREATE TABLE IF NOT EXISTS web_evidence_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger_module TEXT NOT NULL,
    trigger_source TEXT,
    trigger_item_id TEXT,
    trigger_reason TEXT,
    mode TEXT NOT NULL DEFAULT 'realtime',
    provider TEXT NOT NULL,
    query_json TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_web_evidence_runs_trigger
    ON web_evidence_runs(trigger_module, trigger_source, trigger_item_id);
CREATE INDEX IF NOT EXISTS idx_web_evidence_runs_created ON web_evidence_runs(created_at);

CREATE TABLE IF NOT EXISTS web_evidence_docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    query_type TEXT NOT NULL,
    query TEXT NOT NULL,
    result_rank INTEGER NOT NULL,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    title TEXT,
    source TEXT,
    published_at TEXT,
    retrieved_at TEXT NOT NULL,
    snippet TEXT,
    extracted_text TEXT,
    claim TEXT,
    evidence_type TEXT NOT NULL,
    stance TEXT NOT NULL,
    source_quality TEXT,
    score REAL,
    content_hash TEXT NOT NULL,
    raw_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, canonical_url, query_type),
    FOREIGN KEY(run_id) REFERENCES web_evidence_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_web_evidence_docs_run ON web_evidence_docs(run_id);
CREATE INDEX IF NOT EXISTS idx_web_evidence_docs_url ON web_evidence_docs(canonical_url);
"""


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if column not in table_columns(conn, table):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise


def db_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)).fetchone()
    return row is not None


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply additive migrations for existing personal SQLite databases."""
    for column, definition in SEEN_ITEM_LIFECYCLE_COLUMNS.items():
        add_column_if_missing(conn, "seen_items", column, definition)
    stock_relation_columns = {
        "symbol_name": "TEXT",
        "related_name": "TEXT",
        "impact_direction": "TEXT",
        "theme": "TEXT",
        "relation_strength": "TEXT",
        "valid_from": "TEXT",
        "valid_to": "TEXT",
        "last_review_verdict": "TEXT",
        "hit_count": "INTEGER NOT NULL DEFAULT 0",
        "miss_count": "INTEGER NOT NULL DEFAULT 0",
        "enabled": "INTEGER NOT NULL DEFAULT 1",
        "raw_json": "TEXT",
    }
    for column, definition in stock_relation_columns.items():
        add_column_if_missing(conn, "stock_relations", column, definition)
    article_review_columns = {
        "skeptic_json": "TEXT",
        "pre_skeptic_importance": "TEXT",
    }
    if db_table_exists(conn, "article_reviews"):
        for column, definition in article_review_columns.items():
            add_column_if_missing(conn, "article_reviews", column, definition)
    official_review_columns = {
        "skeptic_json": "TEXT",
        "pre_skeptic_importance": "TEXT",
    }
    if db_table_exists(conn, "official_news_reviews"):
        for column, definition in official_review_columns.items():
            add_column_if_missing(conn, "official_news_reviews", column, definition)
    delivery_columns = {
        "market_item_id": "INTEGER",
        "market_review_id": "INTEGER",
        "decision_action": "TEXT",
        "attempted_at": "TEXT",
    }
    for column, definition in delivery_columns.items():
        add_column_if_missing(conn, "deliveries", column, definition)
    add_column_if_missing(conn, "signal_reviews", "target_id", "INTEGER")
    add_column_if_missing(conn, "signal_reviews", "symbol", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_seen_items_first_seen ON seen_items(first_seen_at)")
    if db_table_exists(conn, "article_reviews"):
        conn.execute("CREATE INDEX IF NOT EXISTS idx_article_reviews_created ON article_reviews(created_at)")
    if db_table_exists(conn, "official_news_reviews"):
        conn.execute("CREATE INDEX IF NOT EXISTS idx_official_news_created ON official_news_reviews(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stock_relations_symbol ON stock_relations(symbol, enabled)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stock_relations_related ON stock_relations(related_symbol, enabled)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_relation_suggestions_status ON relation_suggestions(status, updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_skills_enabled ON market_skills(enabled, updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_skills_kind ON market_skills(kind, enabled)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signal_reviews_signal ON signal_reviews(signal_id, review_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signal_reviews_symbol ON signal_reviews(symbol, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signal_reviews_created ON signal_reviews(created_at)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_web_evidence_runs_trigger "
        "ON web_evidence_runs(trigger_module, trigger_source, trigger_item_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_web_evidence_runs_created ON web_evidence_runs(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_web_evidence_docs_run ON web_evidence_docs(run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_web_evidence_docs_url ON web_evidence_docs(canonical_url)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rule_alert_dedup_rule_created "
        "ON rule_alert_dedup(rule_id, created_at)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rule_config_audit_changed ON rule_config_audit(changed_at)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_market_feedback_item "
        "ON market_feedback(item_kind, source, item_id, operator_id, clicked_at_us, id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_feedback_received ON market_feedback(received_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_feedback_label ON market_feedback(label, received_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_items_seen ON market_items(first_seen_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_items_source ON market_items(source, source_item_id)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_market_items_processing "
        "ON market_items(processing_status, updated_at)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_market_reviews_current "
        "ON market_reviews(market_item_id, task) WHERE is_current = 1"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_market_reviews_legacy "
        "ON market_reviews(legacy_store_kind, legacy_store_id) "
        "WHERE legacy_store_kind IS NOT NULL AND legacy_store_id IS NOT NULL"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_reviews_created ON market_reviews(created_at)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_market_reviews_admission "
        "ON market_reviews(admission_status, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_market_reviews_action "
        "ON market_reviews(decision_action, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_deliveries_market_item "
        "ON deliveries(market_item_id, attempted_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_deliveries_market_review "
        "ON deliveries(market_review_id, attempted_at)"
    )
    marker = conn.execute(
        "SELECT 1 FROM source_state WHERE source = 'market_storage_backfill_v1' LIMIT 1"
    ).fetchone()
    if marker is None:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT OR IGNORE INTO market_items (
                source, source_item_id, dedupe_key, content_type, title, summary,
                full_text, url, published_at, first_seen_at, content_hash,
                collection_class, processability_status, processability_reason,
                processing_status, processing_error, legacy_store_kind,
                legacy_store_id, created_at, updated_at
            )
            SELECT source, item_id, source || ':' || item_id, 'unknown', title,
                   summary, '', url, published_at, first_seen_at,
                   'seen:' || source || ':' || item_id, collection_class,
                   processability_status, processability_reason, processing_status,
                   processing_error, 'seen_items', source || ':' || item_id,
                   first_seen_at, COALESCE(lifecycle_updated_at, first_seen_at)
            FROM seen_items
            """
        )
        conn.execute(
            "INSERT INTO source_state(source, state_json, updated_at) VALUES (?, ?, ?)",
            ("market_storage_backfill_v1", '{"status":"completed"}', now),
        )


def init_db(path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_sqlite(path)
    conn.executescript(SCHEMA)
    migrate_schema(conn)
    conn.commit()
    return conn


def main() -> int:
    with init_db() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    print("initialized tables:")
    for (name,) in tables:
        print(f"- {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
