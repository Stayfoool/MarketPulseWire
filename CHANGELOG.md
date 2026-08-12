# Changelog

All notable public changes to MarketPulseWire are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html) for tagged releases.

## [1.0.0] - 2026-08-12

### Added

- Unified market-information processing from collectors through `NormalizedMarketItem`, range admission, `process_market_item`, `decision_engine`, `market_interpreter`, review storage, delivery, and Web/digest views.
- Strict LLM degree decisions with reviewed private rules, per-rule `push` / `daily` / `archive` results, minimum source-evidence validation, and `push > daily > archive` aggregation.
- Fail-closed `failed_retryable` handling for unavailable models, invalid output, evidence failures, rule/version conflicts, and private-audit failures.
- Web workbench for market information, LLM decision review, rules, feedback, source profiles, task/source health, private settings, media keywords, and holdings management.
- Feishu card delivery, delivery deduplication, execution audit, and optional signed usefulness feedback.
- Collectors and source definitions for official company feeds, official trade-policy sources, CNINFO disclosures, China financial media, semiconductor and AI supply-chain media, research summaries, and an optional logged-in X browser source.
- Linux systemd deployment, source health and backoff, log retention, GitHub Actions CI, PR governance, optional SSH deployment, and strict production verification.
- Public security, compliance, source, deployment, architecture, and contribution documentation.

### Security

- Production credentials, real portfolios, browser profiles, private range-admission rules, private LLM decision rules, SQLite data, paid content, and sensitive LLM request/response audits remain outside Git.
- `DecisionResult.action` is the only immediate-push authority; collectors, legacy push fields, interpretation, storage, delivery status, and deduplication cannot create or change eligibility.

[1.0.0]: https://github.com/Stayfoool/MarketPulseWire/releases/tag/v1.0.0
