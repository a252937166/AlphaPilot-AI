# Data Sources

## Free-first foundation

| Source | Initial role | Production caveat |
|---|---|---|
| AKShare | Broad public-data adapter, A-share bars, snapshots, sectors, macro and disclosures | Upstream web interfaces may change; review commercial-use terms per dataset |
| BaoStock | A-share historical daily bars and valuation fields; cross-check source | Not a complete real-time or point-in-time institutional dataset |
| Tushare Pro | Security master, calendars, adjustments, financials and disclosure dates | Many endpoints require points or paid permissions |
| Futu OpenAPI | Real-time watchlist/holding quotes, snapshots, paper trading, HK/US execution | Requires OpenD, account permissions, entitlements and quota management |
| Official exchanges and regulators | Disclosures, rules and enforcement events | Build robust parsers and preserve original documents |
| SEC EDGAR / FRED | US filings and macro expansion | Respect fair-access and dataset-specific rules |

## Core paid gaps

Paid data is likely required before commercial production for:

- licensed full-market real-time A-share quotes;
- Level-2 order book and long historical tick data;
- reliable point-in-time financial statements and revisions;
- historical index/sector constituents;
- analyst estimates and revisions;
- licensed real-time financial news;
- supply-chain and institutional ownership graphs;
- commercial display and redistribution rights.

Potential vendors include Wind, Choice, iFinD, exchange-authorized vendors and broker-specific data services. Procurement must be based on coverage, point-in-time quality, latency, API concurrency, retention and redistribution rights rather than brand alone.

## Source quality policy

1. Define primary, fallback and validation sources per dataset.
2. Quarantine conflicting values instead of silently overwriting them.
3. Retain raw payload hashes and normalized versions.
4. Stop affected predictions when freshness or completeness SLAs fail.
5. Track every feature back to source records.
