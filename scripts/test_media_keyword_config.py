#!/usr/bin/env python3
"""Regression checks for the unified media/semiconductor keyword config."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from tempfile import TemporaryDirectory

from media_keyword_config import (
    keyword_matches_text,
    load_media_keyword_config,
    media_keyword_match,
    save_media_keyword_config,
)
from admission_rules import parse_rule_config


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_CONFIG = ROOT / "config" / "rule_core_v1.test.json"


def _config(path: Path) -> dict[str, object]:
    payload = json.loads(PUBLIC_CONFIG.read_text(encoding="utf-8"))
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def test_save_updates_one_rule_config_atomically_and_preserves_other_rules() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "private-rule-config.json"
        original = _config(path)
        saved = save_media_keyword_config(
            ["HBM", "SMIC", "smic", "JCET"],
            ["培训广告"],
            path,
        )
        assert saved["changed"] is True
        assert Path(str(saved["backup_path"])).is_file()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        updated = json.loads(path.read_text(encoding="utf-8"))
        assert updated["semiconductor_ai_keywords"] == ["HBM", "SMIC", "JCET"]
        assert updated["exclude_keywords"] == ["培训广告"]
        assert updated["macro_data"] == original["macro_data"]
        assert updated["trade_policy"] == original["trade_policy"]
        assert str(updated["config_version"]).startswith("web-rule-config-")
        assert parse_rule_config(updated).semiconductor_ai_keywords == ("HBM", "SMIC", "JCET")

        unchanged = save_media_keyword_config(
            ["HBM", "SMIC", "JCET"], ["培训广告"], path
        )
        assert unchanged["changed"] is False
        assert unchanged["backup_path"] == ""


def test_runtime_match_reads_the_same_private_rule_keywords() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "private-rule-config.json"
        _config(path)
        save_media_keyword_config(["SMIC", "JCET"], ["培训广告"], path)
        previous = os.environ.get("RULE_CORE_CONFIG")
        os.environ["RULE_CORE_CONFIG"] = str(path)
        try:
            assert media_keyword_match("SMIC advances 7nm") == {
                "matched": True,
                "blocked": False,
                "keyword": "SMIC",
                "bucket": "semiconductor_ai",
            }
            blocked = media_keyword_match("SMIC 培训广告")
            assert blocked["blocked"] is True
            assert load_media_keyword_config()["semiconductor_ai_keywords"] == ["SMIC", "JCET"]
            assert keyword_matches_text("SMIC", "SMIC advances") is True
            assert keyword_matches_text("SMIC", "COSMIC advances") is False
        finally:
            if previous is None:
                os.environ.pop("RULE_CORE_CONFIG", None)
            else:
                os.environ["RULE_CORE_CONFIG"] = previous


def main() -> int:
    test_save_updates_one_rule_config_atomically_and_preserves_other_rules()
    test_runtime_match_reads_the_same_private_rule_keywords()
    print("unified media keyword config checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
