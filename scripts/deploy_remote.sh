#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=remote_env.sh
source "$SCRIPT_DIR/remote_env.sh"
# shellcheck source=remote_code_sync.sh
source "$SCRIPT_DIR/remote_code_sync.sh"
require_remote_host

SSH=(ssh -i "$REMOTE_SSH_KEY" -o IdentitiesOnly=yes "$REMOTE_USER@$REMOTE_HOST")
RSYNC_RSH="ssh -i $REMOTE_SSH_KEY -o IdentitiesOnly=yes"

GIT_COMMIT="$(git rev-parse HEAD 2>/dev/null || printf 'unknown')"
GIT_BRANCH="$(git branch --show-current 2>/dev/null || printf 'unknown')"
GIT_DIRTY="0"
if [ -n "$(git status --porcelain 2>/dev/null || true)" ]; then
  GIT_DIRTY="1"
fi
GIT_ORIGIN_COMMIT="$(git rev-parse origin/${GIT_BRANCH} 2>/dev/null || printf 'unknown')"
DEPLOYED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "==> remote preflight: $REMOTE_USER@$REMOTE_HOST"
"${SSH[@]}" "set -euo pipefail
if [ -e '$REMOTE_DIR' ] && [ ! -d '$REMOTE_DIR' ]; then
  echo '$REMOTE_DIR exists but is not a directory' >&2
  exit 1
fi
id '$REMOTE_SERVICE_USER' >/dev/null 2>&1 || useradd --system --home '$REMOTE_DIR' --shell /usr/sbin/nologin '$REMOTE_SERVICE_USER'
mkdir -p '$REMOTE_DIR' '$REMOTE_DIR/logs' '$REMOTE_DIR/data'
chown -R '$REMOTE_SERVICE_USER:$REMOTE_SERVICE_USER' '$REMOTE_DIR'
python3 --version
"

echo "==> overlay code without deleting paths used by installed systemd units"
remote_code_sync overlay

REVISION_FILE="$(mktemp)"
cleanup_revision() {
  rm -f "$REVISION_FILE"
}
trap cleanup_revision EXIT
cat > "$REVISION_FILE" <<EOF_REVISION
commit=$GIT_COMMIT
branch=$GIT_BRANCH
origin_commit=$GIT_ORIGIN_COMMIT
dirty=$GIT_DIRTY
deployed_at=$DEPLOYED_AT
deployed_by=deploy_remote.sh
EOF_REVISION
scp -i "$REMOTE_SSH_KEY" -o IdentitiesOnly=yes "$REVISION_FILE" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/REVISION" >/dev/null

echo "==> remote venv and schema"
"${SSH[@]}" "set -euo pipefail
cd '$REMOTE_DIR'
python3 -m venv .venv
if [ -f requirements.txt ]; then
  .venv/bin/python -m pip install -r requirements.txt
fi
if ! .venv/bin/python scripts/check_ocr_runtime.py --env-file .env --requirements requirements-ocr.txt; then
  PYTHON_BIN=.venv/bin/python scripts/install_ocr_dependencies.sh
  .venv/bin/python scripts/check_ocr_runtime.py --env-file .env --requirements requirements-ocr.txt
fi
.venv/bin/python scripts/market_db.py
chown -R '$REMOTE_SERVICE_USER:$REMOTE_SERVICE_USER' '$REMOTE_DIR'
chmod 700 '$REMOTE_DIR'
if [ -f '$REMOTE_DIR/.env' ]; then chmod 600 '$REMOTE_DIR/.env'; fi
if [ -f '$REMOTE_DIR/config/llm_decision_rules.json' ]; then chmod 600 '$REMOTE_DIR/config/llm_decision_rules.json'; fi
if [ -f '$REMOTE_DIR/REVISION' ]; then chmod 644 '$REMOTE_DIR/REVISION'; fi
"

echo "代码覆盖同步完成。安装 systemd units 后再运行 prune_remote_code.sh 删除旧路径。"
