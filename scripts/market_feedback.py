"""Auditable human feedback for delivered market information."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from db_utils import connect_sqlite
from market_db import DEFAULT_DB_PATH
from market_item import decision_result_from_payload


FEEDBACK_LABELS = {
    "high_value": "特别有用",
    "duplicate": "重复",
    "invalid": "无效",
}
FEEDBACK_REASON_LABELS = {
    "useful_not_urgent": "有用但不紧急",
    "stale": "旧闻",
    "no_increment": "无新增事实",
    "missing_subject": "主体缺失/付费诱饵",
    "irrelevant": "与持仓无关",
    "weak_evidence": "证据不足",
    "wrong_attribution": "归因错误",
    "wrong_interpretation": "解读错误",
}
TOKEN_VERSION = 1


class FeedbackError(ValueError):
    """Rejected feedback input that is safe to show to the operator."""


@dataclass(frozen=True)
class FeedbackIdentity:
    item_kind: str
    source: str
    item_id: str


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def feedback_token_secret() -> str:
    return os.getenv("FEISHU_FEEDBACK_TOKEN_SECRET", "").strip()


def build_feedback_token(identity: FeedbackIdentity, *, secret: str, issued_at: int | None = None) -> str:
    if not secret:
        raise FeedbackError("FEISHU_FEEDBACK_TOKEN_SECRET 未配置")
    payload = {
        "v": TOKEN_VERSION,
        "k": identity.item_kind,
        "s": identity.source,
        "i": identity.item_id,
        "t": int(issued_at if issued_at is not None else time.time()),
    }
    encoded = _b64encode(_json_bytes(payload))
    signature = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_b64encode(signature)}"


def parse_feedback_token(token: str, *, secret: str) -> FeedbackIdentity:
    if not secret:
        raise FeedbackError("FEISHU_FEEDBACK_TOKEN_SECRET 未配置")
    try:
        encoded, supplied_signature = str(token or "").split(".", 1)
        expected = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64decode(supplied_signature)):
            raise FeedbackError("反馈标识签名无效")
        payload = json.loads(_b64decode(encoded).decode("utf-8"))
    except FeedbackError:
        raise
    except Exception as exc:  # noqa: BLE001 - malformed external callback payload
        raise FeedbackError("反馈标识格式无效") from exc
    if payload.get("v") != TOKEN_VERSION:
        raise FeedbackError("反馈标识版本不受支持")
    identity = FeedbackIdentity(
        item_kind=str(payload.get("k") or "").strip(),
        source=str(payload.get("s") or "").strip(),
        item_id=str(payload.get("i") or "").strip(),
    )
    if identity.item_kind not in {"article", "official", "event", "test"}:
        raise FeedbackError("反馈对象类型无效")
    if not identity.source or not identity.item_id:
        raise FeedbackError("反馈对象标识不完整")
    return identity


def allowed_operator_ids(raw: str | None = None) -> set[str]:
    value = os.getenv("FEISHU_FEEDBACK_ALLOWED_OPEN_IDS", "") if raw is None else raw
    return {part.strip() for part in value.replace("；", ",").replace(";", ",").split(",") if part.strip()}


def feedback_actions(identity: FeedbackIdentity, *, secret: str | None = None) -> dict[str, Any]:
    token = build_feedback_token(identity, secret=secret if secret is not None else feedback_token_secret())
    actions = []
    for label, title, button_type in (
        ("high_value", "特别有用", "primary"),
        ("duplicate", "重复", "default"),
        ("invalid", "无效", "danger"),
    ):
        actions.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": title},
                "type": button_type,
                "value": {"feedback_token": token, "label": label},
            }
        )
    actions.append(
        {
            "tag": "overflow",
            "options": [
                {
                    "text": {"tag": "plain_text", "content": title},
                    # Feishu overflow-option values are strings, unlike button values.
                    "value": json.dumps(
                        {"feedback_token": token, "label": "invalid", "reason_tag": reason},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
                for reason, title in FEEDBACK_REASON_LABELS.items()
            ],
        }
    )
    return {"tag": "action", "actions": actions}


def append_feedback_actions(
    card: dict[str, Any],
    identity: FeedbackIdentity,
    *,
    secret: str | None = None,
) -> dict[str, Any]:
    updated = dict(card)
    elements = list(card.get("elements") or [])
    elements.append({"tag": "hr"})
    elements.append(feedback_actions(identity, secret=secret))
    updated["elements"] = elements
    return updated


def _load_json(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _decision_snapshot(payload: dict[str, Any]) -> tuple[str, list[str], str]:
    decision = decision_result_from_payload(payload)
    if not decision:
        return "", [], ""
    rule_ids = [str(hit.get("rule_id") or "") for hit in decision.rule_hits if hit.get("rule_id")]
    version = str(decision.audit_json.get("decision_version") or decision.audit_json.get("schema_version") or "")
    return decision.action, list(dict.fromkeys(rule_ids)), version


def runtime_revision() -> str:
    explicit = os.getenv("SURVEIL_REVISION", "").strip()
    if explicit:
        return explicit
    marker = Path(__file__).resolve().parents[1] / "REVISION"
    if marker.exists():
        for line in marker.read_text(encoding="utf-8").splitlines():
            if line.startswith("commit="):
                return line.split("=", 1)[1].strip()
    return ""


def resolve_feedback_snapshot(
    conn: sqlite3.Connection,
    identity: FeedbackIdentity,
) -> dict[str, Any]:
    if identity.item_kind == "test":
        return {
            "decision_action": "test",
            "rule_ids": [],
            "decision_version": runtime_revision(),
            "delivery_status": "sent",
            "delivery_id": None,
        }
    if identity.item_kind == "article":
        row = conn.execute(
            "SELECT gate_json, pushed_at FROM article_reviews WHERE source = ? AND item_id = ?",
            (identity.source, identity.item_id),
        ).fetchone()
        if not row:
            raise FeedbackError("未找到对应文章审计记录")
        payload = _load_json(row[0])
        action, rule_ids, version = _decision_snapshot(payload)
        return {
            "decision_action": action,
            "rule_ids": rule_ids,
            "decision_version": version,
            "delivery_status": "sent" if row[1] else "",
            "delivery_id": None,
        }
    if identity.item_kind == "official":
        row = conn.execute(
            "SELECT analysis_json, pushed_at FROM official_news_reviews WHERE source = ? AND item_id = ?",
            (identity.source, identity.item_id),
        ).fetchone()
        if not row:
            raise FeedbackError("未找到对应官网新闻审计记录")
        payload = _load_json(row[0])
        action, rule_ids, version = _decision_snapshot(payload)
        return {
            "decision_action": action,
            "rule_ids": rule_ids,
            "decision_version": version,
            "delivery_status": "sent" if row[1] else "",
            "delivery_id": None,
        }
    row = conn.execute(
        """
        SELECT e.source,
               (SELECT analysis_json FROM event_analyses a WHERE a.event_id = e.id ORDER BY a.id DESC LIMIT 1),
               (SELECT id FROM deliveries d WHERE d.event_id = e.id AND d.channel='feishu' AND d.status='sent' ORDER BY d.id DESC LIMIT 1),
               (SELECT status FROM deliveries d WHERE d.event_id = e.id AND d.channel='feishu' AND d.status='sent' ORDER BY d.id DESC LIMIT 1)
        FROM events e WHERE e.id = ?
        """,
        (identity.item_id,),
    ).fetchone()
    if not row or str(row[0]) != identity.source:
        raise FeedbackError("未找到对应事件审计记录")
    payload = _load_json(row[1])
    action, rule_ids, version = _decision_snapshot(payload)
    return {
        "decision_action": action,
        "rule_ids": rule_ids,
        "decision_version": version,
        "delivery_id": row[2],
        "delivery_status": str(row[3] or ""),
    }


def _current_feedback_row(
    conn: sqlite3.Connection,
    identity: FeedbackIdentity,
    operator_id: str,
) -> sqlite3.Row | tuple[Any, ...] | None:
    return conn.execute(
        """
        SELECT id, label, clicked_at_us
        FROM market_feedback
        WHERE item_kind = ? AND source = ? AND item_id = ? AND operator_id = ?
        ORDER BY clicked_at_us DESC, id DESC
        LIMIT 1
        """,
        (identity.item_kind, identity.source, identity.item_id, operator_id),
    ).fetchone()


def record_feedback(
    *,
    feedback_event_id: str,
    identity: FeedbackIdentity,
    label: str,
    operator_id: str,
    clicked_at_us: int,
    message_id: str = "",
    chat_id: str = "",
    reason_tags: Iterable[str] = (),
    note: str = "",
    raw: dict[str, Any] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    if label not in FEEDBACK_LABELS:
        raise FeedbackError("反馈标签无效")
    if not feedback_event_id or not operator_id:
        raise FeedbackError("反馈事件或操作者标识缺失")
    clicked_at_us = int(clicked_at_us or 0)
    if clicked_at_us <= 0:
        clicked_at_us = int(time.time() * 1_000_000)
    with connect_sqlite(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT id, label FROM market_feedback WHERE feedback_event_id = ?",
            (feedback_event_id,),
        ).fetchone()
        if existing:
            current = _current_feedback_row(conn, identity, operator_id)
            conn.rollback()
            return {
                "id": int(existing[0]),
                "label": str(existing[1]),
                "duplicate_event": True,
                "is_current": bool(current and int(current[0]) == int(existing[0])),
                "current_label": str(current[1]) if current else str(existing[1]),
            }
        snapshot = resolve_feedback_snapshot(conn, identity)
        if snapshot.get("delivery_status") != "sent":
            conn.rollback()
            raise FeedbackError("对应信息没有已发送的飞书投递记录")
        current = _current_feedback_row(conn, identity, operator_id)
        supersedes_id = int(current[0]) if current and clicked_at_us >= int(current[2]) else None
        received_at = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            """
            INSERT INTO market_feedback (
                feedback_event_id, item_kind, source, item_id, delivery_id, label,
                reason_tags_json, note, operator_id, message_id, chat_id,
                decision_action, rule_ids_json, delivery_status, decision_version,
                clicked_at_us, received_at, supersedes_id, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feedback_event_id,
                identity.item_kind,
                identity.source,
                identity.item_id,
                snapshot.get("delivery_id"),
                label,
                json.dumps(list(dict.fromkeys(str(tag).strip() for tag in reason_tags if str(tag).strip())), ensure_ascii=False),
                note.strip(),
                operator_id,
                message_id,
                chat_id,
                snapshot.get("decision_action") or "",
                json.dumps(snapshot.get("rule_ids") or [], ensure_ascii=False),
                snapshot.get("delivery_status") or "",
                snapshot.get("decision_version") or runtime_revision(),
                clicked_at_us,
                received_at,
                supersedes_id,
                json.dumps(raw or {}, ensure_ascii=False),
            ),
        )
        inserted_id = int(cursor.lastrowid)
        conn.commit()
        current_after = _current_feedback_row(conn, identity, operator_id)
    return {
        "id": inserted_id,
        "label": label,
        "duplicate_event": False,
        "is_current": bool(current_after and int(current_after[0]) == inserted_id),
        "current_label": str(current_after[1]) if current_after else label,
        "supersedes_id": supersedes_id,
    }


def current_feedback_rows(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    with connect_sqlite(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT f.*
            FROM market_feedback f
            WHERE NOT EXISTS (
                SELECT 1 FROM market_feedback newer
                WHERE newer.item_kind = f.item_kind
                  AND newer.source = f.source
                  AND newer.item_id = f.item_id
                  AND newer.operator_id = f.operator_id
                  AND (
                    newer.clicked_at_us > f.clicked_at_us
                    OR (newer.clicked_at_us = f.clicked_at_us AND newer.id > f.id)
                  )
            )
            ORDER BY f.clicked_at_us DESC, f.id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def callback_payload_fields(payload: dict[str, Any]) -> dict[str, Any]:
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    operator = event.get("operator") if isinstance(event.get("operator"), dict) else {}
    action = event.get("action") if isinstance(event.get("action"), dict) else {}
    raw_value = action.get("value")
    if isinstance(raw_value, dict):
        value = raw_value
    elif isinstance(raw_value, str):
        try:
            parsed_value = json.loads(raw_value)
        except json.JSONDecodeError:
            parsed_value = {}
        value = parsed_value if isinstance(parsed_value, dict) else {}
    else:
        value = {}
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    return {
        "event_id": str(header.get("event_id") or ""),
        "clicked_at_us": int(header.get("create_time") or 0),
        "operator_id": str(operator.get("open_id") or operator.get("union_id") or operator.get("user_id") or ""),
        "feedback_token": str(value.get("feedback_token") or ""),
        "label": str(value.get("label") or ""),
        "message_id": str(context.get("open_message_id") or ""),
        "chat_id": str(context.get("open_chat_id") or ""),
        "reason_tag": str(value.get("reason_tag") or ""),
    }


def handle_feedback_callback(
    payload: dict[str, Any],
    *,
    secret: str | None = None,
    allowed_ids: set[str] | None = None,
    expected_chat_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    fields = callback_payload_fields(payload)
    allowed = allowed_operator_ids() if allowed_ids is None else allowed_ids
    target_chat = os.getenv("FEISHU_FEEDBACK_CHAT_ID", "").strip() if expected_chat_id is None else expected_chat_id.strip()
    if target_chat and fields["chat_id"] != target_chat:
        raise FeedbackError("反馈不属于已配置的飞书会话")
    if "*" not in allowed and fields["operator_id"] not in allowed:
        raise FeedbackError("当前操作者没有反馈权限")
    identity = parse_feedback_token(fields["feedback_token"], secret=secret if secret is not None else feedback_token_secret())
    result = record_feedback(
        feedback_event_id=fields["event_id"],
        identity=identity,
        label=fields["label"],
        operator_id=fields["operator_id"],
        clicked_at_us=fields["clicked_at_us"],
        message_id=fields["message_id"],
        chat_id=fields["chat_id"],
        reason_tags=[fields["reason_tag"]] if fields["reason_tag"] in FEEDBACK_REASON_LABELS else [],
        raw={"event_type": "card.action.trigger"},
        db_path=db_path,
    )
    current_label = FEEDBACK_LABELS.get(str(result.get("current_label") or result.get("label")), "已记录")
    reason_display = FEEDBACK_REASON_LABELS.get(fields["reason_tag"], "")
    if reason_display and result.get("is_current", True):
        current_label = f"{current_label}（{reason_display}）"
    suffix = "（当前选择）" if result.get("is_current", True) else "（较新的选择已保留）"
    return {
        "toast": {"type": "success", "content": f"已记录：{current_label}{suffix}"},
        "result": result,
    }


def _delivered_items(conn: sqlite3.Connection, cutoff: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='article_reviews'").fetchone():
        for row in conn.execute(
            """
            SELECT source, item_id, title, pushed_at, gate_json
            FROM article_reviews
            WHERE COALESCE(pushed_at, '') >= ?
            """,
            (cutoff,),
        ):
            action, rule_ids, version = _decision_snapshot(_load_json(row[4]))
            items.append(
                {
                    "item_kind": "article",
                    "source": str(row[0]),
                    "item_id": str(row[1]),
                    "title": str(row[2] or ""),
                    "sent_at": str(row[3] or ""),
                    "action": action,
                    "rule_ids": rule_ids,
                    "version": version,
                }
            )
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='official_news_reviews'").fetchone():
        for row in conn.execute(
            """
            SELECT source, item_id, title, pushed_at, analysis_json
            FROM official_news_reviews
            WHERE COALESCE(pushed_at, '') >= ?
            """,
            (cutoff,),
        ):
            action, rule_ids, version = _decision_snapshot(_load_json(row[4]))
            items.append(
                {
                    "item_kind": "official",
                    "source": str(row[0]),
                    "item_id": str(row[1]),
                    "title": str(row[2] or ""),
                    "sent_at": str(row[3] or ""),
                    "action": action,
                    "rule_ids": rule_ids,
                    "version": version,
                }
            )
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
        for row in conn.execute(
            """
            SELECT e.id, e.source, e.title, d.sent_at,
                   (SELECT analysis_json FROM event_analyses a WHERE a.event_id=e.id ORDER BY a.id DESC LIMIT 1)
            FROM events e
            JOIN deliveries d ON d.id = (
                SELECT sent.id FROM deliveries sent
                WHERE sent.event_id=e.id AND sent.channel='feishu' AND sent.status='sent'
                ORDER BY sent.id DESC LIMIT 1
            )
            WHERE COALESCE(d.sent_at, '') >= ?
            """,
            (cutoff,),
        ):
            action, rule_ids, version = _decision_snapshot(_load_json(row[4]))
            items.append(
                {
                    "item_kind": "event",
                    "source": str(row[1]),
                    "item_id": str(row[0]),
                    "title": str(row[2] or ""),
                    "sent_at": str(row[3] or ""),
                    "action": action,
                    "rule_ids": rule_ids,
                    "version": version,
                }
            )
    return items


def _metric_rows(items: list[dict[str, Any]], key_fn: Any) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in items:
        keys = key_fn(item)
        if isinstance(keys, str):
            keys = [keys]
        for key in keys or ["未记录规则"]:
            label = str(key or "未记录")
            bucket = grouped.setdefault(
                label,
                {"key": label, "delivered": 0, "labelled": 0, "high_value": 0, "duplicate": 0, "invalid": 0},
            )
            bucket["delivered"] += 1
            feedback_label = str(item.get("feedback_label") or "")
            if feedback_label in FEEDBACK_LABELS:
                bucket["labelled"] += 1
                bucket[feedback_label] += 1
    rows = []
    for bucket in grouped.values():
        delivered = int(bucket["delivered"])
        labelled = int(bucket["labelled"])
        bucket["coverage"] = round(labelled / delivered, 4) if delivered else 0.0
        bucket["low_sample"] = labelled < 5
        for label in FEEDBACK_LABELS:
            bucket[f"{label}_rate"] = round(int(bucket[label]) / labelled, 4) if labelled else 0.0
        rows.append(bucket)
    return sorted(rows, key=lambda row: (-int(row["labelled"]), -int(row["delivered"]), str(row["key"])))


def feedback_quality_payload(*, db_path: Path = DEFAULT_DB_PATH, days: int = 30) -> dict[str, Any]:
    days = max(1, min(365, int(days)))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with connect_sqlite(db_path) as conn:
        delivered = _delivered_items(conn, cutoff)
        current = current_feedback_rows(db_path)
    current_by_item: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in current:
        key = (str(row["item_kind"]), str(row["source"]), str(row["item_id"]))
        current_by_item.setdefault(key, row)
    for item in delivered:
        feedback = current_by_item.get((item["item_kind"], item["source"], item["item_id"]))
        item["feedback_label"] = str(feedback.get("label") or "") if feedback else ""
        item["feedback_at_us"] = int(feedback.get("clicked_at_us") or 0) if feedback else 0
        try:
            reason_tags = json.loads(str(feedback.get("reason_tags_json") or "[]")) if feedback else []
        except json.JSONDecodeError:
            reason_tags = []
        item["feedback_reasons"] = [
            FEEDBACK_REASON_LABELS.get(str(reason), str(reason)) for reason in reason_tags if str(reason)
        ]
    summary_rows = _metric_rows(delivered, lambda _item: "全部")
    summary = summary_rows[0] if summary_rows else {
        "key": "全部",
        "delivered": 0,
        "labelled": 0,
        "high_value": 0,
        "duplicate": 0,
        "invalid": 0,
        "coverage": 0.0,
        "high_value_rate": 0.0,
        "duplicate_rate": 0.0,
        "invalid_rate": 0.0,
    }
    examples = [item for item in delivered if item.get("feedback_label")]
    examples.sort(key=lambda item: int(item.get("feedback_at_us") or 0), reverse=True)
    for item in examples:
        display = FEEDBACK_LABELS.get(str(item.get("feedback_label") or ""), "")
        if item.get("feedback_reasons"):
            display += "（" + "、".join(item["feedback_reasons"]) + "）"
        item["feedback_label_display"] = display
    return {
        "days": days,
        "cutoff": cutoff,
        "summary": summary,
        "sources": _metric_rows(delivered, lambda item: item["source"]),
        "primary_rules": _metric_rows(delivered, lambda item: (item.get("rule_ids") or ["未记录规则"])[0]),
        "rule_associations": _metric_rows(delivered, lambda item: item.get("rule_ids") or ["未记录规则"]),
        "source_primary_rules": _metric_rows(
            delivered,
            lambda item: f"{item['source']} × {(item.get('rule_ids') or ['未记录规则'])[0]}",
        ),
        "examples": examples[:100],
    }
