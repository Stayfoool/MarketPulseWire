set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

test:
    python3 -m py_compile scripts/*.py
    python3 scripts/test_analysis.py
    python3 scripts/test_llm_analysis.py
    python3 scripts/test_llm_json_recovery.py
    python3 scripts/test_trendforce_page_monitor.py
    python3 scripts/test_alphabstract_monitor.py
    python3 scripts/test_trade_policy_monitor.py
    python3 scripts/test_trade_friction.py
    python3 scripts/test_link_enrichment.py
    python3 scripts/test_sina_stock_news.py
    python3 scripts/test_china_finance_media_monitor.py
    python3 scripts/test_value_directory_monitor.py
    python3 scripts/test_value_directory_flow.py
    python3 scripts/test_http_utils.py
    python3 scripts/test_rss_monitor_fetch.py
    python3 scripts/test_push_rules.py
    python3 scripts/test_investment_bank_theme_config.py
    python3 scripts/test_international_bank_fed.py
    python3 scripts/test_rule_alert_dedup.py
    python3 scripts/test_macro_event_dedup.py
    python3 scripts/test_industry_fact_dedup.py
    python3 scripts/test_company_event_dedup.py
    python3 scripts/test_cninfo_disclosure_provider.py
    python3 scripts/test_company_disclosures.py
    python3 scripts/test_ai_compute_supply_demand.py
    python3 scripts/test_market_delivery.py
    python3 scripts/test_market_feedback.py
    python3 scripts/test_decision_engine.py
    python3 scripts/test_ai_credit_risk.py
    python3 scripts/test_rule_center.py
    python3 scripts/test_gate_prompts.py
    python3 scripts/test_sina_zy_client.py
    python3 scripts/test_time_utils.py
    python3 scripts/test_x_stream_health.py
    python3 scripts/test_signals_extract.py
    python3 scripts/test_thin_push_cards.py
    python3 scripts/test_wallstreetcn_monitor.py
    python3 scripts/scan_secrets.py

status:
    python3 scripts/status_sync.py

status-strict:
    python3 scripts/status_sync.py --strict

deploy:
    ./scripts/deploy_remote.sh
    ./scripts/install_remote_systemd.sh

remote-timers:
    ssh -i "$REMOTE_SSH_KEY" -o IdentitiesOnly=yes "$REMOTE_USER@$REMOTE_HOST" 'systemctl list-timers --all "surveil-*" --no-pager'

remote-revision:
    ssh -i "$REMOTE_SSH_KEY" -o IdentitiesOnly=yes "$REMOTE_USER@$REMOTE_HOST" "cat '${REMOTE_DIR:-/opt/surveil}/REVISION'"
