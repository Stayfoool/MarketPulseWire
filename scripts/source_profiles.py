"""Source profile registry for the Web workbench.

Profiles are defined in code and can be overlaid by a private local config.
Runtime fields are overlaid by the private Web-managed config. The
publisher_role field is orthogonal to category/content type so all secondary
news media can share attribution rules.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from china_media_sources import CHINA_MEDIA_FEEDS, CHINA_MEDIA_LABELS
from db_utils import connect_sqlite
from market_db import DEFAULT_DB_PATH
from media_sources import OVERSEAS_MEDIA_FEEDS, OVERSEAS_MEDIA_LABELS
from trade_policy_sources import TRADE_POLICY_SOURCES
from trendforce_sources import DEFAULT_RSS_FEEDS, TREND_FORCE_PAGE_SOURCES


CATEGORY_ORDER = [
    "x_serenity",
    "research_industry_media",
    "official_company",
    "official_policy",
    "news_media",
    "portfolio_stock_news",
    "company_disclosures",
]

CATEGORY_LABELS = {
    "x_serenity": "0. X / Serenity",
    "research_industry_media": "1. 研究机构/行业媒体",
    "official_company": "2. 公司官网",
    "official_policy": "3. 官方贸易政策",
    "news_media": "4. 新闻媒体",
    "portfolio_stock_news": "5. 新浪个股新闻",
    "company_disclosures": "6. 公司公告",
}

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PROFILE_CONFIG_PATH = ROOT / "config/source_profiles.local.json"
EDITABLE_OVERRIDE_FIELDS = {
    "frequency",
    "publisher_role",
    "skeptic_enabled",
    "web_evidence_enabled",
    "proxy_profile",
    "provider",
    "operation_mode",
    "notes",
}


@dataclass(frozen=True)
class SourceProfile:
    id: str
    category: str
    name: str
    source_type: str
    fetch_range: str
    filter_policy: str
    frequency: str
    runtime_shape: str
    pipeline: str
    service_units: tuple[str, ...]
    health_keys: tuple[tuple[str, str], ...]
    fetcher: str = ""
    publisher_role: str = ""
    skeptic_enabled: bool = False
    web_evidence_enabled: bool = False
    tavily_policy: str = "不触发"
    proxy_profile: str = "默认直连"
    text_length_policy: str = ""
    source_priority: str = ""
    url: str = ""
    provider: str = ""
    operation_mode: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["service_units"] = list(self.service_units)
        data["health_keys"] = [{"monitor": monitor, "source": source} for monitor, source in self.health_keys]
        data["category_label"] = CATEGORY_LABELS.get(self.category, self.category)
        return data


def rss_profile(
    source_id: str,
    name: str,
    category: str,
    url: str,
    *,
    source_priority: str = "",
    pipeline: str = "决策层规则快判 + 薄解读；兼容 article_reviews",
    skeptic_enabled: bool = True,
    filter_policy: str = "媒体关键词粗筛；短硬变量走规则快判；不确定项进入补充判读",
    notes: str = "",
) -> SourceProfile:
    return SourceProfile(
        id=source_id,
        category=category,
        name=name,
        source_type="RSS/Atom",
        fetch_range="官方 RSS/Atom 条目，必要时抓取公开正文",
        filter_policy=filter_policy,
        frequency="每 5 分钟 timer",
        runtime_shape="timer one-shot",
        pipeline=pipeline,
        service_units=("surveil-research-collector.timer", "surveil-research-collector.service"),
        health_keys=(("rss_monitor", source_id),),
        fetcher="scripts/research_collector.py -> scripts/rss_monitor.py",
        skeptic_enabled=skeptic_enabled,
        web_evidence_enabled=skeptic_enabled,
        tavily_policy="Skeptic 触发；需 WEB_EVIDENCE_ENABLED=1 和 API key" if skeptic_enabled else "不触发",
        proxy_profile="可通过 SURVEIL_HTTP_PROXY / mihomo",
        text_length_policy="长短文本共用统一决策；长文只影响摘要/截断成本",
        source_priority=source_priority,
        url=url,
        notes=(notes + " " if notes else "") + "生产入口已切到 research collector；健康记录沿用 rss_monitor 标签。",
    )


def build_profiles() -> list[SourceProfile]:
    profiles: list[SourceProfile] = [
        SourceProfile(
            id="x_serenity",
            category="x_serenity",
            name="X / Serenity",
            source_type="X filtered stream",
            fetch_range="X API filtered stream 收到的公开帖；外链和内部引用尽量富化",
            filter_policy="X 规则和账号配置；发送状态 pending/failed 会重试",
            frequency="长连接，异常后重连",
            runtime_shape="常驻 simple",
            pipeline="X 来源采集 + 帖子解读；后续可接统一决策审计 / Skeptic",
            service_units=("surveil-x-stream.service",),
            health_keys=(("x_stream", "stream"), ("x_stream", "link_enrichment")),
            fetcher="scripts/x_stream.py",
            skeptic_enabled=False,
            web_evidence_enabled=False,
            proxy_profile="通常走服务器本机 mihomo 代理",
            notes="订阅内容仍取决于 X API 和账号权限，不绕过访问控制。",
        )
    ]

    research_names = {
        "semianalysis": "SemiAnalysis / RSS",
        "trendforce_semiconductors": "TrendForce / Semiconductors RSS",
        "trendforce_emerging": "TrendForce / Emerging Technology RSS",
        "trendforce_consumer": "TrendForce / Consumer Electronics RSS",
        "trendforce_energy": "TrendForce / Energy RSS",
        "trendforce_display": "TrendForce / Display RSS",
        "trendforce_led": "TrendForce / LED RSS",
        "trendforce_communication": "TrendForce / Communication RSS",
    }
    for source_id, url in DEFAULT_RSS_FEEDS.items():
        if source_id in CORE_COMPANY_FEEDS:
            continue
        if source_id not in research_names:
            continue
        profiles.append(
            rss_profile(
                source_id,
                research_names[source_id],
                "research_industry_media",
                url,
                source_priority="来源优先级：默认即时推送" if source_id == "semianalysis" else "",
                notes="SemiAnalysis 默认视为高价值研究源；TrendForce RSS 属于研究机构/行业媒体线。",
            )
        )

    for page_source in TREND_FORCE_PAGE_SOURCES:
        profiles.append(
            SourceProfile(
                id=page_source.name,
                category="research_industry_media",
                name=page_source.module,
                source_type="公开列表页",
                fetch_range="官方列表页标题、摘要和链接；不绕过付费墙或访问控制",
                filter_policy="页面抽取 + 趋势/半导体硬变量规则；短量化硬变量规则快判",
                frequency="每 5 分钟 timer；页面源内部 900 秒节流",
                runtime_shape="timer one-shot",
                pipeline="决策层规则快判/补充判读 + 薄解读；兼容 article_reviews",
                service_units=("surveil-research-collector.timer", "surveil-research-collector.service"),
                health_keys=(("trendforce_page", page_source.name),),
                fetcher="scripts/research_collector.py -> scripts/trendforce_page_monitor.py",
                skeptic_enabled=True,
                web_evidence_enabled=True,
                tavily_policy="Skeptic 触发；需 WEB_EVIDENCE_ENABLED=1 和 API key",
                proxy_profile="默认直连；必要时可走 SURVEIL_HTTP_PROXY",
                text_length_policy="统一决策；短硬变量直接快判，长文按需截断/摘要",
                url=page_source.url,
                notes=page_source.access_note,
            )
        )

    for source_id, url in OVERSEAS_MEDIA_FEEDS.items():
        profiles.append(
            SourceProfile(
                id=source_id,
                category="research_industry_media",
                name=OVERSEAS_MEDIA_LABELS.get(source_id, source_id),
                source_type="RSS/RDF",
                fetch_range="官方 RSS/RDF 标题、摘要；可访问时抓取公开正文",
                filter_policy="媒体关键词粗筛；短量化硬变量规则快判；不绕过会员权限",
                frequency="每 5 分钟 timer",
                runtime_shape="timer one-shot",
                pipeline="采集层复用 rss_monitor；决策层统一审计；兼容 article_reviews",
                service_units=("surveil-research-collector.timer", "surveil-research-collector.service"),
                health_keys=(("rss_monitor", source_id),),
                fetcher="scripts/research_collector.py -> scripts/overseas_media_monitor.py / rss_monitor.py",
                skeptic_enabled=True,
                web_evidence_enabled=True,
                tavily_policy="Skeptic 触发；需 WEB_EVIDENCE_ENABLED=1 和 API key",
                proxy_profile="通常走服务器本机 mihomo 代理",
                text_length_policy="统一决策；短硬变量直接快判，长文按需截断/摘要",
                url=url,
            )
        )

    profiles.append(
        SourceProfile(
            id="alphabstract_summaries",
            category="research_industry_media",
            name="AlphaAbstract / Summaries",
            source_type="公开 sitemap + summary 页面",
            fetch_range="robots.txt 允许的公开 sitemap 条目和 summary 页面正文、Article JSON-LD、原始来源链接；不访问登录、付费或受控内容",
            filter_policy="公开摘要全文进入统一决策；不因来源本身提升推送资格，依赖跨来源硬变量、持仓/关键词、主题规则和 Skeptic 控制",
            frequency="每 5 分钟 timer；页面源内部 900 秒节流",
            runtime_shape="timer one-shot",
            pipeline="AlphaAbstract sitemap/page -> NormalizedMarketItem -> 统一决策/解读 -> article_reviews -> 统一去重/投递/view",
            service_units=("surveil-research-collector.timer", "surveil-research-collector.service"),
            health_keys=(("alphabstract", "alphabstract_summaries"),),
            fetcher="scripts/research_collector.py -> scripts/alphabstract_monitor.py",
            publisher_role="third_party_research_summary",
            skeptic_enabled=True,
            web_evidence_enabled=True,
            tavily_policy="Skeptic 触发；需 WEB_EVIDENCE_ENABLED=1 和 API key",
            proxy_profile="默认直连；必要时可走 SURVEIL_HTTP_PROXY",
            text_length_policy="读取公开 summary 页正文；长文由统一决策/解读截断和规则处理",
            source_priority="二级研究摘要源，不设来源级推送特权",
            url="https://alphabstract.com/sitemap.xml",
            notes="无 RSS/Atom 端点；以官方 sitemap 为发现入口，保留 isBasedOn 原始访谈/视频链接用于审计。",
        )
    )

    profiles.append(
        SourceProfile(
            id="value_directory_ib_stocks",
            category="research_industry_media",
            name="价值目录 / 国际投行-个股",
            source_type="登录授权列表页",
            fetch_range="用户账号正常可访问的国际投行个股研报索引标题、日期、详情 URL 和详情页可见第一页预览；不下载 PDF，不访问积分/VIP内容",
            filter_policy="列表页元数据 + 可见第一页预览；国际投行个股研报命中直接持仓或持仓“关联新闻关键词”，或明确策略标题的核心产业主题硬规则时即时推送",
            frequency="每天 08:00 timer；首次登录后手动启用",
            runtime_shape="timer one-shot / server browser profile",
            pipeline="价值目录浏览器/首页预览/OCR -> NormalizedMarketItem -> 统一决策/解读 -> article_reviews -> 统一去重/投递/view",
            service_units=("surveil-value-directory.timer", "surveil-value-directory.service"),
            health_keys=(("value_directory", "value_directory_ib_stocks"),),
            fetcher="scripts/value_directory_monitor.py -> scripts/value_directory_browser.py",
            skeptic_enabled=False,
            web_evidence_enabled=False,
            tavily_policy="首期不触发；仅规则命中即时推送",
            proxy_profile="服务器专用持久化 Chromium profile；遇 WAF/验证码停止并告警",
            text_length_policy="标题索引 + 可见第一页预览提取，不抓研报全文",
            source_priority="国际投行单股评级/目标价与重大主题策略硬规则",
            url="https://www.valuelist.cn/ib-research/global-investment-banks-stocks",
            notes="登录态只保存在服务器私有浏览器 profile；不支持导入/导出 cookie。",
        )
    )
    profiles.append(
        SourceProfile(
            id="value_directory_ib_industry_macro",
            category="research_industry_media",
            name="价值目录 / 国际投行-行业宏观",
            source_type="登录授权列表页",
            fetch_range="用户账号正常可访问的国际投行行业/宏观研报索引标题、日期、详情 URL 和详情页可见第一页预览；不下载 PDF，不访问积分/VIP内容",
            filter_policy="半导体、AI 基础设施、存储、设备材料、光通信、PCB、机器人、数据中心电力/液冷、美国核心宏观和持仓“关联新闻关键词”命中时即时推送",
            frequency="每天 08:00 timer；与价值目录个股同一服务运行",
            runtime_shape="timer one-shot / server browser profile",
            pipeline="价值目录浏览器/首页预览/OCR -> NormalizedMarketItem -> 统一决策/解读 -> article_reviews -> 统一去重/投递/view",
            service_units=("surveil-value-directory.timer", "surveil-value-directory.service"),
            health_keys=(("value_directory", "value_directory_ib_industry_macro"),),
            fetcher="scripts/value_directory_monitor.py -> scripts/value_directory_browser.py",
            skeptic_enabled=False,
            web_evidence_enabled=False,
            tavily_policy="首期不触发；仅规则命中即时推送",
            proxy_profile="服务器专用持久化 Chromium profile；遇 WAF/验证码停止并告警",
            text_length_policy="标题索引 + 可见第一页预览提取，不抓研报全文",
            source_priority="国际投行重大主题策略、行业配置和宏观策略硬规则",
            url="https://www.valuelist.cn/ib-research/global-investment-banks",
            notes="登录态只保存在服务器私有浏览器 profile；不支持导入/导出 cookie。",
        )
    )

    official_names = {
        "openai_news": "OpenAI News",
        "nvidia_blog": "NVIDIA Blog",
        "nvidia_developer_blog": "NVIDIA Developer Blog",
        "samsung_semiconductor_news": "Samsung Semiconductor News",
        "samsung_global_semiconductor": "Samsung Global Newsroom / Semiconductor",
        "skhynix_newsroom": "SK hynix Newsroom",
        "micron_news_releases": "Micron News Releases",
    }
    for source_id in CORE_COMPANY_FEEDS:
        url = DEFAULT_RSS_FEEDS[source_id]
        profiles.append(
            SourceProfile(
                id=source_id,
                category="official_company",
                name=official_names.get(source_id, source_id),
                source_type="RSS/Atom",
                fetch_range="官方 RSS/Atom 条目，必要时抓取公开正文",
                filter_policy="公司官网一手消息；普通营销/活动降级，产业链重大变量即时推送",
                frequency="每 10 分钟 timer",
                runtime_shape="timer one-shot",
                pipeline="官网来源 -> 决策层 -> 薄解读；兼容 official_news_reviews",
                service_units=("surveil-official-collector.timer", "surveil-official-collector.service"),
                health_keys=(("rss_monitor", source_id),),
                fetcher="scripts/official_collector.py",
                skeptic_enabled=True,
                web_evidence_enabled=True,
                tavily_policy="Skeptic 触发；需 WEB_EVIDENCE_ENABLED=1 和 API key",
                proxy_profile="可通过 SURVEIL_HTTP_PROXY / mihomo",
                text_length_policy="默认官网新闻流；普通营销/活动降级",
                url=url,
                notes="公司官网源属于一手信息，默认进入 official_news_reviews。",
            )
        )

    source_type_labels = {
        "federal_register_json": "官方 JSON API",
        "eu_rss": "官方 RSS + 详情页",
        "ustr_html": "官方列表页 + 详情页",
        "mofcom_policy_html": "官方列表页 + 详情页",
        "mofcom_spokesperson_html": "官方新闻发布页 + 详情页",
    }
    for policy_source in TRADE_POLICY_SOURCES:
        profiles.append(
            SourceProfile(
                id=policy_source.name,
                category="official_policy",
                name=policy_source.module,
                source_type=source_type_labels.get(policy_source.parser, "官方公开页面/API"),
                fetch_range="官方公开标题、摘要、程序/机构元数据和可访问详情正文；首次发现建立基线",
                filter_policy="所有条目进入统一来源中立贸易摩擦规则；具体程序/工具或明确升级即时推送，弱紧张信号进入 daily",
                frequency="每 2 分钟 news collector timer",
                runtime_shape="timer one-shot",
                pipeline="官方贸易政策采集 -> NormalizedMarketItem -> 统一决策/解读 -> article_reviews -> 统一去重/投递/view",
                service_units=("surveil-news-collector.timer", "surveil-news-collector.service"),
                health_keys=(("trade_policy", policy_source.name),),
                fetcher="scripts/news_collector.py -> scripts/trade_policy_monitor.py",
                publisher_role="government_official",
                skeptic_enabled=True,
                web_evidence_enabled=True,
                tavily_policy="Skeptic 触发；需 WEB_EVIDENCE_ENABLED=1 和 API key",
                proxy_profile="默认直连；必要时可走 SURVEIL_HTTP_PROXY",
                text_length_policy="列表/API 发现后只富化新条目；统一决策按局部证据判断",
                source_priority="官方一手政策与前置程序；来源身份本身不创建推送资格",
                url=policy_source.url,
                notes=policy_source.access_note,
            )
        )

    for source_id, url in CHINA_MEDIA_FEEDS.items():
        if source_id in {"yicai_brief_rsshub", "cls_telegraph_page"}:
            continue
        wallstreetcn = source_id == "wallstreetcn_news"
        profiles.append(
            SourceProfile(
                id=source_id,
                category="news_media",
                name=CHINA_MEDIA_LABELS.get(source_id, source_id),
                source_type="公开资讯/快讯页面 + 官方 sitemap" if wallstreetcn else "公开 API/页面/RSSHub",
                fetch_range=(
                    "公开文章与快讯；分类页和 /live 近实时发现，官方 sitemap 补漏；不访问会员正文"
                    if wallstreetcn
                    else "公开快讯、短新闻、专题列表；不绕过登录或付费墙"
                ),
                filter_policy="新闻媒体来源规则快判；宏观关键事件和产业硬变量优先；长内容只影响解读成本",
                frequency="每 2 分钟 timer",
                runtime_shape="timer one-shot",
                pipeline="新闻媒体来源 -> 统一决策层 -> 薄解读；兼容 article_reviews",
                service_units=("surveil-news-collector.timer", "surveil-news-collector.service"),
                health_keys=(
                    (("wallstreetcn", "articles"), ("wallstreetcn", "livenews"), ("wallstreetcn", "detail"))
                    if wallstreetcn
                    else (("china_finance_media", source_id),)
                ),
                fetcher="scripts/news_collector.py -> scripts/china_finance_media_monitor.py",
                publisher_role="news_media",
                skeptic_enabled=True,
                web_evidence_enabled=True,
                tavily_policy="Skeptic 触发；需 WEB_EVIDENCE_ENABLED=1 和 API key",
                text_length_policy="长短文本共用统一决策；长文按需截断/摘要",
                url=url,
            )
        )

    profiles.extend(
        [
            SourceProfile(
                id="sina_flash",
                category="news_media",
                name="新浪财经 7x24 快讯",
                source_type="公开快讯 API / 可选新浪智研 provider",
                fetch_range="配置 tags/provider 下的全部快讯行",
                filter_policy="命中持仓代码/简称/别名或宏观政策线后入库",
                frequency="脚本内高频轮询，默认 15 秒",
                runtime_shape="常驻 simple",
                pipeline="快讯采集 -> NormalizedMarketItem -> 统一决策/解读 -> events/event_analyses -> delivery/view",
                service_units=("surveil-sina-flash.service",),
                health_keys=(("sina_flash", "*"),),
                fetcher="scripts/sina_flash.py",
                publisher_role="news_media",
                skeptic_enabled=False,
                web_evidence_enabled=False,
                proxy_profile="默认直连",
                text_length_policy="短快讯事件流；重大宏观/硬变量不受字数限制",
            ),
            SourceProfile(
                id="sina_stock_news",
                category="portfolio_stock_news",
                name="新浪个股新闻 / 持仓股",
                source_type="按持仓逐只抓取公开个股新闻页 / 可选新浪智研 provider",
                fetch_range="每只启用持仓最新若干条新闻，默认每股 12 条",
                filter_policy="过滤公告转载、AI 生成页、排除词；模糊项用相关性 LLM 复核",
                frequency="每 30 分钟 timer",
                runtime_shape="timer one-shot",
                pipeline="持仓新闻采集 -> 相关性复核 -> NormalizedMarketItem -> 统一决策/解读 -> delivery/view",
                service_units=("surveil-sina-stock-news.timer", "surveil-sina-stock-news.service"),
                health_keys=(("sina_stock_news", "*"),),
                fetcher="scripts/sina_stock_news.py",
                publisher_role="news_media",
                skeptic_enabled=False,
                web_evidence_enabled=False,
                text_length_policy="按持仓相关性优先；长文/旧闻争议后续可接 Skeptic",
            ),
            SourceProfile(
                id="company_disclosures",
                category="company_disclosures",
                name="巨潮资讯公司公告",
                source_type="巨潮资讯公开查询；可切换 provider adapter",
                fetch_range="启用持仓最近两日普通公告、投资者关系活动记录及官方 PDF",
                filter_policy="持仓范围、provider-neutral 公告身份、技术去重；官方公告原文优先",
                frequency="每天 08:00 / 20:00 timer",
                runtime_shape="timer one-shot",
                pipeline="公告采集 -> NormalizedMarketItem -> 正式披露优先决策/解读 -> delivery/view",
                service_units=("surveil-company-disclosures.timer", "surveil-company-disclosures.service"),
                health_keys=(("company_disclosures", "*"), ("company_disclosure_document", "*")),
                fetcher="scripts/company_disclosures.py -> scripts/cninfo_disclosure_provider.py",
                skeptic_enabled=False,
                web_evidence_enabled=False,
                provider="cninfo_public",
                operation_mode="report_only",
                notes="正式披露以官方公告原文为准；首次部署默认只报告，不进入决策或投递。",
            ),
        ]
    )
    return profiles


def default_profile_map() -> dict[str, dict[str, Any]]:
    return {profile.id: profile.to_dict() for profile in build_profiles()}


def normalize_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw in {"1", "true", "yes", "y", "on", "是", "启用"}:
            return True
        if raw in {"0", "false", "no", "n", "off", "否", "停用"}:
            return False
    if value is None:
        return default
    return bool(value)


def normalize_source_profile_config(raw: Any, valid_ids: set[str] | None = None) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    valid_ids = valid_ids or set(default_profile_map())
    disabled_sources = []
    for source_id in data.get("disabled_sources") or []:
        source_id = str(source_id or "").strip()
        if source_id and source_id in valid_ids and source_id not in disabled_sources:
            disabled_sources.append(source_id)

    overrides: dict[str, dict[str, Any]] = {}
    raw_overrides = data.get("overrides")
    if isinstance(raw_overrides, dict):
        for source_id, item in raw_overrides.items():
            source_id = str(source_id or "").strip()
            if source_id not in valid_ids or not isinstance(item, dict):
                continue
            normalized: dict[str, Any] = {}
            for field in EDITABLE_OVERRIDE_FIELDS:
                if field not in item:
                    continue
                if field in {"skeptic_enabled", "web_evidence_enabled"}:
                    normalized[field] = normalize_bool(item.get(field), False)
                else:
                    normalized[field] = str(item.get(field) or "").strip()
            if normalized:
                overrides[source_id] = normalized
    return {"disabled_sources": disabled_sources, "overrides": overrides}


def load_source_profile_config(path: Path = SOURCE_PROFILE_CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"disabled_sources": [], "overrides": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"disabled_sources": [], "overrides": {}}
    return normalize_source_profile_config(raw)


def config_exists(path: Path = SOURCE_PROFILE_CONFIG_PATH) -> bool:
    return path.exists()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def source_profile_local_rows_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("profiles")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    rows = payload.get("sources")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    return []


def save_source_profile_config(
    payload: dict[str, Any],
    path: Path = SOURCE_PROFILE_CONFIG_PATH,
) -> dict[str, Any]:
    defaults = default_profile_map()
    valid_ids = set(defaults)
    disabled_sources: list[str] = []
    overrides: dict[str, dict[str, Any]] = {}

    for row in source_profile_local_rows_from_payload(payload):
        source_id = str(row.get("id") or "").strip()
        if source_id not in valid_ids:
            continue
        default = defaults[source_id]
        enabled = normalize_bool(row.get("enabled"), True)
        if not enabled:
            disabled_sources.append(source_id)

        item: dict[str, Any] = {}
        for field in ("frequency", "proxy_profile", "provider", "operation_mode", "notes"):
            value = str(row.get(field) or "").strip()
            if value and value != str(default.get(field) or ""):
                item[field] = value
        publisher_role = str(row.get("publisher_role") or "").strip()
        if publisher_role != str(default.get("publisher_role") or ""):
            item["publisher_role"] = publisher_role
        for field in ("skeptic_enabled", "web_evidence_enabled"):
            value = normalize_bool(row.get(field), bool(default.get(field)))
            if value != bool(default.get(field)):
                item[field] = value
        if item:
            overrides[source_id] = item

    config = normalize_source_profile_config(
        {"disabled_sources": disabled_sources, "overrides": overrides},
        valid_ids=valid_ids,
    )
    atomic_write_json(path, config)
    return {
        "path": str(path),
        "disabled_count": len(config["disabled_sources"]),
        "override_count": len(config["overrides"]),
        "config": config,
    }


def apply_local_config(profile: SourceProfile, config: dict[str, Any]) -> dict[str, Any]:
    payload = profile.to_dict()
    overrides = dict(config.get("overrides", {}).get(profile.id, {}))
    disabled = set(config.get("disabled_sources") or [])
    for field in EDITABLE_OVERRIDE_FIELDS:
        payload[f"default_{field}"] = payload.get(field)
    payload["enabled"] = profile.id not in disabled
    for field, value in overrides.items():
        if field in EDITABLE_OVERRIDE_FIELDS:
            payload[field] = value
    payload["override_fields"] = sorted(overrides)
    payload["overrides"] = overrides
    payload["config_modified"] = (not payload["enabled"]) or bool(overrides)
    payload["runtime_effective"] = True
    if payload.get("provider"):
        payload["runtime_note"] = (
            f"来源开关、provider={payload.get('provider')}、mode={payload.get('operation_mode') or 'report_only'} 由运行时读取；"
            "频率和代理暂仅记录。"
        )
    else:
        payload["runtime_note"] = "来源开关由运行时读取；频率和代理暂仅记录。"
    return payload


def runtime_profile_map(config_path: Path = SOURCE_PROFILE_CONFIG_PATH) -> dict[str, dict[str, Any]]:
    config = load_source_profile_config(config_path)
    return {profile.id: apply_local_config(profile, config) for profile in build_profiles()}


def runtime_source_profile(source_id: str, config_path: Path = SOURCE_PROFILE_CONFIG_PATH) -> dict[str, Any] | None:
    source_id = str(source_id or "").strip()
    if not source_id:
        return None
    return runtime_profile_map(config_path).get(source_id)


def source_profile_enabled(
    source_id: str,
    *,
    default: bool = True,
    config_path: Path = SOURCE_PROFILE_CONFIG_PATH,
) -> bool:
    profile = runtime_source_profile(source_id, config_path=config_path)
    if profile is None:
        return default
    return bool(profile.get("enabled", default))


def source_profile_bool(
    source_id: str,
    field: str,
    *,
    default: bool,
    config_path: Path = SOURCE_PROFILE_CONFIG_PATH,
) -> bool:
    profile = runtime_source_profile(source_id, config_path=config_path)
    if profile is None or field not in profile:
        return default
    return normalize_bool(profile.get(field), default)


def source_profile_skeptic_enabled(
    source_id: str,
    *,
    default: bool = True,
    config_path: Path = SOURCE_PROFILE_CONFIG_PATH,
) -> bool:
    return source_profile_bool(source_id, "skeptic_enabled", default=default, config_path=config_path)


def source_profile_web_evidence_enabled(
    source_id: str,
    *,
    default: bool = True,
    config_path: Path = SOURCE_PROFILE_CONFIG_PATH,
) -> bool:
    return source_profile_bool(source_id, "web_evidence_enabled", default=default, config_path=config_path)


def disabled_source_ids(config_path: Path = SOURCE_PROFILE_CONFIG_PATH) -> set[str]:
    config = load_source_profile_config(config_path)
    return set(config.get("disabled_sources") or [])


def filter_enabled_source_mapping(
    sources: dict[str, Any],
    *,
    config_path: Path = SOURCE_PROFILE_CONFIG_PATH,
) -> dict[str, Any]:
    disabled = disabled_source_ids(config_path)
    return {source_id: value for source_id, value in sources.items() if source_id not in disabled}


def filter_enabled_named_sources(
    sources: Iterable[Any],
    *,
    config_path: Path = SOURCE_PROFILE_CONFIG_PATH,
) -> list[Any]:
    disabled = disabled_source_ids(config_path)
    enabled = []
    for source in sources:
        source_id = str(getattr(source, "name", source) or "").strip()
        if source_id and source_id in disabled:
            continue
        enabled.append(source)
    return enabled


CORE_COMPANY_FEEDS = {
    "openai_news",
    "nvidia_blog",
    "nvidia_developer_blog",
    "samsung_semiconductor_news",
    "samsung_global_semiconductor",
    "skhynix_newsroom",
    "micron_news_releases",
}


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return bool(row)


def source_health_lookup(db_path: Path = DEFAULT_DB_PATH) -> dict[tuple[str, str], dict[str, Any]]:
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    with connect_sqlite(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if not table_exists(conn, "source_health"):
            return lookup
        for row in conn.execute(
            """
            SELECT monitor, source, consecutive_failures, last_success_at, last_failure_at,
                   last_error, last_alerted_at, updated_at
            FROM source_health
            """
        ):
            failures = int(row["consecutive_failures"] or 0)
            lookup[(str(row["monitor"] or ""), str(row["source"] or ""))] = {
                "monitor": row["monitor"] or "",
                "source": row["source"] or "",
                "status": "failing" if failures else "ok",
                "consecutive_failures": failures,
                "last_success_at": row["last_success_at"] or "",
                "last_failure_at": row["last_failure_at"] or "",
                "last_error": row["last_error"] or "",
                "last_alerted_at": row["last_alerted_at"] or "",
                "updated_at": row["updated_at"] or "",
            }
    return lookup


def matching_health_rows(
    profile: SourceProfile,
    health: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for monitor, source in profile.health_keys:
        if source == "*":
            rows.extend(row for (row_monitor, _), row in health.items() if row_monitor == monitor)
        elif source.endswith("*"):
            prefix = source[:-1]
            rows.extend(
                row
                for (row_monitor, row_source), row in health.items()
                if row_monitor == monitor and row_source.startswith(prefix)
            )
        elif (monitor, source) in health:
            rows.append(health[(monitor, source)])
    return rows


def attach_health(payload: dict[str, Any], profile: SourceProfile, health: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    rows = matching_health_rows(profile, health)
    payload["health_records"] = rows
    if not rows:
        payload["health_status"] = "unknown"
        payload["last_success_at"] = ""
        payload["last_failure_at"] = ""
        payload["last_error"] = ""
        payload["consecutive_failures"] = 0
        return payload
    failing = [row for row in rows if row.get("status") == "failing"]
    latest = max(rows, key=lambda row: str(row.get("updated_at") or row.get("last_success_at") or row.get("last_failure_at") or ""))
    payload["health_status"] = "failing" if failing else "ok"
    payload["last_success_at"] = latest.get("last_success_at") or ""
    payload["last_failure_at"] = latest.get("last_failure_at") or ""
    payload["last_error"] = latest.get("last_error") or ""
    payload["consecutive_failures"] = max(int(row.get("consecutive_failures") or 0) for row in rows)
    return payload


def source_profiles_payload(
    db_path: Path = DEFAULT_DB_PATH,
    config_path: Path = SOURCE_PROFILE_CONFIG_PATH,
) -> dict[str, Any]:
    health = source_health_lookup(db_path)
    config = load_source_profile_config(config_path)
    profiles = [attach_health(apply_local_config(profile, config), profile, health) for profile in build_profiles()]
    counts = {category: 0 for category in CATEGORY_ORDER}
    disabled = {category: 0 for category in CATEGORY_ORDER}
    failing = {category: 0 for category in CATEGORY_ORDER}
    for profile in profiles:
        category = str(profile.get("category") or "")
        counts[category] = counts.get(category, 0) + 1
        if not profile.get("enabled", True):
            disabled[category] = disabled.get(category, 0) + 1
        if profile.get("health_status") == "failing":
            failing[category] = failing.get(category, 0) + 1
    categories = [
        {
            "id": category,
            "label": CATEGORY_LABELS.get(category, category),
            "count": counts.get(category, 0),
            "disabled": disabled.get(category, 0),
            "failing": failing.get(category, 0),
        }
        for category in CATEGORY_ORDER
    ]
    enabled_profiles = [profile for profile in profiles if profile.get("enabled", True)]
    skeptic_source_count = sum(bool(profile.get("skeptic_enabled")) for profile in enabled_profiles)
    evidence_source_count = sum(
        bool(profile.get("skeptic_enabled")) and bool(profile.get("web_evidence_enabled"))
        for profile in enabled_profiles
    )
    skeptic_global_enabled = normalize_bool(os.getenv("SKEPTIC_EVALUATOR_ENABLED", "1"), True)
    evidence_global_enabled = normalize_bool(os.getenv("WEB_EVIDENCE_ENABLED", "0"), False)
    evidence_api_configured = bool(
        os.getenv("WEB_EVIDENCE_API_KEY", "").strip()
        or os.getenv("TAVILY_API_KEY", "").strip()
        or os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    )
    effective_skeptic_count = skeptic_source_count if skeptic_global_enabled else 0
    effective_evidence_count = evidence_source_count if evidence_global_enabled and evidence_api_configured else 0
    runtime_status = {
        "enabled_sources": len(enabled_profiles),
        "total_sources": len(profiles),
        "skeptic_sources": effective_skeptic_count,
        "skeptic_source_selections": skeptic_source_count,
        "skeptic_global_enabled": skeptic_global_enabled,
        "web_evidence_sources": effective_evidence_count,
        "web_evidence_source_selections": evidence_source_count,
        "web_evidence_global_enabled": evidence_global_enabled,
        "web_evidence_api_configured": evidence_api_configured,
    }
    runtime_note = (
        f"当前启用 {len(enabled_profiles)}/{len(profiles)} 个来源；"
        f"Skeptic 实际启用 {effective_skeptic_count} 个；"
        f"Tavily/Web Evidence 实际可触发 {effective_evidence_count} 个。"
    )
    return {
        "ok": True,
        "categories": categories,
        "profiles": profiles,
        "config_path": str(config_path),
        "config_exists": config_exists(config_path),
        "override_config": config,
        "runtime_effective": True,
        "runtime_status": runtime_status,
        "runtime_note": runtime_note,
    }
