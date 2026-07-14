"""Official trade-policy source definitions and access notes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TradePolicySource:
    name: str
    module: str
    url: str
    parser: str
    access_note: str


TRADE_POLICY_SOURCES: tuple[TradePolicySource, ...] = (
    TradePolicySource(
        name="federal_register_china_trade",
        module="U.S. Federal Register / China trade policy",
        url=(
            "https://www.federalregister.gov/api/v1/documents.json?"
            "per_page=100&order=newest&conditions%5Bterm%5D=China"
        ),
        parser="federal_register_json",
        access_note="FederalRegister.gov 官方公开 JSON API；读取标题、摘要、机构、文号和公开链接。",
    ),
    TradePolicySource(
        name="ustr_press_releases",
        module="USTR / Press Releases",
        url="https://ustr.gov/about-us/policy-offices/press-office/press-releases",
        parser="ustr_html",
        access_note="USTR 官方公开新闻稿列表和详情页；robots.txt 未禁止该路径，不绕过登录或访问控制。",
    ),
    TradePolicySource(
        name="eu_press_corner_trade_policy",
        module="European Commission / Press Corner",
        url="https://ec.europa.eu/commission/presscorner/api/rss?language=en",
        parser="eu_rss",
        access_note="European Commission Press Corner 官方公开 RSS 和详情页；RSS 当前返回最新 10 条。",
    ),
    TradePolicySource(
        name="mofcom_policy_releases",
        module="商务部 / 政策发布",
        url="https://www.mofcom.gov.cn/zcfb/index.html",
        parser="mofcom_policy_html",
        access_note="中华人民共和国商务部官方公开政策列表和详情页；不绕过登录、WAF 或访问控制。",
    ),
    TradePolicySource(
        name="mofcom_spokesperson_statements",
        module="商务部 / 新闻发言人谈话",
        url="https://www.mofcom.gov.cn/xwfb/index.html",
        parser="mofcom_spokesperson_html",
        access_note="中华人民共和国商务部官方新闻发布页中的新闻发言人谈话和详情页。",
    ),
)


TRADE_POLICY_SOURCE_MAP = {source.name: source for source in TRADE_POLICY_SOURCES}
