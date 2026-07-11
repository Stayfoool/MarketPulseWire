#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Python venv not found at $PYTHON_BIN. Run deploy/install first." >&2
  exit 1
fi

echo "Installing optional OCR dependencies from official PyPI packages..."
"$PYTHON_BIN" -m pip install -r "$ROOT_DIR/requirements-ocr.txt"

echo "OCR dependencies installed. First PaddleOCR run may download official OCR model files into the runtime cache."
