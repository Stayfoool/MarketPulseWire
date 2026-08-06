#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=remote_env.sh
source "$SCRIPT_DIR/remote_env.sh"
require_remote_host

VNC_PORT="${X_BROWSER_VNC_PORT:-5909}"
DISPLAY_ID="${X_BROWSER_DISPLAY_ID:-99}"
PROFILE_DIR="${X_BROWSER_PROFILE_DIR:-$REMOTE_DIR/data/browser-profiles/x}"
URL="https://x.com/home"

echo "请先在另一个终端打开 SSH 隧道："
echo "ssh -N -L ${VNC_PORT}:127.0.0.1:${VNC_PORT} -i \"\$REMOTE_SSH_KEY\" -o IdentitiesOnly=yes \"\$REMOTE_USER@\$REMOTE_HOST\""
echo
echo "然后在 Mac 打开：vnc://127.0.0.1:${VNC_PORT}"
echo "在远程浏览器里手动登录 X。登录完成后关闭远程浏览器或回到本终端按 Ctrl-C。"
echo
read -r -p "确认 SSH 隧道已打开后，按回车启动远程临时浏览器登录窗口..."

ssh -i "$REMOTE_SSH_KEY" -o IdentitiesOnly=yes "$REMOTE_USER@$REMOTE_HOST" \
  "REMOTE_DIR='$REMOTE_DIR' REMOTE_SERVICE_USER='$REMOTE_SERVICE_USER' PROFILE_DIR='$PROFILE_DIR' VNC_PORT='$VNC_PORT' DISPLAY_ID='$DISPLAY_ID' URL='$URL' bash -s" <<'REMOTE'
set -euo pipefail

command -v Xvfb >/dev/null 2>&1 || { echo "缺少 Xvfb。请先运行 scripts/install_value_directory_browser.sh 安装共享浏览器依赖。" >&2; exit 1; }
command -v x11vnc >/dev/null 2>&1 || { echo "缺少 x11vnc。请先运行 scripts/install_value_directory_browser.sh 安装共享浏览器依赖。" >&2; exit 1; }
command -v openssl >/dev/null 2>&1 || { echo "缺少 openssl，无法生成临时 VNC 密码。" >&2; exit 1; }
[ -x "$REMOTE_DIR/.venv/bin/python" ] || { echo "缺少 $REMOTE_DIR/.venv/bin/python。请先部署项目。" >&2; exit 1; }

mkdir -p "$PROFILE_DIR"
chown -R "$REMOTE_SERVICE_USER:$REMOTE_SERVICE_USER" "$PROFILE_DIR"
chmod 700 "$PROFILE_DIR"

cleanup() {
  if [ -n "${browser_pid:-}" ]; then kill "$browser_pid" >/dev/null 2>&1 || true; fi
  if [ -n "${vnc_pid:-}" ]; then kill "$vnc_pid" >/dev/null 2>&1 || true; fi
  if [ -n "${xvfb_pid:-}" ]; then kill "$xvfb_pid" >/dev/null 2>&1 || true; fi
  if [ -n "${vnc_password_file:-}" ] && [ -e "$vnc_password_file" ]; then rm "$vnc_password_file"; fi
}
trap cleanup EXIT INT TERM

export DISPLAY=":$DISPLAY_ID"
Xvfb "$DISPLAY" -screen 0 1280x900x24 -nolisten tcp >/tmp/surveil-x-browser-xvfb.log 2>&1 &
xvfb_pid=$!
sleep 1
umask 077
vnc_password_file="$(mktemp /tmp/surveil-x-browser-vnc.XXXXXX)"
vnc_password="$(openssl rand -hex 4)"
x11vnc -storepasswd "$vnc_password" "$vnc_password_file" >/dev/null 2>&1
x11vnc -display "$DISPLAY" -localhost -rfbport "$VNC_PORT" -forever -shared -rfbauth "$vnc_password_file" >/tmp/surveil-x-browser-x11vnc.log 2>&1 &
vnc_pid=$!
sleep 1

if [ -f "$REMOTE_DIR/proxy.env" ]; then
  set -a
  . "$REMOTE_DIR/proxy.env"
  set +a
fi
proxy_env=()
for key in HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy; do
  value="${!key:-}"
  if [ -n "$value" ]; then proxy_env+=("$key=$value"); fi
done

echo "远程 VNC 已启动在 127.0.0.1:$VNC_PORT。临时 VNC 密码：$vnc_password"
runuser -u "$REMOTE_SERVICE_USER" -- env \
  "${proxy_env[@]}" \
  DISPLAY="$DISPLAY" \
  PLAYWRIGHT_BROWSERS_PATH="$REMOTE_DIR/data/ms-playwright" \
  X_BROWSER_PROFILE_DIR="$PROFILE_DIR" \
  X_BROWSER_HEADLESS=0 \
  "$REMOTE_DIR/.venv/bin/python" "$REMOTE_DIR/scripts/x_browser_login.py" --url "$URL" \
  >/tmp/surveil-x-browser-login.log 2>&1 &
browser_pid=$!
wait "$browser_pid"
REMOTE
