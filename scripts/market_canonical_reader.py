"""Read unified market storage while preserving existing external identities."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from market_card_view import card_targets
from market_db import MARKET_RESULTS_MIGRATION_VERSION


STORE_FOR_KIND = {
    "article": "article_reviews",
    "official": "official_news_reviews",
    "event": "event_analyses",
}


def _json_dict(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _payload_parts(row: sqlite3.Row) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _json_dict(row["legacy_payload_json"])
    legacy = payload.get("_legacy_row")
    return payload, legacy if isinstance(legacy, dict) else {}


def _payload_without_legacy_row(payload: dict[str, Any]) -> dict[str, Any]:
    clean = dict(payload)
    clean.pop("_legacy_row", None)
    return clean


def _decision(row: sqlite3.Row) -> dict[str, Any]:
    return _json_dict(row["decision_json"])


def _interpretation(row: sqlite3.Row) -> dict[str, Any]:
    return _json_dict(row["interpretation_json"])


def _with_unified_results(payload: dict[str, Any], row: sqlite3.Row) -> dict[str, Any]:
    result = _payload_without_legacy_row(payload)
    decision = _decision(row)
    interpretation = _interpretation(row)
    if decision and not isinstance(result.get("decision_result"), dict):
        result["decision_result"] = decision
    if interpretation and not isinstance(result.get("_interpretation_result"), dict):
        result["_interpretation_result"] = interpretation
    return result


def migration_ready(conn: sqlite3.Connection) -> bool:
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_state'"
    ).fetchone() is None:
        return False
    return conn.execute(
        "SELECT 1 FROM source_state WHERE source = ? LIMIT 1",
        (MARKET_RESULTS_MIGRATION_VERSION,),
    ).fetchone() is not None


def _aliases(conn: sqlite3.Connection, item_ids: list[int]) -> dict[int, list[dict[str, str]]]:
    if not item_ids:
        return {}
    result: dict[int, list[dict[str, str]]] = {}
    for offset in range(0, len(item_ids), 500):
        batch = item_ids[offset : offset + 500]
        placeholders = ",".join("?" for _ in batch)
        for row in conn.execute(
            f"""
            SELECT market_item_id,item_kind,source,legacy_item_id,legacy_store_kind
            FROM market_item_aliases
            WHERE market_item_id IN ({placeholders})
            ORDER BY CASE item_kind WHEN 'event' THEN 0 WHEN 'official' THEN 1 ELSE 2 END,
                     created_at, legacy_item_id
            """,
            batch,
        ):
            result.setdefault(int(row[0]), []).append(
                {
                    "item_kind": str(row[1]),
                    "source": str(row[2]),
                    "legacy_item_id": str(row[3]),
                    "legacy_store_kind": str(row[4]),
                }
            )
    return result


def _preferred_alias(
    aliases: list[dict[str, str]], review_store: str, content_type: str
) -> dict[str, str]:
    expected_kind = {
        "article_reviews": "article",
        "official_news_reviews": "official",
        "event_analyses": "event",
    }.get(review_store, "")
    for alias in aliases:
        if alias["item_kind"] == expected_kind:
            return alias
    if aliases:
        return aliases[0]
    if content_type == "official_news":
        kind = "official"
    elif content_type in {"article", "research_index", "unknown"}:
        kind = "article"
    else:
        kind = "event"
    return {"item_kind": kind, "source": "", "legacy_item_id": "", "legacy_store_kind": ""}


def _selected_item_rows(
    conn: sqlite3.Connection,
    *,
    start_utc: str,
    end_utc: str,
    time_basis: str,
    include_baseline: bool,
) -> list[sqlite3.Row]:
    """Return one display result per item while retaining all result versions in storage."""
    seen_time = (
        "CASE WHEN r.legacy_store_kind IN ('article_reviews','official_news_reviews') "
        "THEN COALESCE(NULLIF(r.created_at,''),m.first_seen_at) ELSE m.first_seen_at END"
    )
    display_time = (
        f"COALESCE(NULLIF(m.published_at,''),{seen_time})"
        if time_basis == "published"
        else seen_time
    )
    return list(
        conn.execute(
            f"""
            SELECT m.*, r.id AS review_id, r.review_status, r.admission_status,
                   r.decision_action, r.importance, r.decision_json,
                   r.interpretation_json, r.legacy_payload_json,
                   r.legacy_store_kind AS review_store_kind,
                   r.created_at AS review_created_at,
                   r.completed_at AS review_completed_at,
                   (SELECT d.status FROM deliveries d
                    WHERE d.market_item_id=m.id ORDER BY d.id DESC LIMIT 1) delivery_status,
                   (SELECT d.id FROM deliveries d
                    WHERE d.market_item_id=m.id AND d.status='sent'
                    ORDER BY d.id DESC LIMIT 1) delivery_id,
                   (SELECT MAX(d.sent_at) FROM deliveries d
                    WHERE d.market_item_id=m.id AND d.status='sent') delivery_sent_at
            FROM market_items m
            LEFT JOIN market_reviews r ON r.id = (
                SELECT current.id
                FROM market_reviews current
                WHERE current.market_item_id=m.id AND current.is_current=1
                ORDER BY current.id DESC
                LIMIT 1
            )
            WHERE datetime({display_time}) >= datetime(?)
              AND datetime({display_time}) < datetime(?)
              AND (
                  r.id IS NOT NULL
                  OR EXISTS (
                      SELECT 1 FROM market_item_aliases event_alias
                      WHERE event_alias.market_item_id=m.id AND event_alias.item_kind='event'
                  )
                  OR (?=1 AND m.collection_class='baseline')
              )
              AND (
                  r.id IS NULL
                  OR r.admission_status IN ('admitted','legacy_unclassified')
                  OR r.legacy_store_kind IN ('article_reviews','official_news_reviews','event_analyses')
              )
            ORDER BY datetime({display_time}) DESC, m.id DESC
            LIMIT 5000
            """,
            (start_utc, end_utc, int(include_baseline)),
        )
    )


def _sent_at(row: sqlite3.Row, legacy: dict[str, Any]) -> str:
    return str(row["delivery_sent_at"] or legacy.get("pushed_at") or "")


def _display_seen_at(row: sqlite3.Row, item_kind: str) -> str:
    if item_kind in {"article", "official"} and row["review_created_at"]:
        return str(row["review_created_at"])
    return str(row["first_seen_at"] or row["review_created_at"] or "")


def _event_kind(row: sqlite3.Row, legacy: dict[str, Any]) -> str:
    return str(legacy.get("event_type") or row["content_type"] or "event")


def canonical_event_rows(
    conn: sqlite3.Connection,
    *,
    start_utc: str,
    end_utc: str,
    time_basis: str,
    include_baseline: bool,
) -> list[dict[str, Any]]:
    rows = _selected_item_rows(
        conn,
        start_utc=start_utc,
        end_utc=end_utc,
        time_basis=time_basis,
        include_baseline=include_baseline,
    )
    alias_map = _aliases(conn, [int(row["id"]) for row in rows])
    result: list[dict[str, Any]] = []
    for row in rows:
        aliases = alias_map.get(int(row["id"]), [])
        item_alias = _preferred_alias(
            aliases,
            str(row["review_store_kind"] or ""),
            str(row["content_type"] or ""),
        )
        item_kind = item_alias["item_kind"]
        payload, legacy = _payload_parts(row)
        review_status = str(row["review_status"] or "")
        admission_status = str(row["admission_status"] or "")
        baseline = str(row["collection_class"] or "") == "baseline"
        is_legacy_event_without_review = not review_status and any(
            alias["item_kind"] == "event" for alias in aliases
        )

        compatibility_result = str(row["review_store_kind"] or "") in set(STORE_FOR_KIND.values())
        if (
            review_status
            and admission_status not in {"admitted", "legacy_unclassified"}
            and not compatibility_result
        ):
            continue
        if not review_status and not is_legacy_event_without_review:
            if not (include_baseline and baseline):
                continue
        if baseline and not include_baseline:
            continue

        published_at = str(row["published_at"] or "")
        seen_at = _display_seen_at(row, item_kind)
        if baseline and not review_status and item_kind != "event":
            result.append(
                {
                    "kind": "baseline",
                    "source": str(row["source"]),
                    "source_id": str(row["source"]),
                    "id": str(row["source_item_id"]),
                    "title": str(row["title"] or ""),
                    "summary": str(row["summary"] or "首次采集建立去重基线，未进入决策层。"),
                    "url": str(row["url"] or ""),
                    "published_at": published_at,
                    "seen_at": seen_at,
                    "importance": "",
                    "classification": "仅建立去重基线",
                    "push": False,
                    "delivery_status": "baseline",
                    "baseline_only": True,
                    "feedback_identity": None,
                }
            )
            continue

        decision = _decision(row)
        interpretation = _interpretation(row)
        sent_at = _sent_at(row, legacy)
        source_label = str(legacy.get("source_module") or row["source"] or "")
        legacy_item_id = item_alias["legacy_item_id"] or str(row["source_item_id"])
        if item_kind == "event":
            kind = _event_kind(row, legacy)
        elif item_kind == "official":
            kind = "official_news"
        else:
            kind = "article"
        related_payload = _with_unified_results(payload, row)
        result.append(
            {
                "kind": kind,
                "source": source_label,
                "source_id": str(row["source"]),
                "id": legacy_item_id,
                "title": str(row["title"] or ""),
                "summary": str(
                    interpretation.get("core_content")
                    or legacy.get("daily_summary")
                    or row["summary"]
                    or interpretation.get("brief_reason")
                    or decision.get("reason")
                    or ""
                ),
                "url": str(row["url"] or ""),
                "published_at": published_at,
                "seen_at": seen_at,
                "importance": str(row["importance"] or legacy.get("importance") or ""),
                "classification": str(
                    legacy.get("incremental_classification")
                    or legacy.get("classification")
                    or row["decision_action"]
                    or ""
                ),
                "push": str(row["decision_action"] or "") == "push",
                "delivery_status": str(row["delivery_status"] or ("sent" if sent_at else "")),
                "baseline_only": baseline,
                "decision_action": str(row["decision_action"] or ""),
                "decision_reason": str(decision.get("brief_reason") or decision.get("reason") or ""),
                "core_content": str(interpretation.get("core_content") or ""),
                "brief_reason": str(interpretation.get("brief_reason") or ""),
                "related_targets": card_targets(related_payload),
                "feedback_identity": {
                    "item_kind": item_kind,
                    "source": str(item_alias["source"] or row["source"]),
                    "item_id": legacy_item_id,
                    "delivered": bool(sent_at),
                },
            }
        )
    result.sort(key=lambda item: str(item.get("seen_at") or ""), reverse=True)
    return result[:5000]


def _review_rows_for_kind(
    conn: sqlite3.Connection,
    item_kind: str,
    *,
    start_utc: str = "",
    end_utc: str = "",
    since: str = "",
) -> list[sqlite3.Row]:
    store = STORE_FOR_KIND[item_kind]
    params: list[Any] = [store, item_kind]
    time_clause = ""
    if start_utc and end_utc:
        time_clause = "AND datetime(r.created_at) >= datetime(?) AND datetime(r.created_at) < datetime(?)"
        params.extend((start_utc, end_utc))
    elif since:
        time_clause = """
            AND (
                datetime(COALESCE(NULLIF(m.published_at,''),r.created_at)) >= datetime(?)
                OR datetime(m.first_seen_at) >= datetime(?)
            )
        """
        params.extend((since, since))
    return list(
        conn.execute(
            f"""
            SELECT m.*, a.source AS alias_source, a.legacy_item_id,
                   r.id AS review_id, r.review_status, r.admission_status,
                   r.decision_action, r.importance, r.decision_json,
                   r.interpretation_json, r.legacy_payload_json,
                   r.legacy_store_kind AS review_store_kind,
                   r.created_at AS review_created_at,
                   r.completed_at AS review_completed_at,
                   (SELECT d.status FROM deliveries d WHERE d.market_item_id=m.id
                    ORDER BY d.id DESC LIMIT 1) delivery_status,
                   (SELECT d.id FROM deliveries d WHERE d.market_item_id=m.id AND d.status='sent'
                    ORDER BY d.id DESC LIMIT 1) delivery_id,
                   (SELECT MAX(d.sent_at) FROM deliveries d
                    WHERE d.market_item_id=m.id AND d.status='sent') delivery_sent_at
            FROM market_item_aliases a
            JOIN market_items m ON m.id=a.market_item_id
            JOIN market_reviews r ON r.id = (
                SELECT current.id
                FROM market_reviews current
                WHERE current.market_item_id=m.id
                  AND current.is_current=1
                  AND current.legacy_store_kind=?
                ORDER BY current.id DESC
                LIMIT 1
            )
            WHERE a.item_kind=?
              {time_clause}
            ORDER BY r.id DESC
            """,
            params,
        )
    )


def _article_legacy_row(row: sqlite3.Row) -> dict[str, Any]:
    payload, legacy = _payload_parts(row)
    gate = _with_unified_results(payload, row)
    interpretation = _interpretation(row)
    sent_at = _sent_at(row, legacy)
    affected = legacy.get("affected_targets_json")
    if not affected:
        affected = _json_text(card_targets(gate))
    return {
        "source": str(row["alias_source"] or row["source"]),
        "item_id": str(row["legacy_item_id"]),
        "url": str(row["url"] or legacy.get("url") or ""),
        "title": str(row["title"] or legacy.get("title") or ""),
        "source_module": str(legacy.get("source_module") or row["source"] or ""),
        "published_at": str(row["published_at"] or legacy.get("published_at") or ""),
        "importance": str(row["importance"] or legacy.get("importance") or ""),
        "push_now": int(str(row["decision_action"] or "") == "push"),
        "market_impact": str(legacy.get("market_impact") or payload.get("market_impact") or ""),
        "incremental_classification": str(
            legacy.get("incremental_classification")
            or payload.get("incremental_classification")
            or ""
        ),
        "affected_targets_json": str(affected or "[]"),
        "reason": str(legacy.get("reason") or payload.get("reason") or ""),
        "daily_summary": str(
            legacy.get("daily_summary")
            or payload.get("daily_summary")
            or interpretation.get("core_content")
            or ""
        ),
        "confidence": str(legacy.get("confidence") or payload.get("confidence") or ""),
        "gate_json": _json_text(gate),
        "pushed_at": sent_at,
        "created_at": str(row["review_created_at"] or ""),
    }


def _official_analysis(payload: dict[str, Any], row: sqlite3.Row) -> dict[str, Any]:
    nested = payload.get("analysis")
    analysis = dict(nested) if isinstance(nested, dict) else _payload_without_legacy_row(payload)
    decision = _decision(row)
    interpretation = _interpretation(row)
    if decision and not isinstance(analysis.get("_decision_result"), dict):
        analysis["_decision_result"] = decision
    if interpretation and not isinstance(analysis.get("_interpretation_result"), dict):
        analysis["_interpretation_result"] = interpretation
    return analysis


def _official_legacy_row(row: sqlite3.Row) -> dict[str, Any]:
    payload, legacy = _payload_parts(row)
    analysis = _official_analysis(payload, row)
    interpretation = _interpretation(row)
    return {
        "source": str(row["alias_source"] or row["source"]),
        "item_id": str(row["legacy_item_id"]),
        "url": str(row["url"] or legacy.get("url") or ""),
        "title": str(row["title"] or legacy.get("title") or ""),
        "published_at": str(row["published_at"] or legacy.get("published_at") or ""),
        "importance": str(row["importance"] or legacy.get("importance") or ""),
        "should_push_now": int(str(row["decision_action"] or "") == "push"),
        "reason": str(legacy.get("reason") or payload.get("reason") or ""),
        "daily_summary": str(
            legacy.get("daily_summary")
            or payload.get("daily_summary")
            or interpretation.get("core_content")
            or ""
        ),
        "analysis_json": _json_text(analysis),
        "pushed_at": _sent_at(row, legacy),
        "created_at": str(row["review_created_at"] or ""),
    }


def _event_legacy_row(row: sqlite3.Row) -> dict[str, Any]:
    payload, legacy = _payload_parts(row)
    analysis = _with_unified_results(payload, row)
    return {
        "id": int(row["legacy_item_id"]) if str(row["legacy_item_id"]).isdigit() else row["legacy_item_id"],
        "source": str(row["alias_source"] or row["source"]),
        "source_event_id": str(row["source_item_id"] or ""),
        "event_type": _event_kind(row, legacy),
        "title": str(row["title"] or ""),
        "summary": str(row["summary"] or ""),
        "full_text": str(row["full_text"] or ""),
        "url": str(row["url"] or ""),
        "published_at": str(row["published_at"] or ""),
        "first_seen_at": str(row["first_seen_at"] or ""),
        "symbols_json": str(row["symbols_json"] or "[]"),
        "themes_json": str(row["themes_json"] or "[]"),
        "model": str(legacy.get("model") or payload.get("_model") or ""),
        "importance": str(row["importance"] or legacy.get("importance") or ""),
        "classification": str(legacy.get("classification") or ""),
        "direction": str(legacy.get("direction") or ""),
        "impact_duration": str(legacy.get("impact_duration") or ""),
        "should_push": int(str(row["decision_action"] or "") == "push"),
        "analysis_json": _json_text(analysis),
        "analysis_created_at": str(row["review_created_at"] or ""),
        "pushed_at": _sent_at(row, legacy),
    }


def canonical_digest_rows(
    conn: sqlite3.Connection,
    *,
    item_kind: str,
    start_utc: str,
    end_utc: str,
) -> list[dict[str, Any]]:
    if item_kind not in {"article", "official"}:
        raise ValueError(f"unsupported digest item kind: {item_kind}")
    result: list[dict[str, Any]] = []
    for row in _review_rows_for_kind(
        conn, item_kind, start_utc=start_utc, end_utc=end_utc
    ):
        projected = _article_legacy_row(row) if item_kind == "article" else _official_legacy_row(row)
        if projected["pushed_at"]:
            continue
        result.append(projected)
    importance_order = {"medium": 0, "low": 1}
    # The legacy query sorts timestamps descending within each importance group.
    grouped: list[dict[str, Any]] = []
    for rank in (0, 1, 2):
        group = [item for item in result if importance_order.get(str(item.get("importance") or ""), 2) == rank]
        group.sort(key=lambda item: (str(item.get("published_at") or ""), str(item.get("created_at") or "")), reverse=True)
        grouped.extend(group)
    return grouped


def canonical_signal_rows(
    conn: sqlite3.Connection,
    *,
    item_kind: str,
    since: str,
) -> list[dict[str, Any]]:
    if item_kind not in STORE_FOR_KIND:
        raise ValueError(f"unsupported signal item kind: {item_kind}")
    result: list[dict[str, Any]] = []
    for row in _review_rows_for_kind(conn, item_kind, since=since):
        if item_kind == "article":
            result.append(_article_legacy_row(row))
        elif item_kind == "official":
            result.append(_official_legacy_row(row))
        else:
            result.append(_event_legacy_row(row))
    result.sort(key=lambda item: str(item.get("created_at") or item.get("analysis_created_at") or item.get("first_seen_at") or ""))
    return result


def canonical_feedback_snapshot(
    conn: sqlite3.Connection, item_kind: str, source: str, item_id: str
) -> dict[str, Any] | None:
    store = STORE_FOR_KIND.get(item_kind)
    if not store:
        return None
    row = conn.execute(
        """
        SELECT r.decision_json,r.legacy_payload_json,
               (SELECT d.id FROM deliveries d
                WHERE d.market_item_id=m.id AND d.status='sent'
                ORDER BY d.id DESC LIMIT 1) delivery_id,
               (SELECT d.status FROM deliveries d
                WHERE d.market_item_id=m.id AND d.status='sent'
                ORDER BY d.id DESC LIMIT 1) delivery_status
        FROM market_item_aliases a
        JOIN market_items m ON m.id=a.market_item_id
        JOIN market_reviews r ON r.market_item_id=m.id
                             AND r.is_current=1
                             AND r.legacy_store_kind=?
        WHERE a.item_kind=? AND a.source=? AND a.legacy_item_id=?
        ORDER BY r.id DESC
        LIMIT 1
        """,
        (store, item_kind, source, item_id),
    ).fetchone()
    if not row:
        return None
    payload = _json_dict(row[1])
    legacy = payload.get("_legacy_row") if isinstance(payload.get("_legacy_row"), dict) else {}
    historically_sent = bool(str(legacy.get("pushed_at") or ""))
    return {
        "decision": _json_dict(row[0]),
        "legacy_payload": payload,
        "delivery_id": row[2],
        "delivery_status": str(row[3] or ("sent" if historically_sent else "")),
    }


def canonical_delivered_items(conn: sqlite3.Connection, cutoff: str) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    items: list[dict[str, Any]] = []
    for item_kind in STORE_FOR_KIND:
        for row in _review_rows_for_kind(conn, item_kind):
            payload, legacy = _payload_parts(row)
            sent_at = _sent_at(row, legacy)
            if not sent_at or sent_at < cutoff:
                continue
            decision = _decision(row)
            rule_hits = decision.get("rule_hits") if isinstance(decision.get("rule_hits"), list) else []
            rule_ids = [
                str(hit.get("rule_id") or "")
                for hit in rule_hits
                if isinstance(hit, dict) and hit.get("rule_id")
            ]
            audit = decision.get("audit_json") if isinstance(decision.get("audit_json"), dict) else {}
            version = str(audit.get("decision_version") or audit.get("schema_version") or "")
            items.append(
                {
                    "item_kind": item_kind,
                    "source": str(row["alias_source"] or row["source"]),
                    "item_id": str(row["legacy_item_id"]),
                    "title": str(row["title"] or ""),
                    "sent_at": sent_at,
                    "action": str(row["decision_action"] or ""),
                    "rule_ids": list(dict.fromkeys(rule_ids)),
                    "version": version,
                }
            )
    return items
