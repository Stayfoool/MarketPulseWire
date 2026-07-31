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

from cards import div_markdown

from db_utils import connect_sqlite
from market_db import DEFAULT_DB_PATH
from market_canonical_reader import (
    canonical_delivered_items,
    canonical_feedback_snapshot,
)
from market_item import decision_result_from_dict


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
TOKEN_VERSION = 2


class FeedbackError(ValueError):
    """Rejected feedback input that is safe to show to the operator."""


@dataclass(frozen=True)
class FeedbackIdentity:
    market_item_id: int


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
        "m": int(identity.market_item_id),
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
    try:
        identity = FeedbackIdentity(market_item_id=int(payload.get("m")))
    except (TypeError, ValueError) as exc:
        raise FeedbackError("反馈对象标识无效") from exc
    if identity.market_item_id < 0:
        raise FeedbackError("反馈对象标识不完整")
    return identity


def allowed_operator_ids(raw: str | None = None) -> set[str]:
    value = os.getenv("FEISHU_FEEDBACK_ALLOWED_OPEN_IDS", "") if raw is None else raw
    return {part.strip() for part in value.replace("；", ",").replace(";", ",").split(",") if part.strip()}


def _reason_tags_from_json(value: Any) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(tag) for tag in parsed if str(tag) in FEEDBACK_REASON_LABELS]


def _ordered_feedback_labels(labels: Iterable[str]) -> list[str]:
    selected = {str(label) for label in labels}
    return [label for label in FEEDBACK_LABELS if label in selected]


def _active_labels_from_values(label: Any, active_labels_json: Any) -> list[str]:
    if active_labels_json is None:
        legacy_label = str(label or "")
        return [legacy_label] if legacy_label in FEEDBACK_LABELS else []
    try:
        parsed = json.loads(str(active_labels_json))
    except (TypeError, json.JSONDecodeError):
        return []
    return _ordered_feedback_labels(parsed if isinstance(parsed, list) else [])


def feedback_status_display(active_labels: Iterable[str], reason_tags: Iterable[str] = ()) -> str:
    labels = _ordered_feedback_labels(active_labels)
    if not labels:
        return "未选择"
    reasons = [FEEDBACK_REASON_LABELS[tag] for tag in reason_tags if tag in FEEDBACK_REASON_LABELS]
    displays = []
    for label in labels:
        display = FEEDBACK_LABELS[label]
        if label == "invalid" and reasons:
            display += "（" + "、".join(reasons) + "）"
        displays.append(display)
    return f"已标记为「{'、'.join(displays)}」"


def feedback_actions(
    identity: FeedbackIdentity,
    *,
    secret: str | None = None,
    selected_labels: Iterable[str] = (),
    selected_reason_tags: Iterable[str] = (),
) -> dict[str, Any]:
    token = build_feedback_token(identity, secret=secret if secret is not None else feedback_token_secret())
    selected_set = set(_ordered_feedback_labels(selected_labels))
    actions = []
    for label, title, button_type in (
        ("high_value", "特别有用", "primary"),
        ("duplicate", "重复", "default"),
        ("invalid", "无效", "danger"),
    ):
        selected = label in selected_set
        actions.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"✓ {title}" if selected else title},
                "type": button_type if not selected or label == "invalid" else "primary",
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
    selected_labels: Iterable[str] = (),
    selected_reason_tags: Iterable[str] = (),
) -> dict[str, Any]:
    updated = dict(card)
    elements = list(card.get("elements") or [])
    elements.append({"tag": "hr"})
    ordered_labels = _ordered_feedback_labels(selected_labels)
    if ordered_labels:
        elements.append(div_markdown(f"**反馈状态**：{feedback_status_display(ordered_labels, selected_reason_tags)}"))
    elements.append(
        feedback_actions(
            identity,
            secret=secret,
            selected_labels=ordered_labels,
            selected_reason_tags=selected_reason_tags,
        )
    )
    updated["elements"] = elements
    return updated


def feedback_test_card_base() -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "MarketPulseWire 反馈测试"},
        },
        "elements": [
            div_markdown("此卡仅验证飞书反馈回调，不代表市场信息，也不会进入质量统计。"),
        ],
    }


def feedback_test_card(identity: FeedbackIdentity, *, secret: str | None = None) -> dict[str, Any]:
    return append_feedback_actions(feedback_test_card_base(), identity, secret=secret)


def _load_json(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _decision_snapshot(payload: dict[str, Any]) -> tuple[str, list[str], str]:
    decision = decision_result_from_dict(payload)
    if not decision:
        return "", [], ""
    rule_ids = [str(hit.get("rule_id") or "") for hit in decision.rule_hits if hit.get("rule_id")]
    version = str(
        decision.audit_json.get("decision_version")
        or decision.audit_json.get("llm_decision_rule_version")
        or decision.audit_json.get("schema_version")
        or ""
    )
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
    if identity.market_item_id == 0:
        return {
            "decision_action": "test",
            "rule_ids": [],
            "decision_version": runtime_revision(),
            "delivery_status": "sent",
            "delivery_id": None,
        }
    canonical = canonical_feedback_snapshot(conn, identity.market_item_id)
    if canonical is None:
        raise FeedbackError("统一处理结果中未找到对应审计记录")
    action, rule_ids, version = _decision_snapshot(canonical["decision"])
    return {
        "decision_action": action,
        "rule_ids": rule_ids,
        "decision_version": version or canonical.get("application_revision") or "",
        "delivery_status": canonical["delivery_status"],
        "delivery_id": canonical["delivery_id"],
    }


def _current_feedback_row(
    conn: sqlite3.Connection,
    identity: FeedbackIdentity,
    operator_id: str,
) -> sqlite3.Row | tuple[Any, ...] | None:
    return conn.execute(
        """
        SELECT id, label, clicked_at_us, reason_tags_json, active_labels_json
        FROM market_feedback
        WHERE market_item_id IS ? AND operator_id = ?
        ORDER BY clicked_at_us DESC, id DESC
        LIMIT 1
        """,
        (identity.market_item_id or None, operator_id),
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
            active_labels = _active_labels_from_values(current[1], current[4]) if current else []
            conn.rollback()
            return {
                "id": int(existing[0]),
                "label": str(existing[1]),
                "duplicate_event": True,
                "is_current": bool(current and int(current[0]) == int(existing[0])),
                "active_labels": active_labels,
                "current_reason_tags": _reason_tags_from_json(current[3]) if current else [],
            }
        snapshot = resolve_feedback_snapshot(conn, identity)
        if snapshot.get("delivery_status") != "sent":
            conn.rollback()
            raise FeedbackError("对应信息没有已发送的飞书投递记录")
        current = _current_feedback_row(conn, identity, operator_id)
        is_current_event = not current or clicked_at_us >= int(current[2])
        current_active_labels = _active_labels_from_values(current[1], current[4]) if current else []
        active_labels = list(current_active_labels)
        if is_current_event:
            requested_reason_tags = list(
                dict.fromkeys(str(tag).strip() for tag in reason_tags if str(tag).strip())
            )
            if label == "invalid" and requested_reason_tags:
                if label not in active_labels:
                    active_labels.append(label)
            elif label in active_labels:
                active_labels.remove(label)
            else:
                active_labels.append(label)
            active_labels = _ordered_feedback_labels(active_labels)
        current_reason_tags = _reason_tags_from_json(current[3]) if current else []
        if is_current_event and label == "invalid":
            stored_reason_tags = (
                requested_reason_tags
                if "invalid" in active_labels
                else []
            )
        else:
            stored_reason_tags = current_reason_tags if "invalid" in active_labels else []
        supersedes_id = int(current[0]) if current and is_current_event else None
        received_at = datetime.now(timezone.utc).isoformat()
        raw_payload = dict(raw or {})
        raw_payload["toggle"] = (
            "select" if label in active_labels else "cancel"
        ) if is_current_event else "ignored_older"
        cursor = conn.execute(
            """
            INSERT INTO market_feedback (
                feedback_event_id, market_item_id, delivery_id, label, active_labels_json,
                reason_tags_json, note, operator_id, message_id, chat_id,
                decision_action, rule_ids_json, delivery_status, decision_version,
                clicked_at_us, received_at, supersedes_id, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feedback_event_id,
                identity.market_item_id or None,
                snapshot.get("delivery_id"),
                label,
                json.dumps(active_labels, ensure_ascii=False),
                json.dumps(stored_reason_tags, ensure_ascii=False),
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
                json.dumps(raw_payload, ensure_ascii=False),
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
        "active_labels": _active_labels_from_values(current_after[1], current_after[4]) if current_after else active_labels,
        "current_reason_tags": _reason_tags_from_json(current_after[3]) if current_after else [],
        "supersedes_id": supersedes_id,
    }


def current_feedback_rows_from_conn(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'market_feedback'"
    ).fetchone():
        return []
    original_row_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT f.*
            FROM market_feedback f
            WHERE NOT EXISTS (
                SELECT 1 FROM market_feedback newer
                WHERE newer.market_item_id IS f.market_item_id
                  AND newer.operator_id = f.operator_id
                  AND (
                    newer.clicked_at_us > f.clicked_at_us
                    OR (newer.clicked_at_us = f.clicked_at_us AND newer.id > f.id)
                  )
            )
            ORDER BY f.clicked_at_us DESC, f.id DESC
            """
        ).fetchall()
    finally:
        conn.row_factory = original_row_factory
    return [dict(row) for row in rows]


def current_feedback_rows(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    with connect_sqlite(db_path) as conn:
        return current_feedback_rows_from_conn(conn)


def feedback_projection_by_item(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in current_feedback_rows_from_conn(conn):
        market_item_id = row.get("market_item_id")
        if market_item_id is None:
            continue
        active_labels = _active_labels_from_values(row.get("label"), row.get("active_labels_json"))
        if not active_labels:
            continue
        row["active_labels"] = active_labels
        key = int(market_item_id)
        grouped.setdefault(key, []).append(row)

    projection: dict[int, dict[str, Any]] = {}
    label_order = tuple(FEEDBACK_LABELS)
    for key, rows in grouped.items():
        counts = {label: 0 for label in label_order}
        reason_tags: list[str] = []
        latest_received_at = ""
        latest_clicked_at_us = 0
        for row in rows:
            for label in row.get("active_labels") or []:
                counts[label] += 1
            if "invalid" in (row.get("active_labels") or []):
                for tag in _reason_tags_from_json(row.get("reason_tags_json")):
                    if tag not in reason_tags:
                        reason_tags.append(tag)
            clicked_at_us = int(row.get("clicked_at_us") or 0)
            if clicked_at_us >= latest_clicked_at_us:
                latest_clicked_at_us = clicked_at_us
                latest_received_at = str(row.get("received_at") or "")
        active_labels = [label for label in label_order if counts[label]]
        if len(rows) == 1 and len(active_labels) == 1:
            display = FEEDBACK_LABELS[active_labels[0]]
            reasons = [FEEDBACK_REASON_LABELS[tag] for tag in reason_tags if tag in FEEDBACK_REASON_LABELS]
            if reasons:
                display += " · " + "、".join(reasons)
        else:
            display = " · ".join(f"{FEEDBACK_LABELS[label]} {counts[label]}" for label in active_labels)
        projection[key] = {
            "labels": active_labels,
            "label_counts": {label: counts[label] for label in active_labels},
            "display": display,
            "reason_tags": reason_tags,
            "reason_labels": [FEEDBACK_REASON_LABELS[tag] for tag in reason_tags if tag in FEEDBACK_REASON_LABELS],
            "operator_count": len(rows),
            "received_at": latest_received_at,
        }
    return projection


def callback_payload_fields(payload: dict[str, Any]) -> dict[str, Any]:
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    operator = event.get("operator") if isinstance(event.get("operator"), dict) else {}
    action = event.get("action") if isinstance(event.get("action"), dict) else {}
    value: dict[str, Any] = {}
    for raw_value in (action.get("value"), action.get("option")):
        if isinstance(raw_value, dict):
            candidate = raw_value
        elif isinstance(raw_value, str):
            try:
                parsed_value = json.loads(raw_value)
            except json.JSONDecodeError:
                parsed_value = {}
            candidate = parsed_value if isinstance(parsed_value, dict) else {}
        else:
            candidate = {}
        if candidate.get("feedback_token"):
            value = candidate
            break
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
    current_reason_tags = list(result.get("current_reason_tags") or [])
    active_labels = _ordered_feedback_labels(result.get("active_labels") or [])
    current_display = feedback_status_display(active_labels, current_reason_tags)
    suffix = "（当前选择）" if result.get("is_current", True) else "（较新的选择已保留）"
    clicked_label = str(result.get("label") or "")
    if result.get("is_current", True) and clicked_label not in active_labels:
        clicked_display = FEEDBACK_LABELS.get(clicked_label, "该标签")
        toast_content = f"已取消「{clicked_display}」"
        if active_labels:
            toast_content += f"；{current_display}"
    else:
        toast_content = f"已记录：{current_display}{suffix}"
    return {
        "toast": {"type": "success", "content": toast_content},
        "result": result,
        "card_state": {
            "identity": identity,
            "active_labels": active_labels,
            "reason_tags": current_reason_tags,
        },
    }


def _feedback_card_base(conn: sqlite3.Connection, identity: FeedbackIdentity) -> dict[str, Any] | None:
    if identity.market_item_id == 0:
        return feedback_test_card_base()
    canonical = canonical_feedback_snapshot(conn, identity.market_item_id)
    if canonical is None:
        return None
    card = canonical.get("delivery_payload", {}).get("_feedback_card_base")
    return card if isinstance(card, dict) else None


def feedback_card_for_callback(
    identity: FeedbackIdentity,
    active_labels: Iterable[str],
    reason_tags: Iterable[str] = (),
    *,
    secret: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    ordered_labels = _ordered_feedback_labels(active_labels)
    with connect_sqlite(db_path) as conn:
        base = _feedback_card_base(conn, identity)
    if base is None:
        return None
    return append_feedback_actions(
        base,
        identity,
        secret=secret,
        selected_labels=ordered_labels,
        selected_reason_tags=reason_tags,
    )


def _delivered_items(conn: sqlite3.Connection, cutoff: str) -> list[dict[str, Any]]:
    return canonical_delivered_items(conn, cutoff)


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
            feedback_labels = _ordered_feedback_labels(item.get("feedback_labels") or [])
            if feedback_labels:
                bucket["labelled"] += 1
                for feedback_label in feedback_labels:
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
        current_by_item = feedback_projection_by_item(conn)
    for item in delivered:
        feedback = current_by_item.get(int(item["market_item_id"]))
        item["feedback_labels"] = _ordered_feedback_labels(feedback.get("labels") or []) if feedback else []
        item["feedback_at"] = str(feedback.get("received_at") or "") if feedback else ""
        reason_tags = list(feedback.get("reason_tags") or []) if feedback else []
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
    examples = [item for item in delivered if item.get("feedback_labels")]
    examples.sort(key=lambda item: str(item.get("feedback_at") or ""), reverse=True)
    for item in examples:
        labels = list(item.get("feedback_labels") or [])
        display_parts = []
        for label in labels:
            part = FEEDBACK_LABELS[label]
            if label == "invalid" and item.get("feedback_reasons"):
                part += "（" + "、".join(item["feedback_reasons"]) + "）"
            display_parts.append(part)
        display = "、".join(display_parts)
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
