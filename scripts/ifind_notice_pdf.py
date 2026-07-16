"""Download and extract text from iFinD announcement PDFs."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

from disclosure_document import (
    DisclosureDocumentError,
    extract_pdf_text,
    file_sha256,
    normalize_text,
    parse_disclosure_pdf,
)
from env_utils import get_env


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NOTICE_PDF_DIR = ROOT / "data" / "ifind_notices"


class NoticePdfError(DisclosureDocumentError):
    """Raised when a notice PDF cannot be downloaded or parsed."""


def env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"0", "false", "no", "n", "off", "禁用"}:
        return False
    if raw in {"1", "true", "yes", "y", "on", "启用"}:
        return True
    return default


def env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def pdf_dir() -> Path:
    raw = get_env("IFIND_NOTICE_PDF_DIR", default="")
    return Path(raw).expanduser() if raw else DEFAULT_NOTICE_PDF_DIR


def notice_pdf_url(row: dict[str, Any]) -> str:
    return str(row.get("pdfURL") or row.get("pdfUrl") or row.get("PDFURL") or "").strip()


def parse_notice_pdf(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return extracted text and safe parse metadata.

    The signed iFinD PDF URL is intentionally not included in returned metadata.
    """
    metadata: dict[str, Any] = {
        "enabled": env_bool("IFIND_NOTICE_PDF_PARSE", True),
        "status": "skipped",
        "source": "ifind_pdf",
    }
    if not metadata["enabled"]:
        metadata["reason"] = "IFIND_NOTICE_PDF_PARSE disabled"
        return "", metadata

    url = notice_pdf_url(row)
    if not url:
        metadata["reason"] = "missing pdfURL"
        return "", metadata

    max_bytes = env_int("IFIND_NOTICE_PDF_MAX_BYTES", 30 * 1024 * 1024, minimum=1024 * 1024)
    max_pages = env_int("IFIND_NOTICE_PDF_MAX_PAGES", 80, minimum=1)
    max_chars = env_int("IFIND_NOTICE_TEXT_MAX_CHARS", 20000, minimum=1000)
    min_chars = env_int("IFIND_NOTICE_TEXT_MIN_CHARS", 200, minimum=0)

    text, common_metadata = parse_disclosure_pdf(
        url,
        target_dir=pdf_dir(),
        identity_parts=identity_parts(row, url),
        max_bytes=max_bytes,
        max_pages=max_pages,
        max_chars=max_chars,
        min_chars=min_chars,
        user_agent="surveil-ifind-notice-pdf/0.1",
        target_filename=safe_pdf_filename(row, url),
    )
    common_metadata["source"] = "ifind_pdf"
    return text, common_metadata


def identity_parts(row: dict[str, Any], url: str) -> list[str]:
    return [
        str(row.get("thscode") or row.get("THSCODE") or row.get("code") or "notice").strip(),
        str(row.get("seq") or row.get("SEQ") or row.get("id") or "").strip(),
        str(row.get("reportTitle") or row.get("title") or row.get("annTitle") or "").strip(),
        url,
    ]


def safe_pdf_filename(row: dict[str, Any], url: str) -> str:
    symbol, seq, title, _url = identity_parts(row, url)
    seed = "\n".join(part for part in [symbol, seq, title, url] if part)
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    prefix = re.sub(r"[^A-Za-z0-9_.-]+", "_", symbol.upper())[:32] or "notice"
    if seq:
        seq_part = re.sub(r"[^A-Za-z0-9_.-]+", "_", seq)[:32]
        return f"{prefix}_{seq_part}_{digest}.pdf"
    return f"{prefix}_{digest}.pdf"
