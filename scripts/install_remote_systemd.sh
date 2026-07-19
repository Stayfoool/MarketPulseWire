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
cp /tmp/surveil-systemd/*.service /etc/systemd/system/
cp /tmp/surveil-systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
SYSTEMCTL_BIN=\"\$(command -v systemctl)\"
SUDOERS_PATH=/etc/sudoers.d/surveil-web-systemctl
cat > \"\$SUDOERS_PATH\" <<SUDOERS
Cmnd_Alias SURVEIL_WEB_SYSTEMCTL = \\
    \$SYSTEMCTL_BIN --no-block restart surveil-x-stream.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-feishu-feedback.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-rss-monitor.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-trendforce-page-monitor.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-sina-flash.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-overseas-media.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-china-media.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-sina-stock-news.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-article-daily.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-rule-shadow-daily.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-signals-extract.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-signal-outcome.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-signal-review.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-signal-digest.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-research-collector.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-official-collector.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-news-collector.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-value-directory.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-research-collector-shadow.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-official-collector-shadow.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-news-collector-shadow.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-collector-shadow-digest.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-proxy.service, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-sina-stock-news.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-overseas-media.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-china-media.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-article-daily.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-rule-shadow-daily.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-signals-extract.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-signal-outcome.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-signal-review.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-signal-digest.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-company-disclosures.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-jygs-actions.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-research-collector.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-official-collector.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-news-collector.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-value-directory.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-research-collector-shadow.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-official-collector-shadow.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-news-collector-shadow.timer, \\
    \$SYSTEMCTL_BIN --no-block restart surveil-collector-shadow-digest.timer, \\
    \$SYSTEMCTL_BIN --no-block start surveil-sina-stock-news.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-overseas-media.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-china-media.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-article-daily.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-rule-shadow-daily.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-signals-extract.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-signal-outcome.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-signal-review.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-signal-digest.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-company-disclosures.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-jygs-actions.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-research-collector.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-official-collector.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-news-collector.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-value-directory.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-research-collector-shadow.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-official-collector-shadow.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-news-collector-shadow.service, \\
    \$SYSTEMCTL_BIN --no-block start surveil-collector-shadow-digest.service
$REMOTE_SERVICE_USER ALL=(root) NOPASSWD: SURVEIL_WEB_SYSTEMCTL
SUDOERS
chmod 0440 \"\$SUDOERS_PATH\"
visudo -cf \"\$SUDOERS_PATH\" >/dev/null
systemctl enable surveil-db-init.service
systemctl start surveil-db-init.service
systemctl is-enabled surveil-db-init.service
journalctl -u surveil-db-init.service -n 20 --no-pager
systemctl disable --now surveil-ifind-notice.timer >/dev/null 2>&1 || true
systemctl stop surveil-ifind-notice.service >/dev/null 2>&1 || true
rm -f /etc/systemd/system/surveil-ifind-notice.timer /etc/systemd/system/surveil-ifind-notice.service
systemctl daemon-reload
systemctl enable --now surveil-company-disclosures.timer
systemctl enable --now surveil-sina-stock-news.timer
if grep -Eq '^DISABLE_LEGACY_RESEARCH_MONITORS=1$' '$REMOTE_DIR/.env' 2>/dev/null; then
  systemctl disable --now surveil-overseas-media.timer >/dev/null 2>&1 || true
  systemctl stop surveil-overseas-media.service >/dev/null 2>&1 || true
  systemctl enable --now surveil-research-collector.timer
  echo 'DISABLE_LEGACY_RESEARCH_MONITORS=1，保持旧 surveil-overseas-media.timer 停用。'
else
  systemctl enable --now surveil-overseas-media.timer
fi
if grep -Eq '^DISABLE_LEGACY_CHINA_MEDIA_MONITOR=1$' '$REMOTE_DIR/.env' 2>/dev/null; then
  systemctl disable --now surveil-china-media.timer >/dev/null 2>&1 || true
  systemctl stop surveil-china-media.service >/dev/null 2>&1 || true
  systemctl enable --now surveil-news-collector.timer
  echo 'DISABLE_LEGACY_CHINA_MEDIA_MONITOR=1，保持旧 surveil-china-media.timer 停用。'
else
  systemctl enable --now surveil-china-media.timer
fi
systemctl enable --now surveil-article-daily.timer
if grep -Eq '^RULE_CORE_SHADOW_AUTORUN=(1|true|yes|on)$' '$REMOTE_DIR/.env' 2>/dev/null; then
  systemctl enable --now surveil-rule-shadow-daily.timer
else
  systemctl disable --now surveil-rule-shadow-daily.timer >/dev/null 2>&1 || true
  echo 'RULE_CORE_SHADOW_AUTORUN 未启用，保持规则对比日报定时器停用。'
fi
systemctl enable --now surveil-signals-extract.timer
systemctl enable --now surveil-signal-outcome.timer
systemctl enable --now surveil-signal-review.timer
systemctl enable --now surveil-signal-digest.timer
systemctl disable --now surveil-research-collector-shadow.timer >/dev/null 2>&1 || true
systemctl disable --now surveil-official-collector-shadow.timer >/dev/null 2>&1 || true
systemctl disable --now surveil-news-collector-shadow.timer >/dev/null 2>&1 || true
systemctl disable --now surveil-collector-shadow-digest.timer >/dev/null 2>&1 || true
systemctl stop surveil-research-collector-shadow.service >/dev/null 2>&1 || true
systemctl stop surveil-official-collector-shadow.service >/dev/null 2>&1 || true
systemctl stop surveil-news-collector-shadow.service >/dev/null 2>&1 || true
systemctl stop surveil-collector-shadow-digest.service >/dev/null 2>&1 || true
echo 'Collector shadow timers are installed but disabled by default.'
systemctl start surveil-stock-relations-import.service || true
if grep -Eq '^DISABLE_LEGACY_RSS_MONITOR=1$' '$REMOTE_DIR/.env' 2>/dev/null; then
  systemctl disable --now surveil-rss-monitor.service >/dev/null 2>&1 || true
  systemctl enable --now surveil-official-collector.timer
  echo 'DISABLE_LEGACY_RSS_MONITOR=1，保持旧 surveil-rss-monitor.service 停用。'
else
  systemctl enable --now surveil-rss-monitor.service
fi
if grep -Eq '^DISABLE_LEGACY_RESEARCH_MONITORS=1$' '$REMOTE_DIR/.env' 2>/dev/null; then
  systemctl disable --now surveil-trendforce-page-monitor.service >/dev/null 2>&1 || true
  systemctl enable --now surveil-research-collector.timer
  echo 'DISABLE_LEGACY_RESEARCH_MONITORS=1，保持旧 surveil-trendforce-page-monitor.service 停用。'
else
  systemctl enable --now surveil-trendforce-page-monitor.service
fi
systemctl disable --now surveil-ifind-report.timer >/dev/null 2>&1 || true
rm -f /etc/systemd/system/surveil-ifind-report.timer /etc/systemd/system/surveil-ifind-report.service
systemctl daemon-reload
if grep -Eq '^ENABLE_JYGS_TIMER=1$' '$REMOTE_DIR/.env' 2>/dev/null; then
  systemctl enable --now surveil-jygs-actions.timer
else
  systemctl disable --now surveil-jygs-actions.timer >/dev/null 2>&1 || true
  echo '韭研公社异动模块当前默认搁置；如需启用，请在 .env 设置 ENABLE_JYGS_TIMER=1。'
fi
systemctl enable --now surveil-holdings-web.service
if grep -Eq '^FEISHU_FEEDBACK_(LISTENER_)?ENABLED=(1|true|yes|on)$' '$REMOTE_DIR/.env' 2>/dev/null; then
  systemctl enable --now surveil-feishu-feedback.service
else
  systemctl disable --now surveil-feishu-feedback.service >/dev/null 2>&1 || true
  echo 'FEISHU_FEEDBACK_LISTENER_ENABLED / FEISHU_FEEDBACK_ENABLED 未启用，保持 surveil-feishu-feedback.service 停用。'
fi
systemctl enable surveil-sina-flash.service
systemctl restart surveil-sina-flash.service
if grep -Eq '^X_BEARER_TOKEN=[^[:space:]]+' '$REMOTE_DIR/.env' 2>/dev/null; then
  systemctl enable --now surveil-x-stream.service
else
  systemctl disable --now surveil-x-stream.service >/dev/null 2>&1 || true
  echo 'X_BEARER_TOKEN 未配置，保持 surveil-x-stream.service 停用。'
fi
systemctl list-timers --all 'surveil-*' --no-pager
systemctl --no-pager --full status surveil-sina-flash.service || true
systemctl --no-pager --full status surveil-holdings-web.service || true
systemctl --no-pager --full status surveil-feishu-feedback.service || true
systemctl --no-pager --full status surveil-rss-monitor.service || true
systemctl --no-pager --full status surveil-trendforce-page-monitor.service || true
systemctl --no-pager --full status surveil-research-collector.timer || true
systemctl --no-pager --full status surveil-official-collector.timer || true
systemctl --no-pager --full status surveil-news-collector.timer || true
systemctl --no-pager --full status surveil-value-directory.timer || true
systemctl --no-pager --full status surveil-rule-shadow-daily.timer || true
systemctl --no-pager --full status surveil-research-collector-shadow.timer || true
systemctl --no-pager --full status surveil-official-collector-shadow.timer || true
systemctl --no-pager --full status surveil-news-collector-shadow.timer || true
systemctl --no-pager --full status surveil-x-stream.service || true
echo '已安装 surveil-db-init.service，启用公司公告、Sina 个股新闻、生产 collector timers（按 DISABLE_LEGACY_* 切换生产/历史入口）、文章日报、信号抽取/outcome/复盘/复盘日报、持仓 Web UI，并启动新浪快讯常驻服务；report-only collector shadow timers 默认停用。公司公告默认 report_only，可在来源配置审阅后切换 live。'
"
