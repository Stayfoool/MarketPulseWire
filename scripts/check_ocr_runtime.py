#!/usr/bin/env python3
"""Validate the optional ValueList OCR runtime used by remote deployment."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import re
import sys
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = ROOT / ".env"
DEFAULT_REQUIREMENTS_PATH = ROOT / "requirements-ocr.txt"
REQUIRED_DISTRIBUTIONS = ("paddlepaddle", "paddleocr", "numpy")
REQUIRED_IMPORTS = ("paddle", "paddleocr", "numpy", "cv2")


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def env_bool(values: dict[str, str], name: str, default: bool) -> bool:
    raw = values.get(name, "").strip().lower()
    if raw in {"1", "true", "yes", "on", "y", "是"}:
        return True
    if raw in {"0", "false", "no", "off", "n", "否"}:
        return False
    return default


def ocr_runtime_required(values: dict[str, str]) -> bool:
    return env_bool(values, "VALUE_DIRECTORY_PREVIEW_ENABLED", True) and env_bool(
        values,
        "VALUE_DIRECTORY_PREVIEW_OCR_ENABLED",
        True,
    )


def pinned_versions(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    pattern = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)$")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = pattern.fullmatch(line)
        if match:
            pins[match.group(1).lower()] = match.group(2)
    missing = [name for name in REQUIRED_DISTRIBUTIONS if name not in pins]
    if missing:
        raise RuntimeError(f"requirements-ocr.txt missing exact pins: {', '.join(missing)}")
    return pins


def check_ocr_runtime(
    requirements_path: Path,
    *,
    version_lookup: Callable[[str], str] = importlib.metadata.version,
    importer: Callable[[str], Any] = importlib.import_module,
) -> tuple[dict[str, str], list[str]]:
    pins = pinned_versions(requirements_path)
    installed: dict[str, str] = {}
    errors: list[str] = []
    for distribution in REQUIRED_DISTRIBUTIONS:
        expected = pins[distribution]
        try:
            actual = version_lookup(distribution)
        except importlib.metadata.PackageNotFoundError:
            errors.append(f"{distribution} is not installed (expected {expected})")
            continue
        installed[distribution] = actual
        if actual != expected:
            errors.append(f"{distribution}=={actual} does not match pinned {expected}")
    if errors:
        return installed, errors
    for module in REQUIRED_IMPORTS:
        try:
            importer(module)
        except Exception as exc:  # noqa: BLE001 - report optional native import failures cleanly.
            errors.append(f"cannot import {module}: {type(exc).__name__}: {exc}")
    return installed, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the configured ValueList OCR runtime.")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)
    parser.add_argument("--requirements", type=Path, default=DEFAULT_REQUIREMENTS_PATH)
    args = parser.parse_args()

    values = parse_env_file(args.env_file)
    if not ocr_runtime_required(values):
        print("ValueList OCR runtime: disabled by configuration; dependency check skipped.")
        return 0
    try:
        installed, errors = check_ocr_runtime(args.requirements)
    except (OSError, RuntimeError) as exc:
        print(f"ValueList OCR runtime check failed: {exc}", file=sys.stderr)
        return 1
    if errors:
        print("ValueList OCR runtime check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    versions = " ".join(f"{name}={installed[name]}" for name in REQUIRED_DISTRIBUTIONS)
    print(f"ValueList OCR runtime: ready ({versions}; cv2 import ok).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
