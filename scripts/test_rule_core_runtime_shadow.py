#!/usr/bin/env python3
"""Regression checks for comparisons from the production normalized item."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import rule_core_runtime_shadow as runtime_shadow
from market_item import DecisionResult, NormalizedMarketItem


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_CONFIG = ROOT / "config" / "rule_core_v1.test.json"


def _files(root: Path) -> tuple[Path, Path]:
    config = root / "rule.json"
    portfolio = root / "portfolio.json"
    config.write_text(PUBLIC_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    portfolio.write_text("[]\n", encoding="utf-8")
    return config, portfolio


def _env(config: Path, portfolio: Path) -> dict[str, str]:
    return {
        "RULE_CORE_SHADOW_AUTORUN": "1",
        "RULE_CORE_SHADOW_CONFIG": str(config),
        "RULE_CORE_SHADOW_PORTFOLIO": str(portfolio),
    }


def _item() -> NormalizedMarketItem:
    return NormalizedMarketItem(
        source="wallstreetcn_news",
        source_category="news_media",
        collector="news_collector",
        content_type="article",
        title="DRAM价格持续上涨",
        summary="供应继续收紧。",
        full_text="PRIVATE_BODY DRAM价格持续上涨，供应极度紧缺，预计第三季度环比涨幅13%至18%。",
        url="https://example.test/article/1",
        raw={"id": "article:1"},
    )


def test_runtime_item_writes_bounded_comparison_without_body() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        config, portfolio = _files(root)
        result = runtime_shadow.record_runtime_comparison(
            _item(),
            DecisionResult(action="daily", importance="medium", reason="现有规则结果"),
            {"store_kind": "article_reviews", "item_id": "article:1"},
            report_dir=root / "reports",
            env=_env(config, portfolio),
        )
        assert result["status"] == "completed"
        report_path = Path(result["report"])
        assert report_path.name.startswith("rule-core-shadow-news-")
        text = report_path.read_text(encoding="utf-8")
        assert "PRIVATE_BODY" not in text
        payload = json.loads(text)
        assert all(
            "evidence_quote" not in evidence
            for evidence in payload["items"][0]["comparison"]["candidate"]["admission_evidence"]
        )
        assert payload["input_mode"] == "production_normalized_item"
        assert payload["rule_core_version"] == runtime_shadow.RULE_CORE_VERSION
        assert payload["rule_config_version"] == "public-test-v1"
        assert payload["application_revision"] == runtime_shadow._application_revision()
        assert payload["comparison_only"] is True
        assert payload["affects_current_decision"] is False
        assert payload["counts"]["compared"] == 1
        assert payload["items"][0]["item_id"] == "article:1"
        assert payload["items"][0]["input_evidence"]["full_text_chars"] == len(_item().full_text)
        assert payload["items"][0]["comparison"]["current"]["action"] == "daily"
        assert payload["items"][0]["comparison"]["candidate"]["action"] == "archive"
        assert payload["counts"]["action_changes_by_pair"] == {"daily->archive": 1}


def test_runtime_report_records_deployed_application_revision() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        config, portfolio = _files(root)
        (root / "REVISION").write_text("commit=abc123\ndirty=0\n", encoding="utf-8")
        original_root = runtime_shadow.ROOT
        runtime_shadow.ROOT = root
        try:
            result = runtime_shadow.record_runtime_comparison(
                _item(),
                DecisionResult(action="daily"),
                {"item_id": "article:revision"},
                report_dir=root / "reports",
                env=_env(config, portfolio),
            )
        finally:
            runtime_shadow.ROOT = original_root
        payload = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
        assert payload["application_revision"] == "abc123"


def test_disabled_and_invalid_config_are_fail_safe() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        disabled = runtime_shadow.record_runtime_comparison(
            _item(),
            DecisionResult(action="push"),
            {"item_id": "article:1"},
            report_dir=root / "reports",
            env={},
        )
        assert disabled == {"status": "disabled"}
        assert not (root / "reports").exists()

        config, portfolio = _files(root)
        config.write_text("{}", encoding="utf-8")
        failed = runtime_shadow.record_runtime_comparison(
            _item(),
            DecisionResult(action="push"),
            {"item_id": "article:1"},
            report_dir=root / "reports",
            env=_env(config, portfolio),
        )
        assert failed["status"] == "failed"
        assert not (root / "reports").exists() or not list((root / "reports").glob("*.json"))


def test_runtime_report_keeps_only_trusted_institution_id() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        config, portfolio = _files(root)
        quote = "受信研究机构表示，HBM市场规模预测已从100亿美元上调至120亿美元。"
        item = NormalizedMarketItem(
            source="wallstreetcn_news",
            source_category="news_media",
            title=quote,
            raw={
                "id": "article:attributed-1",
                "_attributed_research": {
                    "institution_id": "trusted_research",
                    "attribution": "explicit",
                    "attribution_quote": quote,
                    "claims": [{"event_type": "forecast_revision", "evidence_quote": quote}],
                    "extraction_mode": "llm",
                },
            },
        )
        result = runtime_shadow.record_runtime_comparison(
            item,
            DecisionResult(action="push", importance="high", reason="现有规则结果"),
            {"store_kind": "article_reviews", "item_id": "article:attributed-1"},
            report_dir=root / "reports",
            env=_env(config, portfolio),
        )
        assert result["status"] == "completed"
        payload = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
        comparison = payload["items"][0]["comparison"]
        assert comparison["candidate"]["attributed_institutions"] == ["trusted_research"]
        assert "attribution_quote" not in json.dumps(comparison, ensure_ascii=False)
        assert "claim_quote" not in json.dumps(comparison, ensure_ascii=False)


def test_current_admission_exclusion_compares_without_a_current_decision() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        config, portfolio = _files(root)
        result = runtime_shadow.record_runtime_comparison(
            _item(),
            None,
            {"store_kind": "seen_items", "item_id": "article:1"},
            report_dir=root / "reports",
            env=_env(config, portfolio),
            current_admission_status="excluded",
            current_admission_reason="investment_universe_no_match",
        )
        assert result["status"] == "completed"
        payload = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
        comparison = payload["items"][0]["comparison"]
        assert comparison["current"]["admission_status"] == "excluded"
        assert comparison["current"]["action"] is None
        assert comparison["candidate"]["action"] == "archive"
        assert payload["counts"]["action_changes_by_pair"] == {"none->archive": 1}


def test_config_cache_reloads_only_after_input_changes() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        config, portfolio = _files(root)
        env = _env(config, portfolio)
        runtime_shadow._CONFIG_CACHE = None
        first = runtime_shadow._load_config(env)
        second = runtime_shadow._load_config(env)
        assert first is not None and second is not None
        assert first[0] is second[0]

        payload = json.loads(config.read_text(encoding="utf-8"))
        payload["config_version"] = "public-test-v1-reloaded"
        config.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        third = runtime_shadow._load_config(env)
        assert third is not None
        assert third[0] is not first[0]
        assert third[0].config_version == "public-test-v1-reloaded"


def main() -> int:
    test_runtime_item_writes_bounded_comparison_without_body()
    test_runtime_report_records_deployed_application_revision()
    test_disabled_and_invalid_config_are_fail_safe()
    test_runtime_report_keeps_only_trusted_institution_id()
    test_current_admission_exclusion_compares_without_a_current_decision()
    test_config_cache_reloads_only_after_input_changes()
    print("rule core runtime shadow checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
