"""Deterministic, source-neutral AI credit and funding-risk classification."""

from __future__ import annotations

import hashlib
import re
from typing import Any


RULE_ID = "ai_hyperscaler_credit_stress"

ISSUER_ALIASES: dict[str, tuple[str, ...]] = {
    "alphabet": ("alphabet", "google", "谷歌"),
    "amazon": ("amazon", "aws", "亚马逊"),
    "meta": ("meta", "facebook", "脸书"),
    "microsoft": ("microsoft", "微软"),
    "oracle": ("oracle", "甲骨文"),
    "nvidia": ("nvidia", "英伟达"),
    "spacex": ("spacex",),
    "openai": ("openai",),
}

AI_MARKERS = (
    "artificial intelligence", "ai infrastructure", "ai data center", "ai datacenter",
    "ai capex", "ai capital expenditure", "gpu infrastructure", "人工智能", "ai基础设施",
    "ai 基础设施", "ai数据中心", "ai 数据中心", "ai资本开支", "ai 资本开支", "算力基础设施",
)
DEBT_MARKERS = (
    "bond", "bonds", "note offering", "notes offering", "debt", "refinancing", "credit facility",
    "loan", "borrowings", "financing cost", "funding cost", "leverage", "free cash flow", "liquidity",
    "covenant", "spread", "债券", "发债", "债务", "举债", "再融资", "贷款", "信贷", "融资成本",
    "杠杆", "自由现金流", "流动性", "契约", "利差",
)
ISSUANCE_MARKERS = (
    "issued", "issuance", "offering", "sold bonds", "sell bonds", "raise debt", "raised debt",
    "refinancing", "发债", "发行债", "债券发行", "债务融资", "举债", "再融资",
)
RUMOR_MARKERS = (
    "rumor", "rumour", "unverified", "may reportedly", "据传", "传闻", "市场传言", "未经证实",
)

STRESS_PATTERNS: dict[str, tuple[str, ...]] = {
    "weak_absorption": (
        r"(?:market|investors?|市场|投资者).{0,28}(?:struggl\w* to absorb|could not absorb|难以消化|承接困难|消化困难)",
        r"(?:weak|poor|soft|疲弱|不足).{0,20}(?:investor demand|bond demand|demand for (?:the )?(?:bonds?|offering)|absorption|承接|认购需求)",
        r"(?:investor demand|bond demand|demand for (?:the )?(?:bonds?|offering)|absorption|认购需求|承接).{0,20}(?:was |is |remained )?(?:weak|poor|soft|difficult|疲弱|不足|困难)",
        r"(?:absorption|承接|消化).{0,20}(?:difficult|weak|poor|困难|疲弱|不足|吃力)",
        r"(?:bond|debt|债券|债券市场|发行).{0,32}(?:cold reception|struggled to absorb|遇冷|冷遇|难以招架)",
    ),
    "weak_orderbook": (
        r"(?:weak|thin|poor|undersubscribed|疲弱|薄弱|不足).{0,20}(?:order\s*book|bookbuild|订单簿|认购倍数)",
        r"(?:order\s*book|bookbuild|订单簿|认购倍数).{0,20}(?:weak|thin|poor|below|疲弱|不足|偏低)",
    ),
    "higher_funding_cost": (
        r"(?:financing|funding|borrowing).{0,16}costs?.{0,20}(?:rose|risen|higher|increased|jumped)",
        r"(?:higher|rising|increased|jumped).{0,20}(?:financing|funding|borrowing).{0,12}costs?",
        r"融资成本.{0,16}(?:上升|走高|增加|抬升|攀升)",
        r"(?:上升|走高|增加|抬升|攀升).{0,16}融资成本",
    ),
    "weak_secondary_performance": (
        r"(?:new|newly issued|新发|新发行).{0,24}(?:bonds?|notes?|债券).{0,28}(?:weakened|fell|traded below|underperformed|走弱|下跌|跌破发行价|表现疲弱)",
        r"(?:bonds?|notes?|债券).{0,20}(?:secondary market|二级市场).{0,24}(?:weakened|fell|underperformed|走弱|下跌|表现疲弱)",
    ),
    "spread_widening": (
        r"(?:credit |bond )?spreads?.{0,18}(?:widened|wider|jumped|blew out)",
        r"(?:widening|wider).{0,18}(?:credit |bond )?spreads?",
        r"(?:信用|债券)?利差.{0,16}(?:扩大|走阔|上升)",
        r"(?:扩大|走阔|上升).{0,16}(?:信用|债券)?利差",
    ),
    "leverage_or_fcf_pressure": (
        r"(?:leverage|debt load|net debt).{0,24}(?:rose|rising|increased|elevated|pressure|stretched)",
        r"(?:free cash flow|fcf).{0,24}(?:pressure|squeezed|negative|declined|deteriorat)",
        r"(?:杠杆|债务负担).{0,18}(?:上升|增加|高企|承压|恶化)",
        r"(?:自由现金流|现金流).{0,18}(?:承压|收紧|转负|转为负|下降|恶化)",
    ),
    "capex_funding_constraint": (
        r"(?:ai |artificial intelligence ).{0,20}(?:capex|capital expenditure).{0,32}(?:funding constraint|financing constraint|liquidity pressure|funding pressure)",
        r"(?:funding|financing|liquidity).{0,28}(?:constrain|limit|pressure).{0,24}(?:ai |artificial intelligence ).{0,16}(?:capex|investment)",
        r"(?:融资|资金|流动性).{0,20}(?:约束|限制|压力).{0,24}(?:ai|人工智能).{0,16}(?:资本开支|投资)",
        r"(?:ai|人工智能).{0,16}(?:资本开支|投资).{0,24}(?:融资约束|资金约束|流动性压力|融资压力)",
    ),
}

HARD_OUTCOME_PATTERNS: dict[str, tuple[str, ...]] = {
    "financing_execution_failure": (
        r"(?:bond|note|debt|offering|financing|债券|发债|融资).{0,28}(?:failed|postponed|cancelled|canceled|downsized|失败|推迟|延期|取消|缩减).{0,40}(?:weak demand|lack of demand|poor demand|absorption|需求疲弱|需求不足|承接困难|认购不足)",
        r"(?:failed|postponed|cancelled|canceled|downsized|失败|推迟|延期|取消|缩减).{0,28}(?:bond|note|debt|offering|financing|债券|发债|融资).{0,48}(?:(?:investor )?demand.{0,12}(?:weak|poor|insufficient)|absorption|需求疲弱|需求不足|承接困难|认购不足)",
        r"(?:weak demand|lack of demand|poor demand|需求疲弱|需求不足|承接困难|认购不足).{0,40}(?:failed|postponed|cancelled|canceled|downsized|失败|推迟|延期|取消|缩减).{0,28}(?:bond|note|debt|offering|financing|债券|发债|融资)",
    ),
    "funding_driven_capex_cut": (
        r"(?:cut|delay|postpone|scale back|削减|推迟|延期|缩减).{0,28}(?:ai|artificial intelligence|人工智能).{0,20}(?:capex|capital expenditure|investment|资本开支|投资).{0,40}(?:financing|funding|liquidity|融资|资金|流动性)",
        r"(?:financing|funding|liquidity|融资|资金|流动性).{0,30}(?:forced|prompted|导致|迫使).{0,20}(?:cut|delay|postpone|scale back|削减|推迟|延期|缩减).{0,24}(?:capex|investment|资本开支|投资)",
    ),
    "ai_debt_rating_action": (
        r"(?:downgraded|negative outlook|rating watch negative|下调评级|负面展望|列入负面观察).{0,50}(?:ai debt|ai borrowing|ai capex debt|人工智能债务|ai债务|ai 债务|ai融资|ai 融资)",
        r"(?:ai debt|ai borrowing|ai capex debt|人工智能债务|ai债务|ai 债务|ai融资|ai 融资).{0,50}(?:downgraded|negative outlook|rating watch negative|下调评级|负面展望|列入负面观察)",
    ),
    "liquidity_or_covenant_event": (
        r"(?:liquidity shortfall|liquidity crisis|breached? (?:a )?covenant|covenant breach|流动性缺口|流动性危机|违反契约|触发契约)",
    ),
}

MARKET_OUTCOME_FAMILIES = {
    "weak_absorption", "weak_orderbook", "higher_funding_cost", "weak_secondary_performance", "spread_widening",
}


def _visible_text(value: object) -> str:
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", str(value or ""))
    text = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|tr|h[1-6])>", "\n", text)
    return re.sub(r"(?s)<[^>]+>", " ", text)


def item_text(item: dict[str, Any]) -> str:
    parts = [_visible_text(item.get(key)).strip() for key in ("title", "summary", "content", "full_text")]
    return "\n".join(dict.fromkeys(part for part in parts if part))


def _sentences(text: str) -> list[str]:
    return [part.strip(" -\t") for part in re.split(r"(?<=[。！？!?；;])|(?<=\.)\s+|\n+", text) if part.strip(" -\t")]


def _contains(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(marker.casefold() in lowered for marker in markers)


def _alias_present(text: str, alias: str) -> bool:
    if re.fullmatch(r"[a-z0-9]+", alias):
        return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text, flags=re.I) is not None
    return alias.casefold() in text.casefold()


def issuers_in_text(text: str) -> list[str]:
    return [issuer for issuer, aliases in ISSUER_ALIASES.items() if any(_alias_present(text, alias) for alias in aliases)]


def _pattern_labels(text: str, patterns: dict[str, tuple[str, ...]]) -> list[str]:
    return [label for label, variants in patterns.items() if any(re.search(pattern, text, flags=re.I) for pattern in variants)]


def _is_issuance(text: str) -> bool:
    return _contains(text, ISSUANCE_MARKERS) or bool(
        re.search(r"(?:发行|发售).{0,32}(?:债券|票据)|(?:债券|票据).{0,20}(?:发行|发售)", text)
    )


def _amount(text: str) -> str:
    match = re.search(r"(?:[$¥￥]\s*)?\d[\d,.]*(?:\.\d+)?\s*(?:billion|million|bn|trillion|亿|万亿)", text, flags=re.I)
    return match.group(0).strip() if match else ""


def _instrument(text: str) -> str:
    lowered = text.casefold()
    if "convertible" in lowered or "可转债" in text:
        return "convertible_bond"
    if "bond" in lowered or "note" in lowered or "债券" in text or "发债" in text:
        return "bond"
    if "loan" in lowered or "credit facility" in lowered or "贷款" in text or "信贷" in text:
        return "loan"
    return "debt"


def _event_type(text: str, hard_outcomes: list[str]) -> str:
    if "funding_driven_capex_cut" in hard_outcomes:
        return "capex_funding_response"
    if "ai_debt_rating_action" in hard_outcomes:
        return "rating_action"
    if "liquidity_or_covenant_event" in hard_outcomes:
        return "liquidity_event"
    if _contains(text, ("refinancing", "再融资")):
        return "refinancing"
    return "debt_issuance"


def _dedup_key(issuers: list[str], event_type: str, instrument: str, families: list[str], outcomes: list[str], text: str, item: dict[str, Any]) -> str:
    amount = _amount(text).casefold().replace(" ", "")
    horizon = "|".join(sorted(set(re.findall(r"\b20\d{2}\b", text))))
    if not amount and not horizon:
        published = str(item.get("published_at") or "")
        horizon = published[:10] if re.match(r"\d{4}-\d{2}-\d{2}", published) else "undated"
    identity = "|".join((",".join(sorted(issuers)), event_type, instrument, ",".join(sorted(families)), ",".join(sorted(outcomes)), amount, horizon))
    return f"ai_credit:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"


def classify_ai_credit_risk(item: dict[str, Any]) -> dict[str, Any] | None:
    text = item_text(item)
    if not text or _contains(text, RUMOR_MARKERS):
        return None
    if not (_contains(text, AI_MARKERS) and _contains(text, DEBT_MARKERS)):
        return None
    sentences = _sentences(text)
    claims: list[dict[str, Any]] = []
    for index, sentence in enumerate(sentences):
        local_issuers = issuers_in_text(sentence)
        if not local_issuers:
            continue
        window_parts = [sentence]
        if index > 0:
            previous_issuers = issuers_in_text(sentences[index - 1])
            if not previous_issuers or set(previous_issuers).issubset(local_issuers):
                window_parts.insert(0, sentences[index - 1])
        for following in sentences[index + 1: index + 3]:
            following_issuers = issuers_in_text(following)
            if following_issuers and not set(following_issuers).issubset(local_issuers):
                break
            window_parts.append(following)
        window = " ".join(window_parts)
        if not _contains(window, DEBT_MARKERS):
            continue
        families = _pattern_labels(window, STRESS_PATTERNS)
        outcomes = _pattern_labels(window, HARD_OUTCOME_PATTERNS)
        issuance = _is_issuance(window)
        if not (families or outcomes or issuance):
            continue
        claims.append({
            "issuers": local_issuers,
            "stress_signals": families,
            "hard_outcomes": outcomes,
            "evidence_quote": window[:900],
        })

    if not claims:
        return None
    grouped: dict[tuple[str, ...], dict[str, Any]] = {}
    for claim in claims:
        key = tuple(sorted(claim["issuers"]))
        group = grouped.setdefault(key, {"families": [], "outcomes": [], "evidence": []})
        group["families"].extend(claim["stress_signals"])
        group["outcomes"].extend(claim["hard_outcomes"])
        group["evidence"].append(claim["evidence_quote"])
    candidates: list[dict[str, Any]] = []
    for key, group in grouped.items():
        group_families = list(dict.fromkeys(group["families"]))
        group_outcomes = list(dict.fromkeys(group["outcomes"]))
        group_action = "push" if group_outcomes or (
            len(group_families) >= 2 and bool(set(group_families) & MARKET_OUTCOME_FAMILIES)
        ) else "daily"
        candidates.append({
            "issuers": list(key),
            "families": group_families,
            "outcomes": group_outcomes,
            "evidence": list(dict.fromkeys(group["evidence"]))[:6],
            "action": group_action,
        })
    selected = max(
        candidates,
        key=lambda candidate: (
            candidate["action"] == "push",
            bool(candidate["outcomes"]),
            len(candidate["families"]),
            len(candidate["issuers"]),
        ),
    )
    issuers = selected["issuers"]
    families = selected["families"]
    outcomes = selected["outcomes"]
    evidence = selected["evidence"]
    action = selected["action"]
    event_type = _event_type(text, outcomes)
    instrument = _instrument(text)
    scope = "cohort" if len(issuers) >= 2 else "single"
    if action == "push":
        basis = "明确融资硬结果" if outcomes else "同一发行人/群组出现多个独立信用压力信号"
        reason = f"AI 信用与融资风险规则：{basis}；涉及 {', '.join(issuers)}。"
    else:
        basis = "普通 AI 债务融资" if not families else "单一信用压力信号"
        reason = f"AI 信用与融资风险规则：{basis}，进入日报观察；涉及 {', '.join(issuers)}。"
    return {
        "matched": True,
        "rule_id": RULE_ID,
        "decision_action": action,
        "importance": "high" if action == "push" else "medium",
        "push_now": action == "push",
        "should_push": action == "push",
        "reason": reason,
        "brief_reason": reason,
        "issuer_scope": scope,
        "issuers": issuers,
        "event_type": event_type,
        "instrument": instrument,
        "amount": _amount(text),
        "funding_purpose": "ai_infrastructure",
        "stress_signals": families,
        "hard_outcomes": outcomes,
        "evidence_quotes": evidence,
        "extraction_mode": "deterministic_local_window",
    }


def ai_credit_risk_rule(source: str, item: dict[str, Any]) -> dict[str, Any] | None:
    from rule_center import rule_enabled

    if not rule_enabled(RULE_ID):
        return None
    classification = classify_ai_credit_risk(item)
    if not classification:
        return None
    return {
        **classification,
        "source": source,
        "dedup_key": _dedup_key(
            classification["issuers"],
            str(classification["event_type"]),
            str(classification["instrument"]),
            classification["stress_signals"],
            classification["hard_outcomes"],
            item_text(item),
            item,
        ),
        "dedup_lookback_days": 14,
    }
