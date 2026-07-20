# Roadmap

## Milestone 0 — Foundation

- Repository, architecture, API contracts and CI.
- Mock/AKShare/Futu provider interfaces.
- Baseline forecast, screen, alert, regime and scenario services.
- Risk guardrails with execution disabled.

## Milestone 1 — Point-in-Time data platform

- Security master and trading calendars.
- Raw/normalized Parquet lake.
- PostgreSQL metadata and TimescaleDB bars.
- Corporate actions and financial disclosure availability times.
- Data quality, lineage and provider failover.

## Milestone 2 — Research-grade prediction

- Fundamental quality and valuation factors.
- Cross-sectional LightGBM ranker.
- Market-regime model.
- Sector-ranking model.
- Walk-forward backtest and probability calibration.
- MLflow registry and champion/challenger deployment.

## Milestone 3 — Tracking and automation

- Investment-thesis versioning.
- Event ingestion and LLM event extraction.
- Forecast drift and thesis invalidation.
- Pre-market, intraday and post-market automation.
- WeCom, DingTalk, email and web-push notifications.

## Milestone 4 — MiroFish finance bridge

- Financial ontology and agent archetypes.
- Scenario graph isolation.
- Historical event calibration.
- Report Agent integration.

## Milestone 5 — Paper trading

- Local Futu gateway.
- Portfolio accounting, proposals, approvals and order reconciliation.
- Automated simulated trading and attribution.

## Milestone 6 — Limited live trading

- Broker and regulatory review.
- QMT/PTrade adapters for broader A-share execution where authorized.
- Human-confirmed rollout, limited capital, kill switch and incident response.
