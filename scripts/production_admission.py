"""Production entry for the five reviewed range-admission groups."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from market_item import AdmissionResult, NormalizedMarketItem
from push_rules import load_enabled_holdings_for_rules
from rule_config_schema import RuleConfig, parse_rule_config
from rule_core_v1 import (
    HoldingRule,
    PortfolioRuleConfig,
    SourceAdmissionPolicy,
    admit_market_item,
    apply_source_admission_boundary,
    source_admission_policy,
)


RULE_CONFIG_ENV = "RULE_CORE_CONFIG"

_CONFIG_LOCK = threading.Lock()
_CONFIG_CACHE: tuple[tuple[object, ...], RuleConfig] | None = None


@dataclass(frozen=True)
class ProductionAdmissionContext:
    result: AdmissionResult
    portfolio: PortfolioRuleConfig


def _rule_config_path(env: Mapping[str, str]) -> Path:
    raw = str(env.get(RULE_CONFIG_ENV) or "").strip()
    if not raw:
        raise RuntimeError(f"missing required production rule configuration: {RULE_CONFIG_ENV}")
    path = Path(raw).expanduser()
    if not path.is_file():
        raise RuntimeError(f"production rule configuration is unavailable: {RULE_CONFIG_ENV}")
    return path


def load_production_rule_config(env: Mapping[str, str] | None = None) -> RuleConfig:
    effective_env = env if env is not None else os.environ
    path = _rule_config_path(effective_env)
    stat = path.stat()
    cache_key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    global _CONFIG_CACHE
    with _CONFIG_LOCK:
        if _CONFIG_CACHE is not None and _CONFIG_CACHE[0] == cache_key:
            return _CONFIG_CACHE[1]
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("production rule configuration cannot be read") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("production rule configuration must be a JSON object")
        config = parse_rule_config(payload)
        _CONFIG_CACHE = (cache_key, config)
        return config


def _string_values(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    values: list[str] = []
    seen: set[str] = set()
    for raw in value:
        text = " ".join(str(raw or "").split())
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        values.append(text)
    return tuple(values)


def load_production_portfolio(db_path: Path) -> PortfolioRuleConfig:
    holdings: list[HoldingRule] = []
    for raw_holding in load_enabled_holdings_for_rules(db_path):
        raw = raw_holding.get("raw") if isinstance(raw_holding.get("raw"), dict) else {}
        symbol = " ".join(str(raw_holding.get("symbol") or "").split())
        names = _string_values(
            [
                raw_holding.get("name"),
                raw_holding.get("full_name"),
                *(raw_holding.get("aliases") or []),
            ]
        )
        if not symbol or not names:
            continue
        holdings.append(
            HoldingRule(
                symbol=symbol,
                names=names,
                related_news_keywords=_string_values(raw_holding.get("news_keywords")),
                exclude_keywords=_string_values(raw_holding.get("news_exclude_keywords")),
                immediate_alert_keywords=_string_values(raw.get("immediate_alert_keywords")),
            )
        )
    return PortfolioRuleConfig(tuple(holdings))


def evaluate_production_admission(
    item: NormalizedMarketItem,
    *,
    db_path: Path,
    env: Mapping[str, str] | None = None,
    rule_config: RuleConfig | None = None,
    portfolio: PortfolioRuleConfig | None = None,
    source_policy: SourceAdmissionPolicy | None = None,
) -> AdmissionResult:
    return production_admission_context(
        item,
        db_path=db_path,
        env=env,
        rule_config=rule_config,
        portfolio=portfolio,
        source_policy=source_policy,
    ).result


def production_admission_context(
    item: NormalizedMarketItem,
    *,
    db_path: Path,
    env: Mapping[str, str] | None = None,
    rule_config: RuleConfig | None = None,
    portfolio: PortfolioRuleConfig | None = None,
    source_policy: SourceAdmissionPolicy | None = None,
) -> ProductionAdmissionContext:
    config = rule_config or load_production_rule_config(env)
    current_portfolio = portfolio or load_production_portfolio(db_path)
    policy = source_policy or source_admission_policy(item)
    return ProductionAdmissionContext(
        result=apply_source_admission_boundary(
            item,
            admit_market_item(
                item,
                rule_config=config,
                portfolio=current_portfolio,
                source_policy=policy,
            ),
        ),
        portfolio=current_portfolio,
    )


def admission_lifecycle_values(
    admission: AdmissionResult,
    *,
    processing_status: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "admission_status": admission.status,
        "admission_reason": admission.reason_code,
        "admission_matched_families_json": json.dumps(
            list(admission.matched_families), ensure_ascii=False
        ),
        "admission_evidence_json": json.dumps(
            [evidence.to_dict() for evidence in admission.evidence], ensure_ascii=False
        ),
        "admission_config_version": admission.config_version,
        "admission_rule_contract_version": admission.rule_contract_version,
        "admission_evaluated_at": now,
        "processing_status": processing_status,
        "processing_error": "",
        "lifecycle_updated_at": now,
    }
