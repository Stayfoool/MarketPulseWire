#!/usr/bin/env python3
"""Regression checks for the local Web workbench HTML."""

from __future__ import annotations

from holdings_web import html_page


def test_embedded_script_keeps_newline_escapes() -> None:
    html = html_page(token_required=False)
    assert "showView('overview');" in html
    index = html.find("parsed.lessons.join")
    assert index > 0
    snippet = html[index : index + 40]
    assert repr("\\n") in repr(snippet)
    assert "parsed.lessons.join('\n')" not in html


def main() -> int:
    test_embedded_script_keeps_newline_escapes()
    print("holdings web checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
