#!/usr/bin/env python3
"""Regression tests for the skeptic evaluator."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from market_content_adapter import gate_lines
from market_db import init_db
from market_review_store import official_review_exists, save_official_review
import skeptic_evaluator
from skeptic_evaluator import apply_skeptic_review, history_candidates


def iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def seed_seen_item(conn: sqlite3.Connection, *, title: str, days_ago: int = 3) -> None:
    seen_at = iso_days_ago(days_ago)
    conn.execute(
        """
        INSERT INTO seen_items (source, item_id, url, title, summary, published_at, first_seen_at)
        VALUES ('old_source', 'old-1', 'https://example.com/old', ?, '', ?, ?)
        """,
        (title, seen_at, seen_at),
    )
    conn.commit()


def test_skeptic_downgrades_duplicate_article() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "surveil.sqlite3"
        conn = init_db(path)
        seed_seen_item(conn, title="美光业绩超预期 存储价格继续上涨", days_ago=3)
        review = {
            "importance": "high",
            "push_now": True,
            "market_impact": "利好存储链。",
            "incremental_classification": "增量利好",
            "affected_targets": ["存储"],
            "reason": "业绩超预期。",
            "daily_summary": "美光业绩超预期。",
            "confidence": "中",
        }
        item = {
            "id": "new-1",
            "url": "https://example.com/new",
            "title": "美光业绩超预期 存储价格继续上涨",
            "published_at": datetime.now(timezone.utc).isoformat(),
            "summary": "重复报道。",
            "full_text": "重复报道。",
        }
        updated = apply_skeptic_review(conn, source="new_source", item=item, review=review, push_key="push_now")
        assert updated["push_now"] is False
        assert updated["importance"] == "medium"
        assert updated["skeptic"]["skeptic_verdict"] == "downgrade"
        assert updated["skeptic"]["old_news_risk"] == "high"
        assert "Skeptic" in "\n".join(gate_lines(updated))
        conn.close()


def test_official_review_preserves_skeptic_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "surveil.sqlite3"
        conn = init_db(path)
        seed_seen_item(conn, title="NVIDIA 发布 Rubin 100% 液冷方案", days_ago=4)
        review = {
            "importance": "high",
            "should_push_now": True,
            "reason": "官网发布技术方案。",
            "daily_summary": "NVIDIA 发布 Rubin 液冷方案。",
            "analysis": {"core_content": "NVIDIA 发布 Rubin 100% 液冷方案。"},
        }
        item = {
            "id": "nvidia-rubin",
            "url": "https://example.com/rubin",
            "title": "NVIDIA 发布 Rubin 100% 液冷方案",
            "published_at": datetime.now(timezone.utc).isoformat(),
            "full_text": "官网发布 Rubin 液冷方案。",
        }
        updated = apply_skeptic_review(conn, source="nvidia_blog", item=item, review=review, push_key="should_push_now")
        save_official_review(conn, "nvidia_blog", item, updated)
        loaded = official_review_exists(conn, "nvidia_blog", "nvidia-rubin")
        assert loaded is not None
        assert loaded["should_push_now"] is False
        assert loaded["skeptic"]["skeptic_verdict"] == "downgrade"
        assert loaded["skeptic"]["old_news_risk"] == "high"
        row = conn.execute(
            """
            SELECT skeptic_json, pre_skeptic_importance
            FROM official_news_reviews
            WHERE source = 'nvidia_blog' AND item_id = 'nvidia-rubin'
            """
        ).fetchone()
        assert row[0] and "old_news_risk" in row[0]
        assert row[1] == "high"
        conn.close()


def test_history_candidates_respects_cutoff() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "surveil.sqlite3"
        conn = init_db(path)
        seed_seen_item(conn, title="旧闻测试标题", days_ago=30)
        rows = history_candidates(
            conn,
            source="new_source",
            item={
                "id": "new-cutoff",
                "url": "https://example.com/new-cutoff",
                "title": "旧闻测试标题",
                "published_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        assert rows == []
        conn.close()


def test_source_profile_can_disable_skeptic() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "surveil.sqlite3"
        conn = init_db(path)
        seed_seen_item(conn, title="美光业绩超预期 存储价格继续上涨", days_ago=3)
        original_profile_flag = skeptic_evaluator.source_profile_skeptic_enabled

        def disabled_profile(_source: str, **_kwargs) -> bool:
            return False

        review = {
            "importance": "high",
            "push_now": True,
            "reason": "业绩超预期。",
        }
        item = {
            "id": "new-1",
            "url": "https://example.com/new",
            "title": "美光业绩超预期 存储价格继续上涨",
            "published_at": datetime.now(timezone.utc).isoformat(),
            "summary": "重复报道。",
        }
        try:
            skeptic_evaluator.source_profile_skeptic_enabled = disabled_profile
            updated = apply_skeptic_review(conn, source="cls_telegraph_api", item=item, review=review, push_key="push_now")
        finally:
            skeptic_evaluator.source_profile_skeptic_enabled = original_profile_flag

        assert updated == review
        conn.close()


def test_skeptic_llm_failure_records_health_without_blocking() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "surveil.sqlite3"
        conn = init_db(path)
        original_llm = skeptic_evaluator.llm_skeptic_review

        def fake_llm(**_kwargs):
            raise RuntimeError("synthetic skeptic failure")

        review = {
            "importance": "high",
            "push_now": True,
            "reason": "新增重大事件。",
        }
        item = {
            "id": "fresh-1",
            "url": "https://example.com/fresh",
            "title": "全新事件 无历史重复",
            "published_at": datetime.now(timezone.utc).isoformat(),
            "full_text": "全新事件，无本地历史重复。",
        }
        try:
            skeptic_evaluator.llm_skeptic_review = fake_llm
            updated = apply_skeptic_review(conn, source="fresh_source", item=item, review=review, push_key="push_now")
        finally:
            skeptic_evaluator.llm_skeptic_review = original_llm

        assert updated["push_now"] is True
        assert updated["skeptic"]["mode"] == "llm_error"
        row = conn.execute(
            """
            SELECT consecutive_failures, last_error
            FROM source_health
            WHERE monitor = 'signal_pipeline' AND source = 'skeptic_evaluator'
            """
        ).fetchone()
        assert row[0] == 1
        assert "synthetic skeptic failure" in row[1]
        conn.close()


def test_hbm_hard_variable_override_keeps_push_now() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "surveil.sqlite3"
        conn = init_db(path)
        original_llm = skeptic_evaluator.llm_skeptic_review

        def fake_llm(**_kwargs):
            return {
                "skeptic_verdict": "downgrade",
                "old_news_risk": "medium",
                "price_in_risk": "high",
                "over_linking_risk": "high",
                "hard_variable_score": 30,
                "relation_strength_score": 20,
                "reason": "未披露具体 A 股供应商，存在 price in 风险。",
                "what_would_change_mind": "需要明确供应商。",
                "final_push_suggestion": "daily",
                "mode": "fake",
            }

        review = {
            "importance": "high",
            "push_now": True,
            "market_impact": "利好 HBM4 测试设备和半导体后道测试环节。",
            "incremental_classification": "增量利好",
            "affected_targets": ["半导体设备"],
            "reason": "SK海力士拟采购 HBM4 测试仪，总投资最高 4000亿韩元。",
            "daily_summary": "SK海力士拟订购 HBM4 测试仪。",
            "confidence": "中",
        }
        item = {
            "id": "hbm4-testers",
            "url": "https://example.com/hbm4-testers",
            "title": "SK海力士拟订购逾200台HBM4测试仪 总价最高可达4000亿韩元",
            "published_at": datetime.now(timezone.utc).isoformat(),
            "summary": "清州封装工厂采购约200台检测设备，涵盖下一代高带宽内存HBM4测试仪。",
            "full_text": "SK海力士正推进清州封装工厂检测设备采购谈判，预计采购规模达200台，重点涵盖HBM4测试仪，总投资额最高可达4000亿韩元。",
        }
        try:
            skeptic_evaluator.llm_skeptic_review = fake_llm
            updated = apply_skeptic_review(conn, source="cls_telegraph_api", item=item, review=review, push_key="push_now")
        finally:
            skeptic_evaluator.llm_skeptic_review = original_llm

        assert updated["push_now"] is True
        assert updated["importance"] == "high"
        assert updated["industry_hard_variable_override"] is True
        assert updated["skeptic"]["industry_hard_variable_override"] is True
        assert "受益标的待确认" in updated["affected_targets"]
        assert "产业硬变量覆盖" in updated["reason"]
        conn.close()


def test_nvidia_ai_platform_delay_override_keeps_push_now() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "surveil.sqlite3"
        conn = init_db(path)
        original_llm = skeptic_evaluator.llm_skeptic_review

        def fake_llm(**_kwargs):
            return {
                "skeptic_verdict": "downgrade",
                "old_news_risk": "low",
                "price_in_risk": "medium",
                "over_linking_risk": "high",
                "hard_variable_score": 20,
                "relation_strength_score": 20,
                "reason": "SemiAnalysis 单一研究机构爆料，缺乏 NVIDIA 官方确认。",
                "what_would_change_mind": "需要 NVIDIA 官方确认。",
                "final_push_suggestion": "daily",
                "mode": "fake",
            }

        review = {
            "importance": "high",
            "push_now": True,
            "market_impact": "可能利空英伟达 AI 服务器供应链，并影响高端 PCB 预期。",
            "incremental_classification": "增量利空",
            "affected_targets": ["英伟达", "沪电股份", "鹏鼎控股"],
            "reason": "SemiAnalysis 爆料英伟达 Kyber NVL144 机架因 PCB 中板制造工艺挑战延迟超过12个月。",
            "daily_summary": "英伟达 Kyber NVL144 机架或因 PCB 中板制造工艺挑战延迟至2028年。",
            "confidence": "中",
        }
        item = {
            "id": "kyber-delay",
            "url": "https://example.com/kyber-delay",
            "title": "PCB成关键瓶颈？机构爆料：制造工艺面临挑战 英伟达Kyber机架或遇延迟",
            "published_at": datetime.now(timezone.utc).isoformat(),
            "summary": "SemiAnalysis称英伟达Kyber NVL144机架架构或延迟超过12个月，原因是PCB中板制造工艺仍面临重大挑战。",
            "full_text": "半导体行业研究机构SemiAnalysis指出，英伟达Kyber NVL144机架架构或将遭遇延迟超过12个月，推迟至2028年。延迟原因是PCB中板制造工艺仍面临重大挑战。",
        }
        try:
            skeptic_evaluator.llm_skeptic_review = fake_llm
            updated = apply_skeptic_review(conn, source="cls_telegraph_api", item=item, review=review, push_key="push_now")
        finally:
            skeptic_evaluator.llm_skeptic_review = original_llm

        assert updated["push_now"] is True
        assert updated["importance"] == "high"
        assert updated["industry_hard_variable_override"] is True
        assert updated["skeptic"]["industry_hard_variable_override_type"] == "ai_platform_delay"
        assert "PCB中板/高多层板" in updated["affected_targets"]
        assert "待确认" in updated["reason"]
        conn.close()


def test_attributed_research_rule_ignores_llm_only_downgrade() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "surveil.sqlite3"
        conn = init_db(path)
        original_llm = skeptic_evaluator.llm_skeptic_review

        def fake_llm(**_kwargs):
            return {
                "skeptic_verdict": "downgrade",
                "old_news_risk": "low",
                "price_in_risk": "high",
                "over_linking_risk": "medium",
                "hard_variable_score": 80,
                "relation_strength_score": 80,
                "reason": "模型认为可能已经部分定价。",
                "what_would_change_mind": "",
                "final_push_suggestion": "daily",
                "mode": "llm",
            }

        review = {
            "importance": "high",
            "push_now": True,
            "reason": "SemiAnalysis明确指出存储结构性短缺。",
            "raw": {
                "_decision_rule_ids": ["attributed_research_hard_variable"],
                "_protected_decision_rule_ids": ["attributed_research_hard_variable"],
            },
        }
        item = {
            "id": "attributed-1",
            "title": "SemiAnalysis表示，存储结构性短缺将持续到2028年。",
            "published_at": datetime.now(timezone.utc).isoformat(),
            "summary": "SemiAnalysis表示，存储结构性短缺将持续到2028年。",
        }
        try:
            skeptic_evaluator.llm_skeptic_review = fake_llm
            updated = apply_skeptic_review(
                conn,
                source="cls_telegraph_api",
                item=item,
                review=review,
                push_key="push_now",
            )
        finally:
            skeptic_evaluator.llm_skeptic_review = original_llm

        assert updated["push_now"] is True
        assert updated["importance"] == "high"
        assert updated["skeptic"]["llm_downgrade_ignored"] is True


def test_any_deterministic_push_ignores_llm_only_downgrade() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "surveil.sqlite3"
        conn = init_db(path)
        original_llm = skeptic_evaluator.llm_skeptic_review

        def fake_llm(**_kwargs):
            return {
                "skeptic_verdict": "block",
                "reason": "模型建议阻断。",
                "final_push_suggestion": "ignore",
                "mode": "llm",
            }

        review = {
            "importance": "high",
            "push_now": True,
            "reason": "确定性规则命中。",
            "raw": {"_decision_rule_ids": ["macro_policy_line"]},
        }
        item = {
            "id": "deterministic-1",
            "title": "美国 CPI 大幅低于预期，美债收益率下跌",
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            skeptic_evaluator.llm_skeptic_review = fake_llm
            updated = apply_skeptic_review(
                conn,
                source="yicai_brief",
                item=item,
                review=review,
                push_key="push_now",
            )
        finally:
            skeptic_evaluator.llm_skeptic_review = original_llm
        assert updated["push_now"] is True
        assert updated["importance"] == "high"
        assert updated["skeptic"]["llm_downgrade_ignored"] is True
        assert updated["skeptic"]["final_push_suggestion"] == "push_now"
        conn.close()


def main() -> int:
    test_skeptic_downgrades_duplicate_article()
    test_official_review_preserves_skeptic_metadata()
    test_history_candidates_respects_cutoff()
    test_source_profile_can_disable_skeptic()
    test_skeptic_llm_failure_records_health_without_blocking()
    test_hbm_hard_variable_override_keeps_push_now()
    test_nvidia_ai_platform_delay_override_keeps_push_now()
    test_attributed_research_rule_ignores_llm_only_downgrade()
    test_any_deterministic_push_ignores_llm_only_downgrade()
    print("skeptic evaluator tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
