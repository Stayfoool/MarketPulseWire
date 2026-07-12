# MarketPulseWire Architecture Flow

This document summarizes the current project structure, information sources, processing layers, delivery paths, and feedback loop. It intentionally avoids private server addresses, tokens, cookies, real holdings, and personal account secrets.

## Runtime Spine Boundaries

All general research, news-media, official-company, flash, portfolio-news, notice, and report sources use the same production contract:

`collector -> NormalizedMarketItem -> market_flow -> decision_engine -> market_interpreter -> store adapter -> market_delivery -> view`

- `DecisionResult.action` is the push intent source. LLM output is normalized to `InterpretationResult` and cannot freely set or replace the push action.
- Research/news/official sources still enter through `market_content_flow`, while event-family sources still enter through `market_event_flow`; both wrappers now delegate decision and interpretation orchestration to the shared `market_flow` core. The wrappers adapt source shapes and legacy tables, not separate decision policies.
- `market_flow_adapters` owns raw event ingestion and explicit article/official/event compatibility-store writes. `market_delivery` owns article/official/event reservation, send execution, delivery status, and legacy pushed markers. Neither layer evaluates rules or calls the interpreter.
- `article_reviews`, `official_news_reviews`, and `events/event_analyses` remain compatibility storage truth for existing daily, Web, and signal readers.
- `SURVEIL_CONTENT_DIRECT_PATH` switches research/news/official production collectors as one group. `SURVEIL_EVENT_DIRECT_PATH` switches the four event-family sources as one group.
- X/Serenity intentionally stays on its `seen_posts` and direct-card route because media/thread semantics are source-specific.
- `value_directory_monitor` intentionally stays on its rule-first index/preview/OCR route and compatibility article-review storage. These two routes are explicit exclusions, not incomplete migrations.

## End-to-End Flow

```mermaid
flowchart TD
    subgraph S["Information Sources"]
        X["X / Serenity public posts"]
        Official["Official company feeds<br/>OpenAI / NVIDIA / Samsung / SK hynix / Micron"]
        Industry["Research / industry media<br/>SEMI / TrendForce / DIGITIMES / Nikkei xTECH / The Elec"]
        China["China finance and hard-tech media<br/>Sina / First Yicai / CLS / Star Market Daily / Jin10"]
        Notices["Company notices and filings<br/>iFinD"]
        JYGS["JYGS action monitor<br/>currently low priority"]
        Config["Private configuration<br/>holdings / watchlist / keywords / stock relations / market skill"]
        WebSearch["Optional Web Evidence Retrieval<br/>Tavily now / Brave later"]
    end

    subgraph C["Collectors"]
        XStream["surveil-x-stream<br/>scripts/x_stream.py"]
        ResearchCollector["surveil-research-collector<br/>scripts/research_collector.py"]
        OfficialCollector["surveil-official-collector<br/>scripts/official_collector.py"]
        NewsCollector["surveil-news-collector<br/>scripts/news_collector.py"]
        SinaFlash["surveil-sina-flash<br/>scripts/sina_flash.py"]
        SinaStock["surveil-sina-stock-news<br/>scripts/sina_stock_news.py"]
        Ifind["surveil-ifind-notice/report<br/>scripts/ifind_batch.py"]
        JYGSJob["surveil-jygs-actions<br/>scripts/jygs_actions.py"]
        WebWorkbench["surveil-holdings-web<br/>scripts/holdings_web.py"]
    end

    subgraph D["Storage and State"]
        SQLite["SQLite data/surveil.sqlite3"]
        Seen["seen_items / seen_posts / source_state"]
        Reviews["article_reviews / official_news_reviews / event_analyses"]
        Relations["portfolio_holdings / stock_relations / relation_suggestions / market_skills"]
        Evidence["web_evidence_runs / web_evidence_docs"]
        Signals["signals / signal_targets / signal_evidence / signal_outcomes / signal_reviews"]
        Health["source_health / x_stream_health / deliveries"]
    end

    subgraph A["Decision and Interpretation"]
        Keyword["keyword, macro, and hardline filters"]
        Decision["Decision layer<br/>source profile, rules, dedupe, restricted judgement"]
        Skeptic["Skeptic Evaluator<br/>old news / price-in / over-linking / hard-variable checks"]
        EvidenceLayer["Controlled evidence pack<br/>source, URL, claim, stance, source quality"]
        Interpreter["Thin interpretation<br/>core_content, brief_reason, related targets"]
        SignalExtract["signal extraction<br/>targets, thesis, evidence"]
        Outcome["outcome backfill and review<br/>iFinD quotes, returns, lessons"]
    end

    subgraph O["Outputs"]
        Feishu["Feishu cards and text alerts"]
        Daily["Daily digests<br/>article daily / official daily / signal digest"]
        Workbench["Local Web workbench<br/>holdings, settings, health, relations, feedback"]
        GitHub["GitHub repo and CI<br/>code, docs, tests, PR workflow"]
    end

    X --> XStream
    Official --> OfficialCollector
    Industry --> ResearchCollector
    China --> NewsCollector
    China --> SinaFlash
    China --> SinaStock
    Notices --> Ifind
    JYGS --> JYGSJob
    Config --> WebWorkbench
    WebSearch --> EvidenceLayer

    XStream --> SQLite
    ResearchCollector --> SQLite
    OfficialCollector --> SQLite
    NewsCollector --> SQLite
    SinaFlash --> SQLite
    SinaStock --> SQLite
    Ifind --> SQLite
    JYGSJob --> SQLite
    WebWorkbench --> SQLite

    SQLite --> Seen
    SQLite --> Reviews
    SQLite --> Relations
    SQLite --> Evidence
    SQLite --> Signals
    SQLite --> Health

    Seen --> Keyword
    Reviews --> Decision
    Relations --> Decision
    Keyword --> Decision
    Decision --> Skeptic
    Skeptic --> EvidenceLayer
    EvidenceLayer --> Skeptic
    Skeptic --> Interpreter
    Interpreter --> Feishu
    Decision --> Daily
    Reviews --> SignalExtract
    Relations --> SignalExtract
    SignalExtract --> Signals
    Signals --> Outcome
    Outcome --> Daily
    Health --> Workbench
    Reviews --> Workbench
    Signals --> Workbench
    Relations --> Workbench
```

## Source-to-Service Map

```mermaid
flowchart LR
    subgraph Sources["Sources"]
        Serenity["Serenity on X"]
        SemiAnalysis["SemiAnalysis RSS"]
        TrendRSS["TrendForce RSS"]
        TrendPages["TrendForce public list pages"]
        SemiPR["SEMI releases via PR Newswire"]
        CompanyFeeds["OpenAI / NVIDIA / Samsung / SK hynix / Micron feeds"]
        OverseasFeeds["DIGITIMES / Nikkei xTECH / The Elec feeds"]
        Domestic["First Yicai / CLS / Star Market Daily / Jin10"]
        Sina["Sina flash and stock news"]
        IfindSource["iFinD notices and reports"]
        JYGSSource["JYGS action feed<br/>currently low priority"]
        EvidenceAPI["Tavily search API"]
    end

    subgraph FetchingServices["Fetching Services"]
        XSvc["surveil-x-stream<br/>simple / long connection<br/>x_stream.py"]
        ResearchProd["surveil-research-collector<br/>oneshot timer / 5 min<br/>research_collector.py"]
        OfficialProd["surveil-official-collector<br/>oneshot timer / 10 min<br/>official_collector.py"]
        NewsProd["surveil-news-collector<br/>oneshot timer / 2 min<br/>news_collector.py"]
        SinaFlashSvc["surveil-sina-flash<br/>simple / high-frequency loop<br/>sina_flash.py"]
        SinaStockSvc["surveil-sina-stock-news<br/>oneshot timer / 30 min<br/>sina_stock_news.py"]
        IfindSvc["surveil-ifind-notice/report<br/>oneshot timer<br/>ifind_batch.py"]
        JYGSSvc["surveil-jygs-actions<br/>oneshot timer<br/>jygs_actions.py"]
    end

    subgraph LegacyServices["Historical Compatibility Units<br/>disabled after cutover"]
        RSSSvc["surveil-rss-monitor<br/>legacy 300s loop"]
        TrendSvc["surveil-trendforce-page-monitor<br/>legacy 900s loop"]
        OverseasSvc["surveil-overseas-media<br/>legacy 5 min timer"]
        ChinaSvc["surveil-china-media<br/>legacy 2 min timer"]
    end

    subgraph ShadowServices["Shadow Collector Services"]
        ResearchShadow["surveil-research-collector-shadow<br/>oneshot timer / 15 min<br/>research_collector.py"]
        OfficialShadow["surveil-official-collector-shadow<br/>oneshot timer / 30 min<br/>official_collector.py"]
        NewsShadow["surveil-news-collector-shadow<br/>oneshot timer / 10 min<br/>news_collector.py"]
        ShadowDigest["surveil-collector-shadow-digest<br/>oneshot timer / 21:05<br/>collector_shadow_digest.py"]
    end

    subgraph ProcessingServices["Non-Fetching Processing and Infrastructure"]
        SignalExtractSvc["surveil-signals-extract<br/>oneshot timer / 10 min"]
        OutcomeSvc["surveil-signal-outcome<br/>oneshot timer / 16:20"]
        ReviewSvc["surveil-signal-review<br/>oneshot timer / 16:35"]
        DigestSvc["surveil-signal-digest / article-daily<br/>oneshot timers"]
        WebSvc["surveil-holdings-web<br/>simple / local Web UI"]
        ProxySvc["surveil-proxy<br/>simple / mihomo"]
        EvidenceMod["web_evidence.py<br/>called by Skeptic"]
    end

    Serenity --> XSvc
    SemiAnalysis --> ResearchProd
    SemiAnalysis --> ResearchShadow
    TrendRSS --> ResearchProd
    TrendRSS --> ResearchShadow
    CompanyFeeds --> OfficialProd
    CompanyFeeds --> OfficialShadow
    TrendPages --> ResearchProd
    TrendPages --> ResearchShadow
    SemiPR --> ResearchProd
    SemiPR --> ResearchShadow
    OverseasFeeds --> ResearchProd
    OverseasFeeds --> ResearchShadow
    Domestic --> NewsProd
    Domestic --> NewsShadow
    Sina --> SinaFlashSvc
    Sina --> SinaStockSvc
    IfindSource --> IfindSvc
    JYGSSource --> JYGSSvc
    EvidenceAPI --> EvidenceMod
    ResearchProd --> SignalExtractSvc
    OfficialProd --> SignalExtractSvc
    NewsProd --> SignalExtractSvc
    ResearchShadow --> ShadowDigest
    OfficialShadow --> ShadowDigest
    NewsShadow --> ShadowDigest
    SignalExtractSvc --> OutcomeSvc --> ReviewSvc --> DigestSvc
```

## Fetching Service Analysis Matrix

The health page uses the same high-level grouping: fetching services are separated from non-fetching processing and infrastructure. `simple` services stay alive and generally need a restart after environment changes. `oneshot` services are started by timers, run one batch, and exit; `inactive/dead/success` means the previous batch completed successfully. After the collector cutover, the default Web health view shows production units first and hides shadow / legacy compatibility units unless explicitly requested.

| Unit | Information source | Fetch range | Main filters / routing | Runtime shape | Frequency / trigger | Processing / compatibility path | Skeptic Evaluator | Tavily / Web Evidence |
|---|---|---|---|---|---|---|---|---|
| `surveil-x-stream.service` | X API filtered stream, currently focused on Serenity and configured X rules | Public X posts received from the stream; link/card enrichment is best-effort | X stream rules, account/list configuration, local delivery status retry; no article keyword prefilter | `simple` persistent | Long connection, reconnect on failure | X source path (`seen_posts`, X card/report path); not the legacy article/event review stores | No | No |
| `surveil-research-collector.timer` -> `.service` | SemiAnalysis, TrendForce RSS/pages, SEMI/PRNewswire, DIGITIMES, Nikkei xTECH, The Elec | Official RSS/RDF/list-page entries and public article bodies when accessible | Source profile enabled filtering; RSS/RDF runs every batch; page sources are internally throttled to 15 minutes by default | `oneshot` batch | Timer every 5 minutes; page cadence default 900 seconds | `market_content_flow` compatibility wrapper -> shared `market_flow` core -> Skeptic -> compatible `article_reviews` -> delivery/view | Yes | Yes, only through Skeptic |
| `surveil-value-directory.timer` -> `.service` | ValueList international-bank research lists: stocks and industry/macro | User-account-visible list metadata plus naturally visible first-page preview image/text on detail pages; no PDF download, no purchase/VIP bypass, no cookie export | Source profile enabled filtering; international-bank target/rating, holding keyword, holding relation, and major theme strategy hard rules | `oneshot` browser batch with private server Chromium profile | Daily 08:00, persistent timer | Value directory collection plus visible first-page preview/OCR, then rule-first decision and thin Feishu cards; compatible with `article_reviews` | No | No |
| `surveil-official-collector.timer` -> `.service` | OpenAI, NVIDIA, Samsung, SK hynix, Micron official feeds | Official RSS/Atom feed entries and public article bodies when accessible | Source profile enabled filtering; official-company source list only; ordinary marketing/newsroom items are downgraded by the decision layer | `oneshot` batch | Every 10 minutes | `market_content_flow` compatibility wrapper -> shared `market_flow` core -> Skeptic -> compatible `official_news_reviews` -> delivery/view | Yes | Yes, only through Skeptic |
| `surveil-news-collector.timer` -> `.service` | First Yicai, CLS public front-end roll API, Jin10 public/RSSHub important feed, Star Market Daily | Public flash/news/list entries from configured domestic sources | Source profile enabled filtering; CLS state/backoff and source focus filtering remain collector concerns; mandatory Yicai morning brief is an auditable decision rule | `oneshot` batch | Every 2 minutes | `market_content_flow` compatibility wrapper -> shared `market_flow` core -> Skeptic -> compatible `article_reviews` -> delivery/view | Yes | Yes, only through Skeptic |
| `surveil-sina-flash.service` | Sina Finance 7x24 flash API or optional Sina ZY provider | All fetched flash rows for configured tags/provider page | Match enabled holdings by code/name/aliases or macro policy line; dedupe into `events` | `simple` persistent | Script loop, default `SINA_FLASH_POLL_SECONDS=15` seconds | `market_event_flow` compatibility wrapper -> shared `market_flow` core -> compatible `events/event_analyses` -> delivery/view | No | No |
| `surveil-sina-stock-news.timer` -> `.service` | Sina per-stock public news page or optional Sina ZY stock news provider | For each enabled holding, latest `SINA_STOCK_NEWS_PER_STOCK_LIMIT` items, default 12 | Filter announcement-like items, AI-generated pages, holding exclude keywords; direct mention/business keyword pass; ambiguous items use relevance LLM | `oneshot` batch | Every 30 minutes | Collector relevance filter -> `market_event_flow` wrapper -> shared `market_flow` core -> compatible `events/event_analyses` -> delivery/view | No; current guard is relevance LLM + freshness hint | No |
| `surveil-ifind-notice.timer` -> `.service` | iFinD notices/filings for enabled holdings | Recent notices over the configured lookback window | Holdings universe, iFinD notice kind, event dedupe; PDF text extraction when available | `oneshot` batch | 08:00 and 20:00 | `market_event_flow` wrapper -> shared `market_flow` core with disclosure context -> compatible `events/event_analyses` -> delivery/view | No | No |
| `surveil-ifind-report.timer` -> `.service` | iFinD research/report data pool, if account permissions allow | Recent configured report formulas/report names | Disabled unless report env config is present; current deployment keeps it off when iFinD permission has no report data | `oneshot` batch | 08:00 and 20:00 when enabled | Optional report adapter -> `market_event_flow` wrapper -> shared `market_flow` core -> compatible `events/event_analyses` -> delivery/view | No | No |
| `surveil-jygs-actions.timer` -> `.service` | JYGS action/limit-up feed, currently low priority | Intraday action pool entries when enabled | Requires valid login cookie/API state; `ENABLE_JYGS_TIMER=1` gates the timer; LLM prediction path for selected events | `oneshot` batch | 12:30 and 16:00 when enabled | JYGS-specific event/prediction path, not article gate | No | No |
| `surveil-research-collector-shadow.timer` -> `.service` | SemiAnalysis, TrendForce RSS/pages, SEMI/PRNewswire, DIGITIMES, Nikkei xTECH, The Elec | Same source family as the target research/industry-media collector | Source profile enabled filtering; writes JSON shadow reports only | `oneshot` shadow batch | Every 15 minutes | Report-only direct decision shadow; no production review write or delivery | No | No |
| `surveil-official-collector-shadow.timer` -> `.service` | OpenAI, NVIDIA, Samsung, SK hynix, Micron official feeds | Official RSS/Atom feed candidates | Source profile enabled filtering; compares sampled candidates to existing `seen_items` / `official_news_reviews` | `oneshot` shadow batch | Every 30 minutes | Report-only direct decision shadow; no production review write or delivery | No | No |
| `surveil-news-collector-shadow.timer` -> `.service` | First Yicai, CLS, Star Market Daily, Jin10 | Domestic public news-media candidates | Source profile enabled filtering; focus/mandatory flags and direct decision shadow; does not touch CLS production poll state | `oneshot` shadow batch | Every 10 minutes | Report-only direct decision shadow; no production review write or delivery | No | No |

Non-fetching runtime units are intentionally omitted from this table: `surveil-signals-extract`, `surveil-signal-outcome`, `surveil-signal-review`, `surveil-signal-digest`, `surveil-article-daily`, `surveil-collector-shadow-digest`, `surveil-holdings-web`, and `surveil-proxy` operate on existing state, UI, logs, proxying, or post-processing rather than fetching new market information.

Historical compatibility units remain installed and whitelisted so operators can inspect or manually run them during rollback/debugging, but production deployments keep them disabled after cutover:

| Legacy unit | Replaced by | Notes |
|---|---|---|
| `surveil-rss-monitor.service` | `surveil-research-collector.timer` and `surveil-official-collector.timer` | Kept for rollback/debugging; `DISABLE_LEGACY_RSS_MONITOR=1` keeps it off. |
| `surveil-trendforce-page-monitor.service` | `surveil-research-collector.timer` | Kept for rollback/debugging; `DISABLE_LEGACY_RESEARCH_MONITORS=1` keeps it off. |
| `surveil-overseas-media.timer` -> `.service` | `surveil-research-collector.timer` | Kept for rollback/debugging; `DISABLE_LEGACY_RESEARCH_MONITORS=1` keeps it off. |
| `surveil-china-media.timer` -> `.service` | `surveil-news-collector.timer` | Kept for rollback/debugging; `DISABLE_LEGACY_CHINA_MEDIA_MONITOR=1` keeps it off. |

The Web workbench exposes a `source_profiles.py` catalog above these systemd units. It groups sources into the six target categories used by the production cleanup plan: X / Serenity, Research / industry media, official company sources, news media, Sina portfolio stock news, and iFinD company disclosures. Source profiles now show the unified production collectors while keeping the original `source_health` monitor/source labels for historical continuity.

## Decision and Delivery Layers

```mermaid
flowchart TD
    Raw["Raw item<br/>post, RSS item, page item, flash, notice"]
    Normalize["Collection layer<br/>normalize source, title, URL, time, content shape"]
    Dedupe["Baseline and dedupe<br/>source_state, seen_items, content hash, title similarity"]
    RuleFast["Decision layer: rule quick pass<br/>source profile, holdings, macro line, hard variables"]
    Supplemental["Decision layer: supplemental review<br/>Skeptic, Web Evidence, restricted LLM judgement"]
    Decision["DecisionResult<br/>push, daily, archive, ignore, baseline"]
    Interpreter["Interpretation layer<br/>thin summary, brief reason, related targets"]
    Feishu["Immediate Feishu card"]
    Digest["Daily digest"]
    Archive["Archive only"]
    Legacy["Legacy-compatible review stores<br/>article_reviews, official_news_reviews, event_analyses"]
    Signal["Signal extraction<br/>targets, thesis, evidence"]
    Outcome["Outcome tracking<br/>1/3/5/10/20d returns, hit/miss, lessons"]
    Feedback["Human feedback in Web workbench<br/>old news, priced-in, counter evidence, relation error"]

    Raw --> Normalize --> Dedupe --> RuleFast
    RuleFast -- "clear rule hit / clear reject" --> Decision
    RuleFast -- "uncertain or evidence-sensitive" --> Supplemental
    Supplemental --> Decision
    Decision --> Legacy
    Decision -- "push" --> Interpreter --> Feishu
    Decision -- "daily" --> Digest
    Decision -- "archive / ignore / baseline" --> Archive
    Legacy --> Signal
    Feishu --> Signal
    Digest --> Signal
    Signal --> Outcome --> Feedback
    Feedback --> Supplemental
    Feedback --> RuleFast
```

## Runtime and Configuration

```mermaid
flowchart TD
    GitHub["GitHub main branch<br/>PR + CI + secret scan"]
    Mac["Mac local workspace<br/>development and private commands"]
    Server["Server runtime<br/>/opt/surveil"]
    Env["Private .env<br/>LLM, Feishu, iFinD, X, Web Evidence, proxy"]
    Proxy["Optional mihomo proxy<br/>surveil-proxy.service"]
    Systemd["systemd services and timers"]
    WebUI["Web workbench<br/>127.0.0.1:8787 via SSH tunnel"]
    DB["SQLite runtime DB"]
    Logs["logs/*.log and source_health"]

    GitHub --> Mac
    Mac -->|deploy_remote.sh| Server
    Server --> Env
    Server --> Proxy
    Server --> Systemd
    Systemd --> DB
    Systemd --> Logs
    WebUI --> Env
    WebUI --> DB
    WebUI --> Logs
```

## Main Data Tables

```mermaid
erDiagram
    seen_items ||--o{ article_reviews : "reviewed as"
    article_reviews ||--o{ signals : "extracts"
    official_news_reviews ||--o{ signals : "extracts"
    events ||--o{ event_analyses : "analyzed by"
    events ||--o{ deliveries : "delivered to"
    signals ||--o{ signal_targets : "affects"
    signals ||--o{ signal_evidence : "supported by"
    signals ||--o{ signal_outcomes : "verified by"
    signals ||--o{ signal_reviews : "reviewed by"
    stock_relations ||--o{ relation_suggestions : "candidate updates"
    market_skills ||--o{ signal_evidence : "skill evidence"
    web_evidence_runs ||--o{ web_evidence_docs : "retrieves"
    source_health ||--o{ deliveries : "alerts"
```

## Key Operating Principles

- Primary and official feeds are preferred over page scraping where available.
- Paid, logged-in, or protected content is not bypassed.
- Low-signal items go to daily digests instead of immediate Feishu alerts.
- High-impact semiconductor, AI infrastructure, macro policy, and holdings-related items pass through LLM gate plus Skeptic.
- Web Evidence Retrieval is controlled by the project: the search API returns evidence, MarketPulseWire stores and compresses it, and the configured LLM receives only the evidence pack.
- SQLite is the live runtime state. Private JSON files remain backup/migration snapshots for user-specific settings such as stock relations.
- GitHub is the code source of truth; server `.env`, SQLite, logs, proxy config, and personal holdings remain private runtime state.
