#!/usr/bin/env python3
"""Regression checks for the local Web workbench HTML."""

from __future__ import annotations

from holdings_web import RUN_ONCE_TARGETS, html_page, unit_actions


def test_embedded_script_keeps_newline_escapes() -> None:
    html = html_page(token_required=False)
    assert "showView('overview');" in html
    index = html.find("parsed.lessons.join")
    assert index > 0
    snippet = html[index : index + 40]
    assert repr("\\n") in repr(snippet)
    assert "parsed.lessons.join('\n')" not in html


def test_health_page_exposes_service_action_controls() -> None:
    html = html_page(token_required=False)
    assert "/api/service-action" in html
    assert "runServiceAction" in html
    assert "重启定时器" in html
    assert "立即运行" in html


def test_systemd_actions_are_whitelisted() -> None:
    assert "restart" in unit_actions("surveil-rss-monitor.service")
    assert "restart" in unit_actions("surveil-trendforce-page-monitor.service")
    assert "restart_timer" in unit_actions("surveil-china-media.timer")
    assert "run_once" in unit_actions("surveil-china-media.timer")
    assert RUN_ONCE_TARGETS["surveil-china-media.timer"] == "surveil-china-media.service"
    assert unit_actions("surveil-holdings-web.service") == ["status"]
    assert unit_actions("ssh.service") == []


def main() -> int:
    test_embedded_script_keeps_newline_escapes()
    test_health_page_exposes_service_action_controls()
    test_systemd_actions_are_whitelisted()
    print("holdings web checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
