#!/usr/bin/env python3
"""Load Xquik tweet exports into MarketPulseWire post dictionaries."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TEXT_FIELDS = ("text", "tweet", "full_text", "content", "body")
ID_FIELDS = ("id", "tweet_id", "id_str", "post_id")
TIME_FIELDS = ("created_at", "published_at", "timestamp", "time")
URL_FIELDS = ("url", "tweet_url", "permalink")


def load_xquik_export_posts(path: Path, *, username: str, limit: int) -> list[dict[str, Any]]:
    posts = parse_xquik_export(path.read_text(encoding="utf-8"), path.name)
    normalized = [normalize_xquik_post(post, username=username) for post in posts]
    return [post for post in normalized if post][:limit]


def parse_xquik_export(raw_export: str, filename: str = "tweets.json") -> list[dict[str, Any]]:
    if not raw_export.strip():
        return []

    lowered_name = filename.lower()
    if lowered_name.endswith(".csv"):
        return _parse_csv(raw_export)
    if lowered_name.endswith(".jsonl"):
        return _parse_jsonl(raw_export)

    try:
        parsed = json.loads(raw_export)
    except json.JSONDecodeError as exc:
        if lowered_name.endswith(".json"):
            raise ValueError("Xquik JSON export contains invalid JSON.") from exc
        return _parse_jsonl(raw_export)

    return _records_from_json(parsed)


def normalize_xquik_post(record: dict[str, Any], *, username: str) -> dict[str, Any]:
    text = _first_text(record, TEXT_FIELDS)
    if not text:
        return {}

    post_id = _first_text(record, ID_FIELDS) or _status_id_from_url(_first_text(record, URL_FIELDS))
    if not post_id:
        post_id = f"xquik-{hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]}"

    created_at = _first_text(record, TIME_FIELDS)
    url = _first_text(record, URL_FIELDS) or f"https://x.com/{username}/status/{post_id}"
    return {
        "id": str(post_id),
        "text": text,
        "full_text": text,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "url": url,
        "public_metrics": record.get("public_metrics") if isinstance(record.get("public_metrics"), dict) else {},
        "_media": [],
    }


def _parse_csv(raw_export: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(raw_export.splitlines())
    if reader.fieldnames is None:
        return []
    if _find_field(reader.fieldnames, TEXT_FIELDS) is None:
        raise ValueError("Xquik CSV export needs a text, tweet, full_text, content, or body column.")
    return [dict(row) for row in reader]


def _parse_jsonl(raw_export: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in raw_export.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError("Xquik JSONL export contains an invalid JSON line.") from exc
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def _records_from_json(parsed: Any) -> list[dict[str, Any]]:
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        for key in ("tweets", "items", "data", "results"):
            value = parsed.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [parsed]
    return []


def _first_text(record: dict[str, Any], fields: tuple[str, ...]) -> str:
    field = _find_field(record.keys(), fields)
    if field is None:
        return ""
    value = record.get(field)
    if value is None:
        return ""
    return " ".join(str(value).split())


def _find_field(fields: Any, candidates: tuple[str, ...]) -> str | None:
    normalized_fields = {str(field).lower(): str(field) for field in fields}
    for candidate in candidates:
        if candidate in normalized_fields:
            return normalized_fields[candidate]
    return None


def _status_id_from_url(url: str) -> str:
    match = re.search(r"/status/(\d+)", url)
    return match.group(1) if match else ""
