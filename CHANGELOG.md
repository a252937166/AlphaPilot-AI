# Changelog

## 0.2.0 - 2026-07-20

- Added the SQLAlchemy persistence layer (SQLite default, PostgreSQL via env):
  securities, daily-bar cache, disclosures, watchlist/thesis, forecast snapshots,
  alerts, screening runs, trade proposals, sector snapshots and daily reports.
- Added the BaoStock provider and the `auto` failover chain
  (bars: baostock→akshare→futu; snapshots: futu→akshare; cache as last resort).
- Integrated cninfo (深证信 WebAPI OAuth2 + public announcement endpoints):
  company profiles and disclosure ingestion; credentials live only in `.env`.
- Added service layer: market-data caching, watchlist tracking, sector strength
  sampling via Futu plates, sample market breadth, daily review reports and an
  optional-LLM market summary (deterministic template fallback).
- New API surface: dashboard overview, watchlist CRUD/track, alerts
  refresh/acknowledge, sector strength, market indices/breadth, disclosures,
  daily reports, trade-proposal audit workflow (approve/reject, no execution).
- Rebuilt the web app as a multi-page dashboard (vue-router + ECharts) following
  docs/AlphaPilot-AI-UI-16x9: 总览/AI选股/个股分析/自选追踪/板块预测/大盘监控/交易提醒/AI复盘.
- Fixed the Futu snapshot provider (change_pct derived from prev_close_price)
  and the pytest exit hang caused by undisposed futu SDK threads.
- Alert, guardrail and baseline messages are now user-facing Chinese.

## 0.1.0 - 2026-07-20

- Created the independent AlphaPilot AI foundation repository.
- Added FastAPI and Vue application skeletons.
- Added Mock, AKShare and Futu market-data providers.
- Added baseline forecast, screening, market-regime and alert services.
- Added scenario simulation contracts and a deterministic local simulator.
- Added trading proposal guardrails with live trading disabled by default.
- Added CI, tests, Docker, configuration and architecture documentation.
