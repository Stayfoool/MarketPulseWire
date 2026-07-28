#!/usr/bin/env bash

# Shared rsync contract for deploy overlay and post-systemd stale-file pruning.
remote_code_sync() {
  local mode="${1:-}"
  local -a rsync_args=(-az)
  case "$mode" in
    overlay) ;;
    prune) rsync_args+=(--delete) ;;
    *)
      echo "remote_code_sync mode must be overlay or prune" >&2
      return 2
      ;;
  esac

  : "${RSYNC_RSH:?RSYNC_RSH is required}"
  : "${REMOTE_USER:?REMOTE_USER is required}"
  : "${REMOTE_HOST:?REMOTE_HOST is required}"
  : "${REMOTE_DIR:?REMOTE_DIR is required}"

  local private_proxy_prefix="shadowsocks_"
  local private_proxy_yaml_pattern="${private_proxy_prefix}*.yaml"
  rsync "${rsync_args[@]}" \
    --include '.env.example' \
    --exclude '.env' \
    --exclude '.env.*' \
    --exclude '.git/' \
    --exclude 'REVISION' \
    --exclude 'proxy.env' \
    --exclude "$private_proxy_yaml_pattern" \
    --exclude 'config/portfolio.json' \
    --exclude 'config/media_keywords.json' \
    --exclude 'config/investment_bank_theme_rules.json' \
    --exclude 'config/llm_decision_rules.json' \
    --exclude 'config/push_rules.local.json' \
    --exclude 'config/source_profiles.local.json' \
    --exclude 'config/stock_relations.json' \
    --exclude 'config/market_skill/' \
    --exclude '.venv' \
    --exclude '.cache/' \
    --exclude '.paddleocr/' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'data/' \
    --exclude 'logs/' \
    --exclude 'reports/' \
    --exclude 'docs/monitoring-plan.md' \
    --exclude '.DS_Store' \
    -e "$RSYNC_RSH" \
    ./ "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/"
}
