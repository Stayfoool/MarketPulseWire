#!/usr/bin/env python3
"""Remove sensitive LLM request/response content after the 30-day retention window."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from llm_production_decision import DEFAULT_AUDIT_DIR
from rule_core_shadow_combined import REPORT_DIR
from rule_core_shadow_daily import MODEL_AUDIT_RETENTION_DAYS, redact_expired_model_audits


def redact_expired_production_audits(
    audit_dir: Path = DEFAULT_AUDIT_DIR,
    *,
    now: datetime | None = None,
    retention_days: int = MODEL_AUDIT_RETENTION_DAYS,
) -> int:
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=max(1, retention_days))
    redacted = 0
    if not audit_dir.is_dir():
        return 0
    for path in audit_dir.glob("llm-decision-audit-*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        try:
            generated_at = datetime.fromisoformat(str(payload.get("generated_at") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if generated_at >= cutoff:
            continue
        audit = payload.get("model_audit")
        if not isinstance(audit, dict) or not audit.get("calls"):
            continue
        payload["model_audit"] = {
            "status": "expired",
            "retention_days": retention_days,
            "expired_at": current.isoformat(),
        }
        temporary = path.with_name(f".{path.name}.{os.getpid()}.retention.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
        os.chmod(path, 0o600)
        redacted += 1
    return redacted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--audit-dir", type=Path, default=DEFAULT_AUDIT_DIR)
    args = parser.parse_args()
    historical = redact_expired_model_audits(args.report_dir)
    production = redact_expired_production_audits(args.audit_dir)
    print(
        json.dumps(
            {
                "historical_comparison_audits_redacted": historical,
                "production_decision_audits_redacted": production,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
