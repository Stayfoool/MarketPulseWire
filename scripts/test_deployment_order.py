#!/usr/bin/env python3
"""Regression checks for overlay, systemd install, then stale-code pruning."""

from __future__ import annotations

import sqlite3
import subprocess
import tempfile
from pathlib import Path

from verify_production import VerificationError, exact_requirements, verify_database


ROOT = Path(__file__).resolve().parents[1]


def sync_args(mode: str) -> list[str]:
    script = f"""
set -euo pipefail
source scripts/remote_code_sync.sh
rsync() {{ printf '%s\\n' "$@"; }}
RSYNC_RSH='ssh test'
REMOTE_USER='operator'
REMOTE_HOST='example.invalid'
REMOTE_DIR='/opt/surveil'
remote_code_sync {mode}
"""
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def test_shared_sync_modes_and_private_exclusions() -> None:
    overlay = sync_args("overlay")
    prune = sync_args("prune")
    assert "--delete" not in overlay
    assert prune.count("--delete") == 1
    for exclusion in (
        ".env",
        "REVISION",
        "proxy.env",
        "config/portfolio.json",
        "config/portfolio.lock",
        "config/backups/",
        "config/llm_decision_rules.json",
        "config/source_profiles.local.json",
        "data/",
        "reports/",
    ):
        assert exclusion in overlay
        assert exclusion in prune


def test_workflow_orders_overlay_install_prune_and_verification() -> None:
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    overlay = "run: ./scripts/deploy_remote.sh"
    install = "run: ./scripts/install_remote_systemd.sh"
    prune = "run: ./scripts/prune_remote_code.sh"
    verify = "run: ./scripts/verify_remote_production.sh"
    assert workflow.count(overlay) == 1
    assert workflow.count(install) == 1
    assert workflow.count(prune) == 1
    assert workflow.count(verify) == 1
    assert workflow.index(overlay) < workflow.index(install) < workflow.index(prune) < workflow.index(verify)
    assert "systemctl restart surveil-holdings-web.service || true" not in workflow


def test_prune_requires_matching_systemd_revision() -> None:
    deploy = (ROOT / "scripts" / "deploy_remote.sh").read_text(encoding="utf-8")
    installer = (ROOT / "scripts" / "install_remote_systemd.sh").read_text(encoding="utf-8")
    prune = (ROOT / "scripts" / "prune_remote_code.sh").read_text(encoding="utf-8")
    assert "remote_code_sync overlay" in deploy
    assert "--delete" not in deploy
    assert "data/systemd-installed-revision" in installer
    assert installer.rindex("systemctl daemon-reload") < installer.index("SYSTEMD_MARKER=")
    assert "data/systemd-installed-revision" in prune
    assert 'if [ \\"\\$DEPLOYED_COMMIT\\" != \\"\\$INSTALLED_COMMIT\\" ]; then' in prune
    assert prune.index("INSTALLED_COMMIT=") < prune.index("remote_code_sync prune")


def test_prune_restores_deployment_root_metadata() -> None:
    prune = (ROOT / "scripts" / "prune_remote_code.sh").read_text(encoding="utf-8")
    sync = "remote_code_sync prune"
    owner = "chown -R '$REMOTE_SERVICE_USER:$REMOTE_SERVICE_USER' '$REMOTE_DIR'"
    mode = "chmod 700 '$REMOTE_DIR'"
    assert prune.count(owner) == 1
    assert prune.count(mode) == 1
    assert prune.index(sync) < prune.index(owner) < prune.index(mode)


def test_installer_restarts_web_after_enabling_it() -> None:
    installer = (ROOT / "scripts" / "install_remote_systemd.sh").read_text(encoding="utf-8")
    enable = "systemctl enable --now surveil-holdings-web.service"
    restart = "systemctl restart surveil-holdings-web.service"
    assert installer.count(enable) == 1
    assert installer.count(restart) == 1
    assert installer.index(enable) < installer.index(restart) < installer.index("SYSTEMD_MARKER=")


def test_deploy_entrypoints_run_all_four_stages() -> None:
    justfile = (ROOT / "Justfile").read_text(encoding="utf-8")
    deploy_block = justfile.split("deploy:\n", 1)[1].split("\n\n", 1)[0]
    commands = (
        "./scripts/deploy_remote.sh",
        "./scripts/install_remote_systemd.sh",
        "./scripts/prune_remote_code.sh",
        "./scripts/verify_remote_production.sh",
    )
    assert all(deploy_block.count(command) == 1 for command in commands)
    assert [deploy_block.index(command) for command in commands] == sorted(
        deploy_block.index(command) for command in commands
    )


def test_production_dependencies_are_exactly_pinned() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    expected = {
        "pypdf==6.14.2",
        "feedparser==6.0.12",
        "h2==4.3.0",
        "httpx==0.28.1",
        "lark-oapi==1.7.1",
        "playwright==1.61.0",
        "trafilatura==2.1.0",
    }
    assert {line.strip() for line in requirements if line.strip()} == expected
    deploy = (ROOT / "scripts" / "deploy_remote.sh").read_text(encoding="utf-8")
    assert "pip install --upgrade pip" not in deploy


def test_installer_adds_bounded_ordinary_log_rotation() -> None:
    installer = (ROOT / "scripts" / "install_remote_systemd.sh").read_text(encoding="utf-8")
    policy = (ROOT / "systemd" / "surveil.logrotate").read_text(encoding="utf-8")
    for directive in (
        "__REMOTE_DIR__/logs/*.log",
        "daily",
        "rotate 14",
        "compress",
        "copytruncate",
        "su __SERVICE_USER__ __SERVICE_USER__",
        "create 0600 __SERVICE_USER__ __SERVICE_USER__",
    ):
        assert directive in policy
    assert "reports/" not in policy
    assert "llm-decision-audit" not in policy
    assert "/etc/logrotate.d/surveil" in installer
    assert "logrotate --debug /etc/logrotate.d/surveil" in installer


def test_strict_verifier_covers_production_invariants() -> None:
    verifier = (ROOT / "scripts" / "verify_production.py").read_text(encoding="utf-8")
    remote = (ROOT / "scripts" / "verify_remote_production.sh").read_text(encoding="utf-8")
    for required_check in (
        "systemd-installed-revision",
        "failed_retryable",
        "admitted_pending",
        "PRAGMA quick_check",
        "PRAGMA foreign_key_check",
        "/api/health/summary",
        "/api/source-profiles",
        "/api/current-rules",
        "load_production_rule_config",
        "load_rule_catalog",
        "verify_dependencies",
        "verify_logrotate",
    ):
        assert required_check in verifier
    assert "sudo -u '$REMOTE_SERVICE_USER'" in remote


def test_strict_verifier_rejects_unpinned_dependencies() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        requirements = Path(tmp) / "requirements.txt"
        requirements.write_text("httpx==0.28.1\n", encoding="utf-8")
        assert exact_requirements(requirements) == {"httpx": "0.28.1"}
        requirements.write_text("httpx>=0.28.1\n", encoding="utf-8")
        try:
            exact_requirements(requirements)
        except VerificationError:
            pass
        else:
            raise AssertionError("non-exact production dependency must fail verification")


def test_strict_verifier_checks_current_review_failures_read_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "surveil.sqlite3"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE market_reviews (
                    id INTEGER PRIMARY KEY,
                    is_current INTEGER NOT NULL,
                    review_status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO market_reviews VALUES (1, 1, 'succeeded', datetime('now'))"
            )
        verify_database(db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO market_reviews VALUES (2, 1, 'failed_retryable', datetime('now'))"
            )
        try:
            verify_database(db_path)
        except VerificationError:
            pass
        else:
            raise AssertionError("current failed_retryable review must fail verification")
        with sqlite3.connect(db_path) as conn:
            conn.execute("DELETE FROM market_reviews WHERE id=2")
            conn.execute(
                "INSERT INTO market_reviews VALUES (3, 1, 'admitted_pending', datetime('now', '-31 minutes'))"
            )
        try:
            verify_database(db_path)
        except VerificationError:
            pass
        else:
            raise AssertionError("stale admitted_pending review must fail verification")


def main() -> int:
    test_shared_sync_modes_and_private_exclusions()
    test_workflow_orders_overlay_install_prune_and_verification()
    test_prune_requires_matching_systemd_revision()
    test_prune_restores_deployment_root_metadata()
    test_installer_restarts_web_after_enabling_it()
    test_deploy_entrypoints_run_all_four_stages()
    test_production_dependencies_are_exactly_pinned()
    test_installer_adds_bounded_ordinary_log_rotation()
    test_strict_verifier_covers_production_invariants()
    test_strict_verifier_rejects_unpinned_dependencies()
    test_strict_verifier_checks_current_review_failures_read_only()
    print("deployment order checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
