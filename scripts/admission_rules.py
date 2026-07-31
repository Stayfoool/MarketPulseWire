"""Side-effect-free five-group range-admission rules."""

from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass
from typing import Any, Iterable

from international_bank_fed import (
    allowed_fed_path_banks,
    classify_trusted_financial_leader_macro_judgement,
)
from market_item import AdmissionEvidence, AdmissionResult, NormalizedMarketItem, RuleFamily
from rule_config_schema import (
    RuleConfig,
    RuleConfigError,
    clean_value as _clean,
    parse_rule_config,
    tuple_strings as _tuple_strings,
)
from trade_friction import classify_trade_friction


CONTRACT_VERSION = "rule-core-v1"
FAMILY_ORDER: tuple[RuleFamily, ...] = (
    "holding",
    "semiconductor_ai",
    "macro_data",
    "fed_policy",
    "trade_policy",
)


@dataclass(frozen=True)
class HoldingRule:
    symbol: str
    names: tuple[str, ...]
    related_news_keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    immediate_alert_keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class PortfolioRuleConfig:
    holdings: tuple[HoldingRule, ...] = ()


def parse_portfolio_config(payload: object) -> PortfolioRuleConfig:
    if not isinstance(payload, list):
        raise RuleConfigError("portfolio fixture must be a list")
    holdings: list[HoldingRule] = []
    for index, raw in enumerate(payload):
        if not isinstance(raw, dict):
            raise RuleConfigError(f"portfolio[{index}] must be an object")
        expected = {
            "symbol",
            "names",
            "related_news_keywords",
            "exclude_keywords",
            "immediate_alert_keywords",
        }
        unknown = set(raw) - expected
        missing = {"symbol", "names"} - set(raw)
        if unknown or missing:
            raise RuleConfigError(
                f"portfolio[{index}] keys invalid: missing={sorted(missing)} unknown={sorted(unknown)}"
            )
        symbol = _clean(raw.get("symbol"))
        names = _tuple_strings(raw.get("names"), f"portfolio[{index}].names")
        if not symbol or not names:
            raise RuleConfigError(f"portfolio[{index}] requires symbol and names")
        holdings.append(
            HoldingRule(
                symbol=symbol,
                names=names,
                related_news_keywords=_tuple_strings(
                    raw.get("related_news_keywords", []),
                    f"portfolio[{index}].related_news_keywords",
                ),
                exclude_keywords=_tuple_strings(
                    raw.get("exclude_keywords", []), f"portfolio[{index}].exclude_keywords"
                ),
                immediate_alert_keywords=_tuple_strings(
                    raw.get("immediate_alert_keywords", []),
                    f"portfolio[{index}].immediate_alert_keywords",
                ),
            )
        )
    return PortfolioRuleConfig(tuple(holdings))


@dataclass(frozen=True)
class SourceAdmissionPolicy:
    direct_admission_families: tuple[RuleFamily, ...] = ()

    def __post_init__(self) -> None:
        invalid = set(self.direct_admission_families) - {"trade_policy"}
        if invalid:
            raise RuleConfigError(f"unsupported direct-admission families: {sorted(invalid)}")
        if len(set(self.direct_admission_families)) != len(self.direct_admission_families):
            raise RuleConfigError("direct-admission families cannot contain duplicates")


HOLDING_ONLY_SOURCES = {
    "company_disclosures",
    "company_disclosure",
    "sina_stock_news",
}
HOLDING_ONLY_SOURCE_CATEGORIES = {
    "company_disclosures",
    "company_disclosure",
    "portfolio_stock_news",
}


def source_allowed_families(item: NormalizedMarketItem) -> tuple[RuleFamily, ...]:
    if item.source in HOLDING_ONLY_SOURCES or item.source_category in HOLDING_ONLY_SOURCE_CATEGORIES:
        return ("holding",)
    return FAMILY_ORDER


def apply_source_admission_boundary(
    item: NormalizedMarketItem,
    admission: AdmissionResult,
) -> AdmissionResult:
    if source_allowed_families(item) != ("holding",) or admission.status != "admitted":
        return admission
    if "holding" not in admission.matched_families:
        return AdmissionResult(
            status="excluded",
            reason_code="holding_scope_required_for_source",
            matched_families=(),
            evidence=(),
            config_version=admission.config_version,
            rule_contract_version=admission.rule_contract_version,
        )
    return AdmissionResult(
        status="admitted",
        reason_code="holding_scope_match",
        matched_families=("holding",),
        evidence=tuple(evidence for evidence in admission.evidence if evidence.rule_family == "holding"),
        config_version=admission.config_version,
        rule_contract_version=admission.rule_contract_version,
    )


def source_admission_policy(item: NormalizedMarketItem) -> SourceAdmissionPolicy:
    if item.source_category == "official_policy" or item.publisher_role == "government_official":
        return SourceAdmissionPolicy(direct_admission_families=("trade_policy",))
    return SourceAdmissionPolicy()


def _contains(text: str, term: str) -> bool:
    normalized = term.casefold().strip()
    lowered = text.casefold()
    if not normalized:
        return False
    if re.fullmatch(r"[a-z0-9_.+-]+", normalized):
        return re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", lowered) is not None
    return normalized in lowered


def _matches(text: str, terms: Iterable[str]) -> tuple[str, ...]:
    return tuple(term for term in terms if _contains(text, term))


def _semiconductor_scope_matches(item: NormalizedMarketItem, config: RuleConfig) -> tuple[str, ...]:
    title = item.title or ""
    full_text = item.text_for_rules
    title_only = set(config.semiconductor_ai_title_keywords)
    matches: list[str] = []
    for term in config.semiconductor_ai_keywords:
        haystack = title if term in title_only else full_text
        if _contains(haystack, term):
            matches.append(term)
    return tuple(matches)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[。！？!?；;])|\n+", text) if part.strip()]


def _quote(text: str, terms: Iterable[str]) -> str:
    terms = tuple(terms)
    for sentence in _sentences(text):
        if any(_contains(sentence, term) for term in terms):
            return sentence[:300]
    return text[:300]


def _evidence(
    family: RuleFamily | str,
    reason_code: str,
    text: str,
    terms: Iterable[str],
    *,
    subjects: Iterable[str] = (),
    relation: str = "",
) -> AdmissionEvidence:
    matched_terms = tuple(dict.fromkeys(term for term in terms if term))
    return AdmissionEvidence(
        rule_family=family,
        reason_code=reason_code,
        evidence_quote=_quote(text, matched_terms),
        matched_term_ids=tuple(
            f"term:{hashlib.sha256(term.casefold().encode('utf-8')).hexdigest()[:12]}"
            for term in matched_terms
        ),
        matched_subjects=tuple(dict.fromkeys(subject for subject in subjects if subject)),
        relation=relation,
    )


def _holding_evidence(
    item: NormalizedMarketItem,
    text: str,
    portfolio: PortfolioRuleConfig,
) -> list[AdmissionEvidence]:
    evidence: list[AdmissionEvidence] = []
    item_symbols = set(item.symbols)
    for holding in portfolio.holdings:
        direct_terms = _matches(text, holding.names)
        symbol_match = holding.symbol in item_symbols or _contains(text, holding.symbol)
        if direct_terms or symbol_match:
            evidence.append(
                _evidence(
                    "holding",
                    "holding_direct_identity",
                    text,
                    direct_terms or (holding.symbol,),
                    subjects=holding.names[:1],
                    relation="direct",
                )
            )
        if _matches(text, holding.exclude_keywords):
            continue
        related = _matches(text, holding.related_news_keywords)
        if related:
            evidence.append(
                _evidence(
                    "holding",
                    "holding_related_keyword",
                    text,
                    related,
                    subjects=holding.names[:1],
                    relation="configured_related",
                )
            )
    return evidence


def _classification_item(item: NormalizedMarketItem) -> dict[str, Any]:
    return {
        **dict(item.raw),
        "title": item.title,
        "summary": item.summary,
        "content": item.summary,
        "full_text": item.full_text,
        "source": item.source,
        "published_at": item.published_at,
    }


def admit_market_item(
    item: NormalizedMarketItem,
    *,
    rule_config: RuleConfig,
    portfolio: PortfolioRuleConfig,
    source_policy: SourceAdmissionPolicy,
) -> AdmissionResult:
    text = item.text_for_rules
    if not text:
        return AdmissionResult(
            status="excluded",
            reason_code="empty_rule_text",
            matched_families=(),
            evidence=(),
            config_version=rule_config.config_version,
            rule_contract_version=CONTRACT_VERSION,
        )
    evidence = _holding_evidence(item, text, portfolio)
    direct_holding = any(value.reason_code == "holding_direct_identity" for value in evidence)
    excluded_terms = _matches(text, rule_config.exclude_keywords)

    semiconductor = _semiconductor_scope_matches(item, rule_config)
    if semiconductor:
        evidence.append(_evidence("semiconductor_ai", "semiconductor_ai_scope", text, semiconductor))

    indicators = _matches(text, rule_config.macro_indicators)
    macro_context = _matches(text, rule_config.macro_context_aliases)
    if indicators and macro_context:
        evidence.append(_evidence("macro_data", "macro_data_scope", text, (*indicators, *macro_context)))

    fed_terms = _matches(
        text,
        (*rule_config.fed_event_aliases, *rule_config.fed_actor_aliases, *rule_config.fed_path_aliases),
    )
    if fed_terms:
        evidence.append(_evidence("fed_policy", "fed_policy_scope", text, fed_terms))
    allowed_banks = allowed_fed_path_banks(
        alias for institution in rule_config.trusted_institutions for alias in institution.aliases
    )
    leader_judgement = classify_trusted_financial_leader_macro_judgement(
        _classification_item(item),
        allowed_banks=allowed_banks,
    )
    if leader_judgement:
        institutions = tuple(str(value) for value in leader_judgement.get("institutions") or ())
        evidence.append(
            _evidence(
                "fed_policy",
                "trusted_financial_leader_scope",
                text,
                institutions or ("受信任大型金融机构负责人",),
                subjects=institutions,
                relation="trusted_institution_leader",
            )
        )

    corridor_terms: list[str] = []
    for corridor in rule_config.trade_corridors:
        joint = _matches(text, corridor.joint_terms)
        china = _matches(text, corridor.china_terms)
        counterparty = _matches(text, corridor.counterparty_terms)
        if joint or (china and counterparty):
            corridor_terms.extend(joint or (*china, *counterparty))
    trade_action = _matches(text, (*rule_config.trade_instruments, *rule_config.trade_stages))
    trade_classification = classify_trade_friction(_classification_item(item))
    local_trade_terms: list[str] = []
    for sentence in _sentences(text):
        sentence_action = _matches(sentence, (*rule_config.trade_instruments, *rule_config.trade_stages))
        if not sentence_action:
            continue
        for corridor in rule_config.trade_corridors:
            joint = _matches(sentence, corridor.joint_terms)
            china = _matches(sentence, corridor.china_terms)
            counterparty = _matches(sentence, corridor.counterparty_terms)
            if joint or (china and counterparty):
                local_trade_terms.extend((*joint, *china, *counterparty, *sentence_action))
    if "trade_policy" in source_policy.direct_admission_families:
        evidence.append(
            _evidence(
                "trade_policy",
                "trade_policy_direct_scope",
                text,
                trade_action or tuple(corridor_terms) or ("direct_trade_surface",),
            )
        )
    elif local_trade_terms or trade_classification:
        classified_terms: tuple[str, ...] = ()
        if trade_classification:
            classified_terms = tuple(
                str(value)
                for key in (
                    "corridors",
                    "policy_tools",
                    "action_stages",
                    "strong_tension_terms",
                    "weak_tension_terms",
                )
                for value in trade_classification.get(key) or []
                if str(value).strip()
            )
        evidence.append(
            _evidence(
                "trade_policy",
                "trade_policy_scope",
                text,
                tuple(local_trade_terms) or classified_terms,
            )
        )

    if excluded_terms and not direct_holding:
        return AdmissionResult(
            status="excluded",
            reason_code="global_exclude",
            matched_families=(),
            evidence=(_evidence("global", "global_exclude", text, excluded_terms),),
            config_version=rule_config.config_version,
            rule_contract_version=CONTRACT_VERSION,
        )
    if not evidence:
        return AdmissionResult(
            status="excluded",
            reason_code="out_of_scope",
            matched_families=(),
            evidence=(),
            config_version=rule_config.config_version,
            rule_contract_version=CONTRACT_VERSION,
        )
    by_family: dict[RuleFamily, list[AdmissionEvidence]] = {}
    for item_evidence in evidence:
        if item_evidence.rule_family == "global":
            continue
        by_family.setdefault(item_evidence.rule_family, []).append(item_evidence)
    families = tuple(family for family in FAMILY_ORDER if family in by_family)
    ordered_evidence = tuple(
        item_evidence for family in families for item_evidence in by_family[family]
    )
    return AdmissionResult(
        status="admitted",
        reason_code="content_scope_match",
        matched_families=families,
        evidence=ordered_evidence,
        config_version=rule_config.config_version,
        rule_contract_version=CONTRACT_VERSION,
    )


__all__ = [
    "HoldingRule",
    "PortfolioRuleConfig",
    "RuleConfig",
    "SourceAdmissionPolicy",
    "admit_market_item",
    "apply_source_admission_boundary",
    "parse_portfolio_config",
    "parse_rule_config",
    "source_admission_policy",
    "source_allowed_families",
]
