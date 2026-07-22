# MarketPulseWire

MarketPulseWire is an event-driven market and industry monitoring system for personal research. It watches holdings, watchlists, official company/news sources, filings/notices, RSS feeds, X accounts, and selected industry media; then uses source profiles, deterministic rules, restricted LLM interpretation, and Skeptic checks to decide delivery, produce concise summaries, and send alerts to Feishu or a local Web workbench.

MarketPulseWire is not an investment adviser and does not generate buy/sell recommendations.

## Why This Exists

Market-moving semiconductor and AI infrastructure signals are scattered across X, sell-side-style research headlines, official company blogs, regional supply-chain media, company notices, and paid/authorized data services. MarketPulseWire turns that messy stream into a self-hosted research radar:

- Track your own holdings and adjacent supply-chain names.
- Watch high-signal sources such as Serenity on X, SEMI, TrendForce, DIGITIMES, Nikkei xTECH, The Elec, OpenAI, NVIDIA, Samsung, SK hynix, Micron, Sina Finance, WallstreetCN, iFinD, and JYGS.
- Use a rule-first decision layer to decide what deserves immediate attention and what can wait for a daily digest.
- Keep credentials and personal research data on your own machine or server.

## Features

- Holdings/watchlist management through a local-only Web workbench
- Sina Finance news adapters for holdings-related news
- iFinD notice ingestion and PDF text extraction
- X account monitoring through official API credentials
- RSS/Atom/RDF monitoring for official company and industry sources
- DIGITIMES, Nikkei xTECH, The Elec, TrendForce-style media adapters
- Rule-first decision layer, freshness checks, and thin structured summaries
- Feishu card delivery
- Linux systemd deployment and macOS launchd templates
- GitHub Actions CI and optional SSH deployment workflow

## Built-In Source Radar

MarketPulseWire keeps a public, reusable source catalog for semiconductor and AI infrastructure monitoring:

| Source | Why It Matters |
| --- | --- |
| Serenity on X | High-signal public market commentary around AI infrastructure, photonics, memory, CPO/optical interconnects, and global semiconductor equities. |
| TrendForce | Widely followed supply-chain research source for memory, HBM, MLCC, foundry, panels, LEDs, batteries, AI servers, and component pricing. |
| DIGITIMES | Taiwan-centered supply-chain media with early signals from foundries, IC design, packaging, servers, AI hardware, and electronics manufacturing. |
| Nikkei xTECH | Japan technology and manufacturing coverage, useful for materials, components, equipment, automotive electronics, and industrial technology shifts. |
| The Elec | Korea-centered semiconductor/display/battery supply-chain media, useful for Samsung, SK hynix, OLED, memory, equipment, and materials signals. |
| OpenAI / NVIDIA / Samsung / SK hynix / Micron official feeds | First-party product, architecture, capex, platform, memory, and AI infrastructure announcements. |
| Sina Finance / WallstreetCN / iFinD / JYGS | China-market and global-macro news, company notices, announcements, and A-share event/opportunity tracking. |

See [docs/sources.md](docs/sources.md) for URLs, access methods, and compliance notes.

## Quick Start

```bash
git clone https://github.com/<you>/<repo>.git
cd <repo>
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp config/portfolio.example.json config/portfolio.json
python scripts/market_db.py
```

Set `RULE_CORE_SHADOW_CONFIG` in the private `.env` to a complete private global
rule configuration before using media collection or the Web `媒体关键词` page.
The repository `config/rule_core_v1.test.json` is a test fixture, not a
production configuration.

Start the Web workbench:

```bash
python scripts/holdings_web.py --host 127.0.0.1 --port 8787
```

Open:

```text
http://127.0.0.1:8787
```

For production, run Surveil on a Linux server with systemd. See [docs/deployment.md](docs/deployment.md).

## Configuration

Copy `.env.example` to `.env` and fill only the sources you use.

The preferred LLM configuration is:

```env
LLM_PROVIDER=openai_compatible
LLM_API_KEY=<your_api_key>
LLM_BASE_URL=https://api.example.com/v1
LLM_MODEL=your-model-name
LLM_TIMEOUT_SECONDS=90
LLM_RETRY_COUNT=2
```

Examples:

```env
# DeepSeek OpenAI-compatible
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

```env
# Zhipu GLM Coding Plan / Token Plan
LLM_BASE_URL=https://api.z.ai/api/coding/paas/v4
LLM_MODEL=glm-5.2
```

```env
# Aliyun compatible mode
LLM_BASE_URL=https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
LLM_MODEL=glm-5.2
```

Legacy `OPENAI_*` and `DASHSCOPE_*` variables are kept as compatibility aliases, but new setups should use `LLM_*`.

## Data Sources

MarketPulseWire is designed around official or authorized access paths:

- Sina Finance public pages, with optional paid OpenAPI support
- iFinD REST/API access with your account token
- X API tokens for the account you monitor
- Official RSS feeds and public list pages
- Optional logged-in cookies only for sources where your usage is authorized

Do not commit raw paid content, cookies, private API responses, logs, generated reports, or real portfolios.

## Deployment Options

MarketPulseWire supports three common deployment paths:

- Local development on macOS/Linux
- Linux server deployment with systemd timers/services
- GitHub Actions SSH deployment to your own server

GitHub Actions should deploy code, not run monitors long term. Runtime credentials should normally stay in the target server's `.env`.

See:

- [Deployment](docs/deployment.md)
- [Source Catalog](docs/sources.md)
- [Security](docs/security.md)
- [Compliance](docs/compliance.md)
- [Roadmap](docs/roadmap.md)

## Remote Helper Scripts

Set these variables before using remote helper scripts:

```bash
export REMOTE_HOST=your.server.example.com
export REMOTE_USER=root
export REMOTE_SSH_KEY=~/.ssh/id_ed25519
export REMOTE_DIR=/opt/surveil
export REMOTE_PROXY_DIR=/opt/surveil-proxy
export REMOTE_SERVICE_USER=surveil
```

Deploy and install services:

```bash
./scripts/deploy_remote.sh
./scripts/write_remote_secrets.sh
./scripts/write_remote_feishu.sh
./scripts/install_remote_systemd.sh
```

Pull the private portfolio from the server when the server Web workbench is
your production source of truth:

```bash
./scripts/pull_remote_portfolio.sh
```

This is deliberately one-way (server to local): it validates the downloaded
JSON, backs up the local private file, then imports it into local SQLite. It
does not upload or overwrite the server portfolio.

Open the Web workbench through an SSH tunnel:

```bash
ssh -L 8787:127.0.0.1:8787 \
  -i ~/.ssh/<your_deploy_key> \
  -o IdentitiesOnly=yes \
  <remote_user>@<remote_host>
```

Then open `http://127.0.0.1:8787`.

If local port `8787` is already in use, bind a different local port while keeping the remote port as `8787`:

```bash
ssh -L 8788:127.0.0.1:8787 \
  -i ~/.ssh/<your_deploy_key> \
  -o IdentitiesOnly=yes \
  <remote_user>@<remote_host>
```

Then open `http://127.0.0.1:8788`.

## GitHub Actions Deployment

The repository includes `.github/workflows/deploy.yml`, triggered manually with `workflow_dispatch`.

Configure these repository secrets:

```text
DEPLOY_HOST
DEPLOY_USER
DEPLOY_SSH_KEY
DEPLOY_DIR
DEPLOY_SERVICE_USER
DEPLOY_PROXY_DIR
```

The deploy workflow runs `scripts/deploy_remote.sh` over SSH/rsync. It does not write your business/API secrets; configure those on the server through `.env`, the Web workbench, or the write helper scripts.

## Sync Harness

Use GitHub as the source of truth for code. The server is a runtime target and stores only deployed code, `.env`, SQLite data, logs, and other private runtime state.

Deployment writes `/opt/surveil/REVISION` on the server with the deployed commit, branch, dirty flag, and timestamp. Check local/GitHub/server alignment with:

```bash
python3 scripts/status_sync.py
```

If you use `just`:

```bash
just status
just deploy
just remote-revision
```

`just status-strict` exits non-zero when the local tree is dirty, local `HEAD` differs from `origin/main`, or the server deployed commit differs from GitHub.

## Signal Outcome Loop

MarketPulseWire can turn high-importance alerts into traceable signal records for later review:

- `signals`: one extracted research signal
- `signal_targets`: affected holdings, mapped stocks, or industry links
- `signal_evidence`: source snippets and follow-up checkpoints
- `signal_outcomes`: post-event price reaction metrics
- `signal_reviews`: automatic hit/miss/partial/too-early reviews
- `stock_relations`: optional private supply-chain/competitor/customer relation mappings
- `market_skills`: optional private reusable investment reasoning maps, such as event -> chain -> affected segment patterns distilled from authorized notes

Manual commands:

```bash
python scripts/market_skills.py --skill-dir /path/to/market_skill
python scripts/signals_extract.py --days 14
python scripts/signal_outcome_update.py --days 45
python scripts/signal_review.py --days 60
python scripts/signal_digest.py --mode daily --dry-run
```

The outcome updater currently backfills A-share targets through iFinD history quotes when iFinD credentials are configured. It records unsupported markets or missing quote data explicitly instead of inventing results. JYGS action/prediction tables are not part of this loop.

Relationship mappings can be managed from the local Web workbench's `关系映射` tab. SQLite is the live source used by signal extraction; `config/stock_relations.json` is a private backup and migration snapshot. Web saves automatically update SQLite and export the private JSON snapshot.

To seed private relationship mappings from JSON, copy the example file and import it:

```bash
cp config/stock_relations.example.json config/stock_relations.json
python scripts/stock_relations.py --config config/stock_relations.json
```

`config/stock_relations.json` is gitignored. Use it for personal holdings, supply-chain links, competitors, customers, upstream/downstream names, and theme mappings that should not be published. Mappings can be triggered by direct symbols as well as exact theme/name matches or sufficiently specific title/body context. The Web workbench also provides JSON import/export, diff checks, recent signal backfill, and a pending suggestion queue for future LLM- or analyst-derived mapping ideas.

Market skill notes are also private by default. Put a reusable skill directory under `config/market_skill/`, or set `MARKET_SKILL_DIR`, then import it:

```bash
MARKET_SKILL_DIR=/path/to/market_skill python scripts/market_skills.py
python scripts/market_skills.py --skill-dir /path/to/market_skill --match "Rubin HBM PCB MLCC"
```

`market_skill` records do not directly change push gates or become stock relation facts. During signal extraction they can add `skill_inferred` targets and `market_skill` evidence, so the later review loop can verify whether a reasoning pattern was useful.

### Skeptic Evaluator

High-importance article and official-news candidates pass through a second-stage skeptic before immediate Feishu delivery. The skeptic checks local history and, when LLM credentials are available, asks a dedicated evaluator to look for stale news, repeated coverage, priced-in risk, weak hard variables, or over-extended stock linkage. `downgrade` candidates go to the daily digest instead of immediate push; `block` candidates are marked low importance.

Optionally, the skeptic can use controlled Web Evidence Retrieval. MarketPulseWire performs the search and stores the evidence itself; the LLM only receives a compact evidence pack, not direct web-search access. The first provider is Tavily, while the provider abstraction leaves room for Brave or other search APIs later. This helps check old news, earlier coverage, primary sources, priced-in risk, counter evidence such as capacity expansion or price declines, and macro context.

Useful settings:

```bash
SKEPTIC_EVALUATOR_ENABLED=1
SKEPTIC_STALE_NEWS_DAYS=7
SKEPTIC_DUPLICATE_LOOKBACK_DAYS=14
LLM_SKEPTIC_THINKING_TYPE=enabled
LLM_SKEPTIC_MAX_OUTPUT_TOKENS=1200

# Optional; can also be configured in the Web workbench.
WEB_EVIDENCE_ENABLED=0
WEB_EVIDENCE_PROVIDER=tavily
WEB_EVIDENCE_API_KEY=<your_search_api_key>
WEB_EVIDENCE_MODE=realtime
WEB_EVIDENCE_MAX_QUERIES=5
WEB_EVIDENCE_MAX_RESULTS=4
WEB_EVIDENCE_LOOKBACK_DAYS=30
WEB_EVIDENCE_TIMEOUT_SECONDS=12
WEB_EVIDENCE_FETCH_BODY=0
WEB_EVIDENCE_TAVILY_SEARCH_DEPTH=basic
WEB_EVIDENCE_TAVILY_TOPIC=news
```

### Source Health Noise

Some public feeds throttle or temporarily fail. SemiAnalysis may return `429`, and public RSSHub routes such as Jin10 may return `503`. MarketPulseWire records these in `source_health`, backs off the noisy source, and only alerts after consecutive failures.

Useful settings:

```bash
SOURCE_HEALTH_ALERT_FAILURES=3
SOURCE_HEALTH_ALERT_COOLDOWN_MINUTES=60
SOURCE_HEALTH_ALERT_RECOVERY=1
SOURCE_BACKOFF_SEMIANALYSIS_SECONDS=1800
SOURCE_BACKOFF_JIN10_SECONDS=600
```

Increasing the backoff values reduces Feishu noise and source pressure; lowering them improves freshness but may trigger more upstream rate limits.

## PR Automation

This repository uses GitHub Actions and Dependabot for low-risk PR automation:

- `CI` runs Python compilation, shell syntax checks, lightweight tests, and secret scanning on every PR.
- `PR Governance` labels PRs as `docs-only`, `dependencies`, `safe-to-merge`, `needs-human-review`, or `security-sensitive`.
- `Low-risk PR auto-merge` may squash-merge docs-only PRs after CI passes.
- Dependabot opens weekly PRs for `pip` and GitHub Actions updates.

Core monitoring, alerting, deployment, credentials, database, and LLM behavior changes require maintainer review even when CI passes.

## Pre-Publish Checks

Before making a fork public:

```bash
python -m py_compile scripts/*.py
bash -n scripts/*.sh
python scripts/test_analysis.py
python scripts/test_llm_analysis.py
python scripts/test_trendforce_page_monitor.py
python scripts/test_alphabstract_monitor.py
python scripts/test_link_enrichment.py
python scripts/test_sina_stock_news.py
python scripts/test_china_finance_media_monitor.py
python scripts/test_rss_monitor_fetch.py
python scripts/test_gate_prompts.py
python scripts/test_sina_zy_client.py
python scripts/test_industry_hardline.py
python scripts/test_macro_policy.py
python scripts/test_holdings_web.py
python scripts/test_time_utils.py
python scripts/test_x_stream_health.py
python scripts/test_skeptic_evaluator.py
python scripts/test_web_evidence.py
python scripts/test_signals_extract.py
python scripts/scan_secrets.py
```

The scanner is intentionally lightweight. Also manually inspect the file list before publishing.

## License

MIT. See [LICENSE](LICENSE).
