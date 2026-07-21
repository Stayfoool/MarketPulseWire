"""Deterministic international-bank revisions to the Fed policy-rate path."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

from international_banks import (
    FED_PATH_BANKS,
    INTERNATIONAL_BANK_ALIASES,
    bank_mention_position,
    banks_in_mention_order,
)


RULE_ID = "international_bank_fed_rate_path_revision"
FED_MARKERS = ("美联储", "美聯儲", "联储", "聯儲", "federal reserve", "fomc", " fed ")
HIKES = ("加息", "升息", "rate hike", "rate hikes", "hiking cycle")
CUTS = ("降息", "减息", "rate cut", "rate cuts", "cutting cycle")
REVISION_MARKERS = (
    "上调",
    "下调",
    "调高",
    "调低",
    "修正",
    "调整",
    "改为",
    "转为",
    "转而",
    "从",
    "由",
    "提前",
    "推迟",
    "延后",
    "增加",
    "减少",
    "revised",
    "revises",
    "raised its forecast",
    "lowered its forecast",
    "raises its",
    "lowers its",
    "now expects",
    "shifted",
    "moved forward",
    "pushed back",
    "from",
)
STRONG_REVISION_MARKERS = (
    "上调",
    "下调",
    "调高",
    "调低",
    "改为",
    "转为",
    "转而",
    "不再预计",
    "取消",
    "提前",
    "推迟",
    "延后",
    "revised",
    "revises",
    "raised its forecast",
    "lowered its forecast",
    "raises its",
    "lowers its",
    "no longer expects",
    "moved forward",
    "moves forward",
    "pushed back",
    "pushes back",
)
FORECAST_MARKERS = (
    "预计",
    "预测",
    "预判",
    "展望",
    "forecast",
    "expects",
    "expecting",
    "sees the fed",
    "projects",
)
NEGATIVE_MARKERS = (
    "并未调整",
    "没有调整",
    "维持此前",
    "维持原预测",
    "unchanged forecast",
    "left its forecast unchanged",
    "网传",
    "传闻",
    "未经证实",
    "rumor",
    "reportedly may",
)
RETROSPECTIVE_MARKERS = ("回顾", "去年曾", "此前曾", "historical review", "last year had expected")
TIMING_SHIFT_MARKERS = ("提前", "推迟", "延后", "moved forward", "moves forward", "pushed back", "pushes back", "delayed")
OTHER_CENTRAL_BANK_MARKERS = (
    "欧洲央行",
    "日本央行",
    "韩国央行",
    "中国人民银行",
    "英国央行",
    "ecb",
    "bank of japan",
    "bank of korea",
    "people's bank of china",
    "bank of england",
)
TARGETS = ("美债收益率/美元", "A股风险偏好", "成长股估值")

FINANCIAL_LEADER_ROLE_MARKERS = (
    "首席执行官",
    "行政总裁",
    "董事长",
    "董事会主席",
    "掌门人",
    "chief executive officer",
    "chief executive",
    "ceo",
    "chairman",
)
FINANCIAL_LEADER_STATEMENT_MARKERS = (
    "表示",
    "称",
    "指出",
    "认为",
    "警告",
    "预测",
    "预计",
    "said",
    "stated",
    "believes",
    "warned",
    "expects",
    "predicted",
)
LONG_RATE_MARKERS = (
    "长期美国国债",
    "长期美债",
    "美国国债",
    "美债收益率",
    "10年期国债",
    "十年期国债",
    "利率",
    "long-term treasuries",
    "long-term treasury",
    "u.s. treasuries",
    "us treasuries",
    "treasury bonds",
    "government bonds",
    "treasury yields",
    "10-year treasury",
    "interest rate",
    "rate outlook",
    "higher rates",
)
EQUITY_VALUATION_MARKERS = (
    "股票",
    "股市",
    "标普 500",
    "标普500",
    "估值",
    "stocks",
    "equities",
    "stock market",
    "s&p 500",
    "valuation",
    "valuations",
)
CROSS_ASSET_RISK_MARKERS = (
    "市场低估风险",
    "低估了风险",
    "未充分计入",
    "地缘政治风险",
    "财政风险",
    "预算赤字",
    "重大冲击",
    "风险偏好",
    "underestimates risk",
    "underestimate the risk",
    "underpricing risk",
    "not fully priced",
    "geopolitical risk",
    "fiscal risk",
    "budget deficit",
    "major shock",
    "risk appetite",
)
EXPLICIT_ALLOCATION_STANCE_MARKERS = (
    "不会买入",
    "不会购买",
    "不愿买入",
    "避免买入",
    "would not buy",
    "wouldn't buy",
    "will not buy",
    "avoid buying",
)
RATE_DIRECTION_MARKERS = (
    "利率走高",
    "利率上升",
    "收益率走高",
    "收益率上升",
    "维持在",
    "rates will rise",
    "rates could rise",
    "rates may rise",
    "higher rates",
    "yields will rise",
    "yields could rise",
    "yields may rise",
    "remain at",
)

_CN_NUMBERS = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}


def compact_text(*values: object) -> str:
    return re.sub(r"\s+", " ", " ".join(str(value or "") for value in values)).strip()


def item_text(item: dict[str, Any]) -> str:
    parts = []
    for value in (item.get("title"), item.get("summary"), item.get("content"), item.get("full_text")):
        text = re.sub(r"[ \t\r\f\v]+", " ", str(value or "")).strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = f" {text.casefold()} "
    return any(marker.casefold() in lowered for marker in markers)


def allowed_fed_path_banks(aliases: Iterable[object]) -> set[str]:
    configured = {str(value).casefold().strip() for value in aliases if str(value).strip()}
    return {
        display.casefold()
        for display, bank_aliases in INTERNATIONAL_BANK_ALIASES
        if display in FED_PATH_BANKS
        and (
            display.casefold() in configured
            or any(alias.casefold() in configured for alias in bank_aliases)
        )
    }


def _number(value: str) -> int | None:
    value = value.strip()
    if value.isdigit():
        return int(value)
    return _CN_NUMBERS.get(value)


def _action(text: str) -> str:
    has_hike = contains_any(text, HIKES)
    has_cut = contains_any(text, CUTS)
    if has_hike and not has_cut:
        return "hike"
    if has_cut and not has_hike:
        return "cut"
    if "按兵不动" in text or "维持利率" in text or "hold rates" in text.casefold() or "no rate change" in text.casefold():
        return "hold"
    return ""


def _count(text: str, action: str) -> int | None:
    action_words = HIKES if action == "hike" else CUTS if action == "cut" else ()
    for word in action_words:
        escaped = re.escape(word)
        patterns = (
            rf"{escaped}.{{0,8}}?([0-9一二两三四五六])\s*次",
            rf"([0-9一二两三四五六])\s*次.{{0,8}}?{escaped}",
            rf"([0-9]+)\s*(?:quarter-point|25\s*(?:bp|basis-point)).{{0,12}}?{escaped}",
            rf"{escaped}.{{0,12}}?([0-9]+)\s*(?:times|moves)",
        )
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.I)
            if match:
                return _number(match.group(1))
    if action == "hold" and re.search(r"(?:0|零)\s*次|no\s+(?:rate\s+)?(?:changes?|cuts?|hikes?)", text, flags=re.I):
        return 0
    return None


def _basis_points(text: str) -> int | None:
    values = [int(value) for value in re.findall(r"(?<!\d)(\d{1,3})\s*(?:bp|bps|个?基点)", text, flags=re.I)]
    return max(values) if values else None


def _policy_basis_points(text: str) -> int | None:
    values: list[int] = []
    for sentence in [part.strip() for part in re.split(r"(?<=[。！？!?;；])\s*|\n+", text) if part.strip()]:
        if any(marker in sentence for marker in ("收益率", "利差", "yield spread", "treasury yield")):
            continue
        if not contains_any(sentence, HIKES + CUTS):
            continue
        if not re.search(r"(?:累计|合计|每次|各|分别)|(?:加息|降息|升息|减息).{0,20}\d{1,3}\s*(?:bp|bps|个?基点)", sentence, flags=re.I):
            continue
        value = _basis_points(sentence)
        if value is not None:
            values.append(value)
    return max(values) if values else None


def _basis_point_revision(text: str) -> tuple[int, int] | None:
    patterns = (
        r"(?:从|由)\s*(\d{1,3})\s*(?:bp|bps|个?基点).{0,20}?(?:至|到|为)\s*(\d{1,3})\s*(?:bp|bps|个?基点)",
        r"from\s+(\d{1,3})\s*(?:bp|bps|basis points?).{0,20}?to\s+(\d{1,3})\s*(?:bp|bps|basis points?)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def _terminal_rate(text: str) -> str:
    match = re.search(r"(?:终端利率|terminal rate).{0,18}?(\d+(?:\.\d+)?\s*[%％])", text, flags=re.I)
    return match.group(1).replace("％", "%") if match else ""


def _months(text: str) -> list[str]:
    months = re.findall(r"(?<!\d)(1[0-2]|[1-9])\s*月", text)
    if not months:
        months = re.findall(
            r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b",
            text,
            flags=re.I,
        )
    return list(dict.fromkeys(month.casefold() for month in months))


def _meeting_months(text: str, action: str, timing_shift: bool) -> list[str]:
    for sentence in [part.strip() for part in re.split(r"(?<=[。！？!?;；])\s*|\n+", text) if part.strip()]:
        if action and _action(sentence) not in {action, ""}:
            continue
        if timing_shift or re.search(r"(?:各|分别|每次).{0,8}(?:加息|降息|升息|减息)|(?:加息|降息).{0,12}(?:各|分别|每次)", sentence):
            months = _months(re.sub(r"(?<!\d)(?:1[0-2]|[1-9])月(?:[0-3]?\d)日", "", sentence))
            if months:
                return months
    return []


def _revision_parts(text: str) -> tuple[str, str]:
    patterns = (
        r"(?:从|由)\s*(.{1,100}?)\s*(?:改为|调整为|转为|上调至|下调至|降至|升至|到|至)\s*(.{1,140})",
        r"from\s+(.{1,100}?)\s+to\s+(.{1,140})",
        r"(?:此前|原先|原本)(.{1,100}?)[，,;；。]\s*(?:现|目前|如今|最新)(?:预计|预期|改为|转为)?\s*(.{1,140})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1).strip(" ，,;；。"), match.group(2).strip(" ，,;；。")
    return "", text


def _count_revision(text: str) -> tuple[str, int, int] | None:
    match = re.search(
        r"(?:加息|降息|升息|减息)(?:次数|预期|预测|路径)?.{0,12}?从\s*([0-9一二两三四五六])\s*次.{0,12}?(?:至|到|为)\s*([0-9一二两三四五六])\s*次",
        text,
        flags=re.I,
    )
    if not match:
        match = re.search(
            r"from\s+([0-9]+)\s+(?:cuts?|hikes?).{0,30}?to\s+([0-9]+)\s+(cuts?|hikes?)",
            text,
            flags=re.I,
        )
        if not match:
            return None
        action = "cut" if "cut" in match.group(3).casefold() else "hike"
    else:
        action = "cut" if contains_any(text[: match.start() + 8], CUTS) else "hike"
    previous = _number(match.group(1))
    revised = _number(match.group(2))
    if previous is None or revised is None:
        return None
    return action, previous, revised


def _direction_revision(text: str) -> tuple[str, str] | None:
    match = re.search(
        r"(?:由|从)(?:此前|原先|原本)?(?:预期)?\s*(降息|减息|加息|升息|按兵不动|不调整利率).{0,16}?(?:转为|改为|调整为)(?:预期)?\s*(降息|减息|加息|升息|按兵不动|不调整利率)",
        text,
    )
    if not match:
        stopped = re.search(r"(?:不再预计|取消|no longer expects?).{0,20}?(降息|减息|加息|升息|rate cuts?|rate hikes?)", text, flags=re.I)
        if not stopped:
            return None
        previous = "cut" if contains_any(stopped.group(1), CUTS) else "hike"
        return previous, "hold"
    mapping = {"降息": "cut", "减息": "cut", "加息": "hike", "升息": "hike", "按兵不动": "hold", "不调整利率": "hold"}
    return mapping[match.group(1)], mapping[match.group(2)]


def _evidence_sentences(text: str, banks: list[str]) -> list[str]:
    sentences = [part.strip() for part in re.split(r"(?<=[。！？!?;；])\s*|\n+", text) if part.strip()]
    selected = []
    for sentence in sentences:
        if (
            any(bank in sentence for bank in banks)
            or contains_any(sentence, REVISION_MARKERS)
            or contains_any(sentence, HIKES + CUTS)
        ):
            selected.append(sentence[:360])
    return list(dict.fromkeys(selected))[:3]


def _published_day(item: dict[str, Any]) -> str:
    raw = str(item.get("published_at") or "")
    match = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    return match.group(1) if match else "undated"


def _forecast_year(text: str, item: dict[str, Any]) -> str:
    match = re.search(r"\b(20\d{2})\b", text)
    if match:
        return match.group(1)
    if "今年" in text or "this year" in text.casefold():
        day = _published_day(item)
        return day[:4] if day != "undated" else "current_year"
    if "明年" in text or "next year" in text.casefold():
        day = _published_day(item)
        return str(int(day[:4]) + 1) if day != "undated" else "next_year"
    return "unspecified"


def _source_tier(item: dict[str, Any]) -> str:
    role = str(item.get("publisher_role") or item.get("source_category") or "").casefold()
    text = compact_text(item.get("source_module"), item.get("source_display"), item.get("title"))
    return "机构公开材料" if role in {"first_party", "investment_bank"} or "官网" in text else "媒体明确署名转述"


def fed_path_candidate(item: dict[str, Any]) -> bool:
    return international_bank_fed_rate_path_rule(str(item.get("source") or ""), item) is not None


def classify_trusted_financial_leader_macro_judgement(
    item: dict[str, Any],
    *,
    allowed_banks: set[str] | None = None,
) -> dict[str, Any] | None:
    """Recognize a material cross-asset judgment explicitly attributed to a trusted bank leader."""
    text = item_text(item)
    if not text or (allowed_banks is not None and not allowed_banks):
        return None
    allowed = allowed_banks or {name.casefold() for name in FED_PATH_BANKS}
    banks = banks_in_mention_order(text, allowed_banks=allowed)
    if not banks:
        return None
    sentences = [part.strip() for part in re.split(r"(?<=[。！？!?;；])\s*|\n+", text) if part.strip()]
    for index, sentence in enumerate(sentences):
        selected_bank = next(
            (
                bank
                for bank in banks
                if bank_mention_position(sentence, bank) is not None
                and contains_any(sentence, FINANCIAL_LEADER_ROLE_MARKERS)
            ),
            "",
        )
        if not selected_bank:
            continue
        window_parts = [sentence]
        for following in sentences[index + 1 : index + 7]:
            mentioned = [bank for bank in banks if bank_mention_position(following, bank) is not None]
            if mentioned and selected_bank not in mentioned:
                break
            window_parts.append(following)
        window = compact_text(*window_parts)
        if not contains_any(window, FINANCIAL_LEADER_STATEMENT_MARKERS):
            continue
        has_rates = contains_any(window, LONG_RATE_MARKERS)
        has_equities = contains_any(window, EQUITY_VALUATION_MARKERS)
        has_risk = contains_any(window, CROSS_ASSET_RISK_MARKERS)
        explicit_cross_asset_stance = bool(
            (
                contains_any(window, EXPLICIT_ALLOCATION_STANCE_MARKERS)
                or re.search(r"(?:不会|不愿|避免).{0,20}(?:买入|购买)", window)
            )
            and has_rates
            and has_equities
        )
        explicit_rate_view = bool(
            has_rates
            and contains_any(window, RATE_DIRECTION_MARKERS)
            and (
                re.search(r"\d+(?:\.\d+)?\s*[%％]", window)
                or contains_any(window, ("预测", "预计", "认为", "expects", "predicted", "believes"))
            )
        )
        material_risk_view = bool(
            has_risk
            and contains_any(
                window,
                (
                    "低估",
                    "未充分计入",
                    "警告",
                    "underestimat",
                    "underpricing",
                    "not fully priced",
                    "warned",
                ),
            )
        )
        signals = [
            name
            for name, matched in (
                ("cross_asset_allocation_stance", explicit_cross_asset_stance),
                ("explicit_rate_or_yield_view", explicit_rate_view),
                ("material_cross_asset_risk_view", material_risk_view),
            )
            if matched
        ]
        if len(signals) < 2 or sum((has_rates, has_equities, has_risk)) < 2:
            continue
        reason = (
            f"受信任大型金融机构负责人重大判断：{selected_bank}负责人对利率或长期美债、"
            "股市估值及跨资产风险作出明确判断。"
        )
        return {
            "matched": True,
            "rule_id": "fed_policy_material_exception",
            "decision_action": "push",
            "importance": "high",
            "push_now": True,
            "should_push": True,
            "reason": reason,
            "brief_reason": reason,
            "event_type": "trusted_financial_leader_material_judgement",
            "institutions": [selected_bank],
            "material_signals": signals,
            "affected_targets": list(TARGETS),
            "evidence_quotes": [window[:900]],
        }
    return None


def classify_international_bank_fed_path(
    item: dict[str, Any],
    *,
    allowed_banks: set[str] | None = None,
) -> dict[str, Any] | None:
    text = item_text(item)
    title = compact_text(item.get("title"))
    if not text or not contains_any(text, FED_MARKERS):
        return None
    if contains_any(text, NEGATIVE_MARKERS):
        return None
    if contains_any(title, RETROSPECTIVE_MARKERS) and not contains_any(title, REVISION_MARKERS):
        return None
    if allowed_banks is not None and not allowed_banks:
        return None
    allowed = allowed_banks or {name.casefold() for name in FED_PATH_BANKS}
    banks = banks_in_mention_order(text, allowed_banks=allowed)
    if not banks:
        return None
    if not (
        contains_any(text, HIKES + CUTS)
        or "按兵不动" in text
        or "hold rates" in text.casefold()
        or "终端利率" in text
        or "terminal rate" in text.casefold()
    ):
        return None

    local_sentences = [part.strip() for part in re.split(r"(?<=[。！？!?;；])\s*|\n+", text) if part.strip()]
    qualifying: list[tuple[int, str, str]] = []
    for index, sentence in enumerate(local_sentences):
        for bank in banks:
            if bank_mention_position(sentence, bank) is None:
                continue
            window_parts = [sentence]
            for following in local_sentences[index + 1 : index + 7]:
                mentioned = [name for name in banks if bank_mention_position(following, name) is not None]
                if mentioned and bank not in mentioned:
                    break
                window_parts.append(following)
            window = compact_text(*window_parts)
            if any(bank_mention_position(window, other) is not None for other in banks if other != bank):
                continue
            if contains_any(window, OTHER_CENTRAL_BANK_MARKERS):
                continue
            if not contains_any(window, FED_MARKERS):
                continue
            if not (
                contains_any(window, HIKES + CUTS)
                or "按兵不动" in window
                or "hold rates" in window.casefold()
                or "终端利率" in window
                or "terminal rate" in window.casefold()
            ):
                continue
            if not (contains_any(window, REVISION_MARKERS) or contains_any(window, FORECAST_MARKERS)):
                continue
            qualifying.append((index, bank, window))
    if not qualifying:
        return None
    _first_index, selected_bank, _first_sentence = qualifying[0]
    attributed_path_sentences = [sentence for _index, bank, sentence in qualifying if bank == selected_bank][:3]
    banks = [selected_bank, *[bank for bank in banks if bank != selected_bank]]
    title_has_bank = bank_mention_position(title, selected_bank) is not None
    lead_text = compact_text(title if title_has_bank else "", *attributed_path_sentences)

    previous_path, revised_path = _revision_parts(lead_text)
    previous_action = _action(previous_path)
    revised_action = _action(revised_path)
    if not revised_action:
        revised_action = _action(text)
    if not revised_action and ("终端利率" in lead_text or "terminal rate" in lead_text.casefold()):
        revised_action = "terminal_rate"
    direction_revision = _direction_revision(lead_text)
    if direction_revision:
        previous_action, revised_action = direction_revision
    title_action = _action(title)
    if title_action and contains_any(title, REVISION_MARKERS):
        revised_action = title_action
    timing_shift = contains_any(lead_text, TIMING_SHIFT_MARKERS)
    if timing_shift and _action(lead_text):
        revised_action = _action(lead_text)
    previous_count = _count(previous_path, previous_action) if previous_action else None
    revised_count = _count(revised_path, revised_action) if revised_action else None
    count_revision = _count_revision(lead_text)
    if count_revision:
        revised_action, previous_count, revised_count = count_revision
        previous_action = revised_action
    if revised_count is None and revised_action:
        revised_count = _count(lead_text, revised_action)
    if revised_count is None and revised_action and title_has_bank:
        revised_count = _count(title, revised_action)
    if previous_count is None:
        prior_count = re.search(r"此前(?:是|为|预计)?\s*([0-9一二两三四五六])\s*次", title)
        previous_count = _number(prior_count.group(1)) if prior_count else None
    bp_revision = _basis_point_revision(lead_text)
    previous_basis_points = bp_revision[0] if bp_revision else None
    basis_points = bp_revision[1] if bp_revision else _policy_basis_points(revised_path) or _policy_basis_points(lead_text)
    terminal_rate = _terminal_rate(revised_path) or _terminal_rate(lead_text)
    months = _meeting_months(lead_text, revised_action, timing_shift)
    if not months:
        months = _months(re.sub(r"(?<!\d)(?:1[0-2]|[1-9])月(?:[0-3]?\d)日", "", revised_path))
    if not months or timing_shift:
        months = _months(re.sub(r"(?<!\d)(?:1[0-2]|[1-9])月(?:[0-3]?\d)日", "", lead_text))
    if revised_count is None and revised_action in {"hike", "cut"} and len(months) > 1:
        revised_count = len(months)
    direction_reversal = bool(previous_action and revised_action and previous_action != revised_action)
    count_change = previous_count is not None and revised_count is not None and previous_count != revised_count
    selected_bank_sentences = [
        sentence for sentence in local_sentences if bank_mention_position(sentence, banks[0]) is not None
    ]
    attributed_timing = any(
        contains_any(sentence, TIMING_SHIFT_MARKERS)
        and contains_any(sentence, FED_MARKERS)
        and contains_any(sentence, HIKES + CUTS)
        for sentence in selected_bank_sentences
    )
    attributed_strong_bp = any(
        contains_any(sentence, STRONG_REVISION_MARKERS)
        and contains_any(sentence, FED_MARKERS)
        and (_policy_basis_points(sentence) or 0) >= 25
        for sentence in selected_bank_sentences
    )
    attributed_strong_terminal = any(
        contains_any(sentence, STRONG_REVISION_MARKERS)
        and contains_any(sentence, FED_MARKERS)
        and bool(_terminal_rate(sentence))
        for sentence in selected_bank_sentences
    )
    material = bool(
        direction_reversal
        or count_change
        or bool(bp_revision and previous_basis_points != basis_points)
        or attributed_timing
        or attributed_strong_bp
        or attributed_strong_terminal
    )
    action = "push" if material else "daily"
    importance = "high" if material else "medium"
    forecast_year = _forecast_year(text, item)
    display_action = {"hike": "加息", "cut": "降息", "hold": "维持利率", "terminal_rate": "终端利率"}.get(revised_action, "调整")
    path_display = f"{revised_count}次{display_action}" if revised_count is not None else display_action
    if basis_points:
        path_display += f"（{basis_points}bp）"
    previous_display = previous_path[:80] if previous_path else "此前路径未量化"
    reason = f"国际大行美联储利率路径规则：{banks[0]} {previous_display} -> {path_display}；来源层级：{_source_tier(item)}。"
    evidence = _evidence_sentences(text, [banks[0]])
    rule = {
        "matched": True,
        "rule_id": RULE_ID,
        "decision_action": action,
        "importance": importance,
        "push_now": action == "push",
        "should_push": action == "push",
        "reason": reason,
        "brief_reason": reason,
        "affected_targets": [f"{banks[0]}：{previous_display} -> {path_display}", *TARGETS][:5],
        "related_targets": [
            {"name": target, "code": "", "relation": "国际大行/Fed 利率路径", "direction": "uncertain"}
            for target in TARGETS
        ],
        "banks": banks,
        "policy_target": "Federal Reserve",
        "revision_type": "material_revision" if material else "current_forecast",
        "previous_path": previous_path,
        "revised_path": revised_path[:240],
        "previous_action": previous_action,
        "revised_action": revised_action,
        "previous_count": previous_count,
        "revised_count": revised_count,
        "direction": (
            "higher"
            if revised_action == "hike" or contains_any(lead_text, ("上调", "调高", "raises", "raised"))
            else "lower"
            if revised_action == "cut" or contains_any(lead_text, ("下调", "调低", "lowers", "lowered"))
            else "hold"
        ),
        "first_move_timing": months[0] if months else "",
        "meeting_months": months,
        "cumulative_bp": basis_points,
        "previous_cumulative_bp": previous_basis_points,
        "terminal_rate": terminal_rate,
        "forecast_horizon": forecast_year,
        "report_date": _published_day(item),
        "source_tier": _source_tier(item),
        "evidence_quotes": evidence,
    }
    return rule


def _dedup_key(classification: dict[str, Any]) -> str:
    banks = classification.get("banks") or []
    months = classification.get("meeting_months") or []
    identity = "|".join(
        (
            str(banks[0]) if banks else "unknown",
            str(classification.get("report_date") or "undated"),
            str(classification.get("forecast_horizon") or "unspecified"),
            str(classification.get("revised_action") or "unspecified"),
            str(classification.get("revised_count"))
            if classification.get("revised_count") is not None
            else "unknown",
            str(classification.get("cumulative_bp"))
            if classification.get("cumulative_bp") is not None
            else "unknown",
            str(classification.get("terminal_rate") or "unknown"),
            ",".join(str(value) for value in months),
        )
    )
    digest = hashlib.sha256(identity.casefold().encode("utf-8")).hexdigest()[:20]
    return f"ib_fed_path:{digest}"


def international_bank_fed_rate_path_rule(source: str, item: dict[str, Any]) -> dict[str, Any] | None:
    from rule_center import rule_enabled, rule_settings

    if not rule_enabled(RULE_ID):
        return None
    configured = {
        str(value).casefold()
        for value in rule_settings(RULE_ID).get("allowed_banks") or []
        if str(value).strip()
    }
    classification = classify_international_bank_fed_path(
        item,
        allowed_banks=configured or {name.casefold() for name in FED_PATH_BANKS},
    )
    if not classification:
        return None
    return {
        **classification,
        "dedup_key": _dedup_key(classification),
        "dedup_lookback_days": 14,
        "source": source,
    }
