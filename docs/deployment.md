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
notes, not this repository.

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

Edit `.env`, then run the local Web workbench when needed:

```bash
python scripts/holdings_web.py --host 127.0.0.1 --port 8787
```

Production collectors and persistent services run only on the Linux systemd host; do not run them as Mac background services.

`RULE_CORE_CONFIG` must point to a complete private global rule JSON before any
production collector or the Web `媒体关键词` page is used. Production five-group
range admission fails closed when it is missing or invalid. The repository
`config/rule_core_v1.test.json` is only a CI fixture and must not be used as the
production configuration.

`LLM_DECISION_RULE_CONFIG` must point to the separate private LLM decision-rule
JSON before any production collector is started. Keep the Mac development copy
at `config/llm_decision_rules.json`; it is gitignored and must be mode `0600`.
Keep the Alibaba copy at `/opt/surveil/config/llm_decision_rules.json`, owned by
the production service account and mode `0600`. `deploy_remote.sh` explicitly
excludes this path, so normal deployment neither uploads, deletes nor replaces
the private rules. Git contains only `config/llm_decision_rules.test.json`, whose
synthetic text is for CI and must never be used in production.
Schema v2 lets a rule declare multiple applicable range-admission groups. The
loader retains v1 support for ordered rollout, but either schema selects a rule
only by intersection with the item's already matched and source-allowed groups;
it cannot expand range admission.

Before restarting production after a rule change, validate both files through
the loader and compare their SHA-256 digests without printing their content:

```bash
LLM_DECISION_RULE_CONFIG=config/llm_decision_rules.json \
  PYTHONPATH=scripts python3 -c 'import llm_rule_catalog; print(llm_rule_catalog.LLM_DECISION_RULE_VERSION, len(llm_rule_catalog.RULES))'
shasum -a 256 config/llm_decision_rules.json
ssh surveil-alibaba "sudo -u surveil env PYTHONPATH=/opt/surveil/scripts LLM_DECISION_RULE_CONFIG=/opt/surveil/config/llm_decision_rules.json /opt/surveil/.venv/bin/python -c 'import llm_rule_catalog; print(llm_rule_catalog.LLM_DECISION_RULE_VERSION, len(llm_rule_catalog.RULES))' && sha256sum /opt/surveil/config/llm_decision_rules.json"
```

The version, rule count and SHA-256 digest must match. Missing, unreadable,
wrong-permission or invalid private rules stop systemd installation and fail
production decisions closed; they never fall back to tracked test rules.
When advancing the private file from v1 to v2, deploy and verify the
backward-compatible code first, then stage the reviewed private file, restore
production-service ownership and mode `0600`, compare version/count/SHA-256,
and only then restart affected Alibaba collectors. Never deploy the private
file before the running code supports its schema.

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

`surveil-db-init.service` applies additive unified-storage migrations before
collectors start. Fresh databases create only `market_items`, `market_reviews`,
`market_item_aliases` and unified `deliveries` for market results. Normal
initialization and deployment never delete data.

Use it to verify whether your Mac, GitHub, and server are aligned:

```bash
python3 scripts/status_sync.py
```

Write secrets:

```bash
./scripts/write_remote_secrets.sh
./scripts/write_remote_feishu.sh
./scripts/write_remote_x_credentials.sh
```

Install services and timers:

```bash
./scripts/install_remote_systemd.sh
./scripts/prune_remote_code.sh
```

Every retained service uses `UMask=0077`. The installer also keeps the top-level
`logs/` directory service-account-owned and mode `0700`, and normal log files
mode `0600`; verify these permissions after deployment without printing log
contents.

Code deployment deliberately uses three ordered stages. `deploy_remote.sh`
first overlays the new checkout without deleting paths that may still be used
by the installed systemd units. `install_remote_systemd.sh` then installs and
reloads the new units and records the installed revision. Only
`prune_remote_code.sh` uses rsync deletion, and it refuses to run unless the
installed-systemd revision matches the deployed code revision. This prevents a
timer from invoking a renamed executable after the old path has been deleted
but before the replacement unit is installed. GitHub Deploy runs these three
commands in this order. Both sync stages preserve the server-generated
`REVISION` marker as well as the private configuration, data, logs and reports.
After pruning, `prune_remote_code.sh` restores the deployment root to the
configured service account and mode `0700`, because rsync otherwise applies the
checkout root metadata to that directory.

The installer stops, disables and removes the retired research, official and
news collector shadow units, the collector shadow digest units and the retired
rule-comparison daily units before reloading systemd. The production collectors
use the shared runtime directly.
After five-group range admission, `decision_engine.py` calls the reviewed LLM
degree rules and returns the only production `DecisionResult`. There is no
configuration selector or retained deterministic action code, and
model failure does not fall back. A failed model request, invalid result or
private-audit write marks the current review `failed_retryable` and creates no
interpretation, delivery or dedup reservation. A structurally and evidentially
valid no-match-plus-`uncertain` result instead records terminal
`insufficient_evidence`; it creates no `DecisionResult` and is not automatically
evaluated again.

`RULE_CORE_CONFIG` is the persisted source for production five-group range
admission and the Web workbench's `媒体关键词` page. The page edits only
`semiconductor_ai_keywords`, its validated
`semiconductor_ai_title_keywords` subset and `exclude_keywords`. Terms in the
subset match only titles; other master-list terms match the complete normalized
rule text. The save path validates the complete rule configuration, preserves
every other rule section, writes atomically with mode `0600`, and creates a
private backup beside the rule file. There is no runtime precedence between
code-default, base and include keyword lists.

Production admission uses `RULE_CORE_CONFIG` and current Web-managed production
SQLite holdings. The LLM decision additionally loads its exact degree-decision
rules from `LLM_DECISION_RULE_CONFIG`.

Installation enables `surveil-llm-decision-audit-cleanup.timer` at 15:30
`Asia/Shanghai`. It scans only private production `llm-decision-audit-*` files
and removes expired sensitive model requests/responses after 30 days while
retaining bounded decision results.

An admitted item with a non-empty title enters the production LLM decision even
when `full_text` is empty. Available summary is included. Available body text is
limited by code to its first 3,000 characters and divided into numbered exact
source segments. The model returns segment ids instead of copying quotes; code
resolves those ids to the original text. Each rule may cite at most three exact
segments; response-wide evidence totals remain audit metrics rather than
validity limits, and ellipsis punctuation does not invalidate a segment. The
private file contains only reviewed degree-decision rules. All `not_matched` results
produce `archive`; no match plus any valid `uncertain` result produces no decision
and records terminal `insufficient_evidence`.
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

The authenticated Web workbench's `大模型决策` view reads completed
`DecisionResult` fields from unified SQLite and joins failed attempts to the
bounded `web_projection` stored in the same private audit file. The projection
contains only bounded rule judgments, reasons, evidence/counterevidence and
version metadata; it is not a decision input.

The installer also copies the production collector units:

- `surveil-research-collector.service`
- `surveil-research-collector.timer`
- `surveil-official-collector.service`
- `surveil-official-collector.timer`
- `surveil-news-collector.service`
- `surveil-news-collector.timer`

All general collectors construct `NormalizedMarketItem` and call
`process_market_item(...)`. Production processing, Web Event Center, daily
output, feedback and operational tools read and write `market_items`,
`market_reviews`, `market_item_aliases` and linked `deliveries`. The former
direct/compat runtime switch and compatibility wrappers have been removed;
rollback uses the normal Git/PR/deployment process instead of selecting a
second runtime.
The LLM decision cutover follows the same rule: there is no runtime selector
back to another decision implementation. For later deployments, record the
preceding supported Git revision before deployment. If rollback criteria are
met, stop affected Alibaba collectors, deploy that exact preceding revision,
restart the same services and verify service health, logs and SQLite integrity.
Only revisions using the current unified schema and LLM-only degree decision are
supported; do not rewrite already completed reviews or deliveries during rollback.
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

The production fetching timers to inspect are:

```bash
systemctl status --no-pager \
  surveil-research-collector.timer \
  surveil-official-collector.timer \
  surveil-news-collector.timer \
  surveil-sina-stock-news.timer \
  surveil-company-disclosures.timer
```

`surveil-sina-stock-news.timer` deliberately uses `OnActiveSec` for its first
run. The installer can restart this timer
many days after the host booted, so `OnBootSec` could already be expired and
leave an enabled timer with no next trigger. Its subsequent period remains
controlled by `OnUnitActiveSec` (30 minutes). After installation, verify that
it shows a future `NEXT` value instead of only checking that it is enabled and
active. The installer explicitly restarts it after enabling because
`enable --now` does not restart an already-active timer or reset its monotonic
schedule.

The high-frequency persistent fetchers remain:

```bash
systemctl status --no-pager surveil-x-stream.service surveil-sina-flash.service
```

After the private rule/configuration preflight succeeds, the installer restarts
an enabled `surveil-feishu-feedback.service` and `surveil-x-stream.service` even
when each unit was already active. `systemctl enable --now` alone does not load
new Python code into an existing long-running process. Verify their
`ExecMainStartTimestamp` after deployment, in addition to enabled/active state.

`surveil-company-disclosures.timer` retains the former announcement schedule at
08:00 and 20:00. Its source profile defaults to `provider=cninfo_public`. Every
newly selected provider's first successful fetch updates source state, PDF
cache, source health and baseline-only event audit rows without creating
decisions or deliveries. Later new records enter normal production admission,
decision and delivery.

The Web-managed private source-profile override remains at
`config/source_profiles.local.json`. It is Git-ignored, excluded by
`deploy_remote.sh`, owned by the production service account and mode `0600`.
Every Web save creates the temporary file as `0600` before writing and preserves
that mode across the atomic replacement. Verify the authenticated source-profile
API still reports the expected disabled and override counts. Ordinary deployment
must not upload or replace this production-private file.

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

ValueList runs at 05:00 and 21:00 Beijing time. The installer restarts the timer
to load those times only when the timer was already enabled; it does not enable
a deliberately disabled ValueList timer. Browser launches retain bounded Playwright error and profile-lock
diagnostics without page content, cookies or browser storage. One timer run uses
one persistent context to collect every enabled ValueList list page, then
collects visible first-page previews only for new, `pending`/`failed_retryable`
or explicitly rechecked entries. Completed-but-unpushed entries are not
automatically reprocessed unless `VALUE_DIRECTORY_RECHECK_UNPUSHED=1` is
explicitly configured. The context closes before starting OCR, admission,
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
- X bearer/OAuth tokens
- Feishu webhook/secret
- Sina API key
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
6. Send exactly one explicitly approved test card with `python scripts/send_feishu_feedback_test.py --confirm`. Verify its Toast acknowledgement, same-card state replacement (`反馈状态` plus `✓` on every selected label), independent multi-label selection, same-label toggle-off, `market_feedback` and callback health. Every click appends one audit event containing the clicked label and complete resulting selection; an empty selection remains unlabelled without deleting history. Test rows never enter the `反馈质量` delivered or labelled denominators. Do not use `scripts/test_feishu.py` or `scripts/test_feishu_card.py` for this check: they send unrelated real test messages and are not isolated feedback regressions.
7. Only after this passes and is approved, set `FEISHU_FEEDBACK_ENABLED=1` to switch unified market cards to the application bot. The old webhook configuration remains untouched.

The installer enables `surveil-feishu-feedback.service` when either listener-only or full feedback mode is enabled. If full-feedback settings are incomplete, unified delivery fails closed on the feedback application path rather than sending a second copy through the custom webhook. Disable `FEISHU_FEEDBACK_ENABLED` to return unified cards to the existing webhook sender.

The feedback service runs the official SDK at warning level so temporary
WebSocket connection credentials are not written in INFO connection URLs. Its
stdout and stderr log files are owned by the production service account and
mode `0600`; the unit also uses `UMask=0077`. Bounded callback logs contain only
result class, card-update outcome, item kind and elapsed milliseconds.

Official dependency provenance:

- Feishu callback structure and three-second response contract: `https://open.feishu.cn/document/feishu-cards/card-callback-communication`
- Official long-connection setup and Python SDK example: `https://open.feishu.cn/document/event-subscription-guide/callback-subscription/step-1-choose-a-subscription-mode/configure-callback-request-address`
- Python package: official PyPI `https://pypi.org/project/lark-oapi/`, pinned as `lark-oapi==1.7.1` for current Python compatibility.

See [security.md](security.md) before making a repository public.
