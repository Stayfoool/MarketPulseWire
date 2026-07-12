# Deployment

Surveil can run locally for development or on a Linux server for 24/7 monitoring.

The recommended production setup is:

- Linux server
- Python 3.10+
- SQLite
- systemd services/timers
- Web workbench bound to `127.0.0.1`
- SSH tunnel for browser access

Do not commit `.env`, runtime databases, logs, reports, proxy configs, or real portfolio files.

## Local Development

```bash
git clone https://github.com/<you>/<repo>.git
cd <repo>
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp config/portfolio.example.json config/portfolio.json
cp config/media_keywords.example.json config/media_keywords.json
# Optional private supply-chain/customer/competitor relation mappings:
cp config/stock_relations.example.json config/stock_relations.json
python scripts/market_db.py
```

Relationship mappings can also be created and edited later from the Web workbench's `关系映射` tab. The SQLite database is the live source; `config/stock_relations.json` is a gitignored private seed/backup snapshot.

Edit `.env`, then run individual components:

```bash
python scripts/holdings_web.py --host 127.0.0.1 --port 8787
python scripts/rss_monitor.py --interval 300
python scripts/overseas_media_monitor.py
```

Open:

```text
http://127.0.0.1:8787
```

Local development is convenient, but monitoring stops when your computer sleeps.

## Linux Server With systemd

Set deployment variables on your local machine:

```bash
export REMOTE_HOST=your.server.example.com
export REMOTE_USER=root
export REMOTE_SSH_KEY=~/.ssh/id_ed25519
export REMOTE_DIR=/opt/surveil
export REMOTE_PROXY_DIR=/opt/surveil-proxy
export REMOTE_SERVICE_USER=surveil
```

Deploy code:

```bash
./scripts/deploy_remote.sh
```

`deploy_remote.sh` writes a server-side revision marker at `$REMOTE_DIR/REVISION`:

```text
commit=<local git commit>
branch=<local branch>
origin_commit=<origin branch commit>
dirty=<0 or 1>
deployed_at=<UTC timestamp>
deployed_by=deploy_remote.sh
```

Use it to verify whether your Mac, GitHub, and server are aligned:

```bash
python3 scripts/status_sync.py
```

Write secrets:

```bash
./scripts/write_remote_secrets.sh
./scripts/write_remote_feishu.sh
./scripts/write_remote_x_credentials.sh
./scripts/write_remote_ifind_token.sh
./scripts/write_remote_jygs_cookie.sh
```

Install services and timers:

```bash
./scripts/install_remote_systemd.sh
```

The installer also enables shadow collector timers:

- `surveil-research-collector-shadow.timer`
- `surveil-official-collector-shadow.timer`
- `surveil-news-collector-shadow.timer`
- `surveil-collector-shadow-digest.timer`

These shadow jobs are migration aids. They write JSON/Markdown reports under
`reports/` and logs under `logs/`; they can run report-only `decision_engine`
direct-shadow checks, but they do not send Feishu messages, do not run LLM
gates, and do not write production `seen_items` or review tables.

The installer also copies the production collector units:

- `surveil-research-collector.service`
- `surveil-research-collector.timer`
- `surveil-official-collector.service`
- `surveil-official-collector.timer`
- `surveil-news-collector.service`
- `surveil-news-collector.timer`

In production mode they write the normal `seen_items` / review tables and can
send Feishu cards through `market_content_flow` while preserving the compatible
`article_reviews` / `official_news_reviews` stores. The global runtime switches
are configured from the server Web panel:

```bash
SURVEIL_CONTENT_DIRECT_PATH=1
SURVEIL_EVENT_DIRECT_PATH=1
```

`SURVEIL_CONTENT_DIRECT_PATH` atomically selects the unified production flow for
research, news-media, and official-company collectors. `SURVEIL_EVENT_DIRECT_PATH`
does the same for Sina flash, Sina portfolio news, and iFinD notice/report. Set a
switch to `0` only for rollback to the compatibility wrapper; never run both
paths for the same source. X/Serenity and `value_directory_monitor` are deliberate
independent routes and do not use these switches.

When changing settings programmatically on the server, invoke `settings_store`
as the `surveil` service user. Do not write `/opt/surveil/.env` as root, because
an atomic replacement would change file ownership and prevent services from
reading the production configuration.

After cutover, keep the legacy guards below in `.env`: the installer will keep
the old units disabled and enable the matching production collector timers.

During the earlier research collector cutover, the old RSS monitor could be kept
for official company feeds with:

```bash
RSS_MONITOR_EXCLUDE_PROFILE_CATEGORIES=research_industry_media
```

After the official-company and research/industry-media collector cutovers, keep
the old RSS / TrendForce / overseas media units off across future installs by
setting:

```bash
DISABLE_LEGACY_RSS_MONITOR=1
DISABLE_LEGACY_RESEARCH_MONITORS=1
```

After the domestic news-media collector cutover, keep the old China media timer
off across future installs and enable `surveil-news-collector.timer` by setting:

```bash
DISABLE_LEGACY_CHINA_MEDIA_MONITOR=1
```

With the three cutovers complete, the production fetching timers to inspect are:

```bash
systemctl status --no-pager \
  surveil-research-collector.timer \
  surveil-official-collector.timer \
  surveil-news-collector.timer \
  surveil-sina-stock-news.timer \
  surveil-ifind-notice.timer
```

The high-frequency persistent fetchers remain:

```bash
systemctl status --no-pager surveil-x-stream.service surveil-sina-flash.service
```

Open the Web workbench through an SSH tunnel:

```bash
ssh -L 8787:127.0.0.1:8787 \
  -i ~/.ssh/<your_deploy_key> \
  -o IdentitiesOnly=yes \
  <remote_user>@<remote_host>
```

Then open:

```text
http://127.0.0.1:8787
```

If local port `8787` is already in use, bind another local port while keeping the remote service port as `8787`:

```bash
ssh -L 8788:127.0.0.1:8787 \
  -i ~/.ssh/<your_deploy_key> \
  -o IdentitiesOnly=yes \
  <remote_user>@<remote_host>
```

Then open:

```text
http://127.0.0.1:8788
```

The install script renders systemd units with your `REMOTE_DIR`, `REMOTE_PROXY_DIR`, and `REMOTE_SERVICE_USER` values before uploading them.

## GitHub Actions Deployment

GitHub Actions should not run the monitors long term. Use Actions for CI and for remote deployment to your own server.

Add repository secrets:

```text
DEPLOY_HOST
DEPLOY_USER
DEPLOY_SSH_KEY
DEPLOY_DIR
DEPLOY_SERVICE_USER
DEPLOY_PROXY_DIR
```

Recommended model:

- GitHub Actions deploys code by SSH/rsync.
- Runtime secrets stay on the server in `.env`.
- Use the Web workbench or SSH scripts to edit secrets.

Run the `Deploy` workflow manually from GitHub Actions.

For local operator convenience, the repository also includes a `Justfile`:

```bash
just test
just status
just deploy
just remote-timers
just remote-revision
```

## Optional OCR

ValueList first-page previews can use local PaddleOCR to read visible screenshot text before sending the extracted text to the configured text LLM. This is optional and uses CPU only; it does not require a paid OCR API or GPU.

Install the optional OCR packages on the runtime host after the normal Python virtualenv exists:

```bash
./scripts/install_ocr_dependencies.sh
```

The script installs the version-pinned CPU-compatible packages listed in `requirements-ocr.txt` and prints the installed PaddlePaddle, PaddleOCR, NumPy, and OpenCV versions. It defaults to official PyPI; where official downloads are repeatedly slow or unavailable, set `PIP_INDEX_URL` to an approved mainstream mirror for the same package versions. If OCR is not installed, ValueList hard-rule pushes still work; the preview extraction section will record the OCR failure instead of blocking delivery.

## Optional Proxy

Some overseas media may be unreachable from certain cloud regions. Surveil supports a local-only Mihomo/Clash proxy for selected monitors.

Rules:

- Prefer official downloads for Mihomo releases.
- Keep subscription URLs and proxy YAML files private.
- The generated proxy listens on `127.0.0.1` only.
- Do not commit `proxy.env`, subscriptions, node configs, or downloaded binaries.

Install the proxy runtime from an official release on your local machine, then upload it:

```bash
./scripts/install_remote_proxy_from_local.sh
```

Configure a subscription:

```bash
./scripts/write_remote_proxy_subscription.sh
```

Or upload a locally downloaded Clash/Mihomo YAML:

```bash
./scripts/write_remote_proxy_config_file.sh /path/to/provider-config.yaml
```

## Runtime Secrets

Keep these only in server `.env` or local `.env`:

- LLM API keys
- iFinD refresh/access tokens
- X bearer/OAuth tokens
- Feishu webhook/secret
- Sina API key
- JYGS cookie/session
- Proxy subscription or node configs

See [security.md](security.md) before making a repository public.
