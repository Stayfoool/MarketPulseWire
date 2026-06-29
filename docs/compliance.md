# Compliance

MarketPulseWire is designed for personal research workflows. It helps collect, summarize, and route market information for the operator's own research use.

## Source Policy

Preferred sources:

- Official APIs
- Official RSS/Atom/RDF feeds
- Public pages that allow normal access
- User-authorized APIs or exports

Avoid:

- Using unknown mirrors for official content
- Publishing raw paid content, cookies, tokens, private API responses, personal portfolios, or generated reports that contain private data

## Paid or Logged-in Sources

If a data source requires a subscription, login, cookie, or token:

- Store credentials only in `.env` or another private secret store.
- Keep retrieved paid/full text content private unless your license clearly allows redistribution.
- Commit code, schemas, and examples rather than private retrieved content.

## X / Social Media

Use official APIs where available. Keep private or subscription-only material private unless your account and the platform terms allow the intended use.

## Market Data

Some data providers restrict redistribution. For open-source publication, keep raw licensed data private and publish only code, schemas, configuration examples, and sanitized fixtures.

## Investment Disclaimer

MarketPulseWire generates research candidates and summaries. It is not investment advice, not a recommendation system, and not a substitute for independent judgment.
