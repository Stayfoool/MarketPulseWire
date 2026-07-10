#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=remote_env.sh
source "$SCRIPT_DIR/remote_env.sh"
require_remote_host

SSH=(ssh -i "$REMOTE_SSH_KEY" -o IdentitiesOnly=yes "$REMOTE_USER@$REMOTE_HOST")

echo "==> install browser dependencies for ValueList collector"
"${SSH[@]}" "set -euo pipefail
if ! command -v apt-get >/dev/null 2>&1; then
  echo '当前脚本只支持 apt-get 系统；请手动安装 Chrome/Chromium、xvfb、x11vnc。' >&2
  exit 1
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends chromium-browser xvfb x11vnc xauth dbus-x11 fonts-noto-cjk ca-certificates
mkdir -p '$REMOTE_DIR/data/browser-profiles/valuelist'
chown -R '$REMOTE_SERVICE_USER:$REMOTE_SERVICE_USER' '$REMOTE_DIR/data/browser-profiles'
chmod 700 '$REMOTE_DIR/data/browser-profiles' '$REMOTE_DIR/data/browser-profiles/valuelist'
if [ -x '$REMOTE_DIR/.venv/bin/python' ]; then
  runuser -u '$REMOTE_SERVICE_USER' -- '$REMOTE_DIR/.venv/bin/python' -m playwright install chromium || \
    echo 'Playwright 官方 Chromium 下载失败；将尝试使用系统 Chrome/Chromium。'
fi
if command -v chromium-browser >/dev/null 2>&1; then
  echo 'chromium-browser='\"\$(command -v chromium-browser)\"
elif command -v chromium >/dev/null 2>&1; then
  echo 'chromium='\"\$(command -v chromium)\"
elif command -v google-chrome-stable >/dev/null 2>&1; then
  echo 'google-chrome-stable='\"\$(command -v google-chrome-stable)\"
else
  echo '浏览器安装后仍未找到 chromium/chrome。' >&2
  exit 1
fi
command -v Xvfb
command -v x11vnc
"

echo "已安装/确认浏览器依赖，并创建服务器私有 profile：$REMOTE_DIR/data/browser-profiles/valuelist"
echo "下一步运行：./scripts/open_value_directory_login.sh"
