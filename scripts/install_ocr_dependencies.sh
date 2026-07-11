#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Python venv not found at $PYTHON_BIN. Run deploy/install first." >&2
  exit 1
fi

echo "Installing version-pinned optional OCR dependencies..."
"$PYTHON_BIN" -m pip install --upgrade -r "$ROOT_DIR/requirements-ocr.txt"

"$PYTHON_BIN" - <<'PY'
import cv2
import numpy
import paddle
import paddleocr

print(
    "OCR runtime:",
    f"paddlepaddle={paddle.__version__}",
    f"paddleocr={getattr(paddleocr, '__version__', 'unknown')}",
    f"numpy={numpy.__version__}",
    f"opencv={cv2.__version__}",
)
PY

echo "OCR dependencies installed. First PaddleOCR run may download official OCR model files into the runtime cache."
