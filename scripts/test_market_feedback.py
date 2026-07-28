#!/usr/bin/env python3
"""Regression checks for auditable Feishu market feedback."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import feishu_app
import feishu_feedback_service

from market_feedback import (
    FeedbackError,
    FeedbackIdentity,
    append_feedback_actions,
    build_feedback_token,
    current_feedback_rows,
    feedback_card_for_callback,
    feedback_projection_by_item,
    feedback_quality_payload,
    handle_feedback_callback,
    parse_feedback_token,
)
from market_db import init_db


TEST_SIGNING_KEY = "feedback-test-key"
OPERATOR = "ou_operator"


def insert_delivered_article(db_path: Path) -> None:
    init_db(db_path).close()
    decision = {
        "action": "push",
        "importance": "high",
        "brief_reason": "test",
        "rule_hits": [{"rule_id": "industry_quantified_hardline"}],
        "audit_json": {"decision_version": "test-v1"},
    }
    with sqlite3.connect(db_path) as conn:
        item_id = int(conn.execute(
            """
            INSERT INTO market_items (
                source,source_item_id,dedupe_key,source_category,publisher_role,
                collector,content_type,title,summary,full_text,url,published_at,
                first_seen_at,symbols_json,themes_json,raw_json,access_note,
                content_hash,collection_class,processability_status,
                processability_reason,processing_status,processing_error,
                created_at,updated_at
            ) VALUES ('cls_telegraph_api','item-1','cls_telegraph_api:item-1','','',
                      'test','article','Feedback fixture','','','','',?,
                      '[]','[]','{}','','fixture-hash','live','succeeded','',
                      'succeeded','',?,?)
            """,
            ("2026-07-15T00:00:00+00:00",) * 3,
        ).lastrowid)
        conn.execute(
            """
            INSERT INTO market_item_aliases (
                market_item_id,item_kind,source,legacy_item_id,legacy_store_kind,created_at
            ) VALUES (?, 'article', 'cls_telegraph_api', 'item-1', 'market_items', ?)
            """,
            (item_id, "2026-07-15T00:00:00+00:00"),
        )
        legacy_payload = {
            "raw": {
                "decision_result": decision,
                "_feedback_card_base": {
                    "header": {"title": {"tag": "plain_text", "content": "Feedback fixture"}},
                    "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": "fixture"}}],
                },
            }
        }
        review_id = int(conn.execute(
            """
            INSERT INTO market_reviews (
                market_item_id,task,run_key,is_current,review_status,
                admission_status,admission_reason,admission_matched_families_json,
                admission_evidence_json,admission_json,decision_action,importance,
                decision_json,interpretation_json,legacy_payload_json,
                application_revision,created_at,completed_at
            ) VALUES (?, 'production', 'feedback-fixture', 1, 'succeeded', 'admitted',
                      'test', '[]', '[]', '{}', 'push', 'high', ?, '{}', ?,
                      'test-revision', ?, ?)
            """,
            (
                item_id,
                json.dumps(decision),
                json.dumps(legacy_payload),
                "2026-07-15T00:00:00+00:00",
                "2026-07-15T00:00:00+00:00",
            ),
        ).lastrowid)
        conn.execute(
            """
            INSERT INTO deliveries (
                market_item_id,market_review_id,channel,status,
                decision_action,attempted_at,sent_at,payload_json
            ) VALUES (?, ?, 'feishu', 'sent', 'push', ?, ?, '{}')
            """,
            (item_id, review_id, "2026-07-15T00:00:00+00:00", "2026-07-15T00:00:00+00:00"),
        )
        conn.commit()


def callback(
    token: str,
    label: str,
    event_id: str,
    clicked_at_us: int,
    operator: str = OPERATOR,
    reason_tag: str = "",
) -> dict:
    return {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "create_time": str(clicked_at_us),
            "event_type": "card.action.trigger",
        },
        "event": {
            "operator": {"open_id": operator},
            "action": {
                "tag": "button",
                "value": {"feedback_token": token, "label": label, "reason_tag": reason_tag},
            },
            "context": {"open_message_id": "om_test", "open_chat_id": "oc_test"},
        },
    }


def test_feedback_token_and_card_actions() -> None:
    identity = FeedbackIdentity("article", "cls_telegraph_api", "item-1")
    token = build_feedback_token(identity, secret=TEST_SIGNING_KEY, issued_at=1)
    assert parse_feedback_token(token, secret=TEST_SIGNING_KEY) == identity
    try:
        parse_feedback_token(token + "x", secret=TEST_SIGNING_KEY)
    except FeedbackError as exc:
        assert "签名" in str(exc) or "格式" in str(exc)
    else:
        raise AssertionError("tampered token must fail")
    card = append_feedback_actions({"elements": [{"tag": "div"}]}, identity, secret=TEST_SIGNING_KEY)
    action = card["elements"][-1]
    assert [button["value"]["label"] for button in action["actions"][:3]] == ["high_value", "duplicate", "invalid"]
    assert action["actions"][3]["tag"] == "overflow"
    overflow_value = json.loads(action["actions"][3]["options"][0]["value"])
    assert overflow_value["reason_tag"] == "useful_not_urgent"

    selected = append_feedback_actions(
        {"elements": [{"tag": "div"}]},
        identity,
        secret=TEST_SIGNING_KEY,
        selected_labels=["high_value", "duplicate"],
    )
    assert "已标记为「特别有用、重复」" in selected["elements"][-2]["text"]["content"]
    selected_actions = selected["elements"][-1]["actions"]
    assert selected_actions[0]["text"]["content"] == "✓ 特别有用"
    assert selected_actions[1]["text"]["content"] == "✓ 重复"
    assert selected_actions[1]["type"] == "primary"


def test_last_click_wins_by_feishu_timestamp_and_keeps_history() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "feedback.sqlite3"
        insert_delivered_article(db_path)
        identity = FeedbackIdentity("article", "cls_telegraph_api", "item-1")
        token = build_feedback_token(identity, secret=TEST_SIGNING_KEY, issued_at=1)

        first = handle_feedback_callback(
            callback(token, "high_value", "evt-1", 100),
            secret=TEST_SIGNING_KEY,
            allowed_ids={OPERATOR},
            db_path=db_path,
        )
        latest = handle_feedback_callback(
            callback(token, "invalid", "evt-2", 300),
            secret=TEST_SIGNING_KEY,
            allowed_ids={OPERATOR},
            db_path=db_path,
        )
        delayed_old = handle_feedback_callback(
            callback(token, "duplicate", "evt-3", 200),
            secret=TEST_SIGNING_KEY,
            allowed_ids={OPERATOR},
            db_path=db_path,
        )

        assert first["result"]["is_current"] is True
        assert latest["result"]["is_current"] is True
        assert delayed_old["result"]["is_current"] is False
        assert delayed_old["result"]["active_labels"] == ["high_value", "invalid"]
        current = current_feedback_rows(db_path)
        assert len(current) == 1
        assert current[0]["label"] == "invalid"
        assert json.loads(current[0]["active_labels_json"]) == ["high_value", "invalid"]
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM market_feedback").fetchone()[0] == 3
            stored = conn.execute(
                "SELECT decision_action, rule_ids_json, delivery_status FROM market_feedback WHERE feedback_event_id='evt-2'"
            ).fetchone()
        assert stored == ("push", '["industry_quantified_hardline"]', "sent")

        state_card = feedback_card_for_callback(
            identity,
            ["high_value", "invalid"],
            ["stale"],
            secret=TEST_SIGNING_KEY,
            db_path=db_path,
        )
        assert state_card is not None
        assert "已标记为「特别有用、无效（旧闻）」" in state_card["elements"][-2]["text"]["content"]
        assert state_card["elements"][-1]["actions"][0]["text"]["content"] == "✓ 特别有用"
        assert state_card["elements"][-1]["actions"][2]["text"]["content"] == "✓ 无效"

        quality = feedback_quality_payload(db_path=db_path, days=30)
        assert quality["summary"]["delivered"] == 1
        assert quality["summary"]["labelled"] == 1
        assert quality["summary"]["high_value"] == 1
        assert quality["summary"]["invalid"] == 1
        assert quality["sources"][0]["key"] == "cls_telegraph_api"
        assert quality["primary_rules"][0]["key"] == "industry_quantified_hardline"

        repeated = handle_feedback_callback(
            callback(token, "invalid", "evt-2", 300),
            secret=TEST_SIGNING_KEY,
            allowed_ids={OPERATOR},
            db_path=db_path,
        )
        assert repeated["result"]["duplicate_event"] is True
        repeated_old = handle_feedback_callback(
            callback(token, "high_value", "evt-1", 100),
            secret=TEST_SIGNING_KEY,
            allowed_ids={OPERATOR},
            db_path=db_path,
        )
        assert repeated_old["result"]["is_current"] is False
        assert repeated_old["result"]["active_labels"] == ["high_value", "invalid"]
        unchanged_card = feedback_card_for_callback(
            identity,
            repeated_old["card_state"]["active_labels"],
            repeated_old["card_state"]["reason_tags"],
            secret=TEST_SIGNING_KEY,
            db_path=db_path,
        )
        assert unchanged_card is not None
        assert unchanged_card["elements"][-1]["actions"][2]["text"]["content"] == "✓ 无效"
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM market_feedback").fetchone()[0] == 3


def test_labels_toggle_independently_and_keep_one_current_snapshot() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "feedback.sqlite3"
        insert_delivered_article(db_path)
        identity = FeedbackIdentity("article", "cls_telegraph_api", "item-1")
        token = build_feedback_token(identity, secret=TEST_SIGNING_KEY, issued_at=1)

        useful = handle_feedback_callback(
            callback(token, "high_value", "evt-useful", 100),
            secret=TEST_SIGNING_KEY,
            allowed_ids={OPERATOR},
            db_path=db_path,
        )
        duplicate = handle_feedback_callback(
            callback(token, "duplicate", "evt-duplicate", 200),
            secret=TEST_SIGNING_KEY,
            allowed_ids={OPERATOR},
            db_path=db_path,
        )
        assert useful["result"]["active_labels"] == ["high_value"]
        assert duplicate["result"]["active_labels"] == ["high_value", "duplicate"]

        current = current_feedback_rows(db_path)
        assert len(current) == 1 and current[0]["label"] == "duplicate"
        assert json.loads(current[0]["active_labels_json"]) == ["high_value", "duplicate"]
        with sqlite3.connect(db_path) as conn:
            history = conn.execute(
                "SELECT label,active_labels_json,reason_tags_json,supersedes_id,raw_json "
                "FROM market_feedback ORDER BY id"
            ).fetchall()
            projection = feedback_projection_by_item(conn)
        assert history[0][0:4] == ("high_value", '["high_value"]', "[]", None)
        assert history[1][0:4] == ("duplicate", '["high_value", "duplicate"]', "[]", 1)
        assert json.loads(history[1][4]) == {"event_type": "card.action.trigger", "toggle": "select"}
        projected = projection[("article", "cls_telegraph_api", "item-1")]
        assert projected["labels"] == ["high_value", "duplicate"]

        selected_card = feedback_card_for_callback(
            identity,
            duplicate["card_state"]["active_labels"],
            secret=TEST_SIGNING_KEY,
            db_path=db_path,
        )
        assert selected_card is not None
        assert selected_card["elements"][-1]["actions"][0]["text"]["content"] == "✓ 特别有用"
        assert selected_card["elements"][-1]["actions"][1]["text"]["content"] == "✓ 重复"
        quality = feedback_quality_payload(db_path=db_path, days=30)
        assert quality["summary"]["delivered"] == 1
        assert quality["summary"]["labelled"] == 1
        assert quality["summary"]["high_value"] == 1
        assert quality["summary"]["duplicate"] == 1

        useful_cancelled = handle_feedback_callback(
            callback(token, "high_value", "evt-useful-cancel", 300),
            secret=TEST_SIGNING_KEY,
            allowed_ids={OPERATOR},
            db_path=db_path,
        )
        assert useful_cancelled["result"]["active_labels"] == ["duplicate"]
        assert useful_cancelled["toast"]["content"].startswith("已取消「特别有用」")

        retried_duplicate = handle_feedback_callback(
            callback(token, "duplicate", "evt-duplicate", 200),
            secret=TEST_SIGNING_KEY,
            allowed_ids={OPERATOR},
            db_path=db_path,
        )
        assert retried_duplicate["result"]["duplicate_event"] is True
        assert retried_duplicate["result"]["is_current"] is False
        assert retried_duplicate["result"]["active_labels"] == ["duplicate"]

        delayed_same_label = handle_feedback_callback(
            callback(token, "invalid", "evt-delayed", 250),
            secret=TEST_SIGNING_KEY,
            allowed_ids={OPERATOR},
            db_path=db_path,
        )
        assert delayed_same_label["result"]["label"] == "invalid"
        assert delayed_same_label["result"]["is_current"] is False
        assert delayed_same_label["result"]["active_labels"] == ["duplicate"]
        quality = feedback_quality_payload(db_path=db_path, days=30)
        assert quality["summary"]["labelled"] == 1
        assert quality["summary"]["duplicate"] == 1
        assert quality["summary"]["high_value"] == 0


def test_unauthorized_operator_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "feedback.sqlite3"
        insert_delivered_article(db_path)
        token = build_feedback_token(
            FeedbackIdentity("article", "cls_telegraph_api", "item-1"), secret=TEST_SIGNING_KEY
        )
        try:
            handle_feedback_callback(
                callback(token, "duplicate", "evt-denied", 100, operator="ou_denied"),
                secret=TEST_SIGNING_KEY,
                allowed_ids={OPERATOR},
                db_path=db_path,
            )
        except FeedbackError as exc:
            assert "权限" in str(exc)
        else:
            raise AssertionError("unauthorized feedback must fail")

        try:
            handle_feedback_callback(
                callback(token, "duplicate", "evt-wrong-chat", 101),
                secret=TEST_SIGNING_KEY,
                allowed_ids={OPERATOR},
                expected_chat_id="oc_other",
                db_path=db_path,
            )
        except FeedbackError as exc:
            assert "会话" in str(exc)
        else:
            raise AssertionError("feedback from another chat must fail")


def test_application_sender_returns_message_id() -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"code":0,"msg":"success","data":{"message_id":"om_sent"}}'

    with patch.object(feishu_app, "tenant_access_token", return_value="tenant-token"), patch.object(
        feishu_app.urllib.request, "urlopen", return_value=Response()
    ) as urlopen:
        response = feishu_app.send_interactive_card({"elements": []}, chat_id="oc_test")
    assert response.ok is True
    assert response.message_id == "om_sent"
    request = urlopen.call_args.args[0]
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["receive_id"] == "oc_test"
    assert payload["msg_type"] == "interactive"


def test_listener_only_mode_keeps_natural_feedback_delivery_disabled() -> None:
    keys = {
        "FEISHU_FEEDBACK_ENABLED": "0",
        "FEISHU_FEEDBACK_LISTENER_ENABLED": "1",
        "FEISHU_APP_ID": "cli_test",
        "FEISHU_APP_SECRET": "app_secret",
        "FEISHU_FEEDBACK_CHAT_ID": "oc_test",
        "FEISHU_FEEDBACK_TOKEN_SECRET": "feedback_secret",
        "FEISHU_FEEDBACK_ALLOWED_OPEN_IDS": "ou_test",
    }
    original_env = {key: os.environ.get(key) for key in keys}
    try:
        os.environ.update(keys)
        assert feishu_app.feedback_enabled() is False
        assert feishu_app.feedback_listener_enabled() is True
        assert feishu_app.configured() is True
    finally:
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_test_card_feedback_is_audited_but_excluded_from_quality_metrics() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "feedback.sqlite3"
        init_db(db_path).close()
        token = build_feedback_token(
            FeedbackIdentity("test", "feishu_feedback", "test-1"), secret=TEST_SIGNING_KEY
        )
        result = handle_feedback_callback(
            callback(token, "duplicate", "evt-test", 100),
            secret=TEST_SIGNING_KEY,
            allowed_ids={OPERATOR},
            db_path=db_path,
        )
        assert result["result"]["is_current"] is True
        with sqlite3.connect(db_path) as conn:
            stored = conn.execute(
                "SELECT item_kind, decision_action, delivery_status FROM market_feedback"
            ).fetchone()
        assert stored == ("test", "test", "sent")
        quality = feedback_quality_payload(db_path=db_path, days=30)
        assert quality["summary"]["delivered"] == 0
        assert quality["summary"]["labelled"] == 0


def test_more_reason_is_stored_with_invalid_feedback() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "feedback.sqlite3"
        insert_delivered_article(db_path)
        token = build_feedback_token(
            FeedbackIdentity("article", "cls_telegraph_api", "item-1"), secret=TEST_SIGNING_KEY
        )
        response = handle_feedback_callback(
            callback(token, "invalid", "evt-reason", 100, reason_tag="stale"),
            secret=TEST_SIGNING_KEY,
            allowed_ids={OPERATOR},
            db_path=db_path,
        )
        assert "旧闻" in response["toast"]["content"]
        with sqlite3.connect(db_path) as conn:
            stored = conn.execute("SELECT label, reason_tags_json FROM market_feedback").fetchone()
        assert stored == ("invalid", '["stale"]')
        updated = handle_feedback_callback(
            callback(token, "invalid", "evt-reason-update", 200, reason_tag="weak_evidence"),
            secret=TEST_SIGNING_KEY,
            allowed_ids={OPERATOR},
            db_path=db_path,
        )
        assert updated["result"]["active_labels"] == ["invalid"]
        assert updated["result"]["current_reason_tags"] == ["weak_evidence"]


def test_every_feedback_label_combination_is_rendered_and_counted() -> None:
    labels = ["high_value", "duplicate", "invalid"]
    for mask in range(8):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "feedback.sqlite3"
            insert_delivered_article(db_path)
            identity = FeedbackIdentity("article", "cls_telegraph_api", "item-1")
            token = build_feedback_token(identity, secret=TEST_SIGNING_KEY, issued_at=1)
            expected = [label for index, label in enumerate(labels) if mask & (1 << index)]
            result = None
            for index, label in enumerate(expected, start=1):
                result = handle_feedback_callback(
                    callback(
                        token,
                        label,
                        f"evt-{mask}-{index}",
                        index,
                        reason_tag="stale" if label == "invalid" else "",
                    ),
                    secret=TEST_SIGNING_KEY,
                    allowed_ids={OPERATOR},
                    db_path=db_path,
                )
            active_labels = result["result"]["active_labels"] if result else []
            assert active_labels == expected
            card = feedback_card_for_callback(
                identity,
                active_labels,
                result["card_state"]["reason_tags"] if result else [],
                secret=TEST_SIGNING_KEY,
                db_path=db_path,
            )
            assert card is not None
            buttons = card["elements"][-1]["actions"][:3]
            assert [button["text"]["content"].startswith("✓") for button in buttons] == [
                label in expected for label in labels
            ]
            quality = feedback_quality_payload(db_path=db_path, days=30)
            assert quality["summary"]["labelled"] == int(bool(expected))
            for label in labels:
                assert quality["summary"][label] == int(label in expected)


def test_legacy_single_selection_becomes_multiselect_without_rewriting_history() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "feedback.sqlite3"
        insert_delivered_article(db_path)
        identity = FeedbackIdentity("article", "cls_telegraph_api", "item-1")
        token = build_feedback_token(identity, secret=TEST_SIGNING_KEY, issued_at=1)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO market_feedback (
                    feedback_event_id,item_kind,source,item_id,label,reason_tags_json,
                    operator_id,rule_ids_json,clicked_at_us,received_at,raw_json
                ) VALUES ('legacy','article','cls_telegraph_api','item-1','high_value',
                          '[]',?,'[]',100,'2026-07-15T00:00:00+00:00','{}')
                """,
                (OPERATOR,),
            )
            conn.commit()
        result = handle_feedback_callback(
            callback(token, "duplicate", "evt-new", 200),
            secret=TEST_SIGNING_KEY,
            allowed_ids={OPERATOR},
            db_path=db_path,
        )
        assert result["result"]["active_labels"] == ["high_value", "duplicate"]
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT feedback_event_id,active_labels_json FROM market_feedback ORDER BY id"
            ).fetchall()
        assert rows == [("legacy", None), ("evt-new", '["high_value", "duplicate"]')]


def test_event_card_is_recovered_from_sent_delivery_payload() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "feedback.sqlite3"
        insert_delivered_article(db_path)
        base = {
            "header": {"title": {"tag": "plain_text", "content": "Event fixture"}},
            "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": "event"}}],
        }
        with sqlite3.connect(db_path) as conn:
            payload = json.loads(conn.execute("SELECT legacy_payload_json FROM market_reviews").fetchone()[0])
            payload["raw"].pop("_feedback_card_base")
            conn.execute("UPDATE market_reviews SET legacy_payload_json=?", (json.dumps(payload),))
            conn.execute(
                "UPDATE market_item_aliases SET item_kind='event',source='sina_flash'"
            )
            conn.execute(
                "UPDATE deliveries SET payload_json=?",
                (json.dumps({"_feedback_card_base": base}),),
            )
            conn.commit()
        card = feedback_card_for_callback(
            FeedbackIdentity("event", "sina_flash", "item-1"),
            ["high_value", "duplicate"],
            secret=TEST_SIGNING_KEY,
            db_path=db_path,
        )
        assert card is not None
        assert card["header"]["title"]["content"] == "Event fixture"
        assert card["elements"][-1]["actions"][0]["text"]["content"] == "✓ 特别有用"
        assert card["elements"][-1]["actions"][1]["text"]["content"] == "✓ 重复"


def test_unified_review_without_card_snapshot_keeps_toast_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "feedback.sqlite3"
        insert_delivered_article(db_path)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT legacy_payload_json FROM market_reviews").fetchone()
            payload = json.loads(row[0])
            payload["raw"].pop("_feedback_card_base")
            conn.execute("UPDATE market_reviews SET legacy_payload_json = ?", (json.dumps(payload),))
            conn.commit()
        identity = FeedbackIdentity("article", "cls_telegraph_api", "item-1")
        assert feedback_card_for_callback(identity, "duplicate", secret=TEST_SIGNING_KEY, db_path=db_path) is None


def test_callback_response_replaces_card_when_snapshot_is_available() -> None:
    identity = FeedbackIdentity("test", "feishu_feedback", "test-1")
    replacement = {"elements": [{"tag": "div"}]}
    result = {
        "toast": {"type": "success", "content": "已记录"},
        "card_state": {"identity": identity, "active_labels": ["duplicate"], "reason_tags": []},
    }
    with patch.object(feishu_feedback_service, "handle_feedback_callback", return_value=result), patch.object(
        feishu_feedback_service, "feedback_card_for_callback", return_value=replacement
    ) as render, patch("builtins.print") as logged:
        response = feishu_feedback_service.callback_response({"event": {}})
    assert response == {
        "toast": {"type": "success", "content": "已记录"},
        "card": {"type": "raw", "data": replacement},
    }
    assert render.call_args.args[:2] == (identity, ["duplicate"])
    log_text = " ".join(str(arg) for call in logged.call_args_list for arg in call.args)
    assert "status=recorded" in log_text and "card=updated" in log_text
    assert identity.source not in log_text and identity.item_id not in log_text


def test_callback_response_keeps_successful_feedback_toast_when_card_projection_fails() -> None:
    identity = FeedbackIdentity("test", "feishu_feedback", "test-1")
    result = {
        "toast": {"type": "success", "content": "已记录"},
        "card_state": {"identity": identity, "active_labels": ["duplicate"], "reason_tags": []},
    }
    with patch.object(feishu_feedback_service, "handle_feedback_callback", return_value=result), patch.object(
        feishu_feedback_service, "feedback_card_for_callback", side_effect=RuntimeError("update unavailable")
    ):
        response = feishu_feedback_service.callback_response({"event": {}})
    assert response == {"toast": {"type": "warning", "content": "反馈已记录，但卡片状态未更新"}}


def test_overflow_callback_value_is_parsed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "feedback.sqlite3"
        insert_delivered_article(db_path)
        token = build_feedback_token(
            FeedbackIdentity("article", "cls_telegraph_api", "item-1"), secret=TEST_SIGNING_KEY
        )
        payload = callback(token, "invalid", "evt-overflow", 100)
        payload["event"]["action"] = {
            "tag": "overflow",
            "option": json.dumps({"feedback_token": token, "label": "invalid", "reason_tag": "stale"}),
        }
        response = handle_feedback_callback(
            payload,
            secret=TEST_SIGNING_KEY,
            allowed_ids={OPERATOR},
            db_path=db_path,
        )
        assert "旧闻" in response["toast"]["content"]

        malformed = callback(token, "invalid", "evt-overflow-malformed", 200)
        malformed["event"]["action"] = {"tag": "overflow", "option": "not-json"}
        try:
            handle_feedback_callback(
                malformed,
                secret=TEST_SIGNING_KEY,
                allowed_ids={OPERATOR},
                db_path=db_path,
            )
        except FeedbackError as exc:
            assert "格式" in str(exc)
        else:
            raise AssertionError("malformed overflow option must fail")
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM market_feedback").fetchone()[0] == 1


def main() -> None:
    test_feedback_token_and_card_actions()
    test_last_click_wins_by_feishu_timestamp_and_keeps_history()
    test_labels_toggle_independently_and_keep_one_current_snapshot()
    test_unauthorized_operator_is_rejected()
    test_application_sender_returns_message_id()
    test_listener_only_mode_keeps_natural_feedback_delivery_disabled()
    test_test_card_feedback_is_audited_but_excluded_from_quality_metrics()
    test_more_reason_is_stored_with_invalid_feedback()
    test_every_feedback_label_combination_is_rendered_and_counted()
    test_legacy_single_selection_becomes_multiselect_without_rewriting_history()
    test_event_card_is_recovered_from_sent_delivery_payload()
    test_unified_review_without_card_snapshot_keeps_toast_only()
    test_callback_response_replaces_card_when_snapshot_is_available()
    test_callback_response_keeps_successful_feedback_toast_when_card_projection_fails()
    test_overflow_callback_value_is_parsed()
    print("market feedback checks passed")


if __name__ == "__main__":
    main()
