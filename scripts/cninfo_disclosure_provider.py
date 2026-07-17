"""CNINFO public-site adapter for company disclosures."""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx

from disclosure_providers import DisclosurePage, DisclosureRecord, DisclosureSecurity
from http_utils import http_post_json


TOP_SEARCH_URL = "https://www.cninfo.com.cn/new/information/topSearch/query"
ANNOUNCEMENT_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
STATIC_BASE_URL = "https://static.cninfo.com.cn/"
SEARCH_REFERER = "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search"
BJ = ZoneInfo("Asia/Shanghai")
VALID_CONTENT_KINDS = {"fulltext", "relation"}


class CninfoError(RuntimeError):
    pass


class CninfoResponseError(CninfoError):
    pass


JsonRequester = Callable[[str, dict[str, str]], Any]


class CninfoPublicProvider:
    name = "cninfo_public"

    def __init__(
        self,
        *,
        request_json: JsonRequester | None = None,
        timeout: float = 20.0,
        attempts: int = 3,
    ) -> None:
        self.timeout = max(1.0, float(timeout))
        self.attempts = max(1, int(attempts))
        self._request_json = request_json or self._post_json

    def _post_json(self, url: str, fields: dict[str, str]) -> Any:
        body = urllib.parse.urlencode(fields).encode("utf-8")
        try:
            return http_post_json(
                url,
                content=body,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Origin": "https://www.cninfo.com.cn",
                    "Referer": SEARCH_REFERER,
                    "User-Agent": "Mozilla/5.0",
                },
                timeout=self.timeout,
                retries=self.attempts - 1,
            )
        except httpx.HTTPStatusError as exc:
            raise CninfoError(f"CNINFO HTTP {exc.response.status_code}") from exc
        except (httpx.HTTPError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CninfoError(f"CNINFO request failed: {exc}") from exc

    def resolve_securities(self, symbols: list[str]) -> dict[str, DisclosureSecurity]:
        resolved: dict[str, DisclosureSecurity] = {}
        for symbol in symbols:
            normalized_symbol = str(symbol or "").strip().upper()
            code = normalized_symbol.split(".", 1)[0]
            payload = self._request_json(TOP_SEARCH_URL, {"keyWord": code, "maxNum": "10"})
            if not isinstance(payload, list):
                raise CninfoResponseError(f"CNINFO security lookup returned {type(payload).__name__}")
            candidates = [item for item in payload if isinstance(item, dict) and str(item.get("code") or "") == code]
            active = [item for item in candidates if str(item.get("delisted") or "false").lower() != "true"]
            item = (active or candidates or [None])[0]
            if not isinstance(item, dict) or not str(item.get("orgId") or "").strip():
                raise CninfoResponseError(f"CNINFO could not resolve {normalized_symbol}")
            resolved[normalized_symbol] = DisclosureSecurity(
                symbol=normalized_symbol,
                code=code,
                provider_security_id=str(item["orgId"]).strip(),
                company_name=str(item.get("zwjc") or "").strip(),
                raw_metadata={
                    "category": str(item.get("category") or ""),
                    "type": str(item.get("type") or ""),
                },
            )
        return resolved

    def list_disclosures(
        self,
        securities: list[DisclosureSecurity],
        start_date: str,
        end_date: str,
        content_kind: str,
        page: int,
    ) -> DisclosurePage:
        if content_kind not in VALID_CONTENT_KINDS:
            raise ValueError(f"unsupported CNINFO content kind: {content_kind}")
        if not securities:
            return DisclosurePage(records=(), has_more=False, total=0)
        stock = ";".join(f"{item.code},{item.provider_security_id}" for item in securities)
        fields = {
            "pageNum": str(max(1, int(page))),
            "pageSize": "30",
            "column": "szse",
            "tabName": content_kind,
            "plate": "",
            "stock": stock,
            "searchkey": "",
            "secid": "",
            "category": "category_dyhd_szdy" if content_kind == "relation" else "",
            "trade": "",
            "seDate": f"{start_date}~{end_date}",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }
        payload = self._request_json(ANNOUNCEMENT_URL, fields)
        if not isinstance(payload, dict):
            raise CninfoResponseError(f"CNINFO disclosure query returned {type(payload).__name__}")
        announcements = payload.get("announcements")
        total_raw = payload.get("totalAnnouncement")
        if announcements is None and total_raw in (0, "0"):
            announcements = []
        if not isinstance(announcements, list) or total_raw is None:
            raise CninfoResponseError("CNINFO disclosure response is missing announcements or totalAnnouncement")
        try:
            total = max(0, int(total_raw))
        except (TypeError, ValueError) as exc:
            raise CninfoResponseError("CNINFO totalAnnouncement is invalid") from exc
        symbol_by_code = {item.code: item.symbol for item in securities}
        records: list[DisclosureRecord] = []
        for item in announcements:
            if not isinstance(item, dict):
                raise CninfoResponseError("CNINFO announcement row is not an object")
            code = str(item.get("secCode") or "").strip()
            symbol = symbol_by_code.get(code, "")
            if not symbol:
                continue
            announcement_id = str(item.get("announcementId") or "").strip()
            title = str(item.get("announcementTitle") or "").strip()
            if not announcement_id or not title:
                raise CninfoResponseError("CNINFO announcement row is missing id or title")
            adjunct_url = str(item.get("adjunctUrl") or "").strip()
            document_url = self._document_url(adjunct_url) if adjunct_url else ""
            records.append(
                DisclosureRecord(
                    provider=self.name,
                    provider_record_id=announcement_id,
                    official_record_id=announcement_id,
                    symbol=symbol,
                    company_name=str(item.get("secName") or "").strip(),
                    title=title,
                    published_at=self._published_at(item.get("announcementTime")),
                    document_url=document_url,
                    document_type=str(item.get("adjunctType") or "PDF").strip().upper(),
                    content_kind=content_kind,
                    category=str(item.get("announcementType") or "").strip(),
                    raw_metadata={
                        "announcement_type": str(item.get("announcementType") or ""),
                        "batch_num": str(item.get("batchNum") or ""),
                        "important": bool(item.get("important")),
                    },
                )
            )
        has_more_raw = payload.get("hasMore")
        has_more = has_more_raw is True or str(has_more_raw or "").strip().lower() in {"1", "true", "yes"}
        return DisclosurePage(records=tuple(records), has_more=has_more, total=total)

    @staticmethod
    def _document_url(adjunct_url: str) -> str:
        url = urljoin(STATIC_BASE_URL, adjunct_url.lstrip("/"))
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc.lower() != "static.cninfo.com.cn":
            raise CninfoResponseError("CNINFO returned an unexpected document host")
        return url

    @staticmethod
    def _published_at(value: Any) -> str:
        try:
            milliseconds = int(value)
        except (TypeError, ValueError):
            return str(value or "").strip()
        return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).astimezone(BJ).isoformat()
