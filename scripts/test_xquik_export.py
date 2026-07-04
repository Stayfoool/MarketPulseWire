#!/usr/bin/env python3
"""Regression checks for Xquik export loading."""

from __future__ import annotations

import tempfile
from pathlib import Path

from xquik_export import load_xquik_export_posts, parse_xquik_export


def test_parse_json_export() -> None:
    records = parse_xquik_export('{"tweets": [{"id": "1", "text": "  AI infra signal  "}]}')

    assert records == [{"id": "1", "text": "  AI infra signal  "}]


def test_parse_jsonl_export() -> None:
    records = parse_xquik_export('{"id":"1","full_text":"First"}\n{"id":"2","tweet":"Second"}', "tweets.jsonl")

    assert [record["id"] for record in records] == ["1", "2"]


def test_reject_csv_without_text_column() -> None:
    try:
        parse_xquik_export("id,url\n1,https://x.com/acme/status/1\n", "tweets.csv")
    except ValueError as exc:
        assert "CSV export needs" in str(exc)
    else:
        raise AssertionError("expected missing text column to fail")


def test_load_posts_normalizes_url_and_limit() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        export_path = Path(tmpdir) / "tweets.csv"
        export_path.write_text(
            "id,text,created_at\n1,First post,2026-01-01T00:00:00Z\n2,Second post,2026-01-02T00:00:00Z\n",
            encoding="utf-8",
        )
        posts = load_xquik_export_posts(export_path, username="example", limit=1)

    assert posts == [
        {
            "id": "1",
            "text": "First post",
            "full_text": "First post",
            "created_at": "2026-01-01T00:00:00Z",
            "url": "https://x.com/example/status/1",
            "public_metrics": {},
            "_media": [],
        }
    ]


def main() -> int:
    test_parse_json_export()
    test_parse_jsonl_export()
    test_reject_csv_without_text_column()
    test_load_posts_normalizes_url_and_limit()
    print("xquik export checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
