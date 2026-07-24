# Deployment

Surveil can run locally for development or on a Linux server for 24/7 monitoring.

The recommended production setup is:

- Linux server
- Python 3.10+
- SQLite
- systemd services/timers
- Web workbench bound to `127.0.0.1`
- SSH tunnel for browser access

The current production target is an Alibaba Cloud Debian 12 server with 2
vCPU, 2 GiB plan memory, a persistent 2 GiB swap file, and a 40 GiB system
disk. Host/IP and operator-key details remain in the private local operator
notes, not this repository. Report-only collector shadow timers stay disabled
on this constrained host unless a bounded observation explicitly requires them.

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

`RULE_CORE_CONFIG` must point to a complete private global rule JSON before any
production collector or the Web `媒体关键词` page is used. Production five-group
range admission fails closed when it is missing or invalid. The repository
`config/rule_core_v1.test.json` is only a CI fixture and must not be used as the
production configuration.

The Web process requires the repository `web/` directory alongside `scripts/`.
`deploy_remote.sh` already synchronizes both directories; do not deploy
`scripts/holdings_web.py` by itself. The browser loads `web/index.html`,
`web/styles.css` and `web/app.js` from the same loopback service and origin as
the existing `/api/*` routes.

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

`surveil-db-init.service` applies additive canonical-storage migrations before
collectors start. The first migration creates `market_items` and
`market_reviews`, extends `deliveries`, and copies existing `seen_items`
identities once. It does not delete or rewrite legacy review/event tables and
does not infer missing historical full text, admission evidence or decisions.
Back up `data/surveil.sqlite3` before deploying a revision that first contains
this migration, then verify `PRAGMA quick_check`, canonical row counts and
foreign-key references under the `surveil` service account.

The historical result/read migration is deliberately not run by
`surveil-db-init.service`. Deploying its code only adds the schema and leaves
Web, daily output, feedback and signal readers on the compatibility tables.
After deployment, first create a mode-`0600` SQLite backup and preview the
migration under the service account:

```bash
sudo -u surveil /opt/surveil/.venv/bin/python \
  /opt/surveil/scripts/market_storage_migration.py \
  --db /opt/surveil/data/surveil.sqlite3
```

Compare preview counts with `article_reviews`, `official_news_reviews`,
`events` and `event_analyses`. After review, apply it explicitly:

```bash
sudo -u surveil /opt/surveil/.venv/bin/python \
  /opt/surveil/scripts/market_storage_migration.py \
  --db /opt/surveil/data/surveil.sqlite3 --apply
```

The apply is idempotent: when the completion marker already exists it returns
the retained first-run statistics without scanning or rewriting the database.
The first apply runs inside one explicit SQLite write transaction;
any exception rolls back all item, alias, result and delivery-link changes. It
writes the `market-storage-results-v1` marker only after the transaction
succeeds. That marker switches Web Event Center,
article/official daily output, feedback reads and signal extraction to
`market_items` / `market_reviews`. Do not delete the compatibility tables or
disable their writes in this stage. Verify old-to-new counts, preserved
article/official/event ids, current result selection, daily dry runs, feedback
resolution, signal dry run, foreign keys and SQLite integrity before treating
the read switch as complete.

After deploying the unified-write authority change, compare every new unified
result with its retained compatibility copy. The command is read-only, defaults
to the migration completion time and prints counts only:

```bash
sudo -u surveil /opt/surveil/.venv/bin/python \
  /opt/surveil/scripts/market_storage_audit.py \
  --db /opt/surveil/data/surveil.sqlite3 --fail-on-difference
```

Use `--since <UTC ISO timestamp>` and `--until <UTC ISO timestamp>` to audit a
deployment or observation window. Any missing identity/result, action mismatch,
delivery without unified item/result, duplicate current result, orphan
reference, current result blocked by the compatibility-reference unique
constraint, foreign-key error or failed `quick_check` blocks rollout. Current
retryable and terminal failures are reported as counts; failures unrelated to
the compatibility-reference constraint do not by themselves make this storage
comparison fail. The old tables remain enabled as compatibility copies during
this stage.

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

The installer copies but keeps these standalone report-only collector timers disabled:

- `surveil-research-collector-shadow.timer`
- `surveil-official-collector-shadow.timer`
- `surveil-news-collector-shadow.timer`
- `surveil-collector-shadow-digest.timer`

These standalone jobs are migration aids and are not used by the normal
production schedule. The production collectors use the shared runtime directly.
After five-group range admission, `decision_engine.py` calls the reviewed LLM
degree rules and returns the only production `DecisionResult`. There is no
configuration selector between the LLM and the retained deterministic code, and
model failure does not fall back. A failed model request, invalid result or
private-audit write marks the current review `failed_retryable` and creates no
interpretation, delivery or dedup reservation.

`RULE_CORE_CONFIG` is the persisted source for production five-group range
admission and the Web workbench's `媒体关键词` page. The page edits only
`semiconductor_ai_keywords`, its validated
`semiconductor_ai_title_keywords` subset and `exclude_keywords`. Terms in the
subset match only titles; other master-list terms match the complete normalized
rule text. The save path validates the complete rule configuration, preserves
every other rule section, writes atomically with mode `0600`, and creates a
private backup beside the rule file. There is no runtime precedence between
code-default, base and include keyword lists.

Historical comparison tools may still read `RULE_CORE_SHADOW_CONFIG` and
`RULE_CORE_SHADOW_PORTFOLIO`, but neither is a production decision input.
Production admission and the LLM decision use `RULE_CORE_CONFIG` and current
Web-managed production SQLite holdings.

For an existing installation that still has private
`config/media_keywords.json`, preview the one-time migration after deploying
the new code and before using the Web page:

```bash
sudo -u surveil /opt/surveil/.venv/bin/python \
  /opt/surveil/scripts/migrate_media_keywords.py \
  --env-file /opt/surveil/.env
```

The preview prints counts and hashed term identifiers, not private keyword
values. Review it, then apply the same migration explicitly:

```bash
sudo -u surveil /opt/surveil/.venv/bin/python \
  /opt/surveil/scripts/migrate_media_keywords.py \
  --env-file /opt/surveil/.env \
  --apply
```

The migration starts from the reviewed `semiconductor_ai_keywords`, preserves
actual user additions and exclusions from the old Web configuration, and adds
the approved semiconductor company aliases. The five reviewed generic-power
terms that must remain omitted are reported by hashed identifier and are not
restored. Keep the generated backup and
verify the effective count and configuration version through the authenticated
Web page before restarting or manually running collectors.

After deploying the admission simplification, place the reviewed title-only
subset in a service-private mode-`0600` JSON array. Its values must already
exist in the private `semiconductor_ai_keywords` master list. Preview the
combined keyword and macro migration without printing private values:

```bash
sudo -u surveil /opt/surveil/.venv/bin/python \
  /opt/surveil/scripts/migrate_admission_simplification.py \
  --env-file /opt/surveil/.env \
  --title-keywords-file /opt/surveil-private/semiconductor-ai-title-keywords.json
```

The preview removes standalone generic `AI`/`人工智能`, reports their hashed
identifiers, and replaces legacy `macro_data.tiers` with the old `primary`
list as the sole `macro_data.indicators` list. It does not create exceptions or
reaction-based admission for removed secondary indicators. After reviewing the
redacted counts, apply the same migration explicitly with `--apply`. The apply
validates the complete rule file, creates a mode-`0600` backup and atomically
replaces the private configuration. Verify file ownership/mode, configuration
version, title-only count, core macro-indicator count and representative range
admission replays before restarting affected Alibaba services.

Installation disables `surveil-rule-shadow-daily.timer` and enables
`surveil-llm-decision-audit-cleanup.timer` at 15:30 `Asia/Shanghai`. No new
current-versus-candidate report or Feishu reminder is generated. Existing dated
and combined comparison reports remain available read-only in the Web
workbench. The cleanup retains those historical report files while removing
expired sensitive model requests/responses.

An admitted item with a non-empty title enters the production LLM decision even
when `full_text` is empty. Available summary is included. Available body text is
limited by code to its first 3,000 characters and divided into numbered exact
source segments. The model returns segment ids instead of copying quotes; code
resolves those ids to the original text. Each rule may cite at most three exact
segments; response-wide evidence totals remain audit metrics rather than
validity limits, and ellipsis punctuation does not invalidate a segment. The
catalog contains only reviewed degree-decision rules. All `not_matched` results
produce `archive`; no match plus any `uncertain` result produces no decision.
A structurally invalid, evidence-invalid or conflicting response may receive
one correction request containing the validation errors. Network retries and
that correction share one hard 120-second total wall-clock budget.
The production LLM HTTP client connects to `LLM_BASE_URL` directly and does not
inherit collector `HTTP_PROXY`, `HTTPS_PROXY` or `ALL_PROXY` variables. Source
fetching continues to use `proxy.env`; no SOCKS dependency is required for the
model provider request.

Each production decision audit stores exact requests, raw responses, response
metadata and validation details for all calls under
`reports/llm-decision-audits`. The directory is mode `0700`, files are mode
`0600`, and direct `market_item_id` / `market_review_id` fields link the audit to
SQLite without storing its complete content there. The cleanup task removes
sensitive request/response content after 30 days while retaining bounded result
metadata. Web, Git, Feishu and local report copies never receive complete model
input, article body or raw provider response.

An operator may explicitly rebuild a historical daily file from its retained
per-item comparison reports without sending another reminder:

```bash
sudo -u surveil /opt/surveil/.venv/bin/python \
  /opt/surveil/scripts/rule_core_shadow_daily.py \
  --date YYYY-MM-DD --force-rebuild --dry-run --json
```

This only re-aggregates stored current/candidate decisions. Comparison reports do not
retain article bodies, so the command does not re-run the selected candidate
against historical `NormalizedMarketItem` inputs. The rebuilt report records
that limitation and preserves an existing `notification.status=sent` without
sending a second Feishu reminder. A historical rebuild updates only the dated
report and does not replace the rolling `rule-core-shadow-combined-latest` view.
It fails without changing the dated report if fewer retained per-item reports
are available than the original daily report recorded.

The installer also copies the production collector units:

- `surveil-research-collector.service`
- `surveil-research-collector.timer`
- `surveil-official-collector.service`
- `surveil-official-collector.timer`
- `surveil-news-collector.service`
- `surveil-news-collector.timer`

All general collectors construct `NormalizedMarketItem` and call
`process_market_item(...)`, while preserving the existing `article_reviews`,
`official_news_reviews`, and `events/event_analyses` stores. The former
direct/compat runtime switch and compatibility wrappers have been removed; rollback
now uses the normal Git/PR/deployment process instead of selecting a second runtime.
The LLM decision cutover follows the same rule: there is no runtime selector back
to the deterministic decision. Record the preceding Git revision before deployment.
If rollback criteria are met, stop affected Alibaba collectors, deploy that exact
preceding revision, restart the same services and verify service health, logs and
SQLite integrity. Do not rewrite already completed reviews or deliveries during
rollback.
The research collector also runs public list/sitemap page sources such as
TrendForce/SEMI pages and AlphaAbstract summaries on the same low-frequency page
cadence. AlphaAbstract uses its public `sitemap.xml` and public summary pages;
first production discovery is baselined by default unless `SURVEIL_NOTIFY_BASELINE=1`.
The news collector also runs public official trade-policy sources through
`trade_policy_monitor.py`: Federal Register JSON, USTR press releases, European
Commission Press Corner RSS, MOFCOM policy releases, and MOFCOM spokesperson
statements. Each source establishes its own first-run baseline, records
`trade_policy/<source_id>` health, and sends new items through the same unified
article runtime. The common `trade_friction_escalation` decision rule also applies
to every existing and future normalized source; official-source identity alone
does not create push eligibility.
The same news collector runs WallstreetCN as a peer general news-media source.
Public category/live pages provide normal discovery and official monthly
sitemaps provide bounded catch-up; the source does not use login, member content,
RSSHub, or a separate service. Its items use all existing generic content rules.
The international-bank Fed-path revision rule is cross-source and can be
triggered by any normalized source, not only WallstreetCN.
X/Serenity remains the deliberate independent route. `value_directory_monitor`
keeps its private Playwright/OCR collection boundary, but its final decision,
compatible review write, dedup and delivery use the unified runtime.

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
  surveil-company-disclosures.timer
```

The high-frequency persistent fetchers remain:

```bash
systemctl status --no-pager surveil-x-stream.service surveil-sina-flash.service
```

`surveil-company-disclosures.timer` retains the former announcement schedule at
08:00 and 20:00. Its source profile defaults to `provider=cninfo_public` and
`operation_mode=report_only`; report-only runs update source state, PDF cache,
source health and baseline-only event audit rows, but do not create analyses,
decisions or deliveries.
After the observation window and explicit approval, change only this private
source profile to `operation_mode=live`. A newly selected provider always
baselines its first successful result before processing later records. The
systemd installer disables and removes the expired
`surveil-ifind-notice.timer`/service and never starts them again.

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

Normal remote deployment checks the effective `VALUE_DIRECTORY_PREVIEW_ENABLED`
and `VALUE_DIRECTORY_PREVIEW_OCR_ENABLED` settings after installing the base
requirements. When preview OCR is enabled, deployment verifies the exact direct
versions pinned in `requirements-ocr.txt` plus the `paddle`, `paddleocr`, `numpy`
and `cv2` imports. A missing, mismatched or broken runtime invokes the same
installer and then checks again; deployment fails if the post-install check does
not pass. When preview or OCR is explicitly disabled, the optional dependency
check is skipped. This deployment check does not initialize PaddleOCR or download
model files. The service-account `.paddleocr/` model cache and runtime `reports/`
are excluded from rsync deletion, retained across normal deploys and never copied
back into Git. The model cache is populated on the first approved OCR run.

ValueList browser launches retain bounded Playwright error and profile-lock
diagnostics without page content, cookies or browser storage. One timer run uses
one persistent context to collect every enabled ValueList list page and visible
first-page preview, then closes that context before starting OCR, admission,
decision, storage or delivery. The collector waits briefly for a live
same-profile owner to exit. A launch or shutdown timeout fails the shared browser
stage rather than starting another browser against a profile that is still in
use. Dead-owner lock artifacts remain recoverable by Chromium; the collector does
not blindly delete locks or kill unrelated browser processes.

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

## Feishu Market Feedback

Feedback-enabled cards use an enterprise self-built Feishu application rather than the existing custom-bot webhook. A group can contain both: the old custom webhook (for example, a historical `surveil-huawei` display name) remains in place, while the enterprise application bot (currently `stocksurveil`) sends cards with actionable feedback buttons. A custom webhook is not an application bot and therefore normally does not appear in the Feishu application-bot list.

Use listener-only mode for the first real-group test. `FEISHU_FEEDBACK_LISTENER_ENABLED=1` starts only the callback long connection and permits one explicit test card; it does not switch natural market cards away from the existing webhook. `FEISHU_FEEDBACK_ENABLED=1` is the later, separate switch that sends unified article/official/event cards through the application bot with feedback actions.

Required private settings:

```text
FEISHU_APP_ID
FEISHU_APP_SECRET
FEISHU_FEEDBACK_CHAT_ID
FEISHU_FEEDBACK_ALLOWED_OPEN_IDS
FEISHU_FEEDBACK_TOKEN_SECRET
FEISHU_FEEDBACK_LISTENER_ENABLED
FEISHU_FEEDBACK_ENABLED
```

Setup order:

1. In the Feishu developer console, use an enterprise self-built application, enable its bot, grant only the message-send permissions required by the official API, and publish the application version.
2. Add the application bot to the chosen test group. The existing production group may be used provided it is understood that only the explicit test card is sent in listener-only mode. Put that group's `oc_...` id in `FEISHU_FEEDBACK_CHAT_ID`.
3. Generate an independent random `FEISHU_FEEDBACK_TOKEN_SECRET`; do not reuse the app secret, webhook secret or Web workbench token.
4. Configure the new `card.action.trigger` callback and choose the official long-connection subscription mode. Keep `FEISHU_FEEDBACK_ENABLED=0`, set `FEISHU_FEEDBACK_LISTENER_ENABLED=1`, then install/restart the feedback service and confirm it connects.
5. For the first identity-discovery click only, `FEISHU_FEEDBACK_ALLOWED_OPEN_IDS=*` may be used briefly. Read the resulting operator `open_id` from the stored feedback, replace `*` with the explicit id, then restart the feedback service.
6. Send exactly one explicitly approved test card with `python scripts/send_feishu_feedback_test.py --confirm`. Verify its Toast acknowledgement, same-card state replacement (`反馈状态` plus `✓` on the current label), same-label second-click cancellation, last-click-wins behavior, `market_feedback` and callback health. Cancellation appends a `cleared` audit event and restores the unselected card; it does not delete history or count as labelled feedback. Test rows never enter the `反馈质量` delivered or labelled denominators. Do not use `scripts/test_feishu.py` or `scripts/test_feishu_card.py` for this check: they send unrelated real test messages and are not isolated feedback regressions.
7. Only after this passes and is approved, set `FEISHU_FEEDBACK_ENABLED=1` to switch unified market cards to the application bot. The old webhook configuration remains untouched.

The installer enables `surveil-feishu-feedback.service` when either listener-only or full feedback mode is enabled. If full-feedback settings are incomplete, unified delivery fails closed on the feedback application path rather than sending a second copy through the custom webhook. Disable `FEISHU_FEEDBACK_ENABLED` to return unified cards to the existing webhook sender.

Official dependency provenance:

- Feishu callback structure and three-second response contract: `https://open.feishu.cn/document/feishu-cards/card-callback-communication`
- Official long-connection setup and Python SDK example: `https://open.feishu.cn/document/event-subscription-guide/callback-subscription/step-1-choose-a-subscription-mode/configure-callback-request-address`
- Python package: official PyPI `https://pypi.org/project/lark-oapi/`, pinned as `lark-oapi==1.7.1` for current Python compatibility.

See [security.md](security.md) before making a repository public.
