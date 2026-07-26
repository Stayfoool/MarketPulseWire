"""Bounded read-only projection for the authenticated LLM decision view.

The production DecisionResult remains authoritative in ``market_reviews``.
Private audit files may contain complete model requests and responses; this
module exposes only bounded rule assessments and metadata for Web rendering.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT_DIR = ROOT / "reports" / "llm-decision-audits"
WEB_PROJECTION_VERSION = "llm-decision-web-v1"
MAX_REASON_CHARS = 800
MAX_QUOTE_CHARS = 300
MAX_ERROR_CHARS = 500
MAX_REFERENCES_PER_RULE = 3
MAX_ASSESSMENTS_PER_CALL = 32
MAX_AUDIT_FILES = 5000


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _response_payload(call: dict[str, Any]) -> dict[str, Any] | None:
    response = call.get("response")
    if not isinstance(response, dict) or not isinstance(response.get("content"), str):
        return None
    try:
        payload = json.loads(response["content"])
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _user_payload(call: dict[str, Any]) -> dict[str, Any]:
    request = call.get("request")
    messages = request.get("messages") if isinstance(request, dict) else None
    if not isinstance(messages, list):
        return {}
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        try:
            payload = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _segments_by_id(call: dict[str, Any]) -> dict[str, dict[str, str]]:
    payload = _user_payload(call)
    segments = payload.get("article_segments")
    if not isinstance(segments, list):
        return {}
    result: dict[str, dict[str, str]] = {}
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        segment_id = _text(segment.get("id"), 40)
        text = _text(segment.get("text"), MAX_QUOTE_CHARS)
        if segment_id and text:
            result[segment_id] = {
                "evidence_id": segment_id,
                "field": _text(segment.get("field"), 40),
                "quote": text,
            }
    return result


def _references(value: Any, segments: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_id in value[:MAX_REFERENCES_PER_RULE]:
        segment_id = _text(raw_id, 40)
        segment = segments.get(segment_id)
        if not segment or segment_id in seen:
            continue
        seen.add(segment_id)
        result.append(dict(segment))
    return result


def _assessment(row: Any, segments: dict[str, dict[str, str]]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    judgement = _text(row.get("judgement"), 30)
    if judgement not in {"matched", "not_matched", "uncertain"}:
        return None
    result: dict[str, Any] = {
        "rule_id": _text(row.get("rule_id"), 120),
        "judgement": judgement,
    }
    if judgement == "matched":
        action = _text(row.get("action"), 30)
        if action in {"push", "daily", "archive"}:
            result["action"] = action
        result["reason"] = _text(row.get("reason"), MAX_REASON_CHARS)
        result["evidence"] = _references(row.get("evidence_ids"), segments)
    elif judgement == "uncertain":
        result["reason"] = _text(row.get("reason"), MAX_REASON_CHARS)
        result["counterevidence"] = _references(row.get("counterevidence_ids"), segments)
    return result


def _call_projection(call: Any, index: int) -> dict[str, Any]:
    call = call if isinstance(call, dict) else {}
    validation = call.get("validation") if isinstance(call.get("validation"), dict) else {}
    payload = _response_payload(call)
    segments = _segments_by_id(call)
    raw_results = payload.get("rule_results") if isinstance(payload, dict) else []
    assessments: list[dict[str, Any]] = []
    if isinstance(raw_results, list):
        for raw in raw_results[:MAX_ASSESSMENTS_PER_CALL]:
            item = _assessment(raw, segments)
            if item is not None:
                assessments.append(item)
    errors = validation.get("validation_errors")
    return {
        "call_index": index,
        "rule_assessments": assessments,
        "validation_errors": [_text(item, MAX_ERROR_CHARS) for item in errors[:8]]
        if isinstance(errors, list)
        else [],
        "evidence_reference_count": int(validation.get("evidence_reference_count") or 0),
        "evidence_character_count": int(validation.get("evidence_character_count") or 0),
    }


def _decision_projection(decision: dict[str, Any]) -> dict[str, Any]:
    assessments: list[dict[str, Any]] = []
    hits = decision.get("rule_hits") if isinstance(decision.get("rule_hits"), list) else []
    for hit in hits[:MAX_ASSESSMENTS_PER_CALL]:
        if not isinstance(hit, dict):
            continue
        evidence: list[dict[str, str]] = []
        raw_evidence = hit.get("evidence")
        if isinstance(raw_evidence, list):
            for entry in raw_evidence[:MAX_REFERENCES_PER_RULE]:
                if not isinstance(entry, dict):
                    continue
                quote = _text(entry.get("quote"), MAX_QUOTE_CHARS)
                if quote:
                    evidence.append({"evidence_id": _text(entry.get("evidence_id"), 40), "quote": quote})
        assessments.append(
            {
                "rule_id": _text(hit.get("rule_id"), 120),
                "judgement": "matched",
                "action": _text(hit.get("decision_action"), 30),
                "reason": _text(hit.get("reason"), MAX_REASON_CHARS),
                "evidence": evidence,
            }
        )
    return {
        "action": _text(decision.get("action"), 30),
        "reason": _text(decision.get("brief_reason") or decision.get("reason"), MAX_REASON_CHARS),
        "rule_assessments": assessments,
    }


def build_web_projection(audit: dict[str, Any]) -> dict[str, Any]:
    """Build a safe projection without returning request/response content."""
    status = _text(audit.get("evaluation_status"), 40) or "unknown"
    decision = audit.get("decision") if isinstance(audit.get("decision"), dict) else None
    projection: dict[str, Any] = {
        "version": WEB_PROJECTION_VERSION,
        "evaluation_status": status,
        "failure_reason": _text(audit.get("failure_reason"), MAX_ERROR_CHARS),
        "decision": _decision_projection(decision) if decision else None,
        "calls": [],
    }
    model_audit = audit.get("model_audit") if isinstance(audit.get("model_audit"), dict) else {}
    calls = model_audit.get("calls") if isinstance(model_audit.get("calls"), list) else []
    projection["calls"] = [_call_projection(call, index) for index, call in enumerate(calls[:4], start=1)]
    return projection


def write_web_projection(path: Path, *, apply: bool = False) -> bool:
    """Add/update one private audit projection; return whether it changed."""
    if path.is_symlink() or not path.is_file():
        return False
    if (os.stat(path).st_mode & 0o777) != 0o600:
        raise PermissionError(f"audit file must be mode 0600: {path}")
    try:
        audit = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(audit, dict):
        return False
    existing_projection = audit.get("web_projection")
    model_audit = audit.get("model_audit") if isinstance(audit.get("model_audit"), dict) else {}
    raw_calls = model_audit.get("calls") if isinstance(model_audit.get("calls"), list) else []
    if isinstance(existing_projection, dict) and not raw_calls and not audit.get("decision"):
        # Retain the bounded history after the sensitive request/response cleanup.
        return False
    projection = build_web_projection(audit)
    if audit.get("web_projection") == projection:
        return False
    if not apply:
        return True
    audit["web_projection"] = projection
    temporary = path.with_name(f".{path.name}.web-projection.tmp")
    temporary.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)
    return True


def load_web_projections(audit_dir: Path = DEFAULT_AUDIT_DIR) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if not audit_dir.is_dir() or (os.stat(audit_dir).st_mode & 0o777) != 0o700:
        return result
    for path in sorted(audit_dir.glob("llm-decision-audit-*.json"))[:MAX_AUDIT_FILES]:
        if path.is_symlink() or not path.is_file() or (os.stat(path).st_mode & 0o777) != 0o600:
            continue
        try:
            audit = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(audit, dict) or not isinstance(audit.get("web_projection"), dict):
            continue
        try:
            review_id = int(audit.get("market_review_id") or 0)
        except (TypeError, ValueError):
            continue
        if review_id <= 0:
            continue
        projection = dict(audit["web_projection"])
        projection["generated_at"] = _text(audit.get("generated_at"), 64)
        projection["evaluation_status"] = _text(audit.get("evaluation_status"), 40)
        projection["model"] = _text(audit.get("model"), 200)
        projection["provider"] = _text(audit.get("provider"), 200)
        projection["rule_version"] = _text(audit.get("llm_decision_rule_version"), 120)
        projection["prompt_version"] = _text(audit.get("prompt_version"), 120)
        result[review_id].append(projection)
    for attempts in result.values():
        attempts.sort(key=lambda item: str(item.get("generated_at") or ""))
    return result


def _json_dict_value(value: Any) -> dict[str, Any]:
    return _json_dict(value)


def _decision_assessments(decision: dict[str, Any]) -> list[dict[str, Any]]:
    return _decision_projection(decision).get("rule_assessments", [])


def llm_decision_rows(
    conn: sqlite3.Connection,
    *,
    start_utc: str,
    end_utc: str,
    action: str = "",
    status: str = "",
    source: str = "",
    query: str = "",
    limit: int = 200,
    audit_dir: Path = DEFAULT_AUDIT_DIR,
) -> list[dict[str, Any]]:
    """Read current admitted reviews and merge only stored audit projections."""
    limit = max(1, min(int(limit or 200), 500))
    rows = conn.execute(
        """
        SELECT m.id AS market_item_id, m.source, m.source_item_id, m.title, m.url,
               m.published_at, m.first_seen_at, m.content_type,
               r.id AS market_review_id, r.review_status, r.decision_action,
               r.importance, r.decision_json, r.created_at, r.completed_at,
               COALESCE((SELECT a.source FROM market_item_aliases a
                         WHERE a.market_item_id=m.id ORDER BY a.created_at LIMIT 1), m.source) AS display_source,
               COALESCE((SELECT a.legacy_item_id FROM market_item_aliases a
                         WHERE a.market_item_id=m.id ORDER BY a.created_at LIMIT 1), m.source_item_id) AS display_item_id
        FROM market_reviews r
        JOIN market_items m ON m.id=r.market_item_id
        WHERE r.is_current=1 AND r.admission_status='admitted'
          AND datetime(COALESCE(NULLIF(r.created_at,''),m.first_seen_at)) >= datetime(?)
          AND datetime(COALESCE(NULLIF(r.created_at,''),m.first_seen_at)) < datetime(?)
        ORDER BY datetime(COALESCE(NULLIF(r.created_at,''),m.first_seen_at)) DESC, r.id DESC
        LIMIT 5000
        """,
        (start_utc, end_utc),
    ).fetchall()
    audit_map = load_web_projections(audit_dir)
    source_lower = source.strip().lower()
    query_lower = query.strip().lower()
    action = action.strip().lower()
    status = status.strip().lower()
    result: list[dict[str, Any]] = []
    for row in rows:
        decision = _json_dict_value(row["decision_json"])
        review_id = int(row["market_review_id"])
        attempts = audit_map.get(review_id, [])
        final_action = str(row["decision_action"] or "")
        model_status = "completed" if str(row["review_status"] or "") == "succeeded" and final_action else "pending"
        if attempts and model_status != "completed":
            model_status = str(attempts[-1].get("evaluation_status") or model_status)
        display_source = str(row["display_source"] or row["source"] or "")
        searchable = " ".join((display_source, str(row["title"] or ""), str(row["source_item_id"] or ""))).lower()
        if action and final_action != action:
            continue
        if status and model_status != status:
            continue
        if source_lower and source_lower not in display_source.lower():
            continue
        if query_lower and query_lower not in searchable:
            continue
        result.append(
            {
                "market_item_id": int(row["market_item_id"]),
                "market_review_id": review_id,
                "item_kind": str(row["content_type"] or "unknown"),
                "source": display_source,
                "source_item_id": str(row["display_item_id"] or row["source_item_id"] or ""),
                "title": str(row["title"] or ""),
                "url": str(row["url"] or ""),
                "published_at": str(row["published_at"] or ""),
                "review_created_at": str(row["created_at"] or ""),
                "review_status": str(row["review_status"] or ""),
                "decision_action": final_action,
                "importance": str(row["importance"] or ""),
                "model_status": model_status,
                "decision_reason": _text(decision.get("brief_reason") or decision.get("reason"), MAX_REASON_CHARS),
                "rule_assessments": _decision_assessments(decision),
                "attempts": attempts,
                "uncertain_attempts": sum(1 for item in attempts if item.get("evaluation_status") == "uncertain"),
                "audit_available": bool(attempts),
            }
        )
        if len(result) >= limit:
            break
    return result


def llm_decision_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    actions = Counter(str(row.get("decision_action") or "missing") for row in rows)
    statuses = Counter(str(row.get("model_status") or "unknown") for row in rows)
    uncertain_attempts = sum(int(row.get("uncertain_attempts") or 0) for row in rows)
    recovered = sum(1 for row in rows if row.get("decision_action") and row.get("uncertain_attempts"))
    failed_retryable = sum(1 for row in rows if row.get("review_status") == "failed_retryable")
    return {
        "rows": len(rows),
        "actions": dict(actions),
        "statuses": dict(statuses),
        "uncertain_attempts": uncertain_attempts,
        "uncertain_then_completed": recovered,
        "current_failed_retryable": failed_retryable,
    }
