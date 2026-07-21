"""Side-effect-free extraction of attributed rating and allocation claims."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence


InstitutionAliases = tuple[str, tuple[str, ...]]
SubjectAliases = tuple[str, tuple[str, ...], Mapping[str, Any]]

RATING_STANCES = (
    "买入",
    "卖出",
    "中性",
    "增持",
    "减持",
    "超配",
    "低配",
    "强于大盘",
    "弱于大盘",
    "buy",
    "sell",
    "neutral",
    "overweight",
    "underweight",
    "outperform",
    "underperform",
)

RATING_ACTION_PATTERNS = (
    re.compile(r"(?:目标价|target\s+price|\bTP\b)", re.I),
    re.compile(r"(?:首次|初次|恢复|启动).{0,12}(?:覆盖|评级)|(?:initiat\w*|resume\w*).{0,16}coverage", re.I),
    re.compile(r"(?:上调|下调|调高|调低|维持|重申).{0,16}(?:评级|目标价)", re.I),
    re.compile(r"(?:评级|目标价).{0,16}(?:上调|下调|调高|调低|维持|重申|至|为)", re.I),
    re.compile(r"(?:upgrade\w*|downgrade\w*|raise\w*|lower\w*|reiterate\w*).{0,20}(?:rating|target\s+price)", re.I),
    re.compile(r"(?:rating|target\s+price).{0,20}(?:upgrade\w*|downgrade\w*|raise\w*|lower\w*|reiterate\w*)", re.I),
    re.compile(
        rf"(?:给予|评为|评级为|维持|重申|上调至|下调至).{{0,16}}(?:{'|'.join(map(re.escape, RATING_STANCES[:8]))})",
        re.I,
    ),
    re.compile(rf"(?:{'|'.join(map(re.escape, RATING_STANCES))}).{{0,12}}(?:评级|rating)", re.I),
    re.compile(rf"[:：-]\s*(?:{'|'.join(map(re.escape, RATING_STANCES))})\s*(?=[:：-]|$)", re.I),
)

RATING_REVISION_MARKERS = (
    "上调",
    "下调",
    "调高",
    "调低",
    "upgrade",
    "upgraded",
    "downgrade",
    "downgraded",
    "raise",
    "raises",
    "raised",
    "lower",
    "lowers",
    "lowered",
    "cut",
    "cuts",
)
RATING_MAINTAINED_MARKERS = ("维持", "重申", "maintain", "maintains", "reiterate", "reiterates")
RATING_COVERAGE_MARKERS = (
    "首次覆盖",
    "初次覆盖",
    "恢复覆盖",
    "启动覆盖",
    "首次评级",
    "初次评级",
    "initiates coverage",
    "initiated coverage",
    "resumes coverage",
    "resumed coverage",
)

THEME_STRATEGY_ACTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("做多", ("做多", "long", "go long")),
    ("做空", ("做空", "short", "go short")),
    ("超配", ("超配", "overweight")),
    ("低配", ("低配", "underweight")),
    ("加仓", ("加仓", "增配", "增持", "add exposure")),
    ("减仓", ("减仓", "减配", "减持", "reduce exposure")),
    ("买入", ("买入", "买进", "buy")),
    ("卖出", ("卖出", "sell")),
    ("配置转向", ("配置转向", "资金切换", "资金轮动", "切换至", "rotate", "rotation", "switch to")),
)

_ROTATION_PATTERNS_ALL: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:从|由)\s*(?P<from>[^，,。；;！？!?]{1,80}?)\s*"
        r"(?:(?:轮动|切换|调仓|换仓|撤出|调整)\s*(?:到|至|向|进入|转入)|转向)\s*"
        r"(?P<to>[^。；;！？!?]{1,100})",
        re.I,
    ),
    re.compile(
        r"(?:rotat(?:e|es|ed|ing)|rotation|switch(?:es|ed|ing)?|shift(?:s|ed|ing)?|"
        r"reallocat(?:e|es|ed|ing)|mov(?:e|es|ed|ing))"
        r"(?:\s+(?:capital|funds|money|allocation|allocations|exposure|positioning|investors?)){0,3}"
        r"\s+(?:away\s+)?from\s+(?P<from>[^,.;!?]{1,100}?)\s+"
        r"(?:to|into|toward|towards)\s+(?P<to>[^.;!?]{1,120})",
        re.I,
    ),
    re.compile(
        r"(?:减持|减配|低配|卖出|减仓|降低配置|削减配置)\s*"
        r"(?P<from>[^，,。；;！？!?]{1,80}?)\s*(?:，|,|；|;|、|并(?:且)?|同时|转而)\s*"
        r"(?:增持|增配|超配|买入|加仓|提高配置)\s*(?P<to>[^。；;！？!?]{1,100})",
        re.I,
    ),
    re.compile(
        r"(?:underweight|reduce|trim|sell|cut)\s+(?:exposure|allocation|holdings|positions)?\s*(?:to|in)?\s*"
        r"(?P<from>[^,.;!?]{1,100}?)\s*(?:,|;|\band\b|\bwhile\b)\s*"
        r"(?:overweight|add|increase|buy)\s+(?:exposure|allocation|holdings|positions)?\s*(?:to|in)?\s*"
        r"(?P<to>[^.;!?]{1,120})",
        re.I,
    ),
)

ROTATION_DIRECT_PATTERNS = _ROTATION_PATTERNS_ALL[:2]
ROTATION_PAIRED_PATTERNS = _ROTATION_PATTERNS_ALL[2:]
ROTATION_PATTERNS = (*ROTATION_DIRECT_PATTERNS, *ROTATION_PAIRED_PATTERNS)

ROTATION_RETROSPECTIVE_MARKERS = (
    "去年",
    "前年",
    "上季度",
    "此前曾",
    "回顾",
    "历史上",
    "last year",
    "last quarter",
    "historically",
    "in retrospect",
    "had rotated",
    "previously rotated",
)
ROTATION_RUMOR_OR_NEGATION_MARKERS = (
    "传闻",
    "网传",
    "据传",
    "未经证实",
    "并未建议",
    "没有建议",
    "不建议",
    "否认",
    "rumor",
    "unconfirmed",
    "does not recommend",
    "did not recommend",
    "not recommend",
    "denied",
)
ROTATION_PRICE_ACTION_MARKERS = (
    "市场资金从",
    "板块资金从",
    "股价轮动",
    "涨幅轮动",
    "成交轮动",
    "market rotated",
    "market rotation",
    "price action",
    "fund flows rotated",
)
ROTATION_ADVISORY_MARKERS = (
    "建议",
    "提示投资者",
    "主张",
    "配置策略",
    "投资策略",
    "调整配置",
    "recommend",
    "advise",
    "strategy",
    "allocation",
    "positioning",
    "calls for",
    "urges investors",
)
ROTATION_ALLOCATION_CONTEXT_MARKERS = (
    "投资者",
    "投资策略",
    "资金",
    "配置",
    "仓位",
    "持仓",
    "头寸",
    "调仓",
    "减配",
    "增配",
    "超配",
    "低配",
    "买入",
    "卖出",
    "资产轮动",
    "做多",
    "做空",
    "portfolio",
    "investor",
    "funds",
    "allocation",
    "exposure",
    "positioning",
    "overweight",
    "underweight",
    "buy",
    "sell",
    "reallocate",
    "long",
    "short",
)
ROTATION_NON_ALLOCATION_PATTERNS = (
    re.compile(r"(?:capex|capital expenditure).{0,30}(?:opex|operating expenditure)", re.I),
    re.compile(r"(?:资本开支|资本支出).{0,30}(?:运营开支|运营支出)", re.I),
    re.compile(r"(?:商业模式|盈利模式|会计处理).{0,30}(?:轮动|转向|切换)", re.I),
)
ALLOCATION_CONTEXT_MARKERS = tuple(
    dict.fromkeys((*ROTATION_ADVISORY_MARKERS, *ROTATION_ALLOCATION_CONTEXT_MARKERS, "仓位", "头寸", "做多", "做空", "long", "short"))
)
ALLOCATION_REJECT_MARKERS = tuple(
    dict.fromkeys(
        (*ROTATION_RETROSPECTIVE_MARKERS, *ROTATION_RUMOR_OR_NEGATION_MARKERS, *ROTATION_PRICE_ACTION_MARKERS)
    )
)
ALLOCATION_NON_INVESTMENT_PATTERNS = ROTATION_NON_ALLOCATION_PATTERNS


def compact_text(*values: object) -> str:
    return re.sub(r"\s+", " ", " ".join(str(value or "") for value in values)).strip()


def contains_term(text: str, term: str) -> bool:
    normalized = term.casefold().strip()
    lowered = text.casefold()
    if not normalized:
        return False
    if re.search(r"[a-z0-9]", normalized):
        pattern = re.escape(normalized).replace(r"\ ", r"\s+")
        return re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", lowered) is not None
    return normalized in lowered


def evidence_segments(values: Iterable[object]) -> list[str]:
    segments: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        for part in re.split(r"(?<=[。！？!?；;])|(?<=\.)\s+|\n+", text):
            normalized = compact_text(part)
            if normalized and normalized not in segments:
                segments.append(normalized)
    return segments


def _registry_matches(text: str, registry: Sequence[tuple[str, Sequence[str]]]) -> list[str]:
    return [item_id for item_id, aliases in registry if any(contains_term(text, alias) for alias in aliases)]


def _attributed_ids_for_window(
    window: str,
    *,
    official_institution_ids: Sequence[str],
    attributed_claim_quotes: Mapping[str, Sequence[str]],
    valid_institution_ids: set[str],
) -> list[str]:
    matched = [item_id for item_id in official_institution_ids if item_id in valid_institution_ids]
    normalized_window = compact_text(window)
    for institution_id, quotes in attributed_claim_quotes.items():
        if institution_id not in valid_institution_ids:
            continue
        if any(
            (normalized_quote := compact_text(quote))
            and (normalized_quote in normalized_window or normalized_window in normalized_quote)
            for quote in quotes
        ):
            matched.append(institution_id)
    return list(dict.fromkeys(matched))


def _research_actions(text: str, extra_keywords: Sequence[str]) -> list[str]:
    actions: list[str] = []
    if RATING_ACTION_PATTERNS[0].search(text):
        actions.append("目标价")
    if RATING_ACTION_PATTERNS[1].search(text):
        actions.append("覆盖")
    if any(pattern.search(text) for pattern in RATING_ACTION_PATTERNS[2:]):
        actions.append("评级")
    for keyword in extra_keywords:
        if contains_term(text, keyword):
            actions.append(f"自定义:{keyword}")
    return list(dict.fromkeys(actions))


def _rating_change_type(text: str, actions: Sequence[str]) -> str:
    if any(contains_term(text, marker) for marker in RATING_COVERAGE_MARKERS):
        return "coverage_start"
    if actions and any(contains_term(text, marker) for marker in RATING_REVISION_MARKERS):
        return "revision"
    if actions and any(contains_term(text, marker) for marker in RATING_MAINTAINED_MARKERS):
        return "maintained"
    return "static"


def _rating_transition(text: str) -> tuple[str, str]:
    stance = "|".join(map(re.escape, RATING_STANCES))
    for pattern in (
        rf"(?:从|由)\s*(?P<previous>{stance})\s*(?:评级)?\s*(?:上调|下调|调整)?\s*(?:至|为)\s*(?P<revised>{stance})",
        rf"(?:上调至|下调至|调高至|调低至)\s*(?P<revised>{stance})",
    ):
        match = re.search(pattern, text, flags=re.I)
        if match:
            return str(match.groupdict().get("previous") or ""), str(match.group("revised") or "")
    return "", ""


def _target_prices(text: str) -> dict[str, str]:
    for pattern in (
        r"(?:目标价|target price|\bTP\b).{0,18}?(?:从|由|from)\s*(?P<previous>\d+(?:\.\d+)?).{0,18}?(?:至|到|为|to)\s*(?P<revised>\d+(?:\.\d+)?)",
        r"(?:从|由|from)\s*(?P<previous>\d+(?:\.\d+)?).{0,18}?(?:至|到|为|to)\s*(?P<revised>\d+(?:\.\d+)?).{0,18}?(?:目标价|target price|\bTP\b)",
    ):
        match = re.search(pattern, text, flags=re.I)
        if match:
            return {"previous_target_price": match.group("previous"), "revised_target_price": match.group("revised")}
    return {}


def extract_rating_claims(
    *,
    text_parts: Iterable[object],
    institutions: Sequence[InstitutionAliases],
    subjects: Sequence[SubjectAliases],
    official_institution_ids: Sequence[str] = (),
    attributed_claim_quotes: Mapping[str, Sequence[str]] | None = None,
    extra_keywords: Sequence[str] = (),
) -> list[dict[str, Any]]:
    segments = evidence_segments(text_parts)
    claims: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    institution_registry = [(item_id, aliases) for item_id, aliases in institutions]
    subject_registry = [(item_id, aliases) for item_id, aliases, _payload in subjects]
    subject_payload = {item_id: payload for item_id, _aliases, payload in subjects}
    valid_institution_ids = {item_id for item_id, _aliases in institution_registry}
    attributed_claim_quotes = attributed_claim_quotes or {}

    def append_claim(window: str, institution_ids: list[str], subject_ids: list[str], actions: list[str]) -> None:
        if not institution_ids:
            institution_ids = _attributed_ids_for_window(
                window,
                official_institution_ids=official_institution_ids,
                attributed_claim_quotes=attributed_claim_quotes,
                valid_institution_ids=valid_institution_ids,
            )
        if len(institution_ids) != 1 or len(subject_ids) != 1 or not actions:
            return
        key = (institution_ids[0], subject_ids[0], "|".join(actions))
        if key in seen:
            return
        seen.add(key)
        previous_rating, revised_rating = _rating_transition(window)
        claims.append(
            {
                "institution_id": institution_ids[0],
                "subject_id": subject_ids[0],
                "subject": dict(subject_payload[subject_ids[0]]),
                "research_actions": actions,
                "change_type": _rating_change_type(window, actions),
                "previous_rating": previous_rating,
                "revised_rating": revised_rating,
                "target_price_change": _target_prices(window),
                "evidence_quote": window[:700],
            }
        )

    for window in segments:
        append_claim(
            window,
            _registry_matches(window, institution_registry),
            _registry_matches(window, subject_registry),
            _research_actions(window, extra_keywords),
        )

    for index in range(len(segments) - 1):
        attribution, continuation = segments[index], segments[index + 1]
        institution_ids = _registry_matches(attribution, institution_registry)
        if len(institution_ids) != 1 or _research_actions(attribution, extra_keywords):
            continue
        if _registry_matches(continuation, institution_registry):
            continue
        if not re.match(r"^(?:该行|其|报告|研报|the\s+bank\b|it\b)", continuation, flags=re.I):
            continue
        append_claim(
            compact_text(attribution, continuation),
            institution_ids,
            _registry_matches(continuation, subject_registry),
            _research_actions(continuation, extra_keywords),
        )
    return claims


def _allocation_actions(text: str) -> list[str]:
    return [label for label, aliases in THEME_STRATEGY_ACTIONS if any(contains_term(text, alias) for alias in aliases)]


def _target_families(text: str, targets_by_family: Mapping[str, Sequence[str]]) -> list[str]:
    return [family for family, aliases in targets_by_family.items() if any(contains_term(text, alias) for alias in aliases)]


def extract_allocation_claims(
    *,
    text_parts: Iterable[object],
    institutions: Sequence[InstitutionAliases],
    targets_by_family: Mapping[str, Sequence[str]],
    official_institution_ids: Sequence[str] = (),
    attributed_claim_quotes: Mapping[str, Sequence[str]] | None = None,
) -> list[dict[str, Any]]:
    segments = evidence_segments(text_parts)
    registry = [(item_id, aliases) for item_id, aliases in institutions]
    valid_institution_ids = {item_id for item_id, _aliases in registry}
    attributed_claim_quotes = attributed_claim_quotes or {}
    claims: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for statement in segments:
        if any(contains_term(statement, marker) for marker in ALLOCATION_REJECT_MARKERS):
            continue
        if any(pattern.search(statement) for pattern in ALLOCATION_NON_INVESTMENT_PATTERNS):
            continue
        institution_ids = _registry_matches(statement, registry)
        if not institution_ids:
            institution_ids = _attributed_ids_for_window(
                statement,
                official_institution_ids=official_institution_ids,
                attributed_claim_quotes=attributed_claim_quotes,
                valid_institution_ids=valid_institution_ids,
            )
        if len(institution_ids) != 1:
            continue
        if not any(contains_term(statement, marker) for marker in ALLOCATION_CONTEXT_MARKERS):
            continue
        maintained = any(contains_term(statement, marker) for marker in RATING_MAINTAINED_MARKERS)

        rotation = next((match for pattern in ROTATION_PATTERNS if (match := pattern.search(statement))), None)
        if rotation:
            from_text = rotation.group("from").strip(" ：:，,、")
            to_text = rotation.group("to").strip(" ：:，,、")
            from_families = _target_families(from_text, targets_by_family)
            to_families = _target_families(to_text, targets_by_family)
            if not from_families or not to_families:
                continue
            target_families = list(dict.fromkeys((*from_families, *to_families)))
            key = (institution_ids[0], "rotation", compact_text(from_text, to_text).casefold())
            if key in seen:
                continue
            seen.add(key)
            claims.append(
                {
                    "institution_id": institution_ids[0],
                    "strategy_type": "rotation",
                    "actions": ["配置轮动"],
                    "change_type": "maintained" if maintained else "current_allocation",
                    "target_families": target_families,
                    "from_text": from_text,
                    "to_text": to_text,
                    "evidence_quote": statement[:700],
                }
            )
            continue

        actions = _allocation_actions(statement)
        target_families = _target_families(statement, targets_by_family)
        if (contains_term(statement, "评级") or contains_term(statement, "rating") or contains_term(statement, "目标价") or contains_term(statement, "target price")) and not any(
            contains_term(statement, marker)
            for marker in (
                "建议",
                "配置",
                "仓位",
                "头寸",
                "做多",
                "做空",
                "加仓",
                "减仓",
                "增配",
                "减配",
                "recommend",
                "allocation",
                "exposure",
                "positioning",
                "go long",
                "go short",
            )
        ):
            continue
        if not actions or not target_families:
            continue
        key = (institution_ids[0], "|".join(actions), "|".join(target_families))
        if key in seen:
            continue
        seen.add(key)
        claims.append(
            {
                "institution_id": institution_ids[0],
                "strategy_type": "theme_allocation",
                "actions": actions,
                "change_type": "maintained" if maintained else "current_allocation",
                "target_families": target_families,
                "from_text": "",
                "to_text": "",
                "evidence_quote": statement[:700],
            }
        )
    return claims
