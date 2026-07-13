#!/usr/bin/env python3
"""Focused checks for the explicit JYGS independent-route exception."""

from __future__ import annotations

import os

import jygs_actions


def test_jygs_delivery_uses_only_its_explicit_prediction_contract() -> None:
    original_webhook = os.environ.get("FEISHU_WEBHOOK")
    original_send = jygs_actions.send_card
    original_card = jygs_actions.jygs_event_card
    calls: list[dict] = []
    try:
        os.environ["FEISHU_WEBHOOK"] = "https://example.invalid/webhook"
        jygs_actions.send_card = lambda card: calls.append(card) or True
        jygs_actions.jygs_event_card = lambda *_args, **_kwargs: {"header": {"title": "JYGS test"}}
        assert jygs_actions.deliver_jygs_event(
            1, {"importance": "high", "push_decision": {"should_push": False}}
        ) == "skipped"
        assert jygs_actions.deliver_jygs_event(
            1, {"importance": "low", "push_decision": {"should_push": True}}
        ) == "skipped"
        assert jygs_actions.deliver_jygs_event(
            1, {"importance": "medium", "push_decision": {"should_push": True}}
        ) == "sent"
        assert len(calls) == 1
    finally:
        jygs_actions.send_card = original_send
        jygs_actions.jygs_event_card = original_card
        if original_webhook is None:
            os.environ.pop("FEISHU_WEBHOOK", None)
        else:
            os.environ["FEISHU_WEBHOOK"] = original_webhook


def main() -> int:
    test_jygs_delivery_uses_only_its_explicit_prediction_contract()
    print("jygs action checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
