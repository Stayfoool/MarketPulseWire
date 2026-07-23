"""Record a report-only v1 comparison from the production normalized item."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from llm_analysis import call_chat_completion_raw_with_prompts
from llm_rule_catalog import CATALOG_VERSION
from llm_rule_decision import (
    ENGINE_VERSION as LLM_RULE_ENGINE_VERSION,
    LLMRulePrompt,
    resolve_input_text_scope,
)
from llm_rule_shadow import compare_llm_rule_candidate
from market_item import AdmissionResult, DecisionResult, NormalizedMarketItem
from rule_core_shadow import safe_compare_rule_core
from rule_core_v1 import (
    RULE_CORE_VERSION,
    PortfolioRuleConfig,
    SourceAdmissionPolicy,
    parse_portfolio_config,
    parse_rule_config,
)


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports"
CONTRACT_VERSION = "rule-core-runtime-shadow-report-v3"
CANDIDATE_MODES = {"deterministic", "llm"}

_CONFIG_LOCK = threading.Lock()
_CONFIG_CACHE: tuple[tuple[object, ...], object, object] | None = None


def _enabled(env: Mapping[str, str]) -> bool:
    return str(env.get("RULE_CORE_SHADOW_AUTORUN") or "").strip().lower() in {"1", "true", "yes", "on"}


def _candidate_mode(env: Mapping[str, str]) -> str:
    mode = str(env.get("RULE_COMPARISON_CANDIDATE") or "deterministic").strip().lower()
    if mode not in CANDIDATE_MODES:
        raise ValueError(f"unsupported RULE_COMPARISON_CANDIDATE: {mode}")
    return mode


def _bounded_int(env: Mapping[str, str], key: str, default: int, minimum: int, maximum: int) -> int:
    raw = str(env.get(key) or "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _default_llm_caller(env: Mapping[str, str]):
    thinking = str(env.get("RULE_COMPARISON_LLM_THINKING_TYPE") or "").strip() or None
    max_tokens = _bounded_int(
        env,
        "RULE_COMPARISON_LLM_MAX_OUTPUT_TOKENS",
        6000,
        300,
        16000,
    )

    def call(prompt: LLMRulePrompt):
        return call_chat_completion_raw_with_prompts(
            prompt.system_prompt,
            json.dumps(prompt.user_payload, ensure_ascii=False, separators=(",", ":")),
            user_agent="surveil-llm-rule-comparison/0.1",
            truncate_user_prompt=False,
            thinking_override=thinking,
            max_tokens_override=max_tokens,
            temperature_override=0,
        )

    call.audit_options = {  # type: ignore[attr-defined]
        "temperature": 0,
        "max_output_tokens": max_tokens,
        "thinking_type": thinking or "",
        "truncate_user_prompt": False,
    }
    return call


def _config_paths(env: Mapping[str, str]) -> tuple[Path, Path] | None:
    config = Path(str(env.get("RULE_CORE_SHADOW_CONFIG") or "").strip()).expanduser()
    portfolio = Path(str(env.get("RULE_CORE_SHADOW_PORTFOLIO") or "").strip()).expanduser()
    if not config.is_file() or not portfolio.is_file():
        return None
    return config, portfolio


def _load_config(env: Mapping[str, str]) -> tuple[object, object] | None:
    paths = _config_paths(env)
    if paths is None:
        return None
    config_path, portfolio_path = paths
    config_stat = config_path.stat()
    portfolio_stat = portfolio_path.stat()
    cache_key = (
        str(config_path.resolve()),
        config_stat.st_mtime_ns,
        config_stat.st_size,
        str(portfolio_path.resolve()),
        portfolio_stat.st_mtime_ns,
        portfolio_stat.st_size,
    )
    global _CONFIG_CACHE
    with _CONFIG_LOCK:
        if _CONFIG_CACHE is not None and _CONFIG_CACHE[0] == cache_key:
            return _CONFIG_CACHE[1], _CONFIG_CACHE[2]
        rule_config = parse_rule_config(json.loads(config_path.read_text(encoding="utf-8")))
        portfolio = parse_portfolio_config(json.loads(portfolio_path.read_text(encoding="utf-8")))
        _CONFIG_CACHE = (cache_key, rule_config, portfolio)
        return rule_config, portfolio


def _source_group(item: NormalizedMarketItem) -> str:
    if item.source_category == "research_industry_media":
        return "research"
    if item.source_category == "official_company":
        return "official"
    return "news"


def _application_revision() -> str:
    try:
        marker = (ROOT / "REVISION").read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in marker.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "commit":
            return value.strip()
    return ""


def _item_id(item: NormalizedMarketItem, storage_ref: Mapping[str, Any]) -> str:
    value = storage_ref.get("item_id")
    if value not in (None, ""):
        return str(value)
    for key in ("source_event_id", "id"):
        value = item.raw.get(key)
        if value not in (None, ""):
            return str(value)
    value = storage_ref.get("event_id")
    return str(value) if value not in (None, "") else item.dedupe_key


def _body_source(item: NormalizedMarketItem) -> str:
    recorded = " ".join(str(item.raw.get("body_source") or "").split())[:160]
    if recorded:
        return recorded
    if not item.full_text and item.summary:
        return "标题和摘要（无详情正文）"
    if not item.full_text:
        return "仅标题（无详情正文）"
    return "未记录正文来源"


def source_admission_policy(item: NormalizedMarketItem) -> SourceAdmissionPolicy:
    """Apply the production source boundary to compatibility-only evaluations."""
    if item.source_category == "official_policy" or item.publisher_role == "government_official":
        return SourceAdmissionPolicy(direct_admission_families=("trade_policy",))
    return SourceAdmissionPolicy()


def _report_payload(
    item: NormalizedMarketItem,
    current_decision: DecisionResult | None,
    storage_ref: Mapping[str, Any],
    *,
    rule_config: object,
    portfolio: object,
    current_admission_status: str,
    current_admission_reason: str,
    current_matched_families: tuple[str, ...],
    production_admission: AdmissionResult | None,
    production_portfolio: PortfolioRuleConfig | None,
    env: Mapping[str, str],
    llm_caller: Any = None,
) -> dict[str, Any]:
    candidate_mode = _candidate_mode(env)
    if candidate_mode == "llm":
        comparison = compare_llm_rule_candidate(
            item,
            current_decision=current_decision,
            current_admission_status=current_admission_status,
            current_admission_reason=current_admission_reason,
            current_matched_families=current_matched_families,
            rule_config=rule_config,
            portfolio=production_portfolio or portfolio,
            source_policy=source_admission_policy(item),
            model_caller=llm_caller or _default_llm_caller(env),
            production_admission=production_admission,
            input_text_scope=resolve_input_text_scope(item),
            max_input_chars=_bounded_int(
                env,
                "RULE_COMPARISON_LLM_MAX_INPUT_CHARS",
                120000,
                1000,
                1000000,
            ),
        )
        candidate_engine = LLM_RULE_ENGINE_VERSION
        candidate_version = CATALOG_VERSION
        rule_core_version = ""
    else:
        comparison = safe_compare_rule_core(
            item,
            current_decision=current_decision,
            current_admission_status=current_admission_status,
            current_admission_reason=current_admission_reason,
            current_matched_families=current_matched_families,
            rule_config=rule_config,
            portfolio=portfolio,
            source_policy=source_admission_policy(item),
        )
        candidate_engine = "rule_core_v1"
        candidate_version = RULE_CORE_VERSION
        rule_core_version = RULE_CORE_VERSION
    candidate_payload = comparison.get("candidate") if isinstance(comparison.get("candidate"), dict) else {}
    for evidence in candidate_payload.get("admission_evidence") or []:
        if isinstance(evidence, dict):
            evidence.pop("evidence_quote", None)
    comparable = bool(comparison.get("ok") and comparison.get("comparable", True))
    changed_action = bool(comparable and "action" in comparison.get("changed_fields", []))
    current = comparison.get("current") if isinstance(comparison.get("current"), dict) else {}
    candidate = comparison.get("candidate") if isinstance(comparison.get("candidate"), dict) else {}
    pair = f"{current.get('action') or 'none'}->{candidate.get('action') or 'none'}"
    return {
        "ok": True,
        "contract_version": CONTRACT_VERSION,
        "comparison_only": True,
        "affects_current_decision": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_mode": "production_normalized_item",
        "candidate_mode": candidate_mode,
        "candidate_engine": candidate_engine,
        "candidate_version": candidate_version,
        "rule_core_version": rule_core_version,
        "rule_config_version": str(getattr(rule_config, "config_version", "")),
        "application_revision": _application_revision(),
        "counts": {
            "compared": 1 if comparable else 0,
            "comparison_errors": 0 if comparison.get("ok") else 1,
            "action_changes": 1 if changed_action else 0,
            "skipped": (
                {}
                if comparable
                else {
                    str(candidate.get("evaluation_status") or "comparison_error"): 1
                }
            ),
            "action_changes_by_pair": {pair: 1} if changed_action else {},
        },
        "items": [
            {
                "source": item.source,
                "item_id": _item_id(item, storage_ref),
                "title": item.title[:240],
                "url": item.url[:500],
                "input_evidence": {
                    "title_chars": len(item.title),
                    "summary_chars": len(item.summary),
                    "full_text_chars": len(item.full_text),
                    "body_source": _body_source(item),
                },
                "comparison": comparison,
            }
        ],
    }


def _write_report(payload: dict[str, Any], item: NormalizedMarketItem, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    name = f"rule-core-shadow-{_source_group(item)}-{stamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}.json"
    path = report_dir / name
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)
    return path


def record_runtime_comparison(
    item: NormalizedMarketItem,
    current_decision: DecisionResult | None,
    storage_ref: Mapping[str, Any],
    *,
    report_dir: Path = REPORT_DIR,
    env: Mapping[str, str] | None = None,
    current_admission_status: str = "unknown",
    current_admission_reason: str = "current_runtime_does_not_expose_admission",
    current_matched_families: tuple[str, ...] = (),
    production_admission: AdmissionResult | None = None,
    production_portfolio: PortfolioRuleConfig | None = None,
    llm_caller: Any = None,
) -> dict[str, Any]:
    """Write one bounded comparison without changing the active runtime result."""
    effective_env = env if env is not None else os.environ
    if not _enabled(effective_env):
        return {"status": "disabled"}
    try:
        loaded = _load_config(effective_env)
        if loaded is None:
            return {"status": "skipped", "reason": "rule/portfolio config is unavailable"}
        rule_config, portfolio = loaded
        payload = _report_payload(
            item,
            current_decision,
            storage_ref,
            rule_config=rule_config,
            portfolio=portfolio,
            current_admission_status=current_admission_status,
            current_admission_reason=current_admission_reason,
            current_matched_families=current_matched_families,
            production_admission=production_admission,
            production_portfolio=production_portfolio,
            env=effective_env,
            llm_caller=llm_caller,
        )
        path = _write_report(payload, item, report_dir)
        return {"status": "completed", "report": str(path), "comparison_ok": payload["items"][0]["comparison"]["ok"]}
    except Exception as exc:  # noqa: BLE001 - comparison must never change production processing.
        return {"status": "failed", "reason": f"{type(exc).__name__}: {str(exc)[:500]}"}
