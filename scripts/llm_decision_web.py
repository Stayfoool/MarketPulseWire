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
from datetime import datetime, timezone
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
AuditIdentity = tuple[int, int, str, str]


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
    segments = payload.get("source_segments")
    if not isinstance(segments, list):
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
    action = _text(row.get("action"), 30)
    if action in {"push", "daily", "archive"} and "judgement" not in row:
        result: dict[str, Any] = {
            "rule_id": _text(row.get("rule_id"), 120),
            "action": action,
        }
        probability = row.get("probability")
        if isinstance(probability, (int, float)) and not isinstance(probability, bool):
            result["probability"] = round(float(probability), 4)
        if action != "archive":
            result["reason"] = _text(row.get("reason"), MAX_REASON_CHARS)
            result["evidence"] = _references(row.get("evidence_ids"), segments)
        return result

    # Existing private audits retain the preceding judgement-based response format.
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
        assessment = {
            "rule_id": _text(hit.get("rule_id"), 120),
            "action": _text(hit.get("decision_action"), 30),
            "reason": _text(hit.get("reason"), MAX_REASON_CHARS),
            "evidence": evidence,
        }
        probability = hit.get("probability")
        if isinstance(probability, (int, float)) and not isinstance(probability, bool):
            assessment["probability"] = round(float(probability), 4)
        confidence = hit.get("confidence")
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
            assessment["confidence"] = round(float(confidence), 4)
        assessments.append(assessment)
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
        "decision_engine": _text(audit.get("decision_engine"), 30) or "llm",
        "shadow": bool(audit.get("shadow")),
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


def _audit_generated_for_review(generated_at: str, review_created_at: str) -> bool:
    try:
        generated = datetime.fromisoformat(generated_at)
        review_created = datetime.fromisoformat(review_created_at)
    except (TypeError, ValueError):
        return False
    if generated.tzinfo is None or review_created.tzinfo is None:
        return False
    return generated >= review_created


def load_web_projections(audit_dir: Path = DEFAULT_AUDIT_DIR) -> dict[AuditIdentity, list[dict[str, Any]]]:
    result: dict[AuditIdentity, list[dict[str, Any]]] = defaultdict(list)
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
            market_item_id = int(audit.get("market_item_id") or 0)
        except (TypeError, ValueError):
            continue
        source = str(audit.get("source") or "")
        source_item_id = str(audit.get("source_item_id") or "")
        if review_id <= 0 or market_item_id <= 0 or not source.strip() or not source_item_id.strip():
            continue
        projection = dict(audit["web_projection"])
        projection["generated_at"] = _text(audit.get("generated_at"), 64)
        projection["evaluation_status"] = _text(audit.get("evaluation_status"), 40)
        projection["model"] = _text(audit.get("model"), 200)
        projection["provider"] = _text(audit.get("provider"), 200)
        projection["rule_version"] = _text(audit.get("llm_decision_rule_version"), 120)
        projection["prompt_version"] = _text(audit.get("prompt_version"), 120)
        result[(review_id, market_item_id, source, source_item_id)].append(projection)
    for attempts in result.values():
        attempts.sort(key=lambda item: str(item.get("generated_at") or ""))
    return result


def _json_dict_value(value: Any) -> dict[str, Any]:
    return _json_dict(value)


def _decision_assessments(decision: dict[str, Any]) -> list[dict[str, Any]]:
    return _decision_projection(decision).get("rule_assessments", [])


def _filter_values(values: str | list[str]) -> set[str]:
    raw_values = [values] if isinstance(values, str) else values
    return {str(value or "").strip().lower() for value in raw_values if str(value or "").strip()}


def llm_decision_rows(
    conn: sqlite3.Connection,
    *,
    start_utc: str,
    end_utc: str,
    action: str | list[str] = "",
    status: str | list[str] = "",
    source: str | list[str] = "",
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
               r.decision_json, r.created_at, r.completed_at
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
    sources = _filter_values(source)
    query_lower = query.strip().lower()
    actions = _filter_values(action)
    statuses = _filter_values(status)
    result: list[dict[str, Any]] = []
    for row in rows:
        decision = _json_dict_value(row["decision_json"])
        review_id = int(row["market_review_id"])
        audit_identity = (
            review_id,
            int(row["market_item_id"]),
            str(row["source"] or ""),
            str(row["source_item_id"] or ""),
        )
        review_created_at = str(row["created_at"] or "")
        attempts = [
            attempt
            for attempt in audit_map.get(audit_identity, [])
            if _audit_generated_for_review(str(attempt.get("generated_at") or ""), review_created_at)
        ]
        final_action = str(row["decision_action"] or "")
        review_status = str(row["review_status"] or "")
        model_status = "completed" if review_status == "succeeded" and final_action else "pending"
        if review_status == "insufficient_evidence":
            model_status = "insufficient_evidence"
        elif attempts and model_status != "completed":
            model_status = str(attempts[-1].get("evaluation_status") or model_status)
        display_source = str(row["source"] or "")
        searchable = " ".join((display_source, str(row["title"] or ""), str(row["source_item_id"] or ""))).lower()
        if actions and final_action.lower() not in actions:
            continue
        if statuses and model_status.lower() not in statuses:
            continue
        if sources:
            row_sources = (display_source.lower(), str(row["source"] or "").lower())
            if not any(source in row_source for source in sources for row_source in row_sources):
                continue
        if query_lower and query_lower not in searchable:
            continue
        result.append(
            {
                "market_item_id": int(row["market_item_id"]),
                "market_review_id": review_id,
                "source": display_source,
                "source_item_id": str(row["source_item_id"] or ""),
                "title": str(row["title"] or ""),
                "url": str(row["url"] or ""),
                "published_at": str(row["published_at"] or ""),
                "review_created_at": str(row["created_at"] or ""),
                "review_status": review_status,
                "decision_action": final_action,
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
    insufficient_evidence = sum(1 for row in rows if row.get("review_status") == "insufficient_evidence")
    return {
        "rows": len(rows),
        "actions": dict(actions),
        "statuses": dict(statuses),
        "uncertain_attempts": uncertain_attempts,
        "uncertain_then_completed": recovered,
        "current_failed_retryable": failed_retryable,
        "current_insufficient_evidence": insufficient_evidence,
    }


DEFAULT_JEV_SHADOW_DIR = ROOT / "reports" / "jev-shadow"
JEV_SHADOW_FEEDBACK_FILENAME = "jev-shadow-feedback.jsonl"
JEV_SHADOW_FEEDBACK_WINNERS = ("production", "jev", "both_bad")
MAX_JEV_SHADOW_NOTE_CHARS = 200
MAX_JEV_SHADOW_ROWS = 5000
MAX_JEV_SHADOW_DETAIL_ROWS = 500
MAX_JEV_SHADOW_RULE_ROWS = 24


def load_jev_shadow_rows(
    shadow_dir: Path = DEFAULT_JEV_SHADOW_DIR,
    *,
    start_day: str = "",
    end_day: str = "",
    limit: int = 2000,
) -> list[dict[str, Any]]:
    """Read bounded Jev shadow comparison rows (no article content, no raw model output)."""
    if not shadow_dir.is_dir():
        return []
    start = str(start_day or "").strip()[:10]
    end = str(end_day or "").strip()[:10]
    rows: list[dict[str, Any]] = []
    for path in sorted(shadow_dir.glob("jev-shadow-*.jsonl")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            day = str(row.get("generated_at") or "")[:10]
            if start and day and day < start:
                continue
            if end and day and day > end:
                continue
            rows.append(row)
    rows.sort(key=lambda item: str(item.get("generated_at") or ""))
    return rows[: max(1, min(int(limit or 2000), MAX_JEV_SHADOW_ROWS))]


def _rate(agree: int, total: int) -> float | None:
    return round(agree / total, 4) if total else None


JEV_SHADOW_KINDS = {"", "missed_push", "extra_push", "mismatch"}


def jev_shadow_details(
    rows: list[dict[str, Any]],
    *,
    item_meta: dict[int, dict[str, Any]],
    kind: str = "",
    limit: int = 200,
    feedback: dict[tuple[int, int], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Bounded per-item shadow detail; article bodies stay out, only DB metadata joins in."""
    limit = max(1, min(int(limit or 200), MAX_JEV_SHADOW_DETAIL_ROWS))
    wanted = str(kind or "").strip().lower()
    if wanted not in JEV_SHADOW_KINDS:
        wanted = ""
    details: list[dict[str, Any]] = []
    for row in rows:
        if len(details) >= limit:
            break
        comparison = row.get("comparison") if isinstance(row.get("comparison"), dict) else {}
        jev = row.get("jev") if isinstance(row.get("jev"), dict) else {}
        production = row.get("production") if isinstance(row.get("production"), dict) else {}
        if wanted:
            flag = {
                "missed_push": bool(comparison.get("missed_push")),
                "extra_push": bool(comparison.get("extra_push")),
                "mismatch": comparison.get("item_agree") is False,
            }[wanted]
            if not flag:
                continue
        item_id = int(row.get("market_item_id") or 0)
        meta = item_meta.get(item_id, {}) if item_id else {}
        raw_choices = jev.get("rule_choices") if isinstance(jev.get("rule_choices"), list) else []
        rule_choices = []
        for choice in raw_choices[:MAX_JEV_SHADOW_RULE_ROWS]:
            if not isinstance(choice, dict):
                continue
            entry: dict[str, Any] = {
                "rule_id": _text(choice.get("rule_id"), 120),
                "action": _text(choice.get("action"), 30),
            }
            probability = choice.get("probability")
            if isinstance(probability, (int, float)) and not isinstance(probability, bool):
                entry["probability"] = round(float(probability), 4)
            confidence = choice.get("confidence")
            if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
                entry["confidence"] = round(float(confidence), 4)
            rule_choices.append(entry)
        raw_actions = (
            row.get("production_rule_actions")
            if isinstance(row.get("production_rule_actions"), dict)
            else {}
        )
        production_rule_actions = {
            _text(rule_id, 120): _text(action, 30)
            for rule_id, action in list(raw_actions.items())[:MAX_JEV_SHADOW_RULE_ROWS]
        }
        production_cost = production.get("cost_cny")
        jev_cost = jev.get("cost_cny")
        details.append(
            {
                "generated_at": _text(row.get("generated_at"), 64),
                "market_item_id": item_id,
                "market_review_id": int(row.get("market_review_id") or 0),
                "source": _text(row.get("source"), 120),
                "source_item_id": _text(row.get("source_item_id"), 120),
                "title": _text(meta.get("title"), 200),
                "url": _text(meta.get("url"), 500),
                "published_at": _text(meta.get("published_at"), 40),
                "production_action": _text(production.get("action"), 30),
                "production_model": _text(production.get("model"), 200),
                "production_cost_cny": (
                    round(float(production_cost), 6)
                    if isinstance(production_cost, (int, float)) and not isinstance(production_cost, bool)
                    else None
                ),
                "jev_status": _text(jev.get("status"), 40),
                "jev_action": _text(jev.get("action"), 30),
                "jev_cost_cny": (
                    round(float(jev_cost), 6)
                    if isinstance(jev_cost, (int, float)) and not isinstance(jev_cost, bool)
                    else None
                ),
                "action_pair": _text(comparison.get("action_pair"), 60),
                "item_agree": comparison.get("item_agree"),
                "missed_push": bool(comparison.get("missed_push")),
                "extra_push": bool(comparison.get("extra_push")),
                "rule_agree": comparison.get("rule_agree"),
                "rule_total": comparison.get("rule_total"),
                "jev_rule_choices": rule_choices,
                "production_rule_actions": production_rule_actions,
                "feedback": (feedback or {}).get((item_id, int(row.get("market_review_id") or 0))),
                "error": _text(row.get("error"), MAX_ERROR_CHARS),
            }
        )
    return details


def _feedback_key(market_item_id: Any, market_review_id: Any) -> tuple[int, int] | None:
    try:
        key = (int(market_item_id or 0), int(market_review_id or 0))
    except (TypeError, ValueError):
        return None
    return key if key[0] > 0 and key[1] > 0 else None


def load_jev_shadow_feedback(
    shadow_dir: Path = DEFAULT_JEV_SHADOW_DIR,
) -> dict[tuple[int, int], dict[str, Any]]:
    """Latest user evaluation per shadow row; later entries override earlier ones."""
    path = shadow_dir / JEV_SHADOW_FEEDBACK_FILENAME
    if not path.is_file():
        return {}
    result: dict[tuple[int, int], dict[str, Any]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("winner") not in JEV_SHADOW_FEEDBACK_WINNERS:
            continue
        key = _feedback_key(row.get("market_item_id"), row.get("market_review_id"))
        if key is None:
            continue
        result[key] = {
            "winner": str(row["winner"]),
            "note": _text(row.get("note"), MAX_JEV_SHADOW_NOTE_CHARS),
            "feedback_at": _text(row.get("feedback_at"), 64),
        }
    return result


def append_jev_shadow_feedback(
    *,
    market_item_id: int,
    market_review_id: int,
    winner: str,
    note: str = "",
    shadow_dir: Path = DEFAULT_JEV_SHADOW_DIR,
) -> dict[str, Any]:
    """Append one bounded user evaluation; never rewrites earlier entries."""
    if winner not in JEV_SHADOW_FEEDBACK_WINNERS:
        raise ValueError(f"评估结论只允许：{'/'.join(JEV_SHADOW_FEEDBACK_WINNERS)}")
    key = _feedback_key(market_item_id, market_review_id)
    if key is None:
        raise ValueError("缺少有效的 market_item_id / market_review_id")
    row = {
        "contract_version": "jev-shadow-feedback-v1",
        "feedback_at": datetime.now(timezone.utc).isoformat(),
        "market_item_id": key[0],
        "market_review_id": key[1],
        "winner": winner,
        "note": " ".join(str(note or "").split())[:MAX_JEV_SHADOW_NOTE_CHARS],
    }
    shadow_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(shadow_dir, 0o700)
    path = shadow_dir / JEV_SHADOW_FEEDBACK_FILENAME
    if not path.exists():
        path.write_text("", encoding="utf-8")
        os.chmod(path, 0o600)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.chmod(path, 0o600)
    return row


def jev_shadow_feedback_summary(
    feedback: dict[tuple[int, int], dict[str, Any]],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate latest evaluations, split by the dangerous mismatch kinds."""
    by_kind: dict[str, Counter] = {winner: Counter() for winner in JEV_SHADOW_FEEDBACK_WINNERS}
    matched = 0
    for row in rows:
        key = _feedback_key(row.get("market_item_id"), row.get("market_review_id"))
        entry = feedback.get(key) if key else None
        if not entry:
            continue
        matched += 1
        winner = str(entry.get("winner") or "")
        comparison = row.get("comparison") if isinstance(row.get("comparison"), dict) else {}
        by_kind[winner]["total"] += 1
        if comparison.get("missed_push"):
            by_kind[winner]["missed_push"] += 1
        if comparison.get("extra_push"):
            by_kind[winner]["extra_push"] += 1
        if comparison.get("item_agree") is False:
            by_kind[winner]["mismatch"] += 1
    return {
        "total": matched,
        "by_winner": {winner: counts["total"] for winner, counts in by_kind.items()},
        "by_kind": {
            winner: {
                "missed_push": counts["missed_push"],
                "extra_push": counts["extra_push"],
                "mismatch": counts["mismatch"],
            }
            for winner, counts in by_kind.items()
        },
    }


def jev_shadow_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate Jev shadow comparison rows into bounded Web-facing statistics."""
    completed = [
        row
        for row in rows
        if isinstance(row.get("jev"), dict) and row["jev"].get("status") == "completed"
    ]
    comparisons = [row for row in rows if isinstance(row.get("comparison"), dict) and row["comparison"]]
    action_pairs = Counter(
        str(row["comparison"].get("action_pair") or "unknown") for row in comparisons
    )
    rule_agree = sum(int(row["comparison"].get("rule_agree") or 0) for row in comparisons)
    rule_total = sum(int(row["comparison"].get("rule_total") or 0) for row in comparisons)
    confidences = [
        float(entry.get("confidence"))
        for row in completed
        for entry in (row.get("jev") or {}).get("rule_choices", [])
        if isinstance(entry, dict)
        and isinstance(entry.get("confidence"), (int, float))
        and not isinstance(entry.get("confidence"), bool)
    ]
    production_costs = [
        float(row["production"].get("cost_cny"))
        for row in rows
        if isinstance(row.get("production"), dict)
        and row["production"].get("cost_cny") is not None
    ]
    jev_costs = [
        float(row["jev"].get("cost_cny"))
        for row in rows
        if isinstance(row.get("jev"), dict) and row["jev"].get("cost_cny") is not None
    ]
    return {
        "rows": len(rows),
        "completed": len(completed),
        "errors": len(rows) - len(completed),
        "item_agree": {
            "agree": sum(1 for row in comparisons if row["comparison"].get("item_agree")),
            "total": len(comparisons),
            "rate": _rate(
                sum(1 for row in comparisons if row["comparison"].get("item_agree")),
                len(comparisons),
            ),
        },
        "action_pairs": dict(sorted(action_pairs.items())),
        "rule_agree": {"agree": rule_agree, "total": rule_total, "rate": _rate(rule_agree, rule_total)},
        "missed_push": sum(1 for row in comparisons if row["comparison"].get("missed_push")),
        "extra_push": sum(1 for row in comparisons if row["comparison"].get("extra_push")),
        "avg_jev_confidence": (
            round(sum(confidences) / len(confidences), 4) if confidences else None
        ),
        "cost": {
            "production_cny": round(sum(production_costs), 6) if production_costs else None,
            "jev_cny": round(sum(jev_costs), 6) if jev_costs else None,
            "production_priced_rows": len(production_costs),
            "jev_priced_rows": len(jev_costs),
        },
    }
