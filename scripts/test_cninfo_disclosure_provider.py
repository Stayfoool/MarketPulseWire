#!/usr/bin/env python3
"""Regression checks for the CNINFO disclosure provider adapter."""

from __future__ import annotations

import json
import urllib.parse

import httpx

import cninfo_disclosure_provider
from cninfo_disclosure_provider import (
    ANNOUNCEMENT_URL,
    SEARCH_REFERER,
    TOP_SEARCH_URL,
    CninfoError,
    CninfoPublicProvider,
    CninfoResponseError,
)
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


def test_default_requester_uses_shared_json_post_with_provider_contract() -> None:
    original = cninfo_disclosure_provider.http_post_json
    calls: list[dict] = []

    def fake_http_post_json(url: str, **kwargs):
        calls.append({"url": url, **kwargs})
        return [{"code": "301308", "orgId": "9900048787", "zwjc": "江波龙"}]

    try:
        cninfo_disclosure_provider.http_post_json = fake_http_post_json
        resolved = CninfoPublicProvider(timeout=7, attempts=3).resolve_securities(["301308.SZ"])
    finally:
        cninfo_disclosure_provider.http_post_json = original

    assert resolved["301308.SZ"].provider_security_id == "9900048787"
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == TOP_SEARCH_URL
    assert urllib.parse.parse_qs(call["content"].decode("utf-8")) == {"keyWord": ["301308"], "maxNum": ["10"]}
    assert call["headers"]["Origin"] == "https://www.cninfo.com.cn"
    assert call["headers"]["Referer"] == SEARCH_REFERER
    assert call["headers"]["Content-Type"].startswith("application/x-www-form-urlencoded")
    assert call["timeout"] == 7
    assert call["retries"] == 2


def test_default_requester_preserves_cninfo_error_contract() -> None:
    original = cninfo_disclosure_provider.http_post_json
    request = httpx.Request("POST", TOP_SEARCH_URL)
    response = httpx.Response(503, request=request)

    def fail_status(*_args, **_kwargs):
        raise httpx.HTTPStatusError("unavailable", request=request, response=response)

    try:
        cninfo_disclosure_provider.http_post_json = fail_status
        try:
            CninfoPublicProvider().resolve_securities(["301308.SZ"])
        except CninfoError as exc:
            assert str(exc) == "CNINFO HTTP 503"
        else:
            raise AssertionError("HTTP failures must keep the provider error contract")
    finally:
        cninfo_disclosure_provider.http_post_json = original


def test_default_requester_wraps_decode_exhaustion() -> None:
    original = cninfo_disclosure_provider.http_post_json

    def fail_decode(*_args, **_kwargs):
        raise json.JSONDecodeError("malformed JSON", "{", 1)

    try:
        cninfo_disclosure_provider.http_post_json = fail_decode
        try:
            CninfoPublicProvider().resolve_securities(["301308.SZ"])
        except CninfoError as exc:
            assert str(exc).startswith("CNINFO request failed: malformed JSON")
        else:
            raise AssertionError("decode exhaustion must keep the provider error contract")
    finally:
        cninfo_disclosure_provider.http_post_json = original


def main() -> int:
    test_resolve_securities_covers_szse_sse_and_bse()
    test_fulltext_and_relation_use_distinct_query_shapes()
    test_empty_success_is_distinct_from_malformed_response()
    test_identity_is_stable_across_transport_metadata()
    test_default_requester_uses_shared_json_post_with_provider_contract()
    test_default_requester_preserves_cninfo_error_contract()
    test_default_requester_wraps_decode_exhaustion()
    print("CNINFO disclosure provider checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
