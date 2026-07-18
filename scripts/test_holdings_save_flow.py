#!/usr/bin/env python3
"""Regression checks for bounded, idempotent holdings preview and save."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import holdings_store
import holdings_web
from holdings_store import (
    HoldingsConflictError,
    HoldingsError,
    holdings_revision,
    remote_validation_symbols,
    save_holdings,
    validate_holdings,
)


def holding(
    symbol: str,
    name: str,
    *,
    enabled: bool = True,
    aliases: list[str] | None = None,
    keywords: list[str] | None = None,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "name": name,
        "enabled": enabled,
        "aliases": aliases or [],
        "news_keywords": keywords or [],
    }


def test_remote_validation_targets_only_changed_enabled_identities() -> None:
    current = [
        holding("300179.SZ", "四方达", aliases=["河南四方达"]),
        holding("603773.SH", "沃格光电", enabled=False),
    ]
    keyword_only = [
        holding("300179.SZ", "四方达", aliases=["河南四方达"], keywords=["玻璃基板"]),
        holding("603773.SH", "沃格光电", enabled=False),
    ]
    assert remote_validation_symbols(current, keyword_only) == set()

    changed = [
        holding("300179.SZ", "四方达股份", aliases=["河南四方达"]),
        holding("603773.SH", "沃格光电", enabled=True),
        holding("688498.SH", "源杰科技", enabled=False),
        holding("301308.SZ", "江波龙", enabled=True),
    ]
    assert remote_validation_symbols(current, changed) == {
        "300179.SZ",
        "603773.SH",
        "301308.SZ",
    }


def test_remote_validation_calls_only_selected_symbols() -> None:
    items = [holding("300179.SZ", "四方达"), holding("301308.SZ", "江波龙")]
    calls: list[str] = []
    original = holdings_store.fetch_a_share_name
    try:
        holdings_store.fetch_a_share_name = lambda symbol: calls.append(symbol) or "江波龙"
        validate_holdings(items, remote_symbols={"301308.SZ"})
    finally:
        holdings_store.fetch_a_share_name = original
    assert calls == ["301308.SZ"]


def test_sina_name_lookup_uses_shared_bounded_transport() -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    original = holdings_store.http_get
    try:
        holdings_store.http_get = lambda url, **kwargs: (
            calls.append((url, kwargs))
            or SimpleNamespace(content='var hq_str_sz300179="四方达,1,2";'.encode("gb18030"))
        )
        assert holdings_store.fetch_a_share_name("300179.SZ") == "四方达"
    finally:
        holdings_store.http_get = original
    assert calls[0][0] == "https://hq.sinajs.cn/list=sz300179"
    assert calls[0][1]["timeout"] == 5
    assert calls[0][1]["retries"] == 0


def test_preview_keyword_edit_skips_remote_and_issues_bounded_token() -> None:
    current = [holding("300179.SZ", "四方达")]
    proposed = [holding("300179.SZ", "四方达", keywords=["玻璃基板"])]
    original = holdings_store.fetch_a_share_name
    try:
        holdings_store.fetch_a_share_name = lambda _symbol: (_ for _ in ()).throw(
            AssertionError("keyword-only preview must not fetch a market name")
        )
        preview = holdings_web.prepare_holdings_preview(proposed, current)
    finally:
        holdings_store.fetch_a_share_name = original
    assert preview["remote_checked_count"] == 0
    assert holdings_web.verify_holdings_preview_token(preview["preview_token"]) == (
        holdings_revision(current),
        holdings_revision(preview["normalized"]),
    )


def test_preview_token_rejects_tampering_and_expiry() -> None:
    token = holdings_web.issue_holdings_preview_token("base", "payload", now=100)
    assert holdings_web.verify_holdings_preview_token(token, now=100) == ("base", "payload")
    try:
        holdings_web.verify_holdings_preview_token(token + "x", now=100)
    except HoldingsError as exc:
        assert "无效" in str(exc)
    else:
        raise AssertionError("tampered preview token was accepted")
    try:
        holdings_web.verify_holdings_preview_token(
            token,
            now=100 + holdings_web.HOLDINGS_PREVIEW_TTL_SECONDS + 1,
        )
    except HoldingsError as exc:
        assert "过期" in str(exc)
    else:
        raise AssertionError("expired preview token was accepted")


def test_save_is_idempotent_and_checks_preview_revisions() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        config_path = root / "portfolio.json"
        lock_path = root / "portfolio.lock"
        current = [holding("300179.SZ", "四方达")]
        proposed = [holding("300179.SZ", "四方达", keywords=["玻璃基板"])]
        config_path.write_text(json.dumps({"holdings": current}, ensure_ascii=False), encoding="utf-8")
        normalized_current = holdings_store.normalized_holdings(config_path)
        normalized_proposed = holdings_store.normalize_holdings_for_save(proposed, normalized_current)
        base_revision = holdings_revision(normalized_current)
        payload_revision = holdings_revision(normalized_proposed)
        calls = {"backup": 0, "import": 0, "remote": 0}
        original_lock_path = holdings_store.LOCK_PATH
        original_backup = holdings_store.backup_config
        original_import = holdings_store.import_holdings
        original_fetch = holdings_store.fetch_a_share_name
        original_db_matches = holdings_store.holdings_db_matches
        try:
            holdings_store.LOCK_PATH = lock_path
            holdings_store.backup_config = lambda _path: calls.__setitem__("backup", calls["backup"] + 1) or root / "backup.json"
            holdings_store.import_holdings = lambda _config, _db: calls.__setitem__("import", calls["import"] + 1) or 1
            holdings_store.fetch_a_share_name = lambda _symbol: calls.__setitem__("remote", calls["remote"] + 1) or "四方达"
            holdings_store.holdings_db_matches = lambda _items, _db: calls["import"] > 0
            first = save_holdings(
                proposed,
                config_path=config_path,
                db_path=root / "surveil.sqlite3",
                expected_current_revision=base_revision,
                expected_payload_revision=payload_revision,
            )
            repeated = [
                save_holdings(
                    proposed,
                    config_path=config_path,
                    db_path=root / "surveil.sqlite3",
                    expected_current_revision=base_revision,
                    expected_payload_revision=payload_revision,
                )
                for _ in range(4)
            ]

            assert first.no_change is False
            assert all(result.no_change for result in repeated)
            assert calls == {"backup": 1, "import": 1, "remote": 0}
            assert json.loads(config_path.read_text(encoding="utf-8"))["holdings"][0]["news_keywords"] == ["玻璃基板"]

            changed_again = [holding("300179.SZ", "四方达", keywords=["玻璃基板", "散热"])]
            normalized_live = holdings_store.normalized_holdings(config_path)
            normalized_changed_again = holdings_store.normalize_holdings_for_save(changed_again, normalized_live)
            try:
                save_holdings(
                    changed_again,
                    config_path=config_path,
                    db_path=root / "surveil.sqlite3",
                    expected_current_revision=base_revision,
                    expected_payload_revision=holdings_revision(normalized_changed_again),
                )
            except HoldingsConflictError as exc:
                assert "发生变化" in str(exc)
            else:
                raise AssertionError("stale config revision was accepted")
        finally:
            holdings_store.LOCK_PATH = original_lock_path
            holdings_store.backup_config = original_backup
            holdings_store.import_holdings = original_import
            holdings_store.fetch_a_share_name = original_fetch
            holdings_store.holdings_db_matches = original_db_matches


def test_noop_repairs_missing_sqlite_projection_without_backup() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        config_path = root / "portfolio.json"
        db_path = root / "surveil.sqlite3"
        item = holding("300179.SZ", "四方达", keywords=["玻璃基板"])
        config_path.write_text(json.dumps({"holdings": [item]}, ensure_ascii=False), encoding="utf-8")
        normalized = holdings_store.normalized_holdings(config_path)
        calls = {"backup": 0, "import": 0}
        original_lock_path = holdings_store.LOCK_PATH
        original_backup = holdings_store.backup_config
        original_import = holdings_store.import_holdings
        original_db_matches = holdings_store.holdings_db_matches
        try:
            holdings_store.LOCK_PATH = root / "portfolio.lock"
            holdings_store.backup_config = lambda _path: calls.__setitem__("backup", calls["backup"] + 1)
            holdings_store.import_holdings = lambda _config, _db: calls.__setitem__("import", calls["import"] + 1) or 1
            holdings_store.holdings_db_matches = lambda _items, _db: False
            result = save_holdings(
                normalized,
                config_path=config_path,
                db_path=db_path,
                expected_current_revision=holdings_revision(normalized),
                expected_payload_revision=holdings_revision(normalized),
            )
        finally:
            holdings_store.LOCK_PATH = original_lock_path
            holdings_store.backup_config = original_backup
            holdings_store.import_holdings = original_import
            holdings_store.holdings_db_matches = original_db_matches
        assert result.sync_repaired is True
        assert result.no_change is False
        assert calls == {"backup": 0, "import": 1}


def test_sqlite_projection_match_is_strict() -> None:
    with TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "surveil.sqlite3"
        item = holdings_store.normalize_holding(holding("300179.SZ", "四方达", keywords=["玻璃基板"]))
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE portfolio_holdings (symbol TEXT, enabled INTEGER, raw_json TEXT)")
            conn.execute(
                "INSERT INTO portfolio_holdings VALUES (?, ?, ?)",
                ("300179.SZ", 1, json.dumps(item, ensure_ascii=False, sort_keys=True)),
            )
        assert holdings_store.holdings_db_matches([item], db_path) is True
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE portfolio_holdings SET enabled=0")
        assert holdings_store.holdings_db_matches([item], db_path) is False


def test_frontend_exposes_busy_and_idempotent_states() -> None:
    html = (holdings_web.WEB_ROOT / "index.html").read_text(encoding="utf-8")
    script = (holdings_web.WEB_ROOT / "app.js").read_text(encoding="utf-8")
    styles = (holdings_web.WEB_ROOT / "styles.css").read_text(encoding="utf-8")
    for element_id in (
        "holdingsRefreshButton",
        "holdingsSaveButton",
        "holdingsPreviewCancelButton",
        "holdingsConfirmButton",
    ):
        assert f'id="{element_id}"' in html
    assert "pendingPreviewToken" in script
    assert "beginHoldingsOperation('validating')" in script
    assert "beginHoldingsOperation('saving')" in script
    assert "配置与 SQLite 均为最新，无需重复写入" in script
    assert "联网名称校验：无需执行" in script
    assert ".status.busy" in styles
    assert ".modal-backdrop { position: fixed; inset: 0; z-index: 100;" in styles


def test_save_normalization_never_enriches_symbols_remotely() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        config_path = root / "portfolio.json"
        config_path.write_text('{"holdings": []}\n', encoding="utf-8")
        original_lock_path = holdings_store.LOCK_PATH
        original_backup = holdings_store.backup_config
        original_fetch = holdings_store.fetch_a_share_suggestions
        try:
            holdings_store.LOCK_PATH = root / "portfolio.lock"
            holdings_store.backup_config = lambda _path: root / "portfolio.backup.json"
            holdings_store.fetch_a_share_suggestions = lambda _name: (_ for _ in ()).throw(
                AssertionError("save must not perform remote symbol enrichment")
            )
            result = save_holdings(
                [{"name": "四方达", "enabled": True}],
                config_path=config_path,
                db_path=root / "surveil.sqlite3",
            )
            assert result.changed_count == 1
        finally:
            holdings_store.LOCK_PATH = original_lock_path
            holdings_store.backup_config = original_backup
            holdings_store.fetch_a_share_suggestions = original_fetch


def main() -> int:
    test_remote_validation_targets_only_changed_enabled_identities()
    test_remote_validation_calls_only_selected_symbols()
    test_sina_name_lookup_uses_shared_bounded_transport()
    test_preview_keyword_edit_skips_remote_and_issues_bounded_token()
    test_preview_token_rejects_tampering_and_expiry()
    test_save_is_idempotent_and_checks_preview_revisions()
    test_noop_repairs_missing_sqlite_projection_without_backup()
    test_sqlite_projection_match_is_strict()
    test_frontend_exposes_busy_and_idempotent_states()
    test_save_normalization_never_enriches_symbols_remotely()
    print("holdings save flow checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
