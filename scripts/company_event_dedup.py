"""Delivery-only identities for feedback-confirmed repeated company events."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from market_item import DecisionResult


COMPANY_EVENT_RULE_ID = "company_event_dedup"
COMPANY_EVENT_LOOKBACK_DAYS = 14
BEIJING_TIMEZONE = timezone(timedelta(hours=8))
ELIGIBLE_RULE_IDS = {"holding_keyword_immediate_alert", "industry_quantified_hardline"}

ISSUERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("powerchip", ("力积电", "力積電", "powerchip", "psmc")),
    ("biwin_storage", ("佰维存储", "佰維存儲", "biwin storage", "biwin")),
    ("dapustor", ("大普微", "dapustor")),
    ("jiangfeng_electronics", ("江丰电子", "江豐電子")),
    ("shijia_photons", ("仕佳光子", "shijia photons")),
)

CORRECTION_OR_REVISION_MARKERS = (
    "更正",
    "修正",
    "上修",
    "下修",
    "调整为",
    "調整為",
    "此前预计",
    "此前預計",
    "补充公告",
    "補充公告",
    "correction",
    "corrected",
    "revised",
    "updated guidance",
)
EARNINGS_FORECAST_MARKERS = ("预计", "預計", "预盈", "預盈", "预增", "預增", "业绩预告", "業績預告")
NET_PROFIT_MARKERS = ("净利润", "淨利潤", "net profit")
QUARTER_PATTERNS = (
    ("Q1", re.compile(r"(?:一季度|第一季度|q1|first quarter)", re.IGNORECASE)),
    ("Q2", re.compile(r"(?:二季度|第二季度|q2|second quarter)", re.IGNORECASE)),
    ("Q3", re.compile(r"(?:三季度|第三季度|q3|third quarter)", re.IGNORECASE)),
    ("Q4", re.compile(r"(?:四季度|第四季度|q4|fourth quarter)", re.IGNORECASE)),
)

PLACEMENT_MARKERS = ("定增", "向特定对象发行", "向特定對象發行", "private placement")
PLACEMENT_PROPOSAL_MARKERS = ("拟定增", "擬定增", "拟向特定对象发行", "擬向特定對象發行", "募集资金", "募集資金", "proposed")
PLACEMENT_UPDATE_MARKERS = (
    "获批",
    "獲批",
    "审核通过",
    "審核通過",
    "注册生效",
    "註冊生效",
    "发行完成",
    "發行完成",
    "募集完成",
    "终止",
    "終止",
    "approved",
    "completed",
    "terminated",
)

STORAGE_FOUNDRY_MARKERS = ("存储代工", "存儲代工", "memory foundry", "memory contract manufacturing")
PRICE_INCREASE_MARKERS = ("涨价", "漲價", "报价上调", "報價上調", "价格上调", "價格上調", "price increase", "price hike")


def _text(item: dict[str, Any]) -> str:
    return "\n".join(
        " ".join(str(item.get(key) or "").split())
        for key in ("title", "summary", "content", "full_text")
        if item.get(key)
    )


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(marker.casefold() in lowered for marker in markers)


def _claims(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?:<br\s*/?>|[。！？!?；;]|\n)+", text, flags=re.IGNORECASE) if part.strip()]


def _issuer_claims(text: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for issuer_id, aliases in ISSUERS:
        for claim in _claims(text):
            lowered = claim.casefold()
            if any(alias.casefold() in lowered for alias in aliases):
                key = (issuer_id, claim)
                if key not in seen:
                    seen.add(key)
                    result.append(key)
    return result


def _published_date(value: object) -> datetime | None:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(BEIJING_TIMEZONE)
    return parsed


def _reporting_period(text: str, published_at: object) -> str:
    published = _published_date(published_at)
    h1_match = re.search(r"(?:(20\d{2})年)?(?:上半年|半年度|h1|first half)", text, re.IGNORECASE)
    if h1_match:
        year = int(h1_match.group(1)) if h1_match.group(1) else published.year if published else None
        if not year:
            return ""
        return f"{year}-H1"
    for quarter, pattern in QUARTER_PATTERNS:
        match = pattern.search(text)
        if match:
            prefix = text[max(0, match.start() - 8) : match.start()]
            explicit = re.search(r"(20\d{2})年?$", prefix)
            year = int(explicit.group(1)) if explicit else published.year if published else None
            if not year:
                return ""
            return f"{year}-{quarter}"
    fy_match = re.search(r"(?:(20\d{2})年)?(?:全年|年度|full year|fy)", text, re.IGNORECASE)
    if fy_match:
        year = int(fy_match.group(1)) if fy_match.group(1) else published.year if published else None
        if not year:
            return ""
        return f"{year}-FY"
    return ""


def _identity(key: str, event_facts: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_id": COMPANY_EVENT_RULE_ID,
        "dedup_key": f"company_event:{key}",
        "dedup_lookback_days": COMPANY_EVENT_LOOKBACK_DAYS,
        "dedup_kind": "company_event_fact",
        "event_facts": event_facts,
    }


def _earnings_forecast(item: dict[str, Any], text: str, issuer_id: str) -> dict[str, Any] | None:
    if not _contains_any(text, EARNINGS_FORECAST_MARKERS):
        return None
    if not _contains_any(text, NET_PROFIT_MARKERS) and not _contains_any(text, ("预盈", "預盈")):
        return None
    period = _reporting_period(text, item.get("published_at"))
    if not period:
        return None
    return _identity(
        f"{issuer_id}:earnings_forecast:{period}:net_profit",
        {
            "subject": issuer_id,
            "event_type": "earnings_forecast",
            "reporting_period": period,
            "metric": "net_profit",
            "stage": "forecast",
        },
    )


def _financing_plan(item: dict[str, Any], text: str, issuer_id: str) -> dict[str, Any] | None:
    if issuer_id != "shijia_photons":
        return None
    if not _contains_any(text, PLACEMENT_MARKERS) or not _contains_any(text, PLACEMENT_PROPOSAL_MARKERS):
        return None
    if _contains_any(text, PLACEMENT_UPDATE_MARKERS):
        return None
    published = _published_date(item.get("published_at"))
    if not published:
        return None
    event_date = published.date().isoformat()
    return _identity(
        f"{issuer_id}:private_placement:proposed:{event_date}",
        {
            "subject": issuer_id,
            "event_type": "private_placement",
            "stage": "proposed",
            "event_date": event_date,
        },
    )


def _price_change(item: dict[str, Any], text: str, issuer_id: str) -> dict[str, Any] | None:
    if issuer_id != "powerchip":
        return None
    if not _contains_any(text, STORAGE_FOUNDRY_MARKERS) or not _contains_any(text, PRICE_INCREASE_MARKERS):
        return None
    published = _published_date(item.get("published_at"))
    month_match = re.search(r"(?:(20\d{2})年)?(1[0-2]|[1-9])月(?:起|开始|開始)", text)
    if month_match:
        year = int(month_match.group(1)) if month_match.group(1) else published.year if published else None
        if not year:
            return None
        effective_period = f"{year:04d}-{int(month_match.group(2)):02d}"
    elif _contains_any(text, ("本月起", "本月开始", "本月開始")) and published:
        effective_period = f"{published.year:04d}-{published.month:02d}"
    else:
        return None
    return _identity(
        f"{issuer_id}:price_change:storage_foundry:up:{effective_period}",
        {
            "subject": issuer_id,
            "event_type": "price_change",
            "object": "storage_foundry",
            "direction": "up",
            "effective_period": effective_period,
        },
    )


EXTRACTORS: tuple[Callable[[dict[str, Any], str, str], dict[str, Any] | None], ...] = (
    _earnings_forecast,
    _financing_plan,
    _price_change,
)


def company_event_dedup_hit(item: dict[str, Any], decision: DecisionResult) -> dict[str, Any] | None:
    """Return a bounded company-event identity after an existing push decision."""
    if not decision.should_push:
        return None
    if not any(str(hit.get("rule_id") or "") in ELIGIBLE_RULE_IDS for hit in decision.rule_hits):
        return None
    text = _text(item)
    if not text or _contains_any(text, CORRECTION_OR_REVISION_MARKERS):
        return None
    for issuer_id, claim in _issuer_claims(text):
        for extractor in EXTRACTORS:
            result = extractor(item, claim, issuer_id)
            if result:
                return result
    return None
