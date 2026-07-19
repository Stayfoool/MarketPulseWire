#!/usr/bin/env python3
"""Regressions for the inactive lifecycle, migration-preview, and replay slice."""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

from market_item import NormalizedMarketItem
from market_lifecycle_v1 import (
    AssessmentRecord,
    LifecycleProjection,
    LifecycleState,
    SourceIntegrationContract,
    begin_retry,
    finish_admission,
    finish_processability,
    finish_processing,
    project_legacy_article,
    project_legacy_event,
    retryable_projections,
    start_live_lifecycle,
)
from rule_config_migration_v1 import (
    LEGACY_SCHEMA_VERSION,
    MigrationPreviewError,
    preview_rule_config_migration,
)
from rule_core_replay import CurrentRuleOutcome, ReplayCase, build_replay_report
from rule_core_v1 import SourceAdmissionPolicy, parse_portfolio_config, parse_rule_config


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "rule_core_v1.test.json"


def public_config():
    return parse_rule_config(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))


def test_lifecycle_transitions_keep_decision_and_delivery_external() -> None:
    state = start_live_lifecycle(enrichment_required=True)
    assert state.retryable is True
    state = finish_processability(state, "succeeded", reason="public detail parsed")
    state = finish_admission(state, "admitted", reason="semiconductor_ai_scope")
    state = finish_processing(state, "succeeded")
    assert state.to_dict() == {
        "collection_class": "live",
        "processability_status": "succeeded",
        "admission_status": "admitted",
        "processing_status": "succeeded",
        "processability_reason": "public detail parsed",
        "admission_reason": "semiconductor_ai_scope",
        "processing_error": "",
        "contract_version": "market-lifecycle-v1",
    }
    assert "action" not in state.to_dict()
    assert "delivery" not in state.to_dict()

    projection = LifecycleProjection(
        discovery=project_legacy_article(
            {"source": "source_a", "item_id": "1", "title": "bounded"},
            {"id": "review-1", "decision_action": "push", "delivery_status": "duplicate"},
        ).discovery,
        lifecycle=state,
        assessment=AssessmentRecord("article_reviews", "review-1", "push", "duplicate"),
    )
    assert projection.user_label == "Push / 重复"

    retryable = finish_processing(
        finish_admission(
            finish_processability(start_live_lifecycle(enrichment_required=True), "fallback"),
            "admitted",
            reason="holding_direct_identity",
        ),
        "failed_retryable",
        error="bounded transient failure",
    )
    assert begin_retry(retryable).processing_status == "pending"
    assert retryable_projections((projection, LifecycleProjection(projection.discovery, retryable))) == (
        LifecycleProjection(projection.discovery, retryable),
    )


def test_lifecycle_rejects_invalid_shortcuts_and_terminal_retries() -> None:
    invalid_states = (
        ("baseline", "not_required", "admitted", "pending"),
        ("live", "failed_terminal", "excluded", "not_applicable"),
        ("live", "succeeded", "admitted", "not_applicable"),
        ("legacy_unclassified", "succeeded", "admitted", "succeeded"),
    )
    for values in invalid_states:
        try:
            LifecycleState(*values)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid lifecycle shortcut accepted: {values}")

    terminal = finish_processing(
        finish_admission(start_live_lifecycle(enrichment_required=False), "admitted", reason="scope"),
        "succeeded",
    )
    try:
        begin_retry(terminal)
    except ValueError:
        pass
    else:
        raise AssertionError("succeeded item cannot be selected for retry")


def test_legacy_adapters_classify_only_provable_states() -> None:
    seen = {
        "source": "wallstreetcn_news",
        "item_id": "article:1",
        "title": "bounded title",
        "summary": "bounded summary",
    }
    unknown = project_legacy_article(seen, None)
    assert unknown.user_label == "历史未分类"
    assert unknown.lifecycle.collection_class == "legacy_unclassified"

    reviewed = project_legacy_article(
        seen,
        {"id": "article:1", "decision_action": "daily"},
    )
    assert reviewed.user_label == "Daily"
    assert reviewed.lifecycle.admission_status == "admitted"

    event = {
        "source": "company_disclosures",
        "source_event_id": "notice:1",
        "title": "bounded notice",
        "baseline_only": 1,
    }
    baseline = project_legacy_event(event, None)
    assert baseline.user_label == "基线"
    assert baseline.assessment is None

    unproved_event = dict(event, source_event_id="notice:2", baseline_only=0)
    assert project_legacy_event(unproved_event, {"id": 2, "importance": "high"}).user_label == "历史未分类"


def test_every_normalized_source_contract_uses_all_five_families() -> None:
    article = SourceIntegrationContract(
        source="digitimes",
        store_kind="article",
        refetch_mode="bounded_payload",
        enrichment_required=False,
    )
    event = SourceIntegrationContract(
        source="company_disclosures",
        store_kind="event",
        refetch_mode="source_item_id",
        enrichment_required=True,
    )
    assert article.rule_families == event.rule_families
    assert set(article.rule_families) == {
        "holding", "semiconductor_ai", "macro_data", "fed_policy", "trade_policy"
    }
    try:
        SourceIntegrationContract(
            source="bad_source",
            store_kind="article",
            refetch_mode="url",
            enrichment_required=True,
            rule_families=("semiconductor_ai",),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("source-specific family restriction must fail closed")


def test_private_config_migration_preview_is_redacted_and_not_an_automatic_union() -> None:
    config_payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    legacy = {
        "schema_version": LEGACY_SCHEMA_VERSION,
        "origins": {
            "focus_keywords": ["HBM", "legacy-only-private-term"],
            "base_keywords": ["HBM", "GPU"],
            "include_keywords": ["operator-private-term"],
            "semiconductor_code_keywords": ["CPO", "code-only-private-term"],
        },
    }
    report = preview_rule_config_migration(legacy, config_payload)
    serialized = json.dumps(report, ensure_ascii=False)
    assert report["automatic_union_applied"] is False
    assert report["cross_origin_duplicate_count"] == 1
    assert report["dropped_count"] == 3
    assert report["validated_target_section_counts"]["trusted_domains"] == 2
    assert all(term_id.startswith("term:") for term_id in report["dropped_term_ids"])
    for private_term in (
        "HBM", "GPU", "CPO", "legacy-only-private-term", "operator-private-term", "code-only-private-term"
    ):
        assert private_term not in serialized

    broken = copy.deepcopy(legacy)
    broken["origins"].pop("base_keywords")
    try:
        preview_rule_config_migration(broken, config_payload)
    except MigrationPreviewError:
        pass
    else:
        raise AssertionError("missing legacy origin must fail closed")


def _replay_case(replay_id: str, source: str, text: str, *, group: str) -> ReplayCase:
    return ReplayCase(
        replay_id=replay_id,
        equivalence_group=group,
        item=NormalizedMarketItem(
            source=source,
            source_category="news_media",
            publisher_role="news_media",
            content_type="article",
            title=text,
        ),
        source_policy=SourceAdmissionPolicy(),
        current=CurrentRuleOutcome(
            admission_status="unknown",
            admission_reason="legacy_unclassified",
        ),
    )


def test_replay_reports_changes_invariance_and_missing_config_without_content_echo() -> None:
    config = public_config()
    portfolio = parse_portfolio_config([])
    text = "HBM supply shortage caused long-term contract queues."
    cases = (
        _replay_case("same-a", "digitimes", text, group="same-claim"),
        _replay_case("same-b", "wallstreetcn_news", text, group="same-claim"),
    )
    report = build_replay_report(cases, rule_config=config, portfolio=portfolio)
    assert report["status"] == "ok"
    assert report["changed_count"] == 2
    assert report["source_invariance_violations"] == []
    assert text not in json.dumps(report, ensure_ascii=False)

    divergent = build_replay_report(
        (
            cases[0],
            _replay_case("different-evidence", "wallstreetcn_news", "泛谈 AI 长期前景。", group="same-claim"),
        ),
        rule_config=config,
        portfolio=portfolio,
    )
    assert divergent["source_invariance_violations"] == [
        {
            "equivalence_group": "same-claim",
            "replay_ids": ["same-a", "different-evidence"],
            "sources": ["digitimes", "wallstreetcn_news"],
        }
    ]

    blocked = build_replay_report(
        cases,
        rule_config=config,
        portfolio=portfolio,
        missing_configuration=("portfolio_snapshot", "trusted_attribution.domains"),
    )
    assert blocked["status"] == "blocked"
    assert blocked["changes"] == []
    assert blocked["missing_configuration"] == [
        "portfolio_snapshot", "trusted_attribution.domains"
    ]

    try:
        _replay_case("unsafe title", "wallstreetcn_news", text, group="same-claim")
    except ValueError:
        pass
    else:
        raise AssertionError("replay output identifiers must not accept prose")


def test_inactive_slice_has_no_database_network_llm_or_delivery_imports() -> None:
    allowed_modules = {
        "market_lifecycle_v1.py",
        "rule_config_migration_v1.py",
        "rule_core_replay.py",
    }
    forbidden = {
        "sqlite3", "httpx", "requests", "urllib", "llm_analysis", "market_delivery",
        "market_runtime", "market_review_store",
    }
    for filename in allowed_modules:
        tree = ast.parse((ROOT / "scripts" / filename).read_text(encoding="utf-8"), filename=filename)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert not (imported & forbidden), (filename, imported & forbidden)


def main() -> int:
    test_lifecycle_transitions_keep_decision_and_delivery_external()
    test_lifecycle_rejects_invalid_shortcuts_and_terminal_retries()
    test_legacy_adapters_classify_only_provable_states()
    test_every_normalized_source_contract_uses_all_five_families()
    test_private_config_migration_preview_is_redacted_and_not_an_automatic_union()
    test_replay_reports_changes_invariance_and_missing_config_without_content_echo()
    test_inactive_slice_has_no_database_network_llm_or_delivery_imports()
    print("rule core integration v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
