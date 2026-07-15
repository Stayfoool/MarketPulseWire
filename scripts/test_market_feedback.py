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

from market_feedback import (
    FeedbackError,
    FeedbackIdentity,
    append_feedback_actions,
    build_feedback_token,
    current_feedback_rows,
    feedback_quality_payload,
    handle_feedback_callback,
    parse_feedback_token,
)
from market_db import init_db
from market_review_store import ensure_article_reviews_table


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
        ensure_article_reviews_table(conn)
        conn.execute(
            """
            INSERT INTO article_reviews (
                source, item_id, url, title, source_module, published_at,
                importance, push_now, market_impact, incremental_classification,
                affected_targets_json, reason, daily_summary, confidence,
                gate_json, skeptic_json, pre_skeptic_importance, pushed_at, created_at
            ) VALUES (?, ?, '', 'Feedback fixture', '', '', 'high', 1, '', '', '[]', '', '', '', ?, '{}', '', ?, ?)
            """,
            (
                "cls_telegraph_api",
                "item-1",
                json.dumps({"raw": {"decision_result": decision}}),
                "2026-07-15T00:00:00+00:00",
                "2026-07-15T00:00:00+00:00",
            ),
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
        assert delayed_old["result"]["current_label"] == "invalid"
        current = current_feedback_rows(db_path)
        assert len(current) == 1
        assert current[0]["label"] == "invalid"
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM market_feedback").fetchone()[0] == 3
            stored = conn.execute(
                "SELECT decision_action, rule_ids_json, delivery_status FROM market_feedback WHERE feedback_event_id='evt-2'"
            ).fetchone()
        assert stored == ("push", '["industry_quantified_hardline"]', "sent")

        quality = feedback_quality_payload(db_path=db_path, days=30)
        assert quality["summary"]["delivered"] == 1
        assert quality["summary"]["labelled"] == 1
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
        assert repeated_old["result"]["current_label"] == "invalid"
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM market_feedback").fetchone()[0] == 3


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
            "value": json.dumps({"feedback_token": token, "label": "invalid", "reason_tag": "stale"}),
        }
        response = handle_feedback_callback(
            payload,
            secret=TEST_SIGNING_KEY,
            allowed_ids={OPERATOR},
            db_path=db_path,
        )
        assert "旧闻" in response["toast"]["content"]


def main() -> None:
    test_feedback_token_and_card_actions()
    test_last_click_wins_by_feishu_timestamp_and_keeps_history()
    test_unauthorized_operator_is_rejected()
    test_application_sender_returns_message_id()
    test_listener_only_mode_keeps_natural_feedback_delivery_disabled()
    test_test_card_feedback_is_audited_but_excluded_from_quality_metrics()
    test_more_reason_is_stored_with_invalid_feedback()
    test_overflow_callback_value_is_parsed()
    print("market feedback checks passed")


if __name__ == "__main__":
    main()
