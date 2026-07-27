"""Delivery-only identities for repeated individual-equity bank reports."""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Sequence

from market_item import DecisionResult


INVESTMENT_BANK_REPORT_RULE_ID = "investment_bank_report_dedup"
RATING_RULE_IDS = {"holding_rating_revision", "investment_bank_allocation_change"}
_COVERAGE_MARKERS = (
    "首次覆盖",
    "初次覆盖",
    "启动覆盖",
    "恢复覆盖",
    "initiates coverage",
    "initiated coverage",
    "resumes coverage",
    "resumed coverage",
)
_REVISION_MARKERS = (
    "上调",
    "下调",
    "调高",
    "调低",
    "upgrade",
    "downgrade",
    "raise",
    "lower",
    "cut",
)
_RECOMMENDATION_MARKERS = (
    "给予买入",
    "给予卖出",
    "建议买入",
    "建议卖出",
    "initiate with buy",
    "initiate with sell",
)
_RATINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("buy", ("买入", "增持", "超配", "buy", "overweight", "outperform")),
    ("sell", ("卖出", "减持", "低配", "sell", "underweight", "underperform")),
    ("neutral", ("中性", "持有", "neutral", "hold", "equal-weight", "equal weight")),
)
_INVALID_SUBJECTS = {
    "该公司",
    "公司",
    "个股",
    "股票",
    "目标价",
    "评级",
    "the company",
    "the stock",
}


def _compact(*values: object) -> str:
    return re.sub(r"\s+", " ", " ".join(str(value or "") for value in values)).strip()


def _contains_alias(text: str, alias: str) -> bool:
    normalized = _compact(alias).casefold()
    if not normalized:
        return False
    if re.search(r"[a-z0-9]", normalized):
        pattern = re.escape(normalized).replace(r"\ ", r"\s+")
        return re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", text.casefold()) is not None
    return normalized in text.casefold()


def _default_institutions() -> tuple[tuple[str, tuple[str, ...]], ...]:
    try:
        from production_admission import load_production_rule_config

        config = load_production_rule_config()
    except (OSError, RuntimeError, ValueError):
        return ()
    return tuple((item.institution_id, item.aliases) for item in config.trusted_institutions)


def _winning_hits(decision: DecisionResult) -> list[dict[str, Any]]:
    return [
        hit
        for hit in decision.rule_hits
        if str(hit.get("decision_action") or decision.action) == decision.action
    ]


def _evidence_text(hits: Iterable[dict[str, Any]]) -> str:
    values: list[str] = []
    for hit in hits:
        for evidence in hit.get("evidence") or []:
            if not isinstance(evidence, dict):
                continue
            quote = _compact(evidence.get("quote"))
            if quote and quote not in values:
                values.append(quote)
    return _compact(*values)


def _institution_id(
    text: str,
    institutions: Sequence[tuple[str, Sequence[str]]],
) -> str:
    matched = {
        institution_id
        for institution_id, aliases in institutions
        if any(_contains_alias(text, alias) for alias in aliases)
    }
    return next(iter(matched)) if len(matched) == 1 else ""


def _direct_holding_subject(decision: DecisionResult) -> str:
    admission = decision.audit_json.get("admission")
    if not isinstance(admission, dict):
        return ""
    subjects: list[str] = []
    for evidence in admission.get("evidence") or []:
        if not isinstance(evidence, dict) or evidence.get("reason_code") != "holding_direct_identity":
            continue
        for value in evidence.get("matched_subjects") or []:
            subject = _compact(value)
            if subject and subject not in subjects:
                subjects.append(subject)
    return subjects[0] if len(subjects) == 1 else ""


def _clean_subject(value: str) -> str:
    subject = _compact(value).strip(" ：:，,。；;()（）[]【】\"'")
    subject = re.sub(r"[（(][0-9A-Za-z.]+[)）]", "", subject)
    subject = re.sub(r"(?:股份有限公司|有限责任公司|有限公司)$", "", subject)
    return _compact(subject)


def _subject_from_text(text: str, *, hint: str = "") -> str:
    token = r"[A-Za-z0-9\u4e00-\u9fff·&. -]{2,40}?"
    patterns = (
        rf"(?:对|(?<!首次)(?<!初次)(?<!启动)(?<!恢复)覆盖)\s*(?P<subject>{token})\s*(?:给出|给予|首次覆盖|初次覆盖|启动覆盖|评级|目标价)",
        rf"(?:首次覆盖|初次覆盖|启动覆盖|恢复覆盖)\s*(?P<subject>{token})(?=\s*(?:，|,|。|给出|给予|报告|研报|目标价|评级))",
        rf"(?:上调|下调|调高|调低)\s*(?P<subject>{token})\s*(?:目标价|评级)",
        rf"(?:initiates?|initiated|resumes?|resumed)\s+coverage\s+(?:of|on)\s+(?P<subject>{token})(?=\s*(?:,|\.|with|at|and))",
        rf"target\s+price\s+(?:for|on)\s+(?P<subject>{token})(?=\s*(?:,|\.|to|at|of))",
    )
    for pattern in patterns:
        subjects: list[str] = []
        for match in re.finditer(pattern, text, flags=re.I):
            subject = _clean_subject(match.group("subject"))
            if subject.casefold() in _INVALID_SUBJECTS or len(subject) < 2:
                continue
            if hint and hint.casefold() not in subject.casefold() and subject.casefold() not in hint.casefold():
                continue
            if subject not in subjects:
                subjects.append(subject)
        if subjects:
            longest = max(subjects, key=len)
            return longest if all(subject.casefold() in longest.casefold() for subject in subjects) else ""
    return ""


def _report_action(text: str) -> str:
    lowered = text.casefold()
    if any(marker in lowered for marker in _COVERAGE_MARKERS):
        return "coverage_start"
    if any(marker in lowered for marker in _REVISION_MARKERS):
        if "目标价" in text or "target price" in lowered or re.search(r"\btp\b", lowered):
            return "target_revision"
        return "rating_revision"
    if any(marker in lowered for marker in _RECOMMENDATION_MARKERS):
        return "recommendation_change"
    if "目标价" in text or "target price" in lowered or re.search(r"\btp\b", lowered):
        return "target_valuation"
    return ""


def _rating(text: str) -> str:
    lowered = text.casefold()
    for rating, aliases in _RATINGS:
        if any(_contains_alias(lowered, alias) for alias in aliases):
            return rating
    return ""


def _decimal(value: str) -> str:
    try:
        normalized = Decimal(value.replace(",", ""))
    except InvalidOperation:
        return ""
    return format(normalized.normalize(), "f")


def _target_price(text: str) -> tuple[str, str]:
    currency = r"(?P<currency>人民币|港元|美元|CNY|RMB|HKD|USD|￥|¥|\$)?"
    number = r"(?P<number>\d{1,6}(?:,\d{3})*(?:\.\d+)?)"
    patterns = (
        rf"(?:目标价|target\s+price|\bTP\b)\s*(?:为|至|到|of|at|to|:|：)?\s*{currency}\s*{number}(?:\s*(?:元人民币|人民币|元|港元|美元))?",
        rf"{currency}\s*{number}\s*(?:元人民币|人民币|元|港元|美元)?\s*(?:的)?(?:目标价|target\s+price)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        value = _decimal(match.group("number"))
        if not value:
            continue
        raw_currency = str(match.group("currency") or "").upper()
        matched_text = match.group(0)
        if raw_currency in {"人民币", "CNY", "RMB", "￥", "¥"} or (
            "元" in matched_text and "港元" not in matched_text and "美元" not in matched_text
        ):
            normalized_currency = "CNY"
        elif raw_currency in {"港元", "HKD"} or "港元" in matched_text:
            normalized_currency = "HKD"
        elif raw_currency in {"美元", "USD", "$"} or "美元" in matched_text:
            normalized_currency = "USD"
        else:
            normalized_currency = "UNKNOWN"
        return normalized_currency, value
    return "", ""


def _target_currency_from_context(text: str, target: str) -> str:
    if not target:
        return ""
    currencies: set[str] = set()
    pattern = re.compile(re.escape(target).replace(r"\,", ","))
    for match in pattern.finditer(text):
        nearby = text[max(0, match.start() - 16) : match.end() + 16]
        lowered = nearby.casefold()
        if any(marker in nearby for marker in ("港元",)) or "hkd" in lowered:
            currencies.add("HKD")
        elif any(marker in nearby for marker in ("美元", "$")) or "usd" in lowered:
            currencies.add("USD")
        elif any(marker in nearby for marker in ("人民币", "元", "￥", "¥")) or any(
            marker in lowered for marker in ("cny", "rmb")
        ):
            currencies.add("CNY")
    return next(iter(currencies)) if len(currencies) == 1 else ""


def investment_bank_report_dedup_hit(
    item: dict[str, Any],
    decision: DecisionResult,
    *,
    institutions: Sequence[tuple[str, Sequence[str]]] | None = None,
) -> dict[str, Any] | None:
    """Return one report identity only for a pure rating/target-price push."""
    if not decision.should_push:
        return None
    hits = _winning_hits(decision)
    if not hits or any(str(hit.get("rule_id") or "") not in RATING_RULE_IDS for hit in hits):
        return None
    text = _evidence_text(hits)
    if not text:
        return None
    article_text = _compact(
        item.get("title"), item.get("summary"), item.get("full_text"), item.get("content")
    )
    combined_text = _compact(text, article_text)
    if any(marker in combined_text.casefold() for marker in _REVISION_MARKERS):
        return None
    action = _report_action(combined_text)
    known_institutions = institutions if institutions is not None else _default_institutions()
    institution = _institution_id(text, known_institutions) or _institution_id(
        article_text, known_institutions
    )
    evidence_subject = _subject_from_text(text)
    article_subject = _subject_from_text(article_text, hint=evidence_subject)
    if evidence_subject and article_subject and evidence_subject.casefold() in article_subject.casefold():
        inferred_subject = article_subject
    else:
        inferred_subject = evidence_subject or article_subject
    subject = _direct_holding_subject(decision) or inferred_subject
    currency, target = _target_price(text)
    if not target:
        currency, target = _target_price(article_text)
    elif currency == "UNKNOWN":
        currency = _target_currency_from_context(article_text, target) or currency
    rating = _rating(text) or _rating(article_text)
    if action not in {"coverage_start", "target_valuation"}:
        return None
    if not institution or not subject or not target or not currency or currency == "UNKNOWN":
        return None
    subject_digest = hashlib.sha256(subject.casefold().encode("utf-8")).hexdigest()[:16]
    return {
        "rule_id": INVESTMENT_BANK_REPORT_RULE_ID,
        "dedup_key": (
            f"investment_bank_report:{institution}:{subject_digest}:target:{currency}:{target}"
        ),
        "dedup_lookback_days": 7,
        "dedup_kind": "investment_bank_report",
        "event_facts": {
            "institution_id": institution,
            "subject": subject,
            "report_action": action,
            "rating": rating,
            "target_currency": currency,
            "target_price": target,
        },
    }
