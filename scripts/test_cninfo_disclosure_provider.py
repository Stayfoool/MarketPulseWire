#!/usr/bin/env python3
"""Regression checks for the CNINFO disclosure provider adapter."""

from __future__ import annotations

from cninfo_disclosure_provider import ANNOUNCEMENT_URL, TOP_SEARCH_URL, CninfoPublicProvider, CninfoResponseError
from disclosure_providers import DisclosureRecord, DisclosureSecurity, disclosure_identity


def test_resolve_securities_covers_szse_sse_and_bse() -> None:
    mapping = {
        "301308": ("9900048787", "江波龙"),
        "688017": ("9900041602", "绿的谐波"),
        "920438": ("gfbj0835438", "戈碧迦"),
    }

    def request(url: str, fields: dict[str, str]):
        assert url == TOP_SEARCH_URL
        org_id, name = mapping[fields["keyWord"]]
        return [{"code": fields["keyWord"], "orgId": org_id, "zwjc": name, "category": "A股"}]

    resolved = CninfoPublicProvider(request_json=request).resolve_securities(
        ["301308.SZ", "688017.SH", "920438.BJ"]
    )
    assert {item.provider_security_id for item in resolved.values()} == {
        "9900048787",
        "9900041602",
        "gfbj0835438",
    }


def test_fulltext_and_relation_use_distinct_query_shapes() -> None:
    calls: list[dict[str, str]] = []

    def request(url: str, fields: dict[str, str]):
        assert url == ANNOUNCEMENT_URL
        calls.append(fields)
        return {
            "announcements": [
                {
                    "announcementId": "1225409631",
                    "announcementTitle": "2026年半年度业绩预告",
                    "announcementTime": 1783075627000,
                    "adjunctUrl": "finalpage/2026-07-03/1225409631.PDF",
                    "adjunctType": "PDF",
                    "secCode": "301308",
                    "secName": "江波龙",
                    "announcementType": "01010503||012111",
                }
            ],
            "totalAnnouncement": 1,
            "hasMore": False,
        }

    provider = CninfoPublicProvider(request_json=request)
    securities = [DisclosureSecurity("301308.SZ", "301308", "9900048787", "江波龙")]
    fulltext = provider.list_disclosures(securities, "2026-07-01", "2026-07-16", "fulltext", 1)
    relation = provider.list_disclosures(securities, "2026-07-01", "2026-07-16", "relation", 1)
    assert calls[0]["tabName"] == "fulltext" and calls[0]["category"] == ""
    assert calls[1]["tabName"] == "relation" and calls[1]["category"] == "category_dyhd_szdy"
    assert fulltext.records[0].document_url == "https://static.cninfo.com.cn/finalpage/2026-07-03/1225409631.PDF"
    assert relation.records[0].content_kind == "relation"
    assert fulltext.records[0].published_at.endswith("+08:00")


def test_empty_success_is_distinct_from_malformed_response() -> None:
    security = DisclosureSecurity("301308.SZ", "301308", "9900048787", "江波龙")
    empty = CninfoPublicProvider(
        request_json=lambda _url, _fields: {"announcements": None, "totalAnnouncement": 0, "hasMore": False}
    ).list_disclosures([security], "2026-07-01", "2026-07-16", "fulltext", 1)
    assert empty.records == () and empty.total == 0
    malformed = CninfoPublicProvider(request_json=lambda _url, _fields: {"announcements": []})
    try:
        malformed.list_disclosures([security], "2026-07-01", "2026-07-16", "fulltext", 1)
    except CninfoResponseError:
        pass
    else:
        raise AssertionError("malformed response must fail closed")


def test_identity_is_stable_across_transport_metadata() -> None:
    base = dict(
        provider_record_id="provider-row-1",
        official_record_id="1225409631",
        symbol="301308.SZ",
        company_name="江波龙",
        title="2026年半年度业绩预告",
        published_at="2026-07-03T17:27:07+08:00",
        document_url="https://static.cninfo.com.cn/finalpage/2026-07-03/1225409631.PDF",
        document_type="PDF",
        content_kind="fulltext",
    )
    cninfo = DisclosureRecord(provider="cninfo_public", **base)
    tushare = DisclosureRecord(provider="tushare", **{**base, "provider_record_id": "ts-row-9"})
    assert disclosure_identity(cninfo) == disclosure_identity(tushare) == "announcement:1225409631"


def main() -> int:
    test_resolve_securities_covers_szse_sse_and_bse()
    test_fulltext_and_relation_use_distinct_query_shapes()
    test_empty_success_is_distinct_from_malformed_response()
    test_identity_is_stable_across_transport_metadata()
    print("CNINFO disclosure provider checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
