#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=remote_env.sh
source "$SCRIPT_DIR/remote_env.sh"
require_remote_host

SSH=(ssh -i "$REMOTE_SSH_KEY" -o IdentitiesOnly=yes "$REMOTE_USER@$REMOTE_HOST")
RSYNC_RSH="ssh -i $REMOTE_SSH_KEY -o IdentitiesOnly=yes"

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

escape_sed_replacement() {
  printf '%s' "$1" | sed -e 's/[\/&]/\\&/g'
}

echo "==> render and sync systemd units"
RENDERED_SYSTEMD="$TMP_DIR/systemd"
mkdir -p "$RENDERED_SYSTEMD"
REMOTE_DIR_ESCAPED="$(escape_sed_replacement "$REMOTE_DIR")"
REMOTE_PROXY_DIR_ESCAPED="$(escape_sed_replacement "$REMOTE_PROXY_DIR")"
REMOTE_SERVICE_USER_ESCAPED="$(escape_sed_replacement "$REMOTE_SERVICE_USER")"
for unit in ./systemd/*.service ./systemd/*.timer; do
  sed \
    -e "s/User=surveil/User=$REMOTE_SERVICE_USER_ESCAPED/g" \
    -e "s/\/opt\/surveil-proxy/$REMOTE_PROXY_DIR_ESCAPED/g" \
    -e "s/\/opt\/surveil/$REMOTE_DIR_ESCAPED/g" \
    "$unit" > "$RENDERED_SYSTEMD/$(basename "$unit")"
done
"${SSH[@]}" "rm -rf /tmp/surveil-systemd && mkdir -p /tmp/surveil-systemd"
rsync -az -e "$RSYNC_RSH" "$RENDERED_SYSTEMD/" "$REMOTE_USER@$REMOTE_HOST:/tmp/surveil-systemd/"

echo "==> install units"
"${SSH[@]}" "set -euo pipefail
RULE_CORE_CONFIG_PATH=\"\$(sed -n 's/^RULE_CORE_CONFIG=//p' '$REMOTE_DIR/.env' | tail -n 1)\"
if [ -z \"\$RULE_CORE_CONFIG_PATH\" ] || [ ! -f \"\$RULE_CORE_CONFIG_PATH\" ]; then
  echo 'RULE_CORE_CONFIG 未配置或文件不存在，停止启动生产采集服务。' >&2
  exit 1
fi
if ! sudo -u '$REMOTE_SERVICE_USER' test -r \"\$RULE_CORE_CONFIG_PATH\"; then
  echo 'RULE_CORE_CONFIG 对生产服务账号不可读，停止启动生产采集服务。' >&2
  exit 1
fi
LLM_DECISION_RULE_CONFIG_PATH=\"\$(sed -n 's/^LLM_DECISION_RULE_CONFIG=//p' '$REMOTE_DIR/.env' | tail -n 1)\"
if [ -z \"\$LLM_DECISION_RULE_CONFIG_PATH\" ] || [ ! -f \"\$LLM_DECISION_RULE_CONFIG_PATH\" ]; then
  echo 'LLM_DECISION_RULE_CONFIG 未配置或文件不存在，停止启动生产采集服务。' >&2
  exit 1
fi
if [ \"\$(stat -c '%a' \"\$LLM_DECISION_RULE_CONFIG_PATH\")\" != '600' ]; then
  echo 'LLM_DECISION_RULE_CONFIG 文件权限必须为 0600，停止启动生产采集服务。' >&2
  exit 1
fi
if [ \"\$(stat -c '%U' \"\$LLM_DECISION_RULE_CONFIG_PATH\")\" != '$REMOTE_SERVICE_USER' ]; then
  echo 'LLM_DECISION_RULE_CONFIG 文件所有者必须为生产服务账号，停止启动生产采集服务。' >&2
  exit 1
fi
if ! sudo -u '$REMOTE_SERVICE_USER' test -r \"\$LLM_DECISION_RULE_CONFIG_PATH\"; then
  echo 'LLM_DECISION_RULE_CONFIG 对生产服务账号不可读，停止启动生产采集服务。' >&2
  exit 1
fi
if ! cd '$REMOTE_DIR' || ! sudo -u '$REMOTE_SERVICE_USER' env \
  PYTHONPATH='$REMOTE_DIR/scripts' \
  LLM_DECISION_RULE_CONFIG=\"\$LLM_DECISION_RULE_CONFIG_PATH\" \
  '$REMOTE_DIR/.venv/bin/python' -c 'import llm_rule_catalog'; then
  echo 'LLM_DECISION_RULE_CONFIG 内容校验失败，停止启动生产采集服务。' >&2
  exit 1
fi
cp /tmp/surveil-systemd/*.service /etc/systemd/system/
cp /tmp/surveil-systemd/*.timer /etc/systemd/system/
RETIRED_TIMERS=(
  surveil-overseas-media.timer
  surveil-china-media.timer
  surveil-signals-extract.timer
  surveil-signal-outcome.timer
  surveil-signal-review.timer
  surveil-signal-digest.timer
  surveil-jygs-actions.timer
  surveil-ifind-notice.timer
  surveil-ifind-report.timer
  surveil-research-collector-shadow.timer
  surveil-official-collector-shadow.timer
  surveil-news-collector-shadow.timer
  surveil-collector-shadow-digest.timer
  surveil-rule-shadow-daily.timer
)
RETIRED_SERVICES=(
  surveil-rss-monitor.service
  surveil-trendforce-page-monitor.service
  surveil-overseas-media.service
  surveil-china-media.service
  surveil-signals-extract.service
  surveil-signal-outcome.service
  surveil-signal-review.service
  surveil-signal-digest.service
  surveil-jygs-actions.service
  surveil-ifind-smoke.service
  surveil-ifind-notice.service
  surveil-ifind-report.service
  surveil-research-collector-shadow.service
  surveil-official-collector-shadow.service
  surveil-news-collector-shadow.service
  surveil-collector-shadow-digest.service
  surveil-rule-shadow-daily.service
)
systemctl disable --now "\${RETIRED_TIMERS[@]}" >/dev/null 2>&1 || true
systemctl stop "\${RETIRED_SERVICES[@]}" >/dev/null 2>&1 || true
for unit in "\${RETIRED_TIMERS[@]}" "\${RETIRED_SERVICES[@]}"; do
  rm -f "/etc/systemd/system/\$unit"
done
systemctl daemon-reload
install -d -m 700 -o '$REMOTE_SERVICE_USER' -g '$REMOTE_SERVICE_USER' '$REMOTE_DIR/logs'
RETIRED_LOGS=(
  rss-monitor.log
  rss-monitor.err.log
  trendforce-page-monitor.log
  trendforce-page-monitor.err.log
  overseas-media.log
  overseas-media.err.log
  china-media.log
  china-media.err.log
  signals-extract.log
  signals-extract.err.log
  signal-outcome.log
  signal-outcome.err.log
  signal-review.log
  signal-review.err.log
  signal-digest.log
  signal-digest.err.log
  jygs-actions.log
  jygs-actions.err.log
  ifind-smoke.log
  ifind-smoke.err.log
)
for log_name in "\${RETIRED_LOGS[@]}"; do
  rm -f '$REMOTE_DIR/logs/'"\$log_name"
done
find '$REMOTE_DIR/logs' -maxdepth 1 -type f -exec chown '$REMOTE_SERVICE_USER:$REMOTE_SERVICE_USER' {} +
find '$REMOTE_DIR/logs' -maxdepth 1 -type f -exec chmod 600 {} +
SYSTEMCTL_BIN=\"\$(command -v systemctl)\"
SUDOERS_PATH=/etc/sudoers.d/surveil-web-systemctl
cat > \"\$SUDOERS_PATH\" <<SUDOERS
Cmnd_Alias SURVEIL_WEB_SYSTEMCTL = \\
    \$SYSTEMCTL_BIN --no-block restart surveil-x-stream.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-feishu-feedback.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-sina-flash.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-sina-stock-news.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-article-daily.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-llm-decision-audit-cleanup.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-research-collector.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-official-collector.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-news-collector.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-value-directory.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-proxy.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-sina-stock-news.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-article-daily.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-llm-decision-audit-cleanup.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-company-disclosures.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-research-collector.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-official-collector.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-news-collector.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-value-directory.timer, \\
    \$SYSTEMCTL_BIN --no-block start surveil-sina-stock-news.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-article-daily.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-llm-decision-audit-cleanup.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-company-disclosures.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-research-collector.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-official-collector.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-news-collector.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-value-directory.service
$REMOTE_SERVICE_USER ALL=(root) NOPASSWD: SURVEIL_WEB_SYSTEMCTL
SUDOERS
chmod 0440 \"\$SUDOERS_PATH\"
visudo -cf \"\$SUDOERS_PATH\" >/dev/null
systemctl enable surveil-db-init.service
systemctl start surveil-db-init.service
systemctl is-enabled surveil-db-init.service
journalctl -u surveil-db-init.service -n 20 --no-pager
systemctl enable --now surveil-company-disclosures.timer
systemctl enable --now surveil-sina-stock-news.timer
systemctl restart surveil-sina-stock-news.timer
if systemctl is-enabled --quiet surveil-value-directory.timer; then
  systemctl restart surveil-value-directory.timer
fi
systemctl enable --now surveil-research-collector.timer
systemctl enable --now surveil-official-collector.timer
systemctl enable --now surveil-news-collector.timer
systemctl enable --now surveil-article-daily.timer
systemctl enable --now surveil-llm-decision-audit-cleanup.timer
echo '已启用每日 30 天大模型决策审计清理。'
systemctl start surveil-stock-relations-import.service || true
systemctl enable --now surveil-holdings-web.service
if grep -Eq '^FEISHU_FEEDBACK_(LISTENER_)?ENABLED=(1|true|yes|on)$' '$REMOTE_DIR/.env' 2>/dev/null; then
  touch '$REMOTE_DIR/logs/feishu-feedback.log' '$REMOTE_DIR/logs/feishu-feedback.err.log'
  chown '$REMOTE_SERVICE_USER:$REMOTE_SERVICE_USER' \
    '$REMOTE_DIR/logs/feishu-feedback.log' '$REMOTE_DIR/logs/feishu-feedback.err.log'
  chmod 600 '$REMOTE_DIR/logs/feishu-feedback.log' '$REMOTE_DIR/logs/feishu-feedback.err.log'
  systemctl enable --now surveil-feishu-feedback.service
  systemctl restart surveil-feishu-feedback.service
else
  systemctl disable --now surveil-feishu-feedback.service >/dev/null 2>&1 || true
  echo 'FEISHU_FEEDBACK_LISTENER_ENABLED / FEISHU_FEEDBACK_ENABLED 未启用，保持 surveil-feishu-feedback.service 停用。'
fi
systemctl enable surveil-sina-flash.service
systemctl restart surveil-sina-flash.service
if grep -Eq '^X_BEARER_TOKEN=[^[:space:]]+' '$REMOTE_DIR/.env' 2>/dev/null; then
  systemctl enable --now surveil-x-stream.service
  systemctl restart surveil-x-stream.service
else
  systemctl disable --now surveil-x-stream.service >/dev/null 2>&1 || true
  echo 'X_BEARER_TOKEN 未配置，保持 surveil-x-stream.service 停用。'
fi
systemctl list-timers --all 'surveil-*' --no-pager
systemctl --no-pager --full status surveil-sina-flash.service || true
systemctl --no-pager --full status surveil-holdings-web.service || true
systemctl --no-pager --full status surveil-feishu-feedback.service || true
systemctl --no-pager --full status surveil-research-collector.timer || true
systemctl --no-pager --full status surveil-official-collector.timer || true
systemctl --no-pager --full status surveil-news-collector.timer || true
systemctl --no-pager --full status surveil-value-directory.timer || true
systemctl --no-pager --full status surveil-llm-decision-audit-cleanup.timer || true
systemctl --no-pager --full status surveil-x-stream.service || true
REVISION_COMMIT=\"\$(sed -n 's/^commit=//p' '$REMOTE_DIR/REVISION' | tail -n 1)\"
if [ -z \"\$REVISION_COMMIT\" ]; then
  echo '部署 revision 缺失，不写入 systemd 安装完成标记。' >&2
  exit 1
fi
SYSTEMD_MARKER='$REMOTE_DIR/data/systemd-installed-revision'
SYSTEMD_MARKER_TMP=\"\$SYSTEMD_MARKER.tmp.\$\$\"
printf 'commit=%s\ninstalled_at=%s\n' \"\$REVISION_COMMIT\" \"\$(date -u +%Y-%m-%dT%H:%M:%SZ)\" > \"\$SYSTEMD_MARKER_TMP\"
chown '$REMOTE_SERVICE_USER:$REMOTE_SERVICE_USER' \"\$SYSTEMD_MARKER_TMP\"
chmod 600 \"\$SYSTEMD_MARKER_TMP\"
mv \"\$SYSTEMD_MARKER_TMP\" \"\$SYSTEMD_MARKER\"
echo '已安装生产 systemd 单元，启用公司公告、Sina 个股新闻、三个统一 collector timer、文章日报、大模型审计清理和持仓 Web UI，并启动新浪快讯常驻服务；已删除退役采集入口、投资信号复盘、JYGS、iFinD 和影子任务单元。'
"
