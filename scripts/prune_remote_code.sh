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
SYSTEMD_REVISION_MARKER="$REMOTE_DIR/data/systemd-installed-revision"

echo "==> verify systemd installation revision before pruning"
"${SSH[@]}" "set -euo pipefail
DEPLOYED_COMMIT=\"\$(sed -n 's/^commit=//p' '$REMOTE_DIR/REVISION' | tail -n 1)\"
INSTALLED_COMMIT=\"\$(sed -n 's/^commit=//p' '$SYSTEMD_REVISION_MARKER' | tail -n 1)\"
if [ -z \"\$DEPLOYED_COMMIT\" ] || [ -z \"\$INSTALLED_COMMIT\" ]; then
  echo '部署 revision 或 systemd 安装 revision 缺失，拒绝删除旧代码。' >&2
  exit 1
fi
if [ \"\$DEPLOYED_COMMIT\" != \"\$INSTALLED_COMMIT\" ]; then
  echo 'systemd 尚未安装当前 revision，拒绝删除旧代码。' >&2
  exit 1
fi
"

echo "==> prune code removed by the current revision"
remote_code_sync prune

echo "==> restore deployment root ownership and mode"
"${SSH[@]}" "set -euo pipefail
chown -R '$REMOTE_SERVICE_USER:$REMOTE_SERVICE_USER' '$REMOTE_DIR'
chmod 700 '$REMOTE_DIR'
"
