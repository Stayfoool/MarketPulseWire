"""Provider-neutral contracts for company disclosure collection."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class DisclosureSecurity:
    symbol: str
    code: str
    provider_security_id: str
    company_name: str = ""
    raw_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DisclosureRecord:
    provider: str
    provider_record_id: str
    official_record_id: str
    symbol: str
    company_name: str
    title: str
    published_at: str
    document_url: str
    document_type: str
    content_kind: str
    category: str = ""
    raw_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DisclosurePage:
    records: tuple[DisclosureRecord, ...]
    has_more: bool
    total: int


class DisclosureProvider(Protocol):
    name: str

    def resolve_securities(self, symbols: list[str]) -> dict[str, DisclosureSecurity]: ...

    def list_disclosures(
        self,
        securities: list[DisclosureSecurity],
        start_date: str,
        end_date: str,
        content_kind: str,
        page: int,
    ) -> DisclosurePage: ...


def disclosure_identity(record: DisclosureRecord) -> str:
    """Return a transport-neutral identity suitable for collector deduplication."""
    official_id = str(record.official_record_id or "").strip()
    if official_id:
        return f"announcement:{official_id}"
    match = re.search(r"/(\d+)\.pdf(?:\?|$)", record.document_url, flags=re.IGNORECASE)
    if match:
        return f"announcement:{match.group(1)}"
    normalized_title = " ".join(record.title.split()).lower()
    seed = "\n".join((record.symbol.upper(), normalized_title, record.published_at))
    return "disclosure:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def provider_factory(name: str) -> DisclosureProvider:
    normalized = str(name or "").strip().lower()
    if normalized == "cninfo_public":
        from cninfo_disclosure_provider import CninfoPublicProvider

        return CninfoPublicProvider()
    raise ValueError(f"unsupported company disclosure provider: {name}")
