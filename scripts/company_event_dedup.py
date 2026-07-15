"""Delivery-only identities for source-neutral company events."""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from market_item import DecisionResult


COMPANY_EVENT_RULE_ID = "company_event_dedup"
COMPANY_EVENT_LOOKBACK_DAYS = 90
BEIJING_TIMEZONE = timezone(timedelta(hours=8))
ELIGIBLE_RULE_IDS = {"holding_keyword_immediate_alert", "industry_quantified_hardline"}

# These ids only preserve reservations created by the bounded predecessor. They
# do not control which issuers are eligible for extraction.
LEGACY_SUBJECT_IDS = {
    "力积电": "powerchip",
    "力積電": "powerchip",
    "powerchip": "powerchip",
    "psmc": "powerchip",
    "佰维存储": "biwin_storage",
    "佰維存儲": "biwin_storage",
    "biwinstorage": "biwin_storage",
    "biwin": "biwin_storage",
    "大普微": "dapustor",
    "dapustor": "dapustor",
    "江丰电子": "jiangfeng_electronics",
    "江豐電子": "jiangfeng_electronics",
    "仕佳光子": "shijia_photons",
    "shijiaphotons": "shijia_photons",
}
SUBJECT_CANONICAL_ALIASES = {
    "力積電": "力积电",
    "powerchip": "力积电",
    "psmc": "力积电",
    "佰維存儲": "佰维存储",
    "biwinstorage": "佰维存储",
    "biwin": "佰维存储",
    "dapustor": "大普微",
    "江豐電子": "江丰电子",
    "shijiaphotons": "仕佳光子",
}

CORRECTION_MARKERS = (
    "更正",
    "修正",
    "上修",
    "下修",
    "调整为",
    "調整為",
    "补充公告",
    "補充公告",
    "correction",
    "corrected",
    "revised",
    "updated guidance",
)
PROPOSED_MARKERS = ("拟", "擬", "计划", "計劃", "筹划", "籌劃", "预计", "預計", "proposed", "plans to")
APPROVED_MARKERS = ("获批", "獲批", "审核通过", "審核通過", "注册生效", "註冊生效", "approved")
COMPLETED_MARKERS = (
    "完成交割",
    "发行完成",
    "發行完成",
    "募集完成",
    "工商登记完成",
    "工商登記完成",
    "正式成立",
    "建成投产",
    "建成投產",
    "completed",
)
TERMINATED_MARKERS = ("终止", "終止", "取消", "撤回", "未获通过", "未獲通過", "terminated", "cancelled")

EARNINGS_FORECAST_MARKERS = ("预计", "預計", "预盈", "預盈", "预增", "預增", "预告", "預告", "业绩预告", "業績預告", "guidance")
EARNINGS_RESULT_MARKERS = ("实现", "實現", "录得", "錄得", "业绩快报", "業績快報", "财报", "財報", "results")
EARNINGS_METRIC_MARKERS = (
    "净利润",
    "淨利潤",
    "营业收入",
    "營業收入",
    "营收",
    "營收",
    "扭亏",
    "扭虧",
    "同比增长",
    "同比增長",
    "同比下降",
    "net profit",
    "revenue",
)

FAMILY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("joint_venture", ("合资公司", "合資公司", "共同出资设立", "共同出資設立", "joint venture")),
    ("private_placement", ("定增", "向特定对象发行", "向特定對象發行", "private placement")),
    ("regulatory", ("立案调查", "立案調查", "行政处罚", "行政處罰", "监管问询", "監管問詢", "regulatory investigation")),
    ("litigation", ("诉讼", "訴訟", "仲裁", "判决", "判決", "lawsuit", "arbitration")),
    ("acquisition", ("收购", "收購", "要约收购", "要約收購", "acquisition", "acquire")),
    ("disposal", ("出售", "转让股权", "轉讓股權", "资产剥离", "資產剝離", "divest", "disposal")),
    ("contract_order", ("中标", "中標", "签订合同", "簽訂合同", "签署合同", "获得订单", "獲得訂單", "新增订单", "新增訂單", "订单金额", "訂單金額", "won a bid", "contract")),
    ("investment_project", ("投资建设", "投資建設", "投资项目", "投資項目", "建设项目", "建設項目", "募投项目", "募投項目")),
    ("capacity_change", ("扩产", "擴產", "产能建设", "產能建設", "产能扩建", "產能擴建", "减产", "減產", "停产", "停產")),
    ("price_change", ("涨价", "漲價", "降价", "降價", "报价上调", "報價上調", "报价下调", "報價下調", "价格上调", "價格上調", "价格下调", "價格下調", "price hike", "price cut")),
    ("buyback", ("回购股份", "回購股份", "股份回购", "股份回購", "share buyback", "repurchase")),
    ("shareholding_change", ("增持股份", "减持股份", "減持股份", "持股比例", "stake increase", "stake sale")),
    ("financing", ("发行债券", "發行債券", "可转债", "可轉債", "银行授信", "銀行授信", "融资", "融資", "bond issuance")),
    ("product_release", ("发布新品", "發布新品", "推出新品", "正式发布", "正式發布", "product launch", "launched")),
    ("partnership", ("战略合作", "戰略合作", "签署合作", "簽署合作", "合作协议", "合作協議", "partnership")),
    ("management_change", ("辞任", "辭任", "辞职", "辭職", "聘任", "任命", "management change")),
    ("production_milestone", ("量产", "量產", "投产", "投產", "送样", "送樣", "客户验证", "客戶驗證", "mass production")),
)

OBJECT_MARKERS = (
    "PCB",
    "AI",
    "HBM",
    "DRAM",
    "NAND",
    "SSD",
    "GPU",
    "CPU",
    "CPO",
    "光模块",
    "光模塊",
    "半导体",
    "半導體",
    "存储",
    "存儲",
    "服务器",
    "伺服器",
    "数据中心",
    "數據中心",
    "机器人",
    "機器人",
    "芯片",
    "晶圆",
    "晶圓",
    "合资公司",
    "合資公司",
)

SUBJECT_ACTION_MARKERS = (
    "公告称",
    "公告",
    "预计",
    "預計",
    "拟",
    "擬",
    "宣布",
    "披露",
    "发布",
    "發布",
    "表示",
    "签署",
    "簽署",
    "中标",
    "中標",
    "获批",
    "獲批",
    "完成",
    "终止",
    "終止",
    "上修",
    "下修",
)

INVALID_SUBJECTS = {
    "公司",
    "该公司",
    "該公司",
    "上市公司",
    "双方",
    "雙方",
    "消息人士",
    "财联社",
    "財聯社",
    "第一财经",
    "新浪财经",
    "华尔街见闻",
    "市场",
    "分析师",
    "机构",
    "本月",
    "小财注",
    "金十重要事件",
    "金十数据",
    "全系",
    "央行",
    "壹评级",
    "多家a股公司",
    "更正",
    "修正",
    "上修",
    "下修",
}

INVALID_SUBJECT_FRAGMENTS = (
    "净利润",
    "淨利潤",
    "营业收入",
    "營業收入",
    "同比",
    "环比",
    "環比",
    "扭亏",
    "扭虧",
    "预计",
    "預計",
    "预告",
    "預告",
    "用于",
    "用於",
    "扣除",
    "发行费用",
    "發行費用",
    "小财注",
    "小財註",
    "重要事件",
    "金十",
    "已全部",
    "消息面上",
    "截至发稿",
    "截至發稿",
    "需求驱动",
    "需求驅動",
    "算力需求",
    "行业需求",
    "行業需求",
    "多家公司",
    "多家a股",
    "多家",
    "上市公司盈利",
    "评级",
    "評級",
    "旗下",
    "全系",
)

LEGAL_SUFFIXES = (
    "集团股份有限公司",
    "集團股份有限公司",
    "股份有限公司",
    "有限责任公司",
    "有限責任公司",
    "有限公司",
    " corporation",
    " incorporated",
    " holdings",
    " holding",
    " corp.",
    " corp",
    " inc.",
    " inc",
    " ltd.",
    " ltd",
)

CODE_SUBJECT_RE = re.compile(
    r"(?P<name>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9·・.&\- ]{1,30}?)\s*[（(]"
    r"(?P<code>\d{6}\.(?:SZ|SH|BJ))",
    re.IGNORECASE,
)
AMOUNT_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:\d+(?:\.\d+)?(?:万|萬|亿|億|千)?(?:元|美元|港元|欧元|歐元)|\d+(?:\.\d+)?%)",
    re.IGNORECASE,
)


def _text(item: dict[str, Any]) -> str:
    return "\n".join(
        str(item.get(key) or "") for key in ("title", "summary", "content", "full_text") if item.get(key)
    )


def _clean_markup(value: str) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    return re.sub(r"<[^>]+>", "", text)


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(marker.casefold() in lowered for marker in markers)


def _claims(text: str) -> list[str]:
    cleaned = _clean_markup(text)
    result: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[。！？!?；;\n]+", cleaned):
        claim = " ".join(part.split()).strip(" -—–")
        key = claim.casefold()
        if claim and key not in seen:
            seen.add(key)
            result.append(claim)
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
    patterns = (
        ("H1", r"(?:(20\d{2})年)?(?:上半年|半年度|h1|first half)"),
        ("H2", r"(?:(20\d{2})年)?(?:下半年|h2|second half)"),
        ("Q1", r"(?:(20\d{2})年)?(?:一季度|第一季度|q1|first quarter)"),
        ("Q2", r"(?:(20\d{2})年)?(?:二季度|第二季度|q2|second quarter)"),
        ("Q3", r"(?:(20\d{2})年)?(?:三季度|第三季度|q3|third quarter)"),
        ("Q4", r"(?:(20\d{2})年)?(?:四季度|第四季度|q4|fourth quarter)"),
        ("FY", r"(?:(20\d{2})年)?(?:全年|年度|full year|fy)"),
    )
    for suffix, pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        year = int(match.group(1)) if match.group(1) else published.year if published else None
        if year:
            return f"{year}-{suffix}"
    return ""


def _event_date(text: str, published_at: object) -> str:
    published = _published_date(published_at)
    full = re.search(r"(20\d{2})[年/-](1[0-2]|0?[1-9])[月/-](3[01]|[12]\d|0?[1-9])日?", text)
    if full:
        return f"{int(full.group(1)):04d}-{int(full.group(2)):02d}-{int(full.group(3)):02d}"
    partial = re.search(r"(1[0-2]|[1-9])月(3[01]|[12]\d|[1-9])日", text)
    if partial and published:
        return f"{published.year:04d}-{int(partial.group(1)):02d}-{int(partial.group(2)):02d}"
    return published.date().isoformat() if published else ""


def _normalize_subject_name(value: str) -> str:
    name = _clean_markup(value)
    name = re.sub(r"^[【\[]|[】\]]$", "", name).strip()
    name = re.sub(r"^(?:截至发稿|其中|此外|同时|值得注意的是|\d+月\d+日|\d+连板)", "", name).strip(" ，,:：-—")
    if "——" in name:
        name = name.rsplit("——", 1)[-1]
    name = re.sub(r"[（(]\d{6}\.(?:SZ|SH|BJ)[）)].*$", "", name, flags=re.IGNORECASE).strip()
    name = re.sub(r"[-—](?:UW|U|W)$", "", name, flags=re.IGNORECASE).strip()
    lowered = name.casefold()
    for suffix in LEGAL_SUFFIXES:
        if lowered.endswith(suffix.casefold()) and len(name) > len(suffix) + 1:
            name = name[: -len(suffix)].strip()
            lowered = name.casefold()
            break
    name = re.sub(r"\s+", " ", name).strip(" ，,:：-—")
    name = re.sub(r"(?:近期|近日)?对$", "", name).strip()
    for object_suffix in ("存储代工", "存儲代工", "股份", "股票"):
        if name.endswith(object_suffix) and len(name) > len(object_suffix) + 1:
            name = name[: -len(object_suffix)].strip()
    for event_suffix in ("定增方案", "收购方案", "收購方案", "回购方案", "回購方案", "业绩预告", "業績預告"):
        if name.endswith(event_suffix) and len(name) > len(event_suffix) + 1:
            name = name[: -len(event_suffix)].strip()
    alias_key = re.sub(r"[^\w\u4e00-\u9fff]+", "", name.casefold(), flags=re.UNICODE)
    name = SUBJECT_CANONICAL_ALIASES.get(alias_key, name)
    if name.startswith(("公司全资子公司", "公司全資子公司", "该公司", "該公司")) or "与" in name or "與" in name:
        return ""
    if name in INVALID_SUBJECTS or not (2 <= len(name) <= 32) or name[0].isdigit():
        return ""
    if any(fragment.casefold() in name.casefold() for fragment in INVALID_SUBJECT_FRAGMENTS):
        return ""
    if re.search(r"[\u4e00-\u9fff]", name) and len(name) > 14:
        return ""
    if name.endswith(("之后", "之後", "以后", "以後", "方面", "情况下", "情況下")):
        return ""
    if not re.search(r"[\u4e00-\u9fffA-Za-z]", name):
        return ""
    return name


def _subject_key(name: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", name.casefold(), flags=re.UNICODE)


def _direct_targets(decision: DecisionResult) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for hit in decision.rule_hits:
        for target in hit.get("related_targets") or []:
            if not isinstance(target, dict) or "直接持仓" not in str(target.get("relation") or ""):
                continue
            name = _normalize_subject_name(str(target.get("name") or ""))
            code = str(target.get("code") or "").strip().upper()
            key = _subject_key(name)
            if name and key not in seen:
                seen.add(key)
                result.append((name, code))
    return result


def _subjects_for_claim(claim: str, decision: DecisionResult) -> list[dict[str, str]]:
    candidates: list[tuple[str, str]] = []
    lowered = claim.casefold()
    for name, code in _direct_targets(decision):
        if name.casefold() in lowered or (code and code.casefold() in lowered):
            candidates.append((name, code))
    for match in CODE_SUBJECT_RE.finditer(claim):
        candidates.append((match.group("name"), match.group("code").upper()))
    if "：" in claim or ":" in claim:
        parts = re.split(r"[：:]", claim)
        if len(parts) >= 2:
            candidates.append((parts[-2], ""))
    marker_group = "|".join(re.escape(marker) for marker in SUBJECT_ACTION_MARKERS)
    for match in re.finditer(
        rf"(?:^|[，,；;。：:])(?P<name>[^，,；;。:：]{{2,36}}?)(?:\s*[（(]\d{{6}}\.(?:SZ|SH|BJ)[）)])?\s*(?:{marker_group})",
        claim,
        flags=re.IGNORECASE,
    ):
        candidates.append((match.group("name"), ""))
    if not candidates:
        for match in re.finditer(
            r"(?:^|[，,；;。：:])(?P<name>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9·・.&\- ]{1,20}?)"
            r"(?=(?:本月起|\d{1,2}月起)?(?:存储代工|存儲代工|产品|產品|服务|服務).{0,12}?"
            r"(?:涨价|漲價|降价|降價|报价上调|報價上調|价格上调|價格上調|价格下调|價格下調))",
            claim,
            flags=re.IGNORECASE,
        ):
            candidates.append((match.group("name"), ""))
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_name, code in candidates:
        name = _normalize_subject_name(raw_name)
        key = _subject_key(name)
        if not name or not key or key in seen:
            continue
        seen.add(key)
        result.append({"name": name, "key": key, "code": code})
    return result


def _material_anchors(text: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for match in AMOUNT_RE.finditer(text):
        value = match.group(0).replace(",", "").replace("萬", "万").replace("億", "亿").casefold()
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result[:8]


def _object_markers(text: str) -> list[str]:
    lowered = text.casefold()
    return [marker.casefold() for marker in OBJECT_MARKERS if marker.casefold() in lowered][:6]


def _counterparties(text: str, subject_name: str) -> list[str]:
    result: list[str] = []
    patterns = (
        r"与(?P<name>[\u4e00-\u9fffA-Za-z0-9·・.&\-]{2,30}?)(?:签署|簽署|共同|合资|合資|成立|设立|設立)",
        r"联合(?P<name>[\u4e00-\u9fffA-Za-z0-9·・.&\-]{2,30}?)(?:设立|設立|成立|投资|投資)",
        r"收购(?P<name>[\u4e00-\u9fffA-Za-z0-9·・.&\-]{2,30}?)(?:\d+(?:\.\d+)?%?股权|控股权|控制权)",
        r"收購(?P<name>[\u4e00-\u9fffA-Za-z0-9·・.&\-]{2,30}?)(?:\d+(?:\.\d+)?%?股權|控股權|控制權)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            name = _normalize_subject_name(match.group("name"))
            if name and _subject_key(name) != _subject_key(subject_name) and name not in result:
                result.append(name)
    return result[:4]


def _stage(text: str, family: str) -> str:
    if _contains_any(text, CORRECTION_MARKERS):
        anchors = "|".join(_material_anchors(text)) or " ".join(text.casefold().split())[:160]
        return "revision-" + hashlib.sha256(anchors.encode("utf-8")).hexdigest()[:10]
    if _contains_any(text, TERMINATED_MARKERS):
        return "terminated"
    if _contains_any(text, COMPLETED_MARKERS):
        return "completed"
    if _contains_any(text, APPROVED_MARKERS):
        return "approved"
    if family == "earnings":
        if _contains_any(text, EARNINGS_FORECAST_MARKERS):
            return "forecast"
        return "results"
    if family == "joint_venture" and _contains_any(text, ("设立", "設立", "共同出资", "共同出資")):
        return "proposed"
    if family == "price_change":
        return "down" if _contains_any(text, ("降价", "降價", "下调", "下調", "price cut")) else "up"
    if family == "shareholding_change":
        return "decrease" if _contains_any(text, ("减持", "減持", "stake sale")) else "increase"
    if _contains_any(text, PROPOSED_MARKERS):
        return "proposed"
    return "announced"


def _identity(
    *,
    subject: dict[str, str],
    family: str,
    reference: str,
    stage: str,
    scope_parts: list[str],
    claim: str,
    lookback_days: int = COMPANY_EVENT_LOOKBACK_DAYS,
    alias_keys: list[str] | None = None,
) -> dict[str, Any]:
    scope_value = "|".join(part for part in scope_parts if part)
    scope = hashlib.sha256(scope_value.encode("utf-8")).hexdigest()[:12] if scope_value else "general"
    event_key = f"{subject['key']}:{family}:{reference}:{scope}"
    result = {
        "rule_id": COMPANY_EVENT_RULE_ID,
        "dedup_key": f"company_event:{event_key}:v:{stage}",
        "dedup_lookback_days": lookback_days,
        "dedup_kind": "company_event_fact_set",
        "event_facts": {
            "subject": subject["name"],
            "subject_key": subject["key"],
            "subject_code": subject.get("code") or "",
            "event_key": event_key,
            "event_family": family,
            "reference": reference,
            "stage": stage,
            "scope": scope_parts,
            "material_anchors": _material_anchors(claim),
            "evidence_quote": claim[:700],
        },
    }
    aliases = [value for value in alias_keys or [] if value and value != result["dedup_key"]]
    if aliases:
        result["dedup_alias_keys"] = list(dict.fromkeys(aliases))[:16]
    return result


def _legacy_earnings_alias(subject: dict[str, str], period: str) -> list[str]:
    legacy_id = LEGACY_SUBJECT_IDS.get(subject["key"])
    return [f"company_event:{legacy_id}:earnings_forecast:{period}:net_profit"] if legacy_id else []


def _earnings_event(
    item: dict[str, Any], claim: str, subject: dict[str, str]
) -> dict[str, Any] | None:
    if not _contains_any(claim, EARNINGS_METRIC_MARKERS):
        return None
    if not (_contains_any(claim, EARNINGS_FORECAST_MARKERS) or _contains_any(claim, EARNINGS_RESULT_MARKERS)):
        return None
    period = _reporting_period(claim, item.get("published_at"))
    if not period:
        return None
    stage = _stage(claim, "earnings")
    aliases = _legacy_earnings_alias(subject, period) if stage == "forecast" else []
    return _identity(
        subject=subject,
        family="earnings",
        reference=period,
        stage=stage,
        scope_parts=[],
        claim=claim,
        alias_keys=aliases,
    )


def _event_family(claim: str) -> str:
    for family, markers in FAMILY_PATTERNS:
        if _contains_any(claim, markers):
            return family
    return ""


def _generic_event(
    item: dict[str, Any], claim: str, subject: dict[str, str]
) -> dict[str, Any] | None:
    family = _event_family(claim)
    if not family:
        return None
    counterparties = _counterparties(claim, subject["name"])
    objects = _object_markers(claim)
    anchors = _material_anchors(claim)
    period = _reporting_period(claim, item.get("published_at"))
    reference = period or _event_date(claim, item.get("published_at"))
    if family == "price_change":
        published = _published_date(item.get("published_at"))
        month = re.search(r"(?:(20\d{2})年)?(1[0-2]|[1-9])月(?:起|开始|開始)", claim)
        if month:
            year = int(month.group(1)) if month.group(1) else published.year if published else None
            if year:
                reference = f"{year:04d}-{int(month.group(2)):02d}"
        elif _contains_any(claim, ("本月起", "本月开始", "本月開始")) and published:
            reference = f"{published.year:04d}-{published.month:02d}"
    if not reference:
        return None
    broad_scope = family in {"joint_venture", "private_placement", "buyback"}
    scope_parts = [*(_subject_key(value) for value in counterparties), *objects]
    if broad_scope:
        scope_parts = []
    if not scope_parts and not broad_scope:
        scope_parts.extend(anchors[:2])
    if not scope_parts and not broad_scope:
        return None
    stage = _stage(claim, family)
    aliases: list[str] = []
    legacy_id = LEGACY_SUBJECT_IDS.get(subject["key"])
    if legacy_id == "shijia_photons" and family == "private_placement" and stage == "proposed":
        aliases.append(f"company_event:{legacy_id}:private_placement:proposed:{reference}")
    if legacy_id == "powerchip" and family == "price_change":
        month = reference[:7]
        aliases.append(f"company_event:{legacy_id}:price_change:storage_foundry:up:{month}")
    lookback = 30 if family in {"price_change", "product_release", "production_milestone"} else 90
    return _identity(
        subject=subject,
        family=family,
        reference=reference,
        stage=stage,
        scope_parts=scope_parts,
        claim=claim,
        lookback_days=lookback,
        alias_keys=aliases,
    )


EXTRACTORS: tuple[Callable[[dict[str, Any], str, dict[str, str]], dict[str, Any] | None], ...] = (
    _earnings_event,
    _generic_event,
)


def company_event_dedup_hits(item: dict[str, Any], decision: DecisionResult) -> list[dict[str, Any]]:
    """Return every defensible company-event identity after a push decision."""
    if not decision.should_push:
        return []
    if not any(str(hit.get("rule_id") or "") in ELIGIBLE_RULE_IDS for hit in decision.rule_hits):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for claim in _claims(_text(item)):
        subjects = _subjects_for_claim(claim, decision)
        if not subjects:
            continue
        for subject in subjects:
            for extractor in EXTRACTORS:
                hit = extractor(item, claim, subject)
                if not hit:
                    continue
                key = str(hit["dedup_key"])
                if key not in seen:
                    seen.add(key)
                    result.append(hit)
                break
    return result


def company_event_dedup_hit(item: dict[str, Any], decision: DecisionResult) -> dict[str, Any] | None:
    """Compatibility adapter returning the first extracted company event."""
    hits = company_event_dedup_hits(item, decision)
    return hits[0] if hits else None
