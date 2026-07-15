"""Delivery-only identities for repeated US macro-data coverage."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from macro_policy import (
    MARKET_REACTION_KEYWORDS,
    TRANSMISSION_ASSET_MARKERS,
    fed_policy_impulse,
    generic_fed_transmission_classification,
)
from market_item import DecisionResult


MACRO_PREVIEW_RULE_ID = "macro_data_preview"
MACRO_RELEASE_RULE_ID = "macro_data_release"
MACRO_REACTION_RULE_ID = "macro_market_reaction"
FED_POLICY_REACTION_RULE_ID = "fed_policy_market_reaction"
MACRO_DEDUP_RULE_IDS = {
    MACRO_PREVIEW_RULE_ID,
    MACRO_RELEASE_RULE_ID,
    MACRO_REACTION_RULE_ID,
    FED_POLICY_REACTION_RULE_ID,
}
MACRO_LOOKBACK_DAYS = 90
BEIJING_TIMEZONE = timezone(timedelta(hours=8))

US_MARKERS = ("美国", "美國", "u.s.", "u.s ", "us ", "united states")
INDICATORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("CPI", ("cpi", "消费者价格指数", "消費者價格指數")),
    ("PCE", ("pce", "个人消费支出", "個人消費支出")),
    ("NONFARM", ("非农", "非農", "nonfarm", "payrolls", "payroll")),
)
WARSH_MARKERS = ("沃什", "沃尔什", "沃爾什", "warsh")
WARSH_SPEECH_PATTERNS = (
    r"(?:沃什|沃尔什|沃爾什|warsh)\s*[:：]",
    r"(?:沃什|沃尔什|沃爾什|warsh).{0,24}(?:表示|称|稱|重申|指出|强调|強調|承诺|承諾|警告|认为|認為|宣布|透露|淡化|回应|回應|评价|評價|said|stated|testified|warned)",
    r"(?:表示|称|稱|重申|指出|强调|強調|承诺|承諾|警告|认为|認為|宣布|透露|淡化|回应|回應|评价|評價).{0,18}(?:沃什|沃尔什|沃爾什|warsh)",
)
CORRECTION_MARKERS = ("更正", "修正", "修订", "修訂", "误报", "誤報", "correction", "corrected", "revised")
PREVIEW_MARKERS = (
    "将公布",
    "將公布",
    "即将公布",
    "即將公布",
    "等待",
    "前瞻",
    "竞猜",
    "競猜",
    "日历",
    "日曆",
    "今晚公布",
    "明晚公布",
    "将于",
    "將於",
    "市场预期",
    "市場預期",
    "预计",
    "預計",
    "预期",
    "預期",
    "或将",
    "或將",
    "可能",
    "若",
    "前值",
    "预期值",
    "預期值",
    "due",
    "ahead of",
    "preview",
)
REACTION_DIRECTION_MARKERS = (
    "跳涨",
    "跳漲",
    "跳水",
    "大涨",
    "大漲",
    "大跌",
    "上涨",
    "上漲",
    "下跌",
    "走高",
    "走低",
    "走强",
    "走強",
    "走弱",
    "拉升",
    "回落",
    "削减押注",
    "削減押注",
    "推迟至",
    "推遲至",
    "repric",
    "rall",
    "fell",
    "rose",
    "higher",
    "lower",
)
EXTRA_REACTION_MARKERS = (
    "美债",
    "比特币",
    "比特幣",
    "以太坊",
    "股指期货",
    "股指期貨",
    "道指",
    "标普",
    "標普",
    "纳指",
    "納指",
    "美股",
    "交易员",
    "交易員",
)
SURPRISE_OUTCOME_MARKERS = (
    "超预期",
    "超預期",
    "低于预期",
    "低於預期",
    "高于预期",
    "高於預期",
    "弱于预期",
    "弱於預期",
    "不及预期",
    "不及預期",
    "好于预期",
    "好於預期",
    "above expectations",
    "below expectations",
)

CLAIM_SPLIT = re.compile(r"(?:<br\s*/?>|[。！？!?；;]|\n)+", re.IGNORECASE)
CHINESE_MONTH = re.compile(r"(?:(?P<year>20\d{2})年)?(?P<month>1[0-2]|[1-9])月")
ENGLISH_MONTHS = {
    name: month
    for month, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
ENGLISH_MONTH = re.compile(
    r"\b(?P<month>" + "|".join(sorted(ENGLISH_MONTHS, key=len, reverse=True)) + r")\b(?:\s+(?P<year>20\d{2}))?",
    re.IGNORECASE,
)
ACTUAL_PATTERNS = (
    re.compile(
        r"(?:CPI|PCE|消费者价格指数|消費者價格指數|个人消费支出|個人消費支出).{0,32}"
        r"(?:同比|环比|環比|年率|月率).{0,16}(?:增长|增長|上涨|上漲|下降|回落|持平|录得|錄得|为|為|至|达到|達到)?\s*[-+]?\d+(?:\.\d+)?\s*[%％]?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:同比|环比|環比|年率|月率).{0,16}(?:增长|增長|上涨|上漲|下降|回落|持平|录得|錄得|为|為|至|达到|達到)?\s*[-+]?\d+(?:\.\d+)?\s*[%％]?"
        r".{0,32}(?:CPI|PCE|消费者价格指数|消費者價格指數|个人消费支出|個人消費支出)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:非农|非農|nonfarm|payrolls?).{0,28}(?:新增|增加|减少|減少|录得|錄得|rose|fell|added).{0,12}[-+]?\d", re.IGNORECASE),
)
FORECAST_VALUE_PATTERN = re.compile(
    r"(?:前值|预期值|預期值|预期|預期|预计|預計|预估|預估|预测|預測|可能|或|将|將|forecast|expected)"
    r".{0,24}(?:同比|环比|環比|年率|月率|新增|增加|减少|減少)",
    re.IGNORECASE,
)
FORECAST_TABLE_MARKERS = ("前值", "预期值", "預期值")


def _text(item: dict[str, Any]) -> str:
    return " ".join(str(item.get(key) or "").strip() for key in ("title", "summary", "content", "full_text"))


def _claims(text: str) -> list[str]:
    return [" ".join(claim.split()) for claim in CLAIM_SPLIT.split(text) if claim.strip()]


def _contains_any(text: str, values: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(value.casefold() in lowered for value in values)


def _macro_rule_matched(decision: DecisionResult) -> bool:
    return any(str(hit.get("rule_id") or "") == "macro_policy_line" for hit in decision.rule_hits)


def _indicator_occurrences(claim: str) -> list[tuple[str, int]]:
    lowered = claim.casefold()
    result: list[tuple[str, int]] = []
    for indicator, aliases in INDICATORS:
        for alias in aliases:
            start = lowered.find(alias.casefold())
            if start >= 0:
                result.append((indicator, start))
    return result


def _month_occurrences(claim: str) -> list[tuple[int, int | None, int]]:
    result: list[tuple[int, int | None, int]] = []
    for match in CHINESE_MONTH.finditer(claim):
        if re.match(r"\s*(?:[0-3]?\d)日", claim[match.end() :]):
            continue
        result.append((int(match.group("month")), int(match.group("year")) if match.group("year") else None, match.start()))
    for match in ENGLISH_MONTH.finditer(claim):
        month = ENGLISH_MONTHS[match.group("month").casefold()]
        result.append((month, int(match.group("year")) if match.group("year") else None, match.start()))
    return result


def _published_year_month(value: object) -> tuple[int, int]:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now()
    return parsed.year, parsed.month


def _previous_reference_period(value: object) -> str:
    year, month = _published_year_month(value)
    if month == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month - 1:02d}"


def _reaction_session(value: object) -> str:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(BEIJING_TIMEZONE)
    if parsed.hour < 6:
        parsed -= timedelta(days=1)
    return parsed.date().isoformat()


def _legacy_reaction_keys(country: str, indicator: str, reaction_session: str) -> list[str]:
    try:
        session_date = datetime.fromisoformat(reaction_session)
    except ValueError:
        return []
    return [
        f"macro:market_reaction:{country}:{indicator}:{(session_date - timedelta(days=offset)).date().isoformat()}"
        for offset in range(3)
    ]


def _reference_year(explicit_year: int | None, month: int, published_at: object) -> int:
    if explicit_year:
        return explicit_year
    year, published_month = _published_year_month(published_at)
    return year - 1 if month > published_month + 1 else year


def _has_actual_value(claim: str) -> bool:
    for pattern in ACTUAL_PATTERNS:
        match = pattern.search(claim)
        if not match:
            continue
        matched = match.group(0)
        if _contains_any(matched, FORECAST_TABLE_MARKERS) or FORECAST_VALUE_PATTERN.search(matched):
            continue
        return True
    return False


def _is_preview_claim(claim: str) -> bool:
    return _contains_any(claim, PREVIEW_MARKERS) and not _contains_any(claim, SURPRISE_OUTCOME_MARKERS)


def _is_released_reaction(text: str, fact: dict[str, Any] | None) -> bool:
    if fact and fact.get("actual"):
        return True
    if _contains_any(text, SURPRISE_OUTCOME_MARKERS):
        return True
    return bool(
        re.search(
            r"(?:受.{0,36}(?:CPI|PCE|非农|非農|消费者价格指数|个人消费支出).{0,18}影响|"
            r"(?:CPI|PCE|非农|非農|消费者价格指数|个人消费支出).{0,24}(?:公布后|公佈後|全面降温|全面降溫|降温后|降溫後|意外))",
            text,
            flags=re.IGNORECASE,
        )
    )


def _extract_release_fact(item: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for claim in _claims(_text(item)):
        if not _contains_any(claim, US_MARKERS):
            continue
        indicators = _indicator_occurrences(claim)
        months = _month_occurrences(claim)
        if not indicators or not months:
            continue
        pairs = [
            (abs(month_value[2] - indicator_value[1]), indicator_value, month_value)
            for indicator_value in indicators
            for month_value in months
            if month_value[2] <= indicator_value[1] and abs(month_value[2] - indicator_value[1]) <= 48
        ]
        if not pairs:
            continue
        _, (indicator, _), (month, explicit_year, _) = min(pairs, key=lambda value: value[0])
        year = _reference_year(explicit_year, month, item.get("published_at"))
        actual = _has_actual_value(claim)
        preview = not actual
        candidates.append(
            {
                "country": "US",
                "indicator": indicator,
                "reference_period": f"{year:04d}-{month:02d}",
                "actual": actual,
                "preview": preview,
                "evidence_quote": claim[:500],
            }
        )
    return next((candidate for candidate in candidates if candidate["actual"]), None) or (
        candidates[0] if candidates else None
    )


def _extract_indicator_context(item: dict[str, Any]) -> dict[str, Any] | None:
    for claim in _claims(_text(item)):
        if not _contains_any(claim, US_MARKERS):
            continue
        indicators = _indicator_occurrences(claim)
        if indicators:
            indicator, _ = min(indicators, key=lambda value: value[1])
            return {
                "country": "US",
                "indicator": indicator,
                "reference_period": _previous_reference_period(item.get("published_at")),
                "reference_period_inferred": True,
                "actual": False,
                "preview": _is_preview_claim(claim),
                "evidence_quote": claim[:500],
            }
    return None


def _has_direct_warsh_statement(text: str) -> bool:
    for claim in _claims(text):
        if not _contains_any(claim, WARSH_MARKERS):
            continue
        if any(re.search(pattern, claim, flags=re.IGNORECASE) for pattern in WARSH_SPEECH_PATTERNS):
            return True
    return False


def _has_market_reaction(text: str) -> bool:
    market_terms = tuple(MARKET_REACTION_KEYWORDS) + EXTRA_REACTION_MARKERS + tuple(TRANSMISSION_ASSET_MARKERS)
    return _contains_any(text, market_terms) and _contains_any(text, REACTION_DIRECTION_MARKERS)


def _fed_policy_reaction_impulse(text: str) -> str:
    for claim in _claims(text):
        impulse = fed_policy_impulse(claim)
        if impulse and _has_market_reaction(claim):
            return impulse
    return ""


def macro_event_dedup_hit(item: dict[str, Any], decision: DecisionResult) -> dict[str, Any] | None:
    """Return a source-neutral delivery identity after a push decision already exists."""
    if not decision.should_push or not _macro_rule_matched(decision):
        return None
    text = _text(item)
    if _contains_any(text, CORRECTION_MARKERS):
        return None
    reaction = _has_market_reaction(text)
    direct_warsh_statement = _has_direct_warsh_statement(text)
    if direct_warsh_statement:
        return None
    retained_exceptions = set(generic_fed_transmission_classification(item).get("exceptions") or [])
    if retained_exceptions.intersection(
        {"policy_decision", "quantified_repricing", "asset_hard_fact", "unexpected_relationship"}
    ):
        return None
    fact = _extract_release_fact(item)
    if reaction and _is_released_reaction(text, fact):
        fact = fact or _extract_indicator_context(item)
        reaction_session = _reaction_session(item.get("published_at"))
        if not fact or not reaction_session:
            return None
        fact = {**fact, "preview": False}
        rule_id = MACRO_REACTION_RULE_ID
        phase = "market_reaction"
        fact["reaction_session"] = reaction_session
        identity_suffix = fact["reference_period"] or reaction_session
    elif reaction and (impulse := _fed_policy_reaction_impulse(text)):
        reaction_session = _reaction_session(item.get("published_at"))
        if not reaction_session:
            return None
        rule_id = FED_POLICY_REACTION_RULE_ID
        phase = "fed_policy_market_reaction"
        fact = {
            "country": "US",
            "indicator": "FED_POLICY",
            "reference_period": "",
            "actual": False,
            "preview": False,
            "reaction_session": reaction_session,
            "policy_impulse": impulse,
            "evidence_quote": text[:500],
        }
        identity_suffix = impulse
    elif fact and fact["preview"]:
        rule_id = MACRO_PREVIEW_RULE_ID
        phase = "preview"
        identity_suffix = fact["reference_period"]
    else:
        if not fact:
            return None
        rule_id = MACRO_RELEASE_RULE_ID
        phase = "release"
        identity_suffix = fact["reference_period"]
    key = f"macro:{phase}:{fact['country']}:{fact['indicator']}:{identity_suffix}"
    lookback_days = 14 if rule_id == FED_POLICY_REACTION_RULE_ID else MACRO_LOOKBACK_DAYS
    result = {
        "rule_id": rule_id,
        "dedup_key": key,
        "dedup_lookback_days": lookback_days,
        "dedup_kind": rule_id,
        "event_facts": {**fact, "phase": phase},
    }
    if rule_id == MACRO_REACTION_RULE_ID and fact.get("reference_period") and fact.get("reaction_session"):
        result["dedup_alias_keys"] = _legacy_reaction_keys(
            str(fact["country"]), str(fact["indicator"]), str(fact["reaction_session"])
        )
    return result
