#!/usr/bin/env python3
"""Regression checks for the unified media/semiconductor keyword config."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from tempfile import TemporaryDirectory

from llm_rule_decision import apply_source_admission_boundary
from market_item import NormalizedMarketItem
from media_keyword_config import (
    keyword_matches_text,
    load_media_keyword_config,
    media_keyword_match,
    save_media_keyword_config,
)
from migrate_media_keywords import APPROVED_SAFE_ALIASES, build_migration
from migrate_admission_simplification import build_migration as build_admission_migration
from rule_core_v1 import (
    SourceAdmissionPolicy,
    admit_market_item,
    parse_portfolio_config,
    parse_rule_config,
)


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


def test_migration_preserves_user_delta_without_restoring_generic_power_defaults() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        rule_path = root / "private-rule-config.json"
        payload = _config(rule_path)
        payload["semiconductor_ai_keywords"] = ["HBM", "GPU"]
        payload["semiconductor_ai_title_keywords"] = []
        rule_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        legacy_path = root / "media_keywords.json"
        legacy_path.write_text(
            json.dumps(
                {
                    "base_keywords": ["operator-private-term"],
                    "include_keywords": ["金刚石散热"],
                    "exclude_keywords": ["培训广告"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        migration = build_migration(rule_path=rule_path, legacy_path=legacy_path)
        keywords = migration["keywords"]
        assert keywords[:2] == ["HBM", "GPU"]
        assert "operator-private-term" in keywords
        assert "金刚石散热" in keywords
        assert all(alias in keywords for alias in APPROVED_SAFE_ALIASES)
        assert "电力" not in keywords
        assert "power" not in keywords
        assert migration["omitted_legacy_code_count"] == 5
        assert migration["target_exclude_count"] == 4


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


def test_admission_simplification_migration_is_redacted_and_primary_only() -> None:
    payload = json.loads(PUBLIC_CONFIG.read_text(encoding="utf-8"))
    payload["semiconductor_ai_keywords"] = ["AI", "人工智能", "ModelCo", "HBM"]
    payload["semiconductor_ai_title_keywords"] = []
    payload["macro_data"] = {
        "indicators": ["CPI", "ADP"],
        "context_aliases": ["美国"],
        "tiers": {"primary": ["CPI"], "secondary": ["ADP"]},
    }
    migration = build_admission_migration(payload, title_keywords=["ModelCo"])
    updated = migration["updated"]
    preview = migration["preview"]
    assert updated["semiconductor_ai_keywords"] == ["ModelCo", "HBM"]
    assert updated["semiconductor_ai_title_keywords"] == ["ModelCo"]
    assert updated["macro_data"] == {
        "indicators": ["CPI"],
        "context_aliases": ["美国"],
    }
    assert preview["removed_generic_count"] == 2
    assert preview["removed_macro_indicator_count"] == 1
    assert "ModelCo" not in json.dumps(preview)


def test_admission_simplification_rejects_title_terms_outside_master_list() -> None:
    payload = json.loads(PUBLIC_CONFIG.read_text(encoding="utf-8"))
    try:
        build_admission_migration(payload, title_keywords=["private-unknown-term"])
    except ValueError as exc:
        assert "term:" in str(exc)
        assert "private-unknown-term" not in str(exc)
    else:
        raise AssertionError("unknown title-only terms must fail closed")


def test_new_aliases_are_cross_source_and_holding_only_sources_stay_bounded() -> None:
    payload = json.loads(PUBLIC_CONFIG.read_text(encoding="utf-8"))
    payload["semiconductor_ai_keywords"] = list(APPROVED_SAFE_ALIASES)
    payload["semiconductor_ai_title_keywords"] = []
    config = parse_rule_config(payload)
    portfolio = parse_portfolio_config([])
    for source in ("digitimes_en_daily", "wallstreetcn_news"):
        item = NormalizedMarketItem(
            source=source,
            source_category="industry_media" if "digitimes" in source else "domestic_finance_media",
            title="SMIC 7nm advances while JCET reports stronger results",
        )
        admission = apply_source_admission_boundary(
            item,
            admit_market_item(
                item,
                rule_config=config,
                portfolio=portfolio,
                source_policy=SourceAdmissionPolicy(),
            ),
        )
        assert admission.status == "admitted"
        assert admission.matched_families == ("semiconductor_ai",)

    for source in ("company_disclosures", "sina_stock_news"):
        item = NormalizedMarketItem(source=source, title="SMIC 7nm advances")
        admission = apply_source_admission_boundary(
            item,
            admit_market_item(
                item,
                rule_config=config,
                portfolio=portfolio,
                source_policy=SourceAdmissionPolicy(),
            ),
        )
        assert admission.status == "excluded"
        assert admission.reason_code == "holding_scope_required_for_source"


def main() -> int:
    test_save_updates_one_rule_config_atomically_and_preserves_other_rules()
    test_migration_preserves_user_delta_without_restoring_generic_power_defaults()
    test_runtime_match_reads_the_same_private_rule_keywords()
    test_admission_simplification_migration_is_redacted_and_primary_only()
    test_admission_simplification_rejects_title_terms_outside_master_list()
    test_new_aliases_are_cross_source_and_holding_only_sources_stay_bounded()
    print("unified media keyword config checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
