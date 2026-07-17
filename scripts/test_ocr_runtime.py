#!/usr/bin/env python3
"""Regression checks for conditional ValueList OCR deployment validation."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from check_ocr_runtime import check_ocr_runtime, ocr_runtime_required, pinned_versions


ROOT = Path(__file__).resolve().parents[1]


def fixture_requirements(root: Path) -> Path:
    path = root / "requirements-ocr.txt"
    path.write_text(
        "paddlepaddle==2.6.2\npaddleocr==2.7.3\nnumpy==1.26.4\n",
        encoding="utf-8",
    )
    return path


def test_effective_runtime_enablement_matches_preview_defaults() -> None:
    assert ocr_runtime_required({}) is True
    assert ocr_runtime_required({"VALUE_DIRECTORY_PREVIEW_ENABLED": "0"}) is False
    assert ocr_runtime_required({"VALUE_DIRECTORY_PREVIEW_OCR_ENABLED": "false"}) is False
    assert ocr_runtime_required(
        {"VALUE_DIRECTORY_PREVIEW_ENABLED": "1", "VALUE_DIRECTORY_PREVIEW_OCR_ENABLED": "1"}
    ) is True


def test_repository_ocr_requirements_keep_exact_runtime_pins() -> None:
    pins = pinned_versions(ROOT / "requirements-ocr.txt")
    assert pins == {
        "paddlepaddle": "2.6.2",
        "paddleocr": "2.7.3",
        "numpy": "1.26.4",
    }


def test_valid_runtime_checks_versions_and_imports() -> None:
    with TemporaryDirectory() as tmpdir:
        requirements = fixture_requirements(Path(tmpdir))
        versions = {"paddlepaddle": "2.6.2", "paddleocr": "2.7.3", "numpy": "1.26.4"}
        imported: list[str] = []
        installed, errors = check_ocr_runtime(
            requirements,
            version_lookup=versions.__getitem__,
            importer=lambda name: imported.append(name),
        )
    assert installed == versions
    assert errors == []
    assert imported == ["paddle", "paddleocr", "numpy", "cv2"]


def test_missing_and_wrong_versions_fail_before_native_imports() -> None:
    with TemporaryDirectory() as tmpdir:
        requirements = fixture_requirements(Path(tmpdir))
        imported: list[str] = []

        def version_lookup(name: str) -> str:
            if name == "paddleocr":
                raise importlib.metadata.PackageNotFoundError(name)
            return "3.0.0" if name == "paddlepaddle" else "1.26.4"

        _installed, errors = check_ocr_runtime(
            requirements,
            version_lookup=version_lookup,
            importer=lambda name: imported.append(name),
        )
    assert errors == [
        "paddlepaddle==3.0.0 does not match pinned 2.6.2",
        "paddleocr is not installed (expected 2.7.3)",
    ]
    assert imported == []


def test_native_import_failure_is_reported() -> None:
    with TemporaryDirectory() as tmpdir:
        requirements = fixture_requirements(Path(tmpdir))
        versions = {"paddlepaddle": "2.6.2", "paddleocr": "2.7.3", "numpy": "1.26.4"}

        def importer(name: str) -> None:
            if name == "cv2":
                raise ImportError("libGL missing")

        _installed, errors = check_ocr_runtime(
            requirements,
            version_lookup=versions.__getitem__,
            importer=importer,
        )
    assert errors == ["cannot import cv2: ImportError: libGL missing"]


def test_cli_skips_dependencies_when_ocr_is_explicitly_disabled() -> None:
    with TemporaryDirectory() as tmpdir:
        env_path = Path(tmpdir) / ".env"
        env_path.write_text("VALUE_DIRECTORY_PREVIEW_OCR_ENABLED=0\n", encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "check_ocr_runtime.py"),
                "--env-file",
                str(env_path),
                "--requirements",
                str(ROOT / "requirements-ocr.txt"),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    assert result.returncode == 0
    assert "disabled by configuration" in result.stdout


def test_remote_deploy_checks_before_and_after_optional_install() -> None:
    deploy = (ROOT / "scripts" / "deploy_remote.sh").read_text(encoding="utf-8")
    check = ".venv/bin/python scripts/check_ocr_runtime.py"
    install = "PYTHON_BIN=.venv/bin/python scripts/install_ocr_dependencies.sh"
    assert deploy.count(check) == 2
    assert install in deploy
    assert deploy.index(check) < deploy.index(install) < deploy.rindex(check)


def main() -> int:
    test_effective_runtime_enablement_matches_preview_defaults()
    test_repository_ocr_requirements_keep_exact_runtime_pins()
    test_valid_runtime_checks_versions_and_imports()
    test_missing_and_wrong_versions_fail_before_native_imports()
    test_native_import_failure_is_reported()
    test_cli_skips_dependencies_when_ocr_is_explicitly_disabled()
    test_remote_deploy_checks_before_and_after_optional_install()
    print("OCR runtime deployment checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
