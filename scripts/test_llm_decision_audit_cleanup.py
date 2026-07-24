#!/usr/bin/env python3
"""Retention checks for production LLM decision audits."""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from llm_decision_audit_cleanup import redact_expired_production_audits


def write_audit(path: Path, generated_at: datetime) -> None:
    path.write_text(
        json.dumps(
            {
                "generated_at": generated_at.isoformat(),
                "market_item_id": 1,
                "market_review_id": 2,
                "decision": {"action": "push"},
                "model_audit": {
                    "calls": [
                        {
                            "request": {"messages": [{"content": "PRIVATE_INPUT"}]},
                            "response": {"content": "PRIVATE_OUTPUT"},
                        }
                    ]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


def test_only_expired_sensitive_payload_is_redacted() -> None:
    with TemporaryDirectory() as tmp:
        audit_dir = Path(tmp)
        now = datetime(2026, 7, 24, tzinfo=timezone.utc)
        old_path = audit_dir / "llm-decision-audit-1-2-old.json"
        recent_path = audit_dir / "llm-decision-audit-3-4-recent.json"
        write_audit(old_path, now - timedelta(days=31))
        write_audit(recent_path, now - timedelta(days=29))
        assert redact_expired_production_audits(audit_dir, now=now) == 1
        old = json.loads(old_path.read_text(encoding="utf-8"))
        recent = json.loads(recent_path.read_text(encoding="utf-8"))
        assert old["decision"]["action"] == "push"
        assert old["model_audit"]["status"] == "expired"
        assert "PRIVATE_INPUT" not in old_path.read_text(encoding="utf-8")
        assert "PRIVATE_OUTPUT" in recent_path.read_text(encoding="utf-8")
        assert stat.S_IMODE(old_path.stat().st_mode) == 0o600
        assert recent["model_audit"]["calls"]


def main() -> int:
    test_only_expired_sensitive_payload_is_redacted()
    print("LLM decision audit cleanup checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
