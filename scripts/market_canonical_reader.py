"""Read current unified market information from its direct identities."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from market_card_view import card_targets


def _json_dict(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _decision(row: sqlite3.Row) -> dict[str, Any]:
    return _json_dict(row["decision_json"])


def _interpretation(row: sqlite3.Row) -> dict[str, Any]:
    return _json_dict(row["interpretation_json"])


def _result_projection(row: sqlite3.Row) -> dict[str, Any]:
    result: dict[str, Any] = {}
    decision = _decision(row)
    interpretation = _interpretation(row)
    if decision:
        result["decision_result"] = decision
    if interpretation:
        result["interpretation_result"] = interpretation
    return result


def _review_select() -> str:
    return """
        m.*, r.id AS review_id, r.review_status, r.admission_status,
        r.decision_action, r.decision_json,
        r.interpretation_json, r.created_at AS review_created_at,
        r.completed_at AS review_completed_at,
        (SELECT d.status FROM deliveries d
         WHERE d.market_item_id=m.id ORDER BY d.id DESC LIMIT 1) delivery_status,
        (SELECT d.id FROM deliveries d
         WHERE d.market_item_id=m.id AND d.status='sent'
         ORDER BY d.id DESC LIMIT 1) delivery_id,
        (SELECT MAX(d.sent_at) FROM deliveries d
         WHERE d.market_item_id=m.id AND d.status='sent') delivery_sent_at
    """


def _selected_item_rows(
    conn: sqlite3.Connection,
    *,
    start_utc: str,
    end_utc: str,
    time_basis: str,
    include_baseline: bool,
    source: str = "",
) -> list[sqlite3.Row]:
    seen_time = "COALESCE(NULLIF(r.created_at,''),m.first_seen_at)"
    display_time = (
        f"COALESCE(NULLIF(m.published_at,''),{seen_time})"
        if time_basis == "published"
        else seen_time
    )
    source_clause = "AND m.source = ?" if source else ""
    params: list[Any] = [start_utc, end_utc]
    if source:
        params.append(source)
    params.append(int(include_baseline))
    return list(
        conn.execute(
            f"""
            SELECT {_review_select()}
            FROM market_items m
            LEFT JOIN market_reviews r ON r.id = (
                SELECT current.id FROM market_reviews current
                WHERE current.market_item_id=m.id AND current.is_current=1
                ORDER BY current.id DESC LIMIT 1
            )
            WHERE datetime({display_time}) >= datetime(?)
              AND datetime({display_time}) < datetime(?)
              {source_clause}
              AND (r.id IS NOT NULL OR (?=1 AND m.collection_class='baseline'))
              AND (r.id IS NULL OR r.admission_status='admitted')
            ORDER BY datetime({display_time}) DESC, m.id DESC
            LIMIT 5000
            """,
            params,
        )
    )


def canonical_market_rows(
    conn: sqlite3.Connection,
    *,
    start_utc: str,
    end_utc: str,
    time_basis: str,
    include_baseline: bool,
    source: str = "",
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in _selected_item_rows(
        conn,
        start_utc=start_utc,
        end_utc=end_utc,
        time_basis=time_basis,
        include_baseline=include_baseline,
        source=source,
    ):
        baseline = str(row["collection_class"] or "") == "baseline"
        review_status = str(row["review_status"] or "")
        if baseline and not include_baseline:
            continue
        published_at = str(row["published_at"] or "")
        seen_at = str(row["review_created_at"] or row["first_seen_at"] or "")
        if baseline and not review_status:
            result.append(
                {
                    "market_item_id": int(row["id"]),
                    "source": str(row["source"]),
                    "source_id": str(row["source"]),
                    "id": str(row["source_item_id"]),
                    "title": str(row["title"] or ""),
                    "summary": str(row["summary"] or "首次采集建立去重基线，未进入决策层。"),
                    "url": str(row["url"] or ""),
                    "published_at": published_at,
                    "seen_at": seen_at,
                    "decision_action": "baseline",
                    "delivery_status": "baseline",
                    "baseline_only": True,
                    "feedback_identity": None,
                }
            )
            continue
        decision = _decision(row)
        interpretation = _interpretation(row)
        sent_at = str(row["delivery_sent_at"] or "")
        related_payload = _result_projection(row)
        result.append(
            {
                "market_item_id": int(row["id"]),
                "source": str(row["source"] or ""),
                "source_id": str(row["source"] or ""),
                "id": str(row["source_item_id"]),
                "title": str(row["title"] or ""),
                "summary": str(
                    interpretation.get("core_content")
                    or row["summary"]
                    or decision.get("reason")
                    or ""
                ),
                "url": str(row["url"] or ""),
                "published_at": published_at,
                "seen_at": seen_at,
                "delivery_status": str(row["delivery_status"] or ("sent" if sent_at else "")),
                "baseline_only": baseline,
                "decision_action": str(row["decision_action"] or ""),
                "decision_reason": str(decision.get("brief_reason") or decision.get("reason") or ""),
                "core_content": str(interpretation.get("core_content") or ""),
                "brief_reason": str(interpretation.get("brief_reason") or ""),
                "related_targets": card_targets(related_payload),
                "feedback_identity": {
                    "market_item_id": int(row["id"]),
                    "delivered": bool(sent_at),
                },
            }
        )
    result.sort(key=lambda item: str(item.get("seen_at") or ""), reverse=True)
    return result[:5000]


def _review_rows(
    conn: sqlite3.Connection,
    *,
    start_utc: str = "",
    end_utc: str = "",
    since: str = "",
) -> list[sqlite3.Row]:
    params: list[Any] = []
    time_clause = ""
    if start_utc and end_utc:
        time_clause = "AND datetime(r.created_at) >= datetime(?) AND datetime(r.created_at) < datetime(?)"
        params.extend((start_utc, end_utc))
    elif since:
        time_clause = """
            AND (datetime(COALESCE(NULLIF(m.published_at,''),r.created_at)) >= datetime(?)
                 OR datetime(m.first_seen_at) >= datetime(?))
        """
        params.extend((since, since))
    return list(
        conn.execute(
            f"""
            SELECT {_review_select()}
            FROM market_items m
            JOIN market_reviews r ON r.id = (
                SELECT current.id FROM market_reviews current
                WHERE current.market_item_id=m.id AND current.is_current=1
                ORDER BY current.id DESC LIMIT 1
            )
            WHERE 1=1 {time_clause}
            ORDER BY r.id DESC
            """,
            params,
        )
    )


def _digest_row(row: sqlite3.Row) -> dict[str, Any]:
    result = _result_projection(row)
    interpretation = _interpretation(row)
    affected = _json_text(card_targets(result))
    return {
        "market_review_id": int(row["review_id"]),
        "source": str(row["source"]),
        "item_id": str(row["source_item_id"]),
        "url": str(row["url"] or ""),
        "title": str(row["title"] or ""),
        "source_module": str(row["source"] or ""),
        "published_at": str(row["published_at"] or ""),
        "decision_action": str(row["decision_action"] or ""),
        "affected_targets_json": str(affected or "[]"),
        "reason": str(_decision(row).get("brief_reason") or _decision(row).get("reason") or ""),
        "daily_summary": str(interpretation.get("core_content") or ""),
        "gate_json": _json_text(result),
        "pushed_at": str(row["delivery_sent_at"] or ""),
        "created_at": str(row["review_created_at"] or ""),
    }


def canonical_digest_rows(
    conn: sqlite3.Connection,
    *,
    start_utc: str,
    end_utc: str,
) -> list[dict[str, Any]]:
    result = [
        projected
        for row in _review_rows(conn, start_utc=start_utc, end_utc=end_utc)
        if not (projected := _digest_row(row))["pushed_at"]
    ]
    action_order = {"daily": 0, "archive": 1}
    grouped: list[dict[str, Any]] = []
    for rank in (0, 1, 2):
        group = [item for item in result if action_order.get(str(item.get("decision_action") or ""), 2) == rank]
        group.sort(
            key=lambda item: (str(item.get("published_at") or ""), str(item.get("created_at") or "")),
            reverse=True,
        )
        grouped.extend(group)
    return grouped


def canonical_feedback_snapshot(
    conn: sqlite3.Connection,
    market_item_id: int,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT r.decision_json,r.application_revision,
               (SELECT d.id FROM deliveries d
                WHERE d.market_item_id=m.id AND d.status='sent'
                ORDER BY d.id DESC LIMIT 1) delivery_id,
               (SELECT d.status FROM deliveries d
                WHERE d.market_item_id=m.id AND d.status='sent'
                ORDER BY d.id DESC LIMIT 1) delivery_status,
               (SELECT d.payload_json FROM deliveries d
                WHERE d.market_item_id=m.id AND d.status='sent'
                ORDER BY d.id DESC LIMIT 1) delivery_payload_json
        FROM market_items m
        JOIN market_reviews r ON r.market_item_id=m.id AND r.is_current=1
        WHERE m.id=?
        ORDER BY r.id DESC LIMIT 1
        """,
        (market_item_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "decision": _json_dict(row[0]),
        "delivery_payload": _json_dict(row[4]),
        "application_revision": str(row[1] or ""),
        "delivery_id": row[2],
        "delivery_status": str(row[3] or ""),
    }


def canonical_delivered_items(conn: sqlite3.Connection, cutoff: str) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    items: list[dict[str, Any]] = []
    for row in _review_rows(conn):
        sent_at = str(row["delivery_sent_at"] or "")
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
        items.append(
            {
                "market_item_id": int(row["id"]),
                "source": str(row["source"]),
                "item_id": str(row["source_item_id"]),
                "title": str(row["title"] or ""),
                "sent_at": sent_at,
                "action": str(row["decision_action"] or ""),
                "rule_ids": list(dict.fromkeys(rule_ids)),
                "version": str(audit.get("decision_version") or audit.get("schema_version") or ""),
            }
        )
    return items
