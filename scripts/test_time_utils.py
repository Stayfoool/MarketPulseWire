#!/usr/bin/env python3
"""Regression checks for timestamp normalization."""

from __future__ import annotations

from time_utils import parse_datetime_to_utc_iso, timestamp_to_utc_iso


def main() -> int:
    if timestamp_to_utc_iso("1719806400") != "2024-07-01T04:00:00+00:00":
        raise AssertionError("seconds timestamp should normalize to UTC ISO")
    if timestamp_to_utc_iso("1719806400000") != "2024-07-01T04:00:00+00:00":
        raise AssertionError("milliseconds timestamp should normalize to UTC ISO")
    if parse_datetime_to_utc_iso("Mon, 29 Jun 2026 06:00:00 GMT") != "2026-06-29T06:00:00+00:00":
        raise AssertionError("RFC date should normalize to UTC ISO")
    if parse_datetime_to_utc_iso("2026-06-29T14:00:00+08:00") != "2026-06-29T06:00:00+00:00":
        raise AssertionError("timezone-aware ISO should normalize to UTC ISO")
    if parse_datetime_to_utc_iso("2026-06-29 14:00:00") != "2026-06-29T06:00:00+00:00":
        raise AssertionError("naive China market timestamps should be treated as +08:00")
    print("time utils checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
