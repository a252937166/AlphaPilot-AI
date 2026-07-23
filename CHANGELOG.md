# Changelog

## 0.3.0 - 2026-07-23

- Added the Phase 2 data foundation: full-market security master and daily bars,
  minute snapshots, event calendar/bus, audited scheduler runs, resumable sync,
  circuit breakers, and SQLite WAL/busy-timeout write-contention recovery.
- Added auditable factor, style, sector-forecast, five-dimension stock-score,
  market-sentiment, score-outcome and thesis-drift engines. Sector win rate is
  explicitly model-wide; the UI no longer repeats it as a per-sector metric.
- Completed the Futu `SIMULATE` loop for risk-checked proposals, manual
  confirmation, broker-order synchronization, portfolio snapshots and
  attribution. REAL execution still requires all three independent gates;
  `unlock_trade` remains permanently unavailable over HTTP.
- Added event extraction, stock insight, market-summary polishing and review
  advice through a provider-neutral OpenAI-compatible HTTP client, with
  per-purpose models, structured output, audit rows, timeouts, caching and
  deterministic rule/statistics fallbacks. Qwen is the current deployment;
  Claude CLI and Codex CLI transports are P3 work and are not claimed as
  supported in this release.
- Completed the eight local product pages, notification center, alert targets
  and suggested notional, proposal/order state flow, portfolio attribution,
  `sector_call_excess`, full-market monitoring and honest source/date metadata.
- Verified all eight routes at the 1672×941 design viewport and responsive
  widths, plus whole-product no-LLM and no-OpenD degradation. Missing real-time
  data is now bounded and explained in Chinese instead of leaving indefinite
  loading states.
- Kept design 02 (login/welcome) out of the local single-user Phase 2 scope.
  The acceptance evidence records this decision rather than presenting a
  decorative login screen as real authentication.

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
