# MarketPulseWire Current Architecture

This document is an as-built map of the current code and production shape. Engineering rules live in `AGENTS.md`; active work lives in the local `docs/monitoring-plan.md`; deployment operations live in `docs/deployment.md`.

## Runtime Spine

All general research, industry-media, news-media, official-company, official trade-policy, flash, portfolio-news, company-disclosure, AlphaAbstract, and ValueList items use one runtime entry:

```text
collector
-> NormalizedMarketItem
-> process_market_item
-> decision_engine
-> market_interpreter
-> review store adapter
-> market_delivery
-> Web / digest / Feishu
```

```mermaid
flowchart LR
    Source["Source-specific collector"] --> Item["NormalizedMarketItem"]
    Item --> Runtime["process_market_item"]
    Runtime --> Decision["DecisionResult"]
    Decision --> Interpretation["InterpretationResult"]
    Interpretation --> Store["Existing review store adapter"]
    Store --> Delivery["market_delivery"]
    Store --> View["Web / digest / signals"]
    Delivery --> Outcome["DeliveryOutcome"]
```

`DecisionResult.action` is the only push-eligibility input accepted by delivery. Delivery execution may still produce `sent`, `duplicate`, `skipped`, or `failed`. Missing decisions cannot fall back to legacy push fields. For a push-eligible intraday Chinese equity market move, delivery may derive a conservative source-neutral fact identity from the Beijing market date, direction, literal concept, and an already matched holding/keyword target; the first reservation sends and later matching source retransmissions are recorded as duplicates without changing the decision.

Before an item that requires analysis enters the active decision, `market_runtime.py` calls the existing `prepare_item_for_decision()` at most once. The returned `NormalizedMarketItem`, including any validated `_attributed_research` extraction, is then reused by the active decision/store adapter and the optional report-only new-rule comparison. Baseline and already-existing event rows do not trigger this preparation. The comparison report may retain normalized institution ids but removes attribution quotes, claim quotes and body text.

Pure, source-neutral statements of an established Federal Reserve policy-transmission relationship, such as easing benefiting gold, Bitcoin, non-US currencies or metals, are deterministically downgraded from `push` to `daily` after the macro rule. This downgrade applies only when the item contains no actual policy decision, quantified rate-path repricing, quantified observed asset move, unusual inverse relationship, correction, direct Fed statement or asset-specific hard fact. It retains the original rule hit and records the initial/final action plus local evidence in the decision audit; it cannot promote a non-push action.

Push-eligible US CPI, PCE and nonfarm coverage may also receive a delivery-only identity from locally bound evidence. Preview and actual-release identities use country, indicator and reference period. The extractor considers every indicator occurrence in a claim before binding the nearest preceding reference month, so an early generic `CPI` label cannot hide a later locally complete `6月...CPI月率` fact. Market reactions use the same reference period, conservatively inferring the immediately preceding month when a reaction names the indicator but omits the period, so cross-asset and next-day retellings converge. Each phase can deliver once across sources. Corrections, policy decisions, quantified path repricing, unusual inverse relationships, asset-specific hard facts and direct Kevin Warsh statements bypass the reaction identity, including when a retained fact is mixed with already-covered market interpretation. Other cross-asset reactions to a Fed easing or tightening impulse without a named data release share one direction-specific 14-day delivery identity. The extractors use original item text and deterministic evidence only; delivery dedup does not change the decision or use an LLM.

Push-eligible industry-hardline coverage may receive a bounded 36-hour delivery-only fact identity when original text deterministically supplies subject, event, stage, object and direction. The initial event families cover IBM enterprise spending shifting toward memory hardware and CoreWeave exploring derivatives to hedge storage-chip price downside. Cross-source rewrites remain push decisions but are recorded as duplicates. Corrections, company confirmation or denial, execution-stage changes, material derivative terms and independently attributable HBM/DRAM/NAND supplier production facts bypass the prior identity.

Push-eligible holding or industry-hardline coverage may also receive source-neutral company-event delivery identities. Claim-local stock codes, direct holding entities and validated company-name/action grammar resolve explicit subjects without an issuer allowlist. Common company actions use strict structured slots, while the conservative generic path requires an explicit subject, action family, reference/effective time and distinctive counterparty, object or quantitative anchor. Each item may produce a fact set rather than one selected key. The delivery layer reserves every new identity in one immediate SQLite transaction, suppresses only when the entire set is already covered, confirms all reservations after send success and releases all after failure. Stable event identity is separated from lifecycle/material version so equivalent or subset restatements deduplicate while explicit corrections, revisions, approvals, completions and terminations remain deliverable. The predecessor's five bounded keys remain only as migration aliases. These execution records preserve the original `DecisionResult.action=push`.

The former direct/compat route switch and these wrapper modules have been removed:

- `article_gate.py`
- `official_news_gate.py`
- `content_runtime.py`
- `event_runtime.py`
- `market_content_flow.py`
- `market_event_flow.py`
- `event_pipeline.py`

## Module Ownership

| Module | Current responsibility |
|---|---|
| `market_runtime.py` | Normalization boundary, one-time pre-decision evidence preparation, store adapter selection, orchestration, fail-closed contract handling |
| `decision_engine.py` | Deterministic `DecisionResult`, including final push action |
| `rule_core_v1.py` | Side-effect-free candidate admission/decision core used by the public v1 behavior corpus and the optional production report-only comparison; exports an explicit rule-core version that changes only when rule behavior changes; bounded admission evidence includes all matched content-family evidence and global exclusion evidence, trade-policy scope preserves joint-or-two-sided matching, trusted attribution binds canonical ids to aliases/domains, and its result has no storage or delivery authority |
| `llm_rule_catalog.py` | Versioned catalog of the 22 human-reviewed strength-decision rules across the five existing content rule groups; stores allowed actions, rule text, required facts and exclusions without matching article text, reading private configuration or calling a model |
| `llm_rule_decision.py` | Report-only LLM decision contract: applies the confirmed company-disclosure/Sina-stock-news holding-only source boundary, selects rules only from an existing admitted `AdmissionResult`, builds an untruncated bounded prompt input, strictly validates fixed JSON/rule/action/version/verbatim-evidence consistency and projects only a valid response to `DecisionResult`; it has no review, storage, delivery or dedup authority |
| `llm_rule_shadow.py` | Optional report-only LLM candidate: evaluates the same prepared production `NormalizedMarketItem`, calls the configured text model once for an admitted item, validates the response through `llm_rule_decision.py`, and returns a bounded comparison candidate. Missing full text, model failure or invalid output produces no candidate action and remains visible as unable to compare |
| `investment_bank_research.py` | Side-effect-free local extraction shared by the active investment-bank rating wrapper and the report-only candidate: trusted institution, holding/industry subject, rating/target-price/coverage action, allocation action or complete rotation, and verbatim evidence; it does not read Rule Center, SQLite, source profiles or deliver messages |
| `rule_core_fixture.py` | Strict loader for the sanitized public v1 behavior corpus; test/spec support only |
| `market_lifecycle_v1.py` | Inactive lifecycle/source-integration contract, bounded discovery shape, legal transitions and honest read-only projections over the current article/event physical stores |
| `rule_config_migration_v1.py` | Inactive redacted preview of explicitly supplied legacy keyword origins versus a reviewed v1 target configuration; never writes configuration or prints keyword values |
| `rule_core_replay.py` | Inactive no-write comparison of explicit current outcome snapshots against the pure v1 core, including changed fields and source-invariance violations |
| `rule_core_history_replay.py` | Inactive operator-only reader for an explicit local SQLite snapshot; strict mode requires stored full text and uses `mode=ro`/`query_only`, while optional title/summary proxy screening is explicitly non-comparative; delegates comparison to `rule_core_replay.py` |
| `rule_core_shadow.py` | Side-effect-free comparison of the active `DecisionResult` with `rule_core_v1` for the same normalized item; records only bounded differences and cannot change review or delivery |
| `rule_core_runtime_shadow.py` | Optional production report writer called after the current `DecisionResult` exists and before delivery; uses the same prepared production `NormalizedMarketItem` as the active decision. `RULE_COMPARISON_CANDIDATE` selects the deterministic new-rule candidate by default or the reviewed LLM candidate only when privately set to `llm`. It records comparison time, candidate engine/version, private configuration version and deployed code revision, caches explicit private configuration by file identity/mtime/size, and cannot write reviews, reserve delivery keys or send messages |
| `rule_core_shadow_combined.py` | Report-only combiner for existing comparison reports; preserves per-item comparison status, candidate engine/version, bounded rule evidence, model metadata, token usage and elapsed time, and refreshes one latest Markdown/JSON view across research, official and news production batches. Unable-to-compare rows remain visible and are not counted as action downgrades |
| `rule_core_shadow_daily.py` | Report-only daily review job; freezes one 15:30-to-15:30 Beijing Markdown/JSON report and sends at most one Feishu reminder when the interval has comparable or unable-to-compare items. Report/reminder labels follow the selected candidate. An explicit historical rebuild only re-aggregates retained comparisons, records that the candidate was not re-evaluated and preserves any prior sent reminder |
| `rule_shadow_report_store.py` | Bounded read-only loader for dated rule comparison reports used by the authenticated Web workbench; the Web view can combine latest-rule-version, action-change and current/new action filters in memory, while the loader cannot evaluate rules or send messages |
| `rule_core_shadow_report.py` | Inactive operator-only reader for a shadow collector JSON report; supplies complete retained item text to `rule_core_shadow.py` and writes only a bounded comparison report |
| `run_production_with_rule_shadow.py` | Production service entry wrapper; runs the existing collector once and, when comparison is enabled, refreshes the combined report after runtime comparison files have been written; it does not start a second collector or evaluate rules |
| `ai_credit_risk.py` | Source-neutral deterministic AI borrower, funding-event and qualitative credit-stress evidence classification |
| `ai_compute_supply_demand.py` | Source-neutral deterministic AI compute supply, demand, capacity and constraint classification |
| `macro_policy.py` | Source-neutral macro-data release/reaction and generic Fed policy-transmission evidence classification; production wrappers read Rule Center lazily and preserve the active production decision contract |
| `trade_friction.py` | Source-neutral China-US / China-EU trade-friction classification and evidence extraction |
| `trade_policy_monitor.py` | Official API/RSS/list discovery, new-item detail enrichment, baseline and source health |
| `company_disclosures.py` | One logical portfolio-disclosure collector, provider selection, baseline, source state and health |
| `disclosure_providers.py` / `cninfo_disclosure_provider.py` | Provider-neutral disclosure contract and CNINFO public-query transport |
| `disclosure_document.py` | Shared bounded PDF download, SHA-256 and `pypdf` text extraction |
| `market_interpreter.py` | Thin interpretation and bounded LLM output normalization |
| `market_content_adapter.py` | Article and official-news compatibility payload/store shape |
| `market_event_adapter.py` | Event compatibility payload/store shape |
| `market_review_store.py` | SQLite review/event persistence and historical row loading |
| `market_delivery.py` | Rule/fact dedup reservation, Feishu execution, delivery status, pushed markers |
| `market_feedback.py` | Cross-source append-only human feedback, signed item identity, last-click-wins projection and quality aggregates |
| `feishu_app.py` / `feishu_feedback_service.py` | Feedback-enabled application-bot send and official long-connection card callbacks |
| `macro_event_dedup.py` | Delivery-only US macro preview/release/reaction and Fed policy cross-asset reaction identities, including mixed-Warsh handling |
| `industry_fact_dedup.py` | Bounded delivery-only industry fact identities and material-update exclusions |
| `company_event_dedup.py` | Generic claim-local company-event fact sets, lifecycle versions and legacy reservation aliases |
| `market_view.py` | Read-only unified projection across existing stores |
| `source_profiles.py` | Source catalog, runtime ownership, health keys and editable source settings |

## Production Sources

| Source group | Production entry | Item processing |
|---|---|---|
| Research and industry media | `research_collector.py` -> `rss_monitor.py` / `trendforce_page_monitor.py` / `alphabstract_monitor.py` | Unified runtime, article store |
| Official company feeds | `official_collector.py` -> `rss_monitor.py` | Unified runtime, official-news store |
| Domestic and overseas news media | `news_collector.py` -> `china_finance_media_monitor.py` / `wallstreetcn_monitor.py` / RSS helpers | Sina, Yicai, CLS, Jin10 and WallstreetCN public article/flash discovery; unified runtime, article store |
| Official trade policy | `news_collector.py` -> `trade_policy_monitor.py` | Federal Register, USTR, European Commission and MOFCOM public sources; unified runtime, article store |
| Sina 7x24 flash | `sina_flash.py` | Unified runtime, event store |
| Sina portfolio stock news | `sina_stock_news.py` | Relevance enrichment, then unified runtime and event store |
| Company disclosures | `company_disclosures.py` -> `cninfo_disclosure_provider.py` | Twice daily CNINFO fulltext/relation discovery and official-PDF enrichment; report-only writes baseline event audits, while live mode enables analysis and delivery |
| AlphaAbstract research summaries | `alphabstract_monitor.py` through `research_collector.py` | Public sitemap/page enrichment, then unified runtime and article store |
| ValueList research directory | `value_directory_monitor.py` | Private browser/OCR enrichment, then unified runtime and article store |

Source-specific login, WAF, API, sitemap discovery, polling, browser profile, OCR and attachment behavior ends before the normalized runtime boundary.

Domestic finance media now reserve each technically identifiable live discovery in `seen_items` before detail enrichment, then record processability and construct one `NormalizedMarketItem`. The current admission check and the report-only v1 comparison fork from that same normalized item. Current-admitted items continue through the active decision/review/delivery path; current-excluded items may produce only a bounded comparison report and do not invoke the current interpreter, review store or delivery. Rediscovered domestic items whose processability, admission evaluation or processing remains `pending`/`failed_retryable` are eligible for retry without deleting their discovery reservation.

This lifecycle integration is intentionally incomplete across sources. Overseas/industry RSS, TrendForce pages and official-company RSS still apply their existing media-focus filter before `seen_items`; their newly inserted rows therefore remain `legacy_unclassified` until a later reviewed migration. Event-backed sources retain their existing `events` lifecycle fields and adapters.

Synchronous HTTP connection pools are isolated per worker thread. A source retry or timeout-key change may close only that thread's client; concurrent collectors cannot close another thread's in-flight TLS connection or leave a stale network writer targeting a reused SQLite file descriptor.

Ordinary bounded collector/provider requests use the shared `http_utils` transport. This includes CNINFO's form-encoded JSON lookup and disclosure-list POSTs, whose provider adapter retains only its required headers, form shape, response validation and `CninfoError` contract. Direct `urllib.request` runtime use is closed by an architecture-invariant registry: current entries are bounded streaming/binary transfers, the X long-lived stream, provider-specialized LLM/Feishu behavior, explicitly tracked legacy request paths and standalone operator tools. The shared buffered response helper is not used for disclosure PDFs because their downloader enforces a byte ceiling while streaming to an atomic temporary file.

Company disclosures use the logical source `company_disclosures`. `transport_provider` remains raw audit metadata and cannot affect importance or action. The current fixed provider factory contains `cninfo_public`; a future provider implements the same security-resolution and paginated-list contract and is selected through the private source profile. CNINFO `orgId` mappings, provider baselines and provider-neutral known identities use the existing `source_state`. Fulltext announcements and `relation/category_dyhd_szdy` investor-relations records are queried separately, then normalized identically. A provider's first successful run and every `report_only` discovery enter the unified event runtime only as `baseline_only` audits with analysis and delivery disabled. They remain visible behind Event Center's baseline filter but cannot create a decision, AI interpretation or notification. Historical `ifind_notice` event rows remain readable compatibility data; the expired iFinD announcement timer is removed.

CLS telegraph collection preserves bounded official product metadata in the normalized raw audit: numeric `type`, the official bracketed product label, `share_img`/VIP status, and parsed `author_extends` stock names/codes. Article cards display these fields for an observation phase approved by the user. The metadata does not enter deterministic rule matching, importance or `DecisionResult.action`; the existing public `content` remains the decision text.

The `trade_friction_escalation` rule is not tied to the official source group. It runs in `decision_engine.py` for every normalized current or future source. Explicit policy procedures, instruments, retaliation or worsening China-US / China-EU relations can produce `push`; weaker explicit tension can produce `daily`; routine administrative reviews and generic diplomacy do not receive an alert action.

The `international_bank_fed_rate_path_revision` rule is also source-neutral. It requires local attributed evidence that an audited major international bank changed its expected Federal Reserve hike/cut direction, count, timing, cumulative basis points or terminal rate. Material revisions produce `push`; a concrete current forecast without a provable revision produces `daily`. WallstreetCN identity and category metadata cannot create eligibility. Same-report reposts use the existing `rule_alert_dedup` reservation, while a later genuine path revision remains eligible.

Attributed-research delivery identities normally use the validated institution, topic, event family and locally retained horizon. The feedback-confirmed SEMI 2026 equipment-sales forecast uses a bounded canonical report identity anchored by institution, equipment-sales subject, 2026 horizon and normalized USD 165.9 billion metric; Chinese and English rewrites converge while each rewrite carries its prior generic hash as a migration alias. Other SEMI reports continue using the generic attributed-research identity.

The ordered `investment_bank_rating_target_direct_holding` rule requires one local evidence window to bind a recognized institution, one directly mentioned holding and an actual rating, target-price or coverage action. An attached collector symbol, a generic earnings-estimate revision or institution/holding/action terms scattered across a multi-company article cannot create this rule hit. Bounded adjacent-sentence attribution is accepted only when the second sentence explicitly continues with `该行` / `其` / `the bank` or an equivalent report reference.

The report-only new rule core applies trusted-institution rating, target-price and coverage changes only to the holding rule family. It applies explicit buy/sell/long/short/add/reduce/overweight/underweight allocation changes and complete two-sided rotations only to the holding and semiconductor/AI rule families. Macro-data releases, international-bank Fed-path forecasts/revisions and trade-policy changes continue through their dedicated content rules; an allocation verb plus a macro/Fed/trade term cannot promote those families or replace their evidence requirements.

Within the same report-only `fed_policy` decision group, a separately reported material view from a configured trusted international bank's explicitly identified chief executive or chair can use the existing `fed_policy_material_exception`. It requires local leader attribution and at least two independently supported signals across an explicit stocks/long-Treasuries stance, a directional or quantified rate/yield view, and a material cross-asset risk judgment. A bank name, analyst comment, generic leadership interview or single-asset valuation view cannot create `push`; the active production international-bank/Fed wrappers are unchanged.

The Rule Center exposes execution semantics from the runtime registry. Rules inside `first_matching_push_rule()` use `ordered_first_match` and retain an editable priority. Fed-path, trade-friction, attributed-research, industry-hardline and AI credit-risk rules are evaluated independently in `decision_engine`, use `parallel_merge`, and expose no priority setting; multiple push-eligible hits are combined rather than suppressing one another.

The `ai_hyperscaler_credit_stress` rule is source-neutral and uses deterministic local evidence only. It covers Alphabet/Google, Amazon/AWS, Meta, Microsoft, Oracle, NVIDIA, SpaceX and OpenAI when AI infrastructure purpose and debt context are locally bound. Ordinary issuance and one qualitative concern produce `daily`; an explicit financing/capex/rating/liquidity hard outcome, or at least two independent stress families including a concrete market outcome, can produce `push`. The rule uses no LLM extraction, external bond feed or numeric spread/leverage threshold. Generic financing no longer counts as an industry-hardline capex/investment event by itself.

The report-only new rule invocation additionally treats a formally maintained regulatory guarantee, collateral or letter-of-credit requirement and a locally bound AI-infrastructure credit-rating downgrade as hard funding outcomes. Possible or unconfirmed future requirements and background contract/investment amounts do not qualify. The shared classifier keeps this extension off by default so the active production wrapper retains its existing behavior until the new rule core is approved for authority.

The `ai_compute_supply_demand` rule is a source-neutral deterministic `parallel_merge` rule. It binds subject, compute resource, event, direction, stage and verbatim evidence. Generic confidence, forecasts, non-binding intentions, downstream demand and unbound price moves remain `daily` or unmatched. Its catalyst identity uses the existing atomic `rule_alert_dedup` path.

## Storage

The project keeps the existing physical stores:

- `article_reviews`
- `official_news_reviews`
- `events` / `event_analyses`
- `seen_items`, `seen_posts`, `source_state`
- `rule_alert_dedup`, `deliveries` (`rule_alert_dedup` also records delivery-only intraday market-move, US macro event, bounded industry-fact and generic company-event fact-set reservations)
- `market_feedback` (append-only Feishu feedback events; the latest valid operator/item click is the current projection)
- `source_health`, `x_stream_health`
- portfolio, relation, evidence and signal tables

`article`, `official` and `event` are storage/audit identities, not decision-pipeline identities. All three arrive through the unified runtime above. `article_reviews` remains the broad media/research review store, `official_news_reviews` remains an active compatibility store for official-news readers and daily output, and `events` / `event_analyses` / `deliveries` retain event identity, repeated analyses and explicit delivery audits. Their schemas originated before runtime unification, but current production readers still use them; removing them requires a separate canonical-schema migration with backfill, dual-read verification and rollback.

`seen_items` keeps discovery identity as its primary responsibility. Additive compatibility columns record `collection_class`, processability, admission and processing status for newly collected domestic finance-media items. Existing rows are migrated as `legacy_unclassified`; no historical baseline/exclusion/failure state is inferred. `DecisionResult.action` and delivery status are not copied into this ledger and remain authoritative in the existing review/delivery paths.

`push_now`, `should_push_now` and `should_push` remain compatibility columns for historical readers and old rows. New delivery code does not read them as action inputs. `pushed_at` and delivery rows record what happened, not what should be sent.

When Feishu market feedback is explicitly enabled, unified article, official-news and event cards are sent by the configured enterprise application bot and carry signed `特别有用` / `重复` / `无效` actions. The delivery audit retains the feedback-card base payload for cards sent after this feature is enabled. After a valid action, the official long-connection callback appends only to `market_feedback` and returns a replacement of that same Feishu card with `反馈状态` and a `✓` on the current label; clicking that selected label again appends a superseding `cleared` event and restores the unselected card instead of deleting history. It cannot modify decisions, delivery reservations, source settings or rule settings. Legacy cards without a retained base payload keep their Toast acknowledgement rather than receiving a lossy replacement. `FEISHU_FEEDBACK_LISTENER_ENABLED` may start that listener for an isolated test card while leaving natural unified delivery on the pre-existing custom webhook. Test-card rows and current `cleared` states are excluded from quality denominators and Event Center feedback projection. Current feedback is selected by Feishu action time, then insertion id, so delayed callbacks cannot overwrite or cancel a newer choice. The Web workbench exposes feedback coverage and observed labelled-card outcomes by source, primary rule, all rule associations and source-by-primary-rule. Its Event Center also reads the same current projection through `item_kind + source + item_id`, showing feedback on the three active store adapters and filtering inside each store query before limits. This projection is read-only, excludes test cards and operator identities, and distinguishes delivered-but-unlabelled, not-delivered and unsupported-route rows.

The Web workbench exposes a lightweight authenticated `/api/health/summary` projection for separate Task Health and Information Sources badges. One batched read-only `systemctl show` call pairs each production timer with its execution service; `task_failures` counts current logical-task failures, while `source_failures` counts only failing enabled profiles that are visible in the Information Sources view. Shadow units, cut-over legacy units, the disabled-by-default JYGS path and disabled source profiles do not contribute. The browser refreshes this summary only while visible. The full Task Health view retains detailed systemd rows, raw source-health/X connection diagnostics and bounded log tails even when a raw diagnostic does not map to a source-profile badge count.

The optional comparison remains report-only. With the private switch enabled, `process_market_item` first completes the current decision, then passes that exact production `NormalizedMarketItem` and current `DecisionResult` to the optional comparison writer before delivery. The writer emits one bounded per-item JSON record without a body field and records comparison time, candidate engine/version, private configuration version and deployed code revision; configuration, import, evaluation or write failure is isolated from the active result. The production collector wrapper never re-collects the item and only refreshes `rule-core-shadow-combined-latest` after the normal collector exits successfully. Candidate results do not change the current `DecisionResult`, review storage, dedup reservation or delivery. When the private comparison switch is enabled, `surveil-rule-shadow-daily.timer` runs at 15:30 Beijing time and freezes `rule-core-shadow-daily-YYYY-MM-DD.md/json` for the preceding 24-hour 15:30 boundary. It sends one Feishu reminder only when that interval has comparable or unable-to-compare items, and a successful reminder prevents duplicate sends for the same report date. The authenticated Web workbench reads these dated JSON files through a bounded read-only API and can combine latest-candidate-version, execution-status, action-change and current/candidate action filters in memory; it exposes no rule, decision, configuration or delivery mutation from that view. Comparison before the production `NormalizedMarketItem` boundary is not implemented.

The reviewed LLM rule catalog, validation contract and optional report-only runtime are connected behind a private selector. The selector defaults to the deterministic new-rule candidate, so deploying this code does not create model calls. When `RULE_COMPARISON_CANDIDATE=llm` is explicitly approved and configured, each admitted production item is sent once to the configured text model after the current production decision already exists. Full text is required unless the source id is explicitly listed in the private title/summary-only allowlist. The current production action is not included in the model input. A valid structured result can create only a report candidate; model unavailability, insufficient input, invalid JSON, invalid evidence or conflicting output creates no candidate action and cannot fall back to the deterministic candidate. Reports retain bounded evidence, model name/provider hostname, token usage, attempts and elapsed time, but not the complete article body, provider raw response or response id. The Web workbench can filter completed and unable-to-compare rows and inspect these bounded fields. Fixed-response CI covers every action allowed by every catalog rule, source applicability, complete-input handling, unknown/missing/duplicate rules, undefined actions, invalid JSON, verbatim evidence, final-action conflicts, prompt injection and failure results with no candidate action.

The same `holdings_web.py` process serves the workbench shell and its same-origin assets. `web/index.html` owns the document structure, `web/styles.css` owns presentation and `web/app.js` owns browser rendering and `/api/*` calls. The Python handler substitutes only the environment/token-hint placeholders and exposes an explicit `/static/styles.css` and `/static/app.js` allowlist; it is not a generic file server. API routes, authentication behavior, loopback binding and SSH-tunnel access remain unchanged.

Holdings preview always applies whole-list local structure validation, but market-name lookup runs only for a new, newly enabled or changed symbol/name/alias identity. Keyword and business-description edits do not revalidate unchanged identities. Preview returns a short-lived process-local signed token bound to the normalized payload and current portfolio revision. Save verifies that token and revision inside the existing file lock, then performs the atomic private-JSON write and SQLite import without external network I/O. An already-current identical payload returns an idempotent no-change result without another backup or import only when the SQLite portfolio projection also matches; a partial prior JSON-write/SQLite-import failure repairs only the SQLite projection. The browser exposes validating/saving/refreshing states and prevents concurrent holdings submissions; bounded request logs retain only request id, duration, digest prefix, remote-check count and outcome.

## Independent Routes

### X / Serenity

`x_stream.py` keeps its dedicated stream, thread/media enrichment, `seen_posts` state and X card delivery. The general article/event stores do not currently represent those semantics cleanly. Regression coverage lives in `test_x_stream_health.py`.

Review condition: reconsider convergence when X posts can be represented without losing thread/media rendering or stream retry state.

### JYGS Actions

`jygs_actions.py` remains a disabled-by-default legacy product path for JYGS action prediction and its dedicated card. It is not a general market-information source profile. Its direct LLM prediction contract is isolated in that module and covered by `test_jygs_actions.py`.

Review condition: retire the path or move it behind `NormalizedMarketItem` and deterministic decisions before enabling it as a normal production source.

## Runtime And Deployment Facts

- Production runs on an Alibaba Cloud Debian 12 server under systemd; collector timers and persistent services are listed in `docs/deployment.md`.
- The server Web panel and private server `.env` are the production configuration truth.
- Private `.env`, portfolio data, SQLite, browser profiles, cookies and local source overrides are excluded from Git and deployment replacement.
- X/Serenity and ValueList access stay within the API/account-visible boundary; the project does not bypass subscriptions, paywalls, WAF or authentication controls.
- CI compiles scripts, checks shell syntax, scans secrets and invokes `scripts/run_test_suite.py`, the same canonical CI-safe regression manifest used by `Justfile`. Every `scripts/test_*.py` is classified exactly once. The three Feishu/X operator smoke scripts remain outside ordinary CI because they load private configuration and send messages, fetch live X content or upload media. Manifest drift fails closed before tests run, and `test_architecture_invariants.py` remains part of the required suite to prevent the unified spine from drifting.
