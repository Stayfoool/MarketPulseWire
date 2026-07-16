"""Common download and text extraction for official disclosure PDFs."""

from __future__ import annotations

import hashlib
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class DisclosureDocumentError(RuntimeError):
    pass


def safe_pdf_filename(identity_parts: list[str]) -> str:
    seed = "\n".join(str(part or "").strip() for part in identity_parts if str(part or "").strip())
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
    prefix = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(identity_parts[0] if identity_parts else "disclosure"))[:40]
    return f"{prefix or 'disclosure'}_{digest}.pdf"


def parse_disclosure_pdf(
    url: str,
    *,
    target_dir: Path,
    identity_parts: list[str],
    enabled: bool = True,
    max_bytes: int = 30 * 1024 * 1024,
    max_pages: int = 80,
    max_chars: int = 20_000,
    min_chars: int = 200,
    user_agent: str = "surveil-company-disclosures/1.0",
    target_filename: str | None = None,
) -> tuple[str, dict[str, Any]]:
    metadata: dict[str, Any] = {
        "enabled": bool(enabled),
        "status": "skipped",
        "source": "disclosure_pdf",
    }
    if not enabled:
        metadata["reason"] = "document parsing disabled"
        return "", metadata
    if not url:
        metadata["reason"] = "missing document URL"
        return "", metadata

    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / (target_filename or safe_pdf_filename(identity_parts))
    metadata.update(
        {
            "file_name": target.name,
            "max_bytes": max_bytes,
            "max_pages": max_pages,
            "max_chars": max_chars,
            "min_chars": min_chars,
        }
    )
    try:
        if not target.exists() or target.stat().st_size == 0:
            download_pdf(url, target, max_bytes=max_bytes, user_agent=user_agent)
        digest, size = file_sha256(target)
        text, extract_meta = extract_pdf_text(target, max_pages=max_pages, max_chars=max_chars)
    except Exception as exc:  # noqa: BLE001 - title/metadata ingestion remains available
        metadata.update({"status": "failed", "error": str(exc)[:500]})
        return "", metadata

    text = normalize_text(text)
    status = "ok" if len(text) >= min_chars else "low_text"
    metadata.update(
        {
            "status": status,
            "file_sha256": digest,
            "file_size": size,
            "extracted_chars": len(text),
            **extract_meta,
        }
    )
    if status == "low_text":
        metadata["reason"] = "extracted text shorter than threshold"
    return text[:max_chars], metadata


def download_pdf(url: str, target: Path, *, max_bytes: int, user_agent: str) -> None:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/pdf,application/octet-stream,*/*", "User-Agent": user_agent},
        method="GET",
    )
    temp = target.with_suffix(f".{int(time.time())}.tmp")
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > max_bytes:
                        raise DisclosureDocumentError(f"PDF too large: {content_length} bytes")
                except ValueError:
                    pass
            with temp.open("wb") as fh:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise DisclosureDocumentError(f"PDF exceeded max bytes: {total}")
                    fh.write(chunk)
    except urllib.error.HTTPError as exc:
        raise DisclosureDocumentError(f"PDF download HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise DisclosureDocumentError(f"PDF download failed: {exc}") from exc
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    if total < 4:
        temp.unlink(missing_ok=True)
        raise DisclosureDocumentError("PDF download returned empty file")
    with temp.open("rb") as fh:
        header = fh.read(8)
    if not header.startswith(b"%PDF"):
        temp.unlink(missing_ok=True)
        raise DisclosureDocumentError("downloaded file is not a PDF")
    temp.replace(target)


def extract_pdf_text(path: Path, *, max_pages: int, max_chars: int) -> tuple[str, dict[str, Any]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DisclosureDocumentError("missing dependency pypdf") from exc
    reader = PdfReader(str(path))
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception as exc:  # noqa: BLE001
            raise DisclosureDocumentError(f"encrypted PDF cannot be decrypted: {exc}") from exc
    pages_total = len(reader.pages)
    pages_read = min(pages_total, max_pages)
    parts: list[str] = []
    length = 0
    for index in range(pages_read):
        try:
            page_text = reader.pages[index].extract_text() or ""
        except Exception as exc:  # noqa: BLE001
            page_text = f"\n[第 {index + 1} 页文本抽取失败：{exc}]\n"
        if page_text.strip():
            parts.append(page_text)
            length += len(page_text)
        if length >= max_chars:
            break
    return "\n\n".join(parts)[:max_chars], {"pages_total": pages_total, "pages_read": pages_read}


def normalize_text(text: str) -> str:
    cleaned = text.replace("\r", "\n").replace("\x00", "")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def file_sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(256 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size
