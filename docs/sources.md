# Source Catalog

Surveil ships with a reusable source catalog focused on semiconductors, AI infrastructure, data centers, memory, advanced packaging, optical interconnects, and related supply chains.

The catalog is public configuration and code. Credentials, cookies, paid-content access, personal usernames, real portfolios, and server settings stay private.

## Why These Sources

| Source Group | Influence / Signal Value |
| --- | --- |
| Serenity on X | Useful for market-facing interpretation of AI infrastructure, photonics, memory, CPO, and global semiconductor equity narratives. It is not an official data source; treat it as a high-signal opinion stream that still needs verification. |
| TrendForce | A widely cited research provider for memory, panels, foundry, components, AI servers, MLCC, and pricing/supply-demand trends. Its public headlines and summaries often flag important supply-chain direction before broader market discussion. |
| AlphaAbstract | Public third-party summaries of AI investment research, founder/operator interviews, and infrastructure theses. Treat as a useful secondary research-summary source with preserved original-source provenance. |
| SEMI | A primary industry association for semiconductor equipment, fabs, materials, and market statistics. Its equipment forecasts and market-data releases can directly reprice equipment and components expectations. |
| DIGITIMES | Taiwan supply-chain coverage is especially relevant to TSMC, IC design, advanced packaging, servers, ODMs, PCBs, components, and AI hardware manufacturing. |
| Nikkei xTECH | Japan is important in semiconductor equipment, materials, components, industrial automation, and automotive electronics. Nikkei xTECH helps surface Japan-side technology and supply-chain changes. |
| The Elec | Korea is central to memory, HBM, OLED/display, batteries, equipment, and materials. The Elec can surface Samsung/SK hynix/LG-adjacent supply-chain signals. |
| Official company feeds | First-party announcements from OpenAI, NVIDIA, Samsung Semiconductor, SK hynix, and Micron are primary sources for architecture, product, capex, platform, and supply-chain changes. |
| Official trade-policy sources | Federal Register, USTR, European Commission and MOFCOM expose investigations, public comments, hearings, tariffs, export controls, trade remedies and official escalation language before or at formal action. |
| Sina Finance / iFinD / JYGS | China-market channels for holdings-related news, official company notices, announcements, and A-share event/action monitoring. |
| First Yicai / CLS / Star Market Daily / Jin10 / WallstreetCN | Domestic market-moving context for A-share risk appetite, hard-tech company updates, macro/Fed policy reaction, and China-side semiconductor/AI narratives. |

## X Accounts

| Source | Default Account | Method | Notes |
| --- | --- | --- | --- |
| Serenity | `aleabitoreddit` | X API filtered stream or polling | Configure privately with `X_USERNAME=aleabitoreddit` and your own X API credentials. Public posts only unless X provides an authorized API path for your account. |

Surveil does not commit X tokens. The repository only contains the monitor logic.

## Official Company Feeds

These feeds are included in `scripts/trendforce_sources.py` through `DEFAULT_RSS_FEEDS`.

| Source Key | Source | URL | Method |
| --- | --- | --- | --- |
| `openai_news` | OpenAI News | `https://openai.com/news/rss.xml` | RSS |
| `nvidia_blog` | NVIDIA Blog | `https://blogs.nvidia.com/feed/` | RSS |
| `nvidia_developer_blog` | NVIDIA Developer Blog | `https://developer.nvidia.com/blog/feed/` | Atom/RSS |
| `samsung_semiconductor_news` | Samsung Semiconductor News | `https://news.samsungsemiconductor.com/global/feed/` | RSS |
| `samsung_global_semiconductor` | Samsung Newsroom Semiconductors | `https://news.samsung.com/global/category/products/semiconductor/feed` | RSS |
| `skhynix_newsroom` | SK hynix Newsroom | `https://news.skhynix.com/feed/` | RSS |
| `micron_news_releases` | Micron News Releases | `https://investors.micron.com/rss/news-releases.xml` | RSS |

Official company news goes through the unified decision layer first. High-impact semiconductor/AI infrastructure items can be pushed immediately; lower-signal items can be collected into a daily digest, and the LLM only supplies thin interpretation or restricted supplemental judgement.

## Official Trade Policy

These public official sources are defined in `scripts/trade_policy_sources.py` and run through `scripts/news_collector.py -> scripts/trade_policy_monitor.py`.

| Source Key | Source | URL / Method |
| --- | --- | --- |
| `federal_register_china_trade` | U.S. Federal Register | Official JSON API query for recent China documents |
| `ustr_press_releases` | U.S. Trade Representative | Public press-release list and new-item detail pages |
| `eu_press_corner_trade_policy` | European Commission Press Corner | Official RSS and new-item detail pages |
| `mofcom_policy_releases` | 中华人民共和国商务部 / 政策发布 | Public policy list and new-item detail pages |
| `mofcom_spokesperson_statements` | 中华人民共和国商务部 / 新闻发言人谈话 | Public news-release list and new-item detail pages |

The first production discovery for each source is a baseline and does not replay historical items by default. New items normalize to `official_policy` content and enter the unified article store/runtime. The `trade_friction_escalation` rule is source-neutral and therefore also evaluates domestic media, industry media, research summaries, flashes and future sources. Reuters, FT and Bloomberg are not part of this source batch.

## TrendForce

Surveil includes TrendForce RSS categories and public list-page monitors.

RSS categories:

| Source Key | URL |
| --- | --- |
| `trendforce_semiconductors` | `https://www.trendforce.com/feed/Semiconductors.html` |
| `trendforce_emerging` | `https://www.trendforce.com/feed/Emerging_technology.html` |
| `trendforce_consumer` | `https://www.trendforce.com/feed/Consumer_electronics.html` |
| `trendforce_energy` | `https://www.trendforce.com/feed/Energy.html` |
| `trendforce_display` | `https://www.trendforce.com/feed/Display.html` |
| `trendforce_led` | `https://www.trendforce.com/feed/LED.html` |
| `trendforce_communication` | `https://www.trendforce.com/feed/Communication.html` |

Public page monitors include:

- Research Report latest
- DRAM
- NAND Flash
- MLCC
- Wafer Foundries
- Compound Semiconductor
- AI Server / HBM Server
- Cloud and Edge Computing
- Artificial Intelligence
- Display Supply Chain
- Upstream Components
- IR LED / VCSEL / LiDAR Laser
- Lithium Battery and Energy Storage
- Selected Topics: Semiconductors, Telecommunications, Computer System, Green Energy and Storage, Display Panel and LED
- Press Centre In-Depth Analyses

Research Report and Selected Topics pages may contain member or paid content. Surveil only reads public list-page titles/summaries and does not bypass access controls.

## AlphaAbstract

AlphaAbstract is monitored as a public third-party research-summary site. It does not currently expose RSS/Atom, so MarketPulseWire uses its public sitemap as the discovery entry and then reads public `/summaries/...` pages.

| Source Key | Source | URL | Method |
| --- | --- | --- | --- |
| `alphabstract_summaries` | AlphaAbstract / Summaries | `https://alphabstract.com/sitemap.xml` | Public sitemap + public summary pages |

The collector preserves Article JSON-LD metadata, canonical URL, published/modified dates, author, and `isBasedOn` original-source links when present. AlphaAbstract does not receive source-level push privilege: items still go through `NormalizedMarketItem`, deterministic cross-source rules, restricted interpretation, Skeptic controls, deduplication, and final `DecisionResult.action`.

## SEMI

SEMI is monitored as a first-tier semiconductor industry source, alongside TrendForce, DIGITIMES, Nikkei xTECH, and The Elec.

| Source Key | Source | URL | Method |
| --- | --- | --- | --- |
| `semi_prnewswire_semiconductors` | SEMI releases on PR Newswire Semiconductors | `https://www.prnewswire.com/news-releases/business-technology-latest-news/semiconductors-list/` | Public list page filtered to SEMI releases |

SEMI's own website may use Cloudflare or similar bot protection. MarketPulseWire does not bypass access controls. The default source therefore monitors SEMI's public releases as distributed through PR Newswire and routes them through the same LLM gate, skeptic, signal extraction, and Feishu delivery path as other major semiconductor media.

## Industry Media

These sources are defined in `scripts/media_sources.py`.

| Source Key | Source | URL | Method |
| --- | --- | --- | --- |
| `digitimes_tw_semiconductors_components` | DIGITIMES Taiwan / Semiconductors and Components | `https://www.digitimes.com.tw/tech/rss/xml/xmlrss_10_40.xml` | RSS |
| `digitimes_tw_ic_design` | DIGITIMES Taiwan / IC Design | `https://www.digitimes.com.tw/tech/rss/xml/xmlrss_30_16.xml` | RSS |
| `digitimes_tw_ic_manufacturing` | DIGITIMES Taiwan / IC Manufacturing | `https://www.digitimes.com.tw/tech/rss/xml/xmlrss_30_17.xml` | RSS |
| `digitimes_tw_ai_focus` | DIGITIMES Taiwan / AI Focus | `https://www.digitimes.com.tw/tech/rss/xml/xmlrss_30_25.xml` | RSS |
| `digitimes_tw_server` | DIGITIMES Taiwan / Server | `https://www.digitimes.com.tw/tech/rss/xml/xmlrss_30_26.xml` | RSS |
| `digitimes_en_daily` | DIGITIMES English Daily | `https://www.digitimes.com/rss/daily.xml` | RSS |
| `nikkei_xtech_all` | Nikkei xTECH | `https://xtech.nikkei.com/rss/index.rdf` | RDF |
| `thelec_kr_semiconductor` | The Elec Korea / Semiconductor | `https://www.thelec.kr/rss/S1N2.xml` | RSS |
| `thelec_kr_all` | The Elec Korea / All Articles | `https://www.thelec.kr/rss/allArticle.xml` | RSS |

These feeds are filtered by configurable media keywords before LLM gating. The default keywords cover AI, semiconductors, HBM, MLCC, advanced packaging, PCB, glass substrates, liquid cooling, optical interconnects, diamond cooling, and related infrastructure.

## Sina Finance and iFinD

| Source | Method | Credentials |
| --- | --- | --- |
| Sina Finance news | OpenAPI, MCP backup, or legacy public pages | `SINA_ZY_API_KEY` if using OpenAPI |
| iFinD notices | iFinD REST/API | `IFIND_REFRESH_TOKEN` or access token |

iFinD is the preferred source for company notices/announcements. Sina news filters out announcement-like reposts where possible so iFinD remains the authoritative notice path.

## Domestic Finance and Hard-Tech Media

These sources are defined in `scripts/china_media_sources.py` and run through `scripts/china_finance_media_monitor.py`.

| Source Key | Source | URL / Method | Notes |
| --- | --- | --- | --- |
| `yicai_brief` | First Yicai / brief news | `https://www.yicai.com/api/ajax/getbrieflist?type=0&page=1&pagesize=20` | Public JSON endpoint. The daily broker morning brief is treated as a mandatory user-requested push. |
| `cls_telegraph_api` | CLS / telegraph | `https://api3.cls.cn/v1/roll/get_roll_list` | Public frontend endpoint with low-frequency polling. Star Market Daily items inside CLS telegraph are labeled as `科创板日报 / 财联社电报`. |
| `star_market_daily_subject` | Star Market Daily / 科创板最新动态 | `https://www.cls.cn/subject/1777` | Public topic page. The monitor reads the page's public Next.js data for title, summary, stocks, subjects, timestamp, and article link. |
| `jin10_rsshub_important` | Jin10 / important events | RSSHub route | Public RSSHub backup route for important events; it may be rate-limited or temporarily unavailable. |
| `wallstreetcn_news` | WallstreetCN / 华尔街见闻 | Public `/news/global`, `/live`, and official monthly sitemaps | Peer general news-media source. Public list pages provide near-real-time ids; sitemaps provide baseline/catch-up. Public detail only; member content is not opened or used as full evidence. |

Star Market Daily is useful for China hard-tech and STAR Market signals, including semiconductors, AI, advanced manufacturing, materials, IPO/refinancing, and listed-company research notes. It is not pushed unconditionally: items still pass the media keyword/macro filters, LLM article gate, skeptic evaluator, and duplicate checks before immediate Feishu delivery.

## JYGS

JYGS action analysis is supported as a low-frequency monitor. It requires user-authorized private configuration:

- `JYGS_COOKIE` or `JYGS_SESSION`
- `JYGS_SIGN_SECRET`

Do not commit these values. If the source changes login, signing, or access rules, use only authorized access paths.

## Customization

Users can customize:

- Holdings and watchlist: `config/portfolio.json` or the Web workbench
- Media keywords: the private global rule file selected by
  `RULE_CORE_SHADOW_CONFIG`, edited through the Web workbench's `媒体关键词` page
- LLM provider: `.env` `LLM_*`
- Enabled services: systemd units/timers or local commands

The public examples are intentionally generic. Runtime choices belong in private `.env` and local config files.
