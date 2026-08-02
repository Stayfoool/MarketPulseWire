#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=remote_env.sh
source "$SCRIPT_DIR/remote_env.sh"
require_remote_host

SSH=(ssh -i "$REMOTE_SSH_KEY" -o IdentitiesOnly=yes "$REMOTE_USER@$REMOTE_HOST")

echo "==> strict read-only production verification"
"${SSH[@]}" "sudo -u '$REMOTE_SERVICE_USER' env \
  SURVEIL_ROOT='$REMOTE_DIR' \
  SURVEIL_SERVICE_USER='$REMOTE_SERVICE_USER' \
  '$REMOTE_DIR/.venv/bin/python' '$REMOTE_DIR/scripts/verify_production.py'"
