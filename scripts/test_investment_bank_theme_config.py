#!/usr/bin/env python3
"""Regression checks for private international-bank theme rule configuration."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from investment_bank_theme_config import DEFAULT_CONFIG, load_config, normalize_config, save_config


def test_normalize_config_bounds_and_dedup() -> None:
    config = normalize_config(
        {
            "enabled": 0,
            "allowed_banks": ["高盛", "goldman sachs", "高盛"],
            "extra_theme_keywords": ["卫星通信", "卫星通信"],
            "extra_action_keywords": ["战略性增配"],
            "min_evidence_score": 99,
            "allow_secondary_sources": 0,
            "dedup_lookback_days": 0,
        }
    )
    assert config["enabled"] is False
    assert config["allowed_banks"] == ["高盛", "goldman sachs"]
    assert config["min_evidence_score"] == 8
    assert config["dedup_lookback_days"] == 1
    assert config["allow_secondary_sources"] is False


def test_roundtrip_private_config() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "investment_bank_theme_rules.json"
        assert load_config(path) == DEFAULT_CONFIG
        saved = save_config({"extra_theme_keywords": ["卫星通信"], "min_evidence_score": 3}, path)
        assert saved["extra_theme_keywords"] == ["卫星通信"]
        assert load_config(path)["min_evidence_score"] == 3


def main() -> int:
    test_normalize_config_bounds_and_dedup()
    test_roundtrip_private_config()
    print("investment bank theme config checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
