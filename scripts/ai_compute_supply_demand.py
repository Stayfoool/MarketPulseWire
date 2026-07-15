"""Deterministic, source-neutral AI compute supply and demand classification."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from rule_center import rule_enabled


RULE_ID = "ai_compute_supply_demand"
DEDUP_LOOKBACK_MINUTES = 14 * 24 * 60

SUBJECT_ALIASES: dict[str, tuple[str, ...]] = {
    "meta": ("meta", "facebook", "脸书"),
    "google": ("google", "alphabet", "谷歌"),
    "microsoft": ("microsoft", "azure", "微软"),
    "amazon": ("amazon", "aws", "亚马逊"),
    "oracle": ("oracle", "oci", "甲骨文"),
    "nvidia": ("nvidia", "英伟达"),
    "coreweave": ("coreweave", "crwv"),
    "nebius": ("nebius",),
    "openai": ("openai",),
    "anthropic": ("anthropic",),
    "xai": ("xai",),
    "spacex": ("spacex",),
    "alibaba": ("alibaba", "aliyun", "阿里云", "阿里巴巴"),
    "tencent": ("tencent", "腾讯云", "腾讯"),
    "bytedance": ("bytedance", "字节跳动", "火山引擎"),
    "huawei": ("huawei", "华为云", "华为"),
    "baidu": ("baidu", "百度智能云", "百度"),
    "lenovo": ("lenovo", "联想"),
    "sunrun": ("sunrun",),
    "xingyun_technology": ("行云科技",),
    "new_york_state": ("美国纽约州", "美纽约州", "纽约州"),
    "national_supercomputing_network": ("国家超算互联网核心节点", "国家超算互联网"),
}

COMPUTE_OPERATORS = {
    "meta", "google", "microsoft", "amazon", "oracle", "nvidia", "coreweave", "nebius",
    "openai", "anthropic", "xai", "alibaba", "tencent", "bytedance", "huawei", "baidu",
}

RESOURCE_MARKERS: dict[str, tuple[str, ...]] = {
    "ai_compute": (
        "ai compute", "compute capacity", "computing capacity", "gpu capacity", "gpu compute",
        "算力", "计算能力", "智算能力", "智能算力",
    ),
    "gpu_cluster": (
        "gpu cluster", "gpu clusters", "accelerator cluster", "accelerator capacity", "gpu集群",
        "gpu 集群", "加速卡集群", "万卡集群", "万卡智算集群",
    ),
    "ai_cloud_capacity": (
        "ai cloud", "gpu cloud", "cloud capacity", "cloud compute", "算力云", "ai云", "ai 云",
        "智算云", "云算力", "算力租赁", "算力服务",
    ),
    "data_center_capacity": (
        "data center", "datacenter", "data center capacity", "datacenter capacity", "data center compute",
        "data center demand", "数据中心", "数据中心容量", "数据中心算力", "智算中心", "算力中心",
    ),
}

EXCESS_MARKERS = (
    "excess compute", "excess capacity", "surplus compute", "surplus capacity", "idle compute",
    "idle capacity", "overcapacity", "over capacity", "过剩算力", "算力过剩", "过剩的ai算力",
    "富余算力", "多余算力", "闲置算力", "闲置ai算力", "闲置容量", "富余容量",
)
SHORTAGE_MARKERS = (
    "compute shortage", "capacity shortage", "capacity constrained", "capacity constraint",
    "insufficient capacity", "not enough capacity", "demand exceeded", "demand exceeds",
    "fully utilized", "capacity exhausted", "sold out", "supply shortage", "supply tight",
    "算力紧缺", "算力短缺", "算力告急", "容量不足", "承载能力不足", "超出承载能力",
    "供给受限", "供应受限", "供给约束", "供应约束", "供不应求", "满负荷", "满载",
)
SHORTAGE_PATTERNS = (
    r"(?:算力)?需求.{0,32}(?:超出|超过).{0,24}(?:承载能力|可用容量|现有容量)",
    r"(?:demand|compute demand).{0,32}(?:exceed\w*|outstrip\w*).{0,24}(?:capacity|available compute)",
    r"(?:capacity|算力|承载能力).{0,24}(?:不足|耗尽|告急|exhausted|insufficient|not enough)",
)
EXTERNAL_CAPACITY_MARKERS = (
    "sell excess", "sell surplus", "sell its compute", "sell ai compute", "sell computing power",
    "lease excess", "rent excess", "offer compute externally", "offer capacity externally",
    "cloud business to sell", "cloud service to sell", "出售过剩", "出售富余", "出售闲置",
    "出售其过剩", "出售ai算力", "出售 AI 算力", "出租富余", "出租闲置", "对外出售算力",
    "对外出租算力", "对外提供算力", "开放算力", "租售算力", "卖算力",
)
OPERATIONAL_CONSEQUENCE_MARKERS = (
    "restrict", "limit access", "ration", "allocated", "allocation", "waitlist", "waiting list",
    "delayed", "postponed", "project delay", "unable to serve", "turned away", "暂停接单",
    "限制", "限流", "配额", "分配", "优先分配", "排队", "等候名单", "推迟", "延期",
    "项目延后", "项目被迫推迟", "无法承接", "停止接单",
)
BINDING_DEMAND_MARKERS = (
    "signed contract", "binding contract", "purchase agreement", "service contract", "lease contract",
    "backlog", "order backlog", "fully booked", "sold out", "waitlist", "waiting list",
    "合同", "协议", "订单", "在手订单", "大单", "售罄", "订满", "排队", "等候名单",
)
BINDING_COMPUTE_PATTERNS = (
    r"(?:ai算力|ai 算力|gpu算力|gpu 算力|云算力|算力服务|计算平台服务).{0,28}(?:合同|协议|订单|大单|积压|售罄|订满|排队)",
    r"(?:合同|协议|订单|大单|积压|售罄|订满|排队).{0,28}(?:ai算力|ai 算力|gpu算力|gpu 算力|云算力|算力服务|计算平台服务)",
    r"(?:ai compute|gpu compute|gpu capacity|cloud capacity|compute service).{0,36}(?:contract|agreement|order|backlog|sold out|waitlist)",
    r"(?:contract|agreement|order|backlog|sold out|waitlist).{0,36}(?:ai compute|gpu compute|gpu capacity|cloud capacity|compute service)",
)
CANCELLATION_MARKERS = (
    "cancelled", "canceled", "terminated", "downsized", "scaled back", "did not renew",
    "lease cancellation", "取消", "终止", "解约", "缩减", "削减", "不再续租", "退租",
)
PRICE_MARKERS = (
    "price", "pricing", "lease rate", "rental rate", "rent", "价格", "定价", "租金", "租赁费",
    "服务费",
)
PRICE_CHANGE_MARKERS = (
    "increased", "raised", "higher", "surged", "cut", "lowered", "declined", "repriced",
    "上调", "上涨", "提高", "涨价", "翻倍", "下调", "下降", "降价", "调降", "重新定价",
)
PRICE_COMPUTE_PATTERNS = (
    r"(?:算力服务|云算力|gpu算力|gpu 算力|api调用|api 调用).{0,24}(?:价格|定价|租金|租赁费|服务费).{0,24}(?:上调|上涨|提高|涨价|翻倍|下调|下降|降价|调降)",
    r"(?:价格|定价|租金|租赁费|服务费).{0,24}(?:上调|上涨|提高|涨价|翻倍|下调|下降|降价|调降).{0,24}(?:算力服务|云算力|gpu算力|gpu 算力|api)",
    r"(?:ai compute|gpu compute|cloud capacity|compute service|api).{0,28}(?:price|pricing|lease rate|rental rate).{0,24}(?:increase|raise|higher|surge|cut|lower|decline|reprice)",
)
UTILIZATION_MARKERS = (
    "utilization", "utilisation", "occupancy", "usage rate", "capacity use", "利用率", "使用率",
    "上架率", "机柜利用", "出租率",
)
UTILIZATION_CHANGE_MARKERS = (
    "rose", "increased", "improved", "fell", "declined", "dropped", "reached", "exceeded",
    "上升", "提升", "提高", "下降", "下滑", "达到", "超过", "转为空闲", "闲置",
)
CAPACITY_CHANGE_MARKERS = (
    "add capacity", "added capacity", "expand capacity", "capacity expansion", "double capacity",
    "bring online", "came online", "opened", "launch", "under construction", "build capacity",
    "cut capacity", "remove capacity", "shut down", "新增算力", "增加算力", "扩充算力",
    "算力翻倍", "计算能力翻倍", "削减算力",
)
CAPACITY_CHANGE_PATTERNS = (
    r"(?:ai算力|ai 算力|算力|计算能力|gpu集群|gpu 集群|数据中心|智算中心|算力中心).{0,24}(?:扩建|扩容|新增|增加|扩充|翻倍|投入运营|正式上线|上线运行|关闭|停运|下线)",
    r"(?:扩建|新增|增加|扩充|开工建设|开始建设|正在建设|关闭|停运|下线).{0,24}(?:数据中心|智算中心|算力中心|gpu集群|gpu 集群|算力容量)",
    r"(?:ai compute|gpu capacity|gpu cluster|data ?center capacity).{0,32}(?:expand|increase|double|come online|launch|shut down|close|remove)",
    r"(?:build|open|launch|shut down|close|remove).{0,24}(?:ai compute|gpu capacity|gpu cluster|data ?center)",
)
PLANNING_MARKERS = (
    "plans to", "planning to", "considering", "evaluating", "proposed", "may build", "计划",
    "拟", "考虑", "筹划", "评估", "有望", "预计",
)
NON_BINDING_MARKERS = (
    "意向", "框架协议", "谅解备忘录", "可能", "拟议", "non-binding", "letter of intent",
    "memorandum of understanding", "framework agreement", "may", "could",
)
PENDING_PROCEDURE_MARKERS = (
    "请求法院", "诉讼请求", "申请法院", "拟申请", "寻求禁令", "lawsuit seeks", "asked a court",
    "petitioned", "proposed order",
)
EXECUTION_MARKERS = (
    "is building", "are building", "began", "started", "launched", "now offering", "currently offers",
    "正在构建", "正在建设", "已开始", "开始提供", "正式推出", "正式上线", "投入运营", "已经开放",
)
POWER_SITE_MARKERS = (
    "grid connection", "power connection", "power availability", "electricity supply", "site permit",
    "data center moratorium", "datacenter moratorium", "电网接入", "电力供应", "供电能力",
    "用电指标", "能耗指标", "数据中心禁令", "暂停新建", "选址受限",
)
POWER_CONSTRAINT_MARKERS = (
    "denied", "rejected", "unavailable", "constrained", "shortage", "moratorium", "suspended",
    "拒绝", "驳回", "不足", "受限", "短缺", "暂停", "禁令", "无法接入", "排队",
)
CORRECTION_MARKERS = (
    "confirmed", "denied", "clarified", "corrected", "confirmation", "denial", "确认", "证实",
    "否认", "澄清", "更正", "纠正", "回应",
)
DIRECT_DISCLOSURE_MARKERS = (
    "公告", "官方", "披露", "表示", "宣布", "确认", "company said", "announced", "disclosed",
    "reported that it", "confirmed",
)
DEMAND_DIRECTION_MARKERS = (
    "demand surge", "demand growth", "demand increased", "demand declined", "demand fell",
    "strong demand", "weak demand", "需求激增", "需求爆发", "需求增长", "需求旺盛", "需求饱满",
    "需求下滑", "需求下降", "需求疲软", "需求收缩", "需求见顶", "需求未达峰值",
)
OPINION_MARKERS = (
    "analyst", "research report", "believes", "expects", "long-term", "in the long run",
    "分析师", "研报", "机构认为", "认为", "预计", "有望", "长期看", "继续看好", "观点", "高管",
)
QUESTION_OR_PROMO_MARKERS = (
    "投资者提问", "有投资者问", "这家公司", "另一家", "分析师持续看好", "建议关注",
)

QUANTIFIED_PATTERNS = (
    r"(?:[$¥￥]\s*)?\d[\d,.]*(?:\.\d+)?\s*(?:%|％|billion|million|bn|万亿元|亿元|万亿|万元|亿)",
    r"\d[\d,.]*(?:\.\d+)?\s*(?:gw|mw|kw|p(?:flops)?|eflops|gpus?|块gpu|张卡|台服务器|座|个集群)",
    r"(?:翻倍|double(?:d)?|triple(?:d)?)",
)


def _visible_text(value: object) -> str:
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", str(value or ""))
    text = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|tr|h[1-6])>", "\n", text)
    return re.sub(r"(?s)<[^>]+>", " ", text)


def item_text(item: dict[str, Any]) -> str:
    parts = [_visible_text(item.get(key)).strip() for key in ("title", "summary", "content", "full_text")]
    return "\n".join(dict.fromkeys(part for part in parts if part))


def _sentences(text: str) -> list[str]:
    return [
        part.strip(" -\t")
        for part in re.split(r"(?<=[。！？!?；;])|(?<=\.)\s+|\n+", text)
        if part.strip(" -\t")
    ]


def _contains(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(marker.casefold() in lowered for marker in markers)


def _shortage_present(text: str) -> bool:
    return _contains(text, SHORTAGE_MARKERS) or any(
        re.search(pattern, text, flags=re.I) for pattern in SHORTAGE_PATTERNS
    )


def _alias_present(text: str, alias: str) -> bool:
    if re.fullmatch(r"[a-z0-9]+", alias):
        return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text, flags=re.I) is not None
    return alias.casefold() in text.casefold()


def subjects_in_text(text: str) -> list[str]:
    subjects = [
        subject
        for subject, aliases in SUBJECT_ALIASES.items()
        if any(_alias_present(text, alias) for alias in aliases)
    ]
    if subjects:
        return subjects
    labels = re.findall(r"(?:^|[①②③④⑤⑥⑦⑧⑨⑩、；;])\s*([^：:】|]{2,28})(?:：|:)\s*", text)
    for label in reversed(labels):
        cleaned = re.sub(r"^[①②③④⑤⑥⑦⑧⑨⑩0-9一二三四五六七八九十）).、\s]+", "", label).strip(" 【】")
        if cleaned and not _contains(cleaned, ("机构", "研报", "盘前", "盘中", "收盘", "要闻", "新闻", "公司新闻")):
            return [re.sub(r"\s+", "_", cleaned).casefold()]
    title_subject = re.search(r"【\s*([^：:】]{2,24})(?:：|:)\s*", text)
    if title_subject and not _contains(title_subject.group(1), ("机构", "研报", "盘前", "盘中", "收盘")):
        return [re.sub(r"\s+", "_", title_subject.group(1).strip()).casefold()]
    direct_subject = re.match(
        r"\s*([\u4e00-\u9fffA-Za-z0-9·.&-]{2,24}?)(?:公告|表示|宣布|确认|否认|澄清|正在|已开始|计划)",
        text,
    )
    if direct_subject:
        value = direct_subject.group(1).strip()
        if not _contains(value, ("公司", "市场", "分析", "机构", "业内", "专家")):
            return [re.sub(r"\s+", "_", value).casefold()]
    jurisdiction = re.search(r"((?:美国)?[\u4e00-\u9fff]{2,12}(?:州|省|市)(?:政府|州长)?)", text)
    if jurisdiction:
        return [re.sub(r"\s+", "_", jurisdiction.group(1).strip()).casefold()]
    if _contains(text, ("高端算力服务市场", "算力租赁市场", "ai compute market", "gpu cloud market")):
        return ["compute_service_market"]
    return []


def explicit_subjects(text: str) -> list[str]:
    patterns = (
        r"(?:^|[①②③④⑤⑥⑦⑧⑨⑩、；;])\s*(?:【\s*)?([^：:】|]{2,28})(?:：|:)\s*",
        r"(?:^|[①②③④⑤⑥⑦⑧⑨⑩、；;])\s*(?:【\s*)?([\u4e00-\u9fffA-Za-z0-9·.&-]{2,24}?)(?:公告|宣布|确认|否认|澄清)",
    )
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for value in reversed(matches):
            cleaned = re.sub(
                r"^[①②③④⑤⑥⑦⑧⑨⑩0-9一二三四五六七八九十）).、\s]+",
                "",
                value,
            ).strip(" 【】")
            if not cleaned or _contains(cleaned, ("公司", "要闻", "新闻", "汇总", "机构", "研报", "盘前", "盘中", "收盘")):
                continue
            for subject, aliases in SUBJECT_ALIASES.items():
                if any(_alias_present(cleaned, alias) for alias in aliases):
                    return [subject]
            return [re.sub(r"\s+", "_", cleaned).casefold()]
    return []


def _first_subject_before(text: str, subjects: list[str], markers: tuple[str, ...]) -> list[str]:
    lowered = text.casefold()
    marker_positions = [lowered.find(marker.casefold()) for marker in markers if marker.casefold() in lowered]
    marker_position = min(marker_positions) if marker_positions else len(text)
    candidates: list[tuple[int, str]] = []
    for subject in subjects:
        aliases = SUBJECT_ALIASES.get(subject, (subject,))
        positions = [lowered.find(alias.casefold()) for alias in aliases if alias.casefold() in lowered]
        if not positions:
            continue
        position = min(positions)
        if position <= marker_position:
            candidates.append((position, subject))
    if candidates:
        return [max(candidates, key=lambda value: value[0])[1]]
    return subjects[:1]


def title_context_subjects(title: object) -> list[str]:
    text = _visible_text(title).strip()
    patterns = (
        r"^(?:【\s*)?([^：:】|]{2,28})(?:：|:)\s*",
        r"^(?:【\s*)?([\u4e00-\u9fffA-Za-z0-9·.&-]{2,24}?)(?:公告|宣布|确认|否认|澄清)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        value = re.sub(r"^[0-9一二三四五六七八九十）).、\s]+", "", match.group(1)).strip()
        if not value or _contains(value, ("要闻", "新闻", "汇总", "机构", "研报", "盘前", "盘中", "收盘")):
            continue
        for subject, aliases in SUBJECT_ALIASES.items():
            if any(_alias_present(value, alias) for alias in aliases):
                return [subject]
        return [re.sub(r"\s+", "_", value).casefold()]
    return []


def resource_scopes(text: str) -> list[str]:
    return [scope for scope, markers in RESOURCE_MARKERS.items() if _contains(text, markers)]


def quantified_terms(text: str) -> list[str]:
    values: list[str] = []
    for pattern in QUANTIFIED_PATTERNS:
        values.extend(match.group(0).strip() for match in re.finditer(pattern, text, flags=re.I))
    return list(dict.fromkeys(value for value in values if value))[:12]


def _evidence_tier(text: str, quantities: list[str]) -> str:
    if _contains(text, ("公告", "官方", "首席执行官", "ceo", "company said", "announced")):
        return "issuer_or_official_statement"
    if quantities:
        return "quantified_local_fact"
    if _contains(text, ("据悉", "报道称", "消息人士", "according to", "reportedly")):
        return "attributed_report"
    return "local_fact"


def _stage(text: str, *, correction: bool = False) -> str:
    if correction:
        if _contains(text, ("否认", "denied", "denial")):
            return "denied"
        if _contains(text, ("更正", "纠正", "corrected")):
            return "corrected"
        return "confirmed"
    if _contains(text, ("正式上线", "投入运营", "正式推出", "now offering", "launched", "came online")):
        return "operational"
    if _contains(text, EXECUTION_MARKERS):
        return "in_execution"
    if _contains(text, PLANNING_MARKERS):
        return "reported_plan"
    return "reported"


def _claim(
    sentence: str,
    subjects: list[str],
    resources: list[str],
    *,
    event_type: str,
    direction: str,
    action: str,
    stage: str | None = None,
) -> dict[str, Any]:
    quantities = quantified_terms(sentence)
    if event_type in {
        "external_capacity_opened",
        "capacity_excess_or_idle",
        "capacity_shortage_or_rationing",
        "issuer_confirmation_or_correction",
    }:
        quantities = [
            value
            for value in quantities
            if re.search(r"(?:gw|mw|kw|p(?:flops)?|eflops|gpus?|块gpu|张卡|台服务器|个集群)$", value, flags=re.I)
        ]
    return {
        "subjects": subjects,
        "resource_scope": resources,
        "event_type": event_type,
        "direction": direction,
        "stage": stage or _stage(sentence),
        "evidence_tier": _evidence_tier(sentence, quantities),
        "quantified_terms": quantities,
        "evidence_quote": sentence[:800],
        "decision_action": action,
    }


def _sentence_claim(sentence: str, context_subjects: list[str] | None = None) -> dict[str, Any] | None:
    resources = resource_scopes(sentence)
    if not resources:
        return None
    subjects = subjects_in_text(sentence) or list(context_subjects or [])
    if _contains(sentence, QUESTION_OR_PROMO_MARKERS):
        return None

    correction_context = _contains(sentence, (*EXCESS_MARKERS, *SHORTAGE_MARKERS, *EXTERNAL_CAPACITY_MARKERS))
    operator_subjects = [subject for subject in subjects if subject in COMPUTE_OPERATORS]
    if operator_subjects and correction_context and _contains(sentence, CORRECTION_MARKERS):
        direction = "excess_denied" if _contains(sentence, ("否认", "denied")) else "supply_state_confirmed"
        return _claim(
            sentence,
            operator_subjects,
            resources,
            event_type="issuer_confirmation_or_correction",
            direction=direction,
            action="push",
            stage=_stage(sentence, correction=True),
        )

    if subjects and _contains(sentence, EXTERNAL_CAPACITY_MARKERS):
        actor_subjects = _first_subject_before(sentence, subjects, EXTERNAL_CAPACITY_MARKERS)
        external_stage = (
            "operational"
            if _contains(sentence, ("正式上线", "投入运营", "正式推出", "now offering", "launched"))
            else "reported_plan"
        )
        return _claim(
            sentence,
            actor_subjects,
            resources,
            event_type="external_capacity_opened",
            direction="market_supply_up",
            action="push",
            stage=external_stage,
        )

    if subjects and _contains(sentence, EXCESS_MARKERS):
        measurable_state = bool(quantified_terms(sentence)) and _contains(sentence, UTILIZATION_MARKERS)
        action = (
            "push"
            if measurable_state
            or (
                any(subject in COMPUTE_OPERATORS for subject in subjects)
                and _contains(sentence, DIRECT_DISCLOSURE_MARKERS)
            )
            else "daily"
        )
        if _contains(sentence, OPINION_MARKERS):
            action = "daily"
        return _claim(
            sentence,
            subjects,
            resources,
            event_type="capacity_excess_or_idle",
            direction="excess",
            action=action,
        )

    shortage = _shortage_present(sentence)
    consequence = _contains(sentence, OPERATIONAL_CONSEQUENCE_MARKERS)
    if subjects and shortage and consequence:
        return _claim(
            sentence,
            subjects,
            resources,
            event_type="capacity_shortage_or_rationing",
            direction="supply_tight",
            action="push",
            stage="observed",
        )

    binding = _contains(sentence, BINDING_DEMAND_MARKERS) and any(
        re.search(pattern, sentence, flags=re.I) for pattern in BINDING_COMPUTE_PATTERNS
    )
    cancellation = _contains(sentence, CANCELLATION_MARKERS)
    if subjects and binding and (cancellation or quantified_terms(sentence) or shortage):
        actor_subjects = explicit_subjects(sentence) or subjects[:1]
        action = "daily" if _contains(sentence, NON_BINDING_MARKERS) else "push"
        return _claim(
            sentence,
            actor_subjects,
            resources,
            event_type="binding_demand_or_cancellation",
            direction="demand_down" if cancellation else "demand_up",
            action=action,
        )

    compute_price_change = any(re.search(pattern, sentence, flags=re.I) for pattern in PRICE_COMPUTE_PATTERNS)
    if subjects and compute_price_change and _contains(sentence, PRICE_MARKERS) and _contains(sentence, PRICE_CHANGE_MARKERS):
        actor_subjects = explicit_subjects(sentence) or subjects[:1]
        action = "push" if quantified_terms(sentence) or binding else "daily"
        if _contains(sentence, OPINION_MARKERS) or (
            _contains(sentence, PLANNING_MARKERS) and not _contains(sentence, EXECUTION_MARKERS)
        ):
            action = "daily"
        return _claim(
            sentence,
            actor_subjects,
            resources,
            event_type="price_or_lease_change",
            direction="repriced",
            action=action,
        )

    if subjects and _contains(sentence, UTILIZATION_MARKERS) and _contains(sentence, UTILIZATION_CHANGE_MARKERS):
        action = "push" if quantified_terms(sentence) or _contains(sentence, EXECUTION_MARKERS) else "daily"
        return _claim(
            sentence,
            subjects,
            resources,
            event_type="utilization_change",
            direction="utilization_changed",
            action=action,
        )

    capacity_change = _contains(sentence, CAPACITY_CHANGE_MARKERS) or any(
        re.search(pattern, sentence, flags=re.I) for pattern in CAPACITY_CHANGE_PATTERNS
    )
    if subjects and capacity_change:
        actor_subjects = subjects[:1]
        material = bool(quantified_terms(sentence)) or _contains(sentence, EXECUTION_MARKERS)
        if _contains(sentence, PENDING_PROCEDURE_MARKERS):
            material = False
        direction = "capacity_down" if _contains(sentence, (*CANCELLATION_MARKERS, "关闭", "停运", "下线")) else "capacity_up"
        return _claim(
            sentence,
            actor_subjects,
            resources,
            event_type="capacity_addition_or_removal",
            direction=direction,
            action="push" if material else "daily",
        )

    if subjects and _contains(sentence, POWER_SITE_MARKERS) and _contains(sentence, POWER_CONSTRAINT_MARKERS):
        return _claim(
            sentence,
            subjects,
            resources,
            event_type="power_or_site_constraint",
            direction="supply_tight",
            action="push",
        )

    if subjects and (shortage or _contains(sentence, DEMAND_DIRECTION_MARKERS)):
        return _claim(
            sentence,
            subjects,
            resources,
            event_type="attributed_supply_demand_view",
            direction="supply_tight" if shortage else "demand_view",
            action="daily",
        )
    return None


def extract_compute_supply_demand(item: dict[str, Any]) -> dict[str, Any]:
    text = item_text(item)
    if not text:
        return {}
    context_subjects = title_context_subjects(item.get("title"))
    claims = [
        claim
        for sentence in _sentences(text)
        if (
            claim := _sentence_claim(
                sentence,
                context_subjects=(
                    context_subjects
                    if re.match(r"\s*(?:公司|该公司|其|公司同日|公司日前)", sentence)
                    else []
                ),
            )
        )
    ]
    if not claims:
        return {}
    push_claims = [claim for claim in claims if claim["decision_action"] == "push"]
    selected = push_claims or claims
    primary = selected[0]
    identity_resource_by_event = {
        "external_capacity_opened": "ai_compute_market",
        "capacity_excess_or_idle": "ai_compute_capacity",
        "capacity_shortage_or_rationing": "ai_compute_capacity",
        "utilization_change": "ai_compute_capacity",
        "binding_demand_or_cancellation": "ai_compute_service",
        "price_or_lease_change": "ai_compute_service",
        "capacity_addition_or_removal": "ai_compute_capacity",
        "power_or_site_constraint": "data_center_site",
        "issuer_confirmation_or_correction": "ai_compute_capacity",
    }
    return {
        "subjects": list(dict.fromkeys(subject for claim in selected for subject in claim["subjects"])),
        "resource_scope": list(dict.fromkeys(scope for claim in selected for scope in claim["resource_scope"])),
        "event_type": primary["event_type"],
        "direction": primary["direction"],
        "stage": primary["stage"],
        "evidence_tier": primary["evidence_tier"],
        "quantified_terms": list(
            dict.fromkeys(term for claim in selected for term in claim["quantified_terms"])
        )[:12],
        "evidence_quotes": list(dict.fromkeys(claim["evidence_quote"] for claim in selected))[:6],
        "claims": selected[:10],
        "identity_subjects": list(primary["subjects"]),
        "identity_resource_scope": [
            identity_resource_by_event.get(primary["event_type"], primary["resource_scope"][0])
        ],
        "identity_quantified_terms": list(primary["quantified_terms"]),
        "decision_action": "push" if push_claims else "daily",
        "extraction_mode": "deterministic_local_sentence",
    }


def _dedup_key(extraction: dict[str, Any]) -> str:
    normalized_quantities: list[str] = []
    for value in extraction.get("identity_quantified_terms") or extraction["quantified_terms"]:
        compact = str(value).casefold().replace(" ", "").replace(",", "")
        chinese_amount = re.fullmatch(r"([0-9.]+)(万亿元|亿元|万元|亿)", compact)
        if chinese_amount:
            number = float(chinese_amount.group(1))
            unit = chinese_amount.group(2)
            multiplier = {"万亿元": 10000.0, "亿元": 1.0, "亿": 1.0, "万元": 0.0001}[unit]
            compact = f"cny_yi:{number * multiplier:.2f}"
        normalized_quantities.append(compact)
    quantities = "|".join(sorted(set(normalized_quantities)))
    quantity_signature = hashlib.sha256(quantities.encode("utf-8")).hexdigest()[:10] if quantities else "unquantified"
    identity = "|".join(
        (
            ",".join(sorted(extraction.get("identity_subjects") or extraction["subjects"])),
            ",".join(sorted(extraction.get("identity_resource_scope") or extraction["resource_scope"])),
            extraction["event_type"],
            extraction["direction"],
            extraction["stage"],
            quantity_signature,
        )
    )
    return f"ai_compute:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"


def ai_compute_supply_demand_rule(source: str, item: dict[str, Any]) -> dict[str, Any] | None:
    if not rule_enabled(RULE_ID):
        return None
    extraction = extract_compute_supply_demand(item)
    if not extraction:
        return None
    action = str(extraction["decision_action"])
    subjects = ", ".join(extraction["subjects"][:4])
    event_type = str(extraction["event_type"])
    direction = str(extraction["direction"])
    reason = f"AI算力供需规则：{subjects} 出现 {event_type}/{direction} 的本地可归因事件。"
    return {
        "matched": True,
        "rule_id": RULE_ID,
        "decision_action": action,
        "importance": "high" if action == "push" else "medium",
        "push_now": action == "push",
        "should_push": action == "push",
        "reason": reason,
        "brief_reason": reason,
        "affected_targets": ["AI算力供需", "数据中心", "GPU/服务器", "半导体产业链"],
        "related_targets": [
            {"name": "AI算力供需", "code": "", "relation": event_type, "direction": direction},
        ],
        "dedup_key": _dedup_key(extraction),
        "dedup_lookback_minutes": DEDUP_LOOKBACK_MINUTES,
        "dedup_kind": "ai_compute_supply_demand",
        **extraction,
        "source": source,
    }
