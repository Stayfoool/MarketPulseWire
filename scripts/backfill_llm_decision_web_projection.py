#!/usr/bin/env python3
"""Preview or write bounded Web projections into private LLM audit files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from llm_decision_web import DEFAULT_AUDIT_DIR, write_web_projection


def run(audit_dir: Path, *, apply: bool) -> dict[str, int]:
    counts = {"files": 0, "changed": 0, "skipped": 0}
    if not audit_dir.is_dir():
        return counts
    for path in sorted(audit_dir.glob("llm-decision-audit-*.json")):
        counts["files"] += 1
        try:
            changed = write_web_projection(path, apply=apply)
        except (OSError, PermissionError):
            counts["skipped"] += 1
            continue
        counts["changed"] += int(changed)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, default=DEFAULT_AUDIT_DIR)
    parser.add_argument("--apply", action="store_true", help="write the projection into private audit files")
    args = parser.parse_args()
    print(json.dumps({"ok": True, **run(args.audit_dir, apply=args.apply)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
