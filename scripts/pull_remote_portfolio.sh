#!/usr/bin/env bash
set -euo pipefail

# Pull the private production portfolio from the server to this workstation.
# This intentionally has no local-to-server counterpart: the server Web panel
# is the production source of truth.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=remote_env.sh
source "$SCRIPT_DIR/remote_env.sh"
require_remote_host

LOCAL_PORTFOLIO="$ROOT/config/portfolio.json"
REMOTE_PORTFOLIO="$REMOTE_DIR/config/portfolio.json"
SSH=(ssh -i "$REMOTE_SSH_KEY" -o IdentitiesOnly=yes "$REMOTE_USER@$REMOTE_HOST")
TMP_FILE="$(mktemp "$ROOT/config/.portfolio.remote.XXXXXX")"

cleanup() {
  rm -f "$TMP_FILE"
}
trap cleanup EXIT

echo "==> checking production portfolio on server"
"${SSH[@]}" "test -f '$REMOTE_PORTFOLIO'"

echo "==> downloading private portfolio"
scp -q -i "$REMOTE_SSH_KEY" -o IdentitiesOnly=yes \
  "$REMOTE_USER@$REMOTE_HOST:$REMOTE_PORTFOLIO" "$TMP_FILE"

echo "==> validating downloaded portfolio"
HOLDING_COUNT="$(
  python3 - "$TMP_FILE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"远程持仓 JSON 无法读取：{exc}")
holdings = data.get("holdings") if isinstance(data, dict) else None
if not isinstance(holdings, list):
    raise SystemExit("远程持仓配置缺少 holdings 数组，已停止，不覆盖本地文件。")
if any(not isinstance(item, dict) for item in holdings):
    raise SystemExit("远程 holdings 数组包含非对象项，已停止，不覆盖本地文件。")
print(len(holdings))
PY
)"

if [ -f "$LOCAL_PORTFOLIO" ]; then
  BACKUP_PATH="$LOCAL_PORTFOLIO.bak-$(date +%Y%m%dT%H%M%S)"
  cp -p "$LOCAL_PORTFOLIO" "$BACKUP_PATH"
  echo "==> backed up local private portfolio to $(basename "$BACKUP_PATH")"
fi

chmod 600 "$TMP_FILE"
mv "$TMP_FILE" "$LOCAL_PORTFOLIO"
trap - EXIT

PYTHON="$ROOT/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  PYTHON="python3"
fi

echo "==> importing $HOLDING_COUNT holdings into local SQLite"
"$PYTHON" "$ROOT/scripts/portfolio_import.py" --config "$LOCAL_PORTFOLIO"
echo "完成：本地私有持仓已按服务器生产配置回拉。服务器未被写入。"
