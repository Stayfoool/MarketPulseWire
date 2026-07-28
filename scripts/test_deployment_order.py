#!/usr/bin/env python3
"""Regression checks for overlay, systemd install, then stale-code pruning."""

from __future__ import annotations

import subprocess
from pathlib import Path


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
        "config/llm_decision_rules.json",
        "config/source_profiles.local.json",
        "data/",
        "reports/",
    ):
        assert exclusion in overlay
        assert exclusion in prune


def test_workflow_orders_overlay_install_and_prune() -> None:
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    overlay = "run: ./scripts/deploy_remote.sh"
    install = "run: ./scripts/install_remote_systemd.sh"
    prune = "run: ./scripts/prune_remote_code.sh"
    assert workflow.count(overlay) == 1
    assert workflow.count(install) == 1
    assert workflow.count(prune) == 1
    assert workflow.index(overlay) < workflow.index(install) < workflow.index(prune)


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
    owner = "chown '$REMOTE_SERVICE_USER:$REMOTE_SERVICE_USER' '$REMOTE_DIR'"
    mode = "chmod 700 '$REMOTE_DIR'"
    assert prune.count(owner) == 1
    assert prune.count(mode) == 1
    assert prune.index(sync) < prune.index(owner) < prune.index(mode)


def main() -> int:
    test_shared_sync_modes_and_private_exclusions()
    test_workflow_orders_overlay_install_and_prune()
    test_prune_requires_matching_systemd_revision()
    test_prune_restores_deployment_root_metadata()
    print("deployment order checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
