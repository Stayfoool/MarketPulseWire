# MarketPulseWire Architecture Flow

This document summarizes the current project structure, information sources, processing pipeline, delivery paths, and feedback loop. It intentionally avoids private server addresses, tokens, cookies, real holdings, and personal account secrets.

## End-to-End Flow

```mermaid
flowchart TD
    subgraph S["Information Sources"]
        X["X / Serenity public posts"]
        Official["Official company feeds<br/>OpenAI / NVIDIA / Samsung / SK hynix / Micron"]
        Industry["Industry media and research<br/>SEMI / TrendForce / DIGITIMES / Nikkei xTECH / The Elec"]
        China["China finance and hard-tech media<br/>Sina / First Yicai / CLS / Star Market Daily / Jin10"]
        Notices["Company notices and filings<br/>iFinD"]
        JYGS["JYGS action monitor<br/>currently low priority"]
        Config["Private configuration<br/>holdings / watchlist / keywords / stock relations / market skill"]
        WebSearch["Optional Web Evidence Retrieval<br/>Tavily now / Brave later"]
    end

    subgraph C["Collectors"]
        XStream["surveil-x-stream<br/>scripts/x_stream.py"]
        RSS["surveil-rss-monitor<br/>scripts/rss_monitor.py"]
        Overseas["surveil-overseas-media<br/>scripts/overseas_media_monitor.py"]
        TrendPage["surveil-trendforce-page-monitor<br/>scripts/trendforce_page_monitor.py"]
        ChinaMedia["surveil-china-media<br/>scripts/china_finance_media_monitor.py"]
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

    subgraph A["Analysis"]
        Keyword["keyword and macro/hardline filters"]
        Gate["LLM importance gate<br/>article_gate / official_news_gate / event_pipeline"]
        Skeptic["Skeptic Evaluator<br/>old news / price-in / over-linking / hard-variable checks"]
        EvidenceLayer["Controlled evidence pack<br/>source, URL, claim, stance, source quality"]
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
    Official --> RSS
    Industry --> RSS
    Industry --> Overseas
    Industry --> TrendPage
    China --> ChinaMedia
    China --> SinaFlash
    China --> SinaStock
    Notices --> Ifind
    JYGS --> JYGSJob
    Config --> WebWorkbench
    WebSearch --> EvidenceLayer

    XStream --> SQLite
    RSS --> SQLite
    Overseas --> SQLite
    TrendPage --> SQLite
    ChinaMedia --> SQLite
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
    Reviews --> Gate
    Relations --> Gate
    Keyword --> Gate
    Gate --> Skeptic
    Skeptic --> EvidenceLayer
    EvidenceLayer --> Skeptic
    Skeptic --> Feishu
    Skeptic --> Daily
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
        EvidenceAPI["Tavily search API"]
    end

    subgraph Services["Systemd Services and Scripts"]
        XSvc["surveil-x-stream<br/>x_stream.py"]
        RSSSvc["surveil-rss-monitor<br/>rss_monitor.py"]
        TrendSvc["surveil-trendforce-page-monitor<br/>trendforce_page_monitor.py"]
        OverseasSvc["surveil-overseas-media<br/>overseas_media_monitor.py"]
        ChinaSvc["surveil-china-media<br/>china_finance_media_monitor.py"]
        SinaFlashSvc["surveil-sina-flash<br/>sina_flash.py"]
        SinaStockSvc["surveil-sina-stock-news<br/>sina_stock_news.py"]
        IfindSvc["surveil-ifind-notice/report<br/>ifind_batch.py"]
        EvidenceMod["web_evidence.py<br/>called by Skeptic"]
    end

    Serenity --> XSvc
    SemiAnalysis --> RSSSvc
    TrendRSS --> RSSSvc
    CompanyFeeds --> RSSSvc
    TrendPages --> TrendSvc
    SemiPR --> TrendSvc
    OverseasFeeds --> OverseasSvc
    Domestic --> ChinaSvc
    Sina --> SinaFlashSvc
    Sina --> SinaStockSvc
    IfindSource --> IfindSvc
    EvidenceAPI --> EvidenceMod
```

## Decision and Delivery Pipeline

```mermaid
flowchart TD
    Raw["Raw item<br/>post, RSS item, page item, flash, notice"]
    Normalize["Normalize<br/>canonical URL, title, published_at, source module"]
    Dedupe["Deduplicate<br/>source_state, seen_items, content hash, title similarity"]
    Prefilter["Prefilter<br/>media keywords, holdings/watchlist, macro policy, industry hardline"]
    Gate["LLM Gate<br/>importance, push_now, affected targets, market impact"]
    Hardline["Hardline Overrides<br/>capex, HBM/HBM4, Nvidia AI rack delay, quantified supply-chain variables"]
    Skeptic["Skeptic Evaluator<br/>old news, price-in, weak evidence, over-linking"]
    WebEvidence["Optional Web Evidence Retrieval<br/>prior coverage, primary source, counter evidence, macro background"]
    PushDecision{"Push now?"}
    Feishu["Immediate Feishu card"]
    Digest["Daily digest"]
    Archive["Archive only"]
    Signal["Signal extraction<br/>targets, thesis, evidence"]
    Outcome["Outcome tracking<br/>1/3/5/10/20d returns, hit/miss, lessons"]
    Feedback["Human feedback in Web workbench<br/>old news, priced-in, counter evidence, relation error"]

    Raw --> Normalize --> Dedupe --> Prefilter --> Gate --> Hardline --> Skeptic
    Skeptic --> WebEvidence --> Skeptic
    Skeptic --> PushDecision
    PushDecision -- "high and push_now" --> Feishu
    PushDecision -- "medium or downgraded" --> Digest
    PushDecision -- "low or duplicate" --> Archive
    Feishu --> Signal
    Digest --> Signal
    Signal --> Outcome --> Feedback
    Feedback --> Skeptic
    Feedback --> Gate
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

