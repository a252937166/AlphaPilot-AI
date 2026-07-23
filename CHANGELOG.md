# Changelog

## 0.4.1 - 2026-07-23

- Corrected the acceptance-only decision that had stopped after one Futu paper
  order. Added an opt-in `paper_auto_trade` job that refreshes audited watchlist
  alerts at 09:35, 13:35 and 14:35 on CN trading days, creates `paper_auto`
  proposals and submits eligible orders to Futu `SIMULATE`.
- Kept automation fail-closed behind an explicit mode plus separate scheduler,
  paper-trading, trade-query and trade-mutation gates. REAL must remain disabled;
  Kill Switch, fresh market data, alert provenance, confidence, cash, position,
  sector, open-order and broker-account checks still run before every order.
- Bounded automatic activity to one attempt per symbol per day, at most three
  submitted orders per day and 2% of current simulated equity per order. A
  single run submits at most one order so broker fills and account state can
  settle before the next scheduled decision.
- Exposed automatic-paper status in `/health` and labelled automatic proposals
  distinctly in the alert audit UI. Added offline coverage for scheduling,
  duplicate prevention, stale quotes and SIMULATE-only broker mutation.

## 0.4.0 - 2026-07-23

- Added the P3-M1 point-in-time backtest foundation: audited adjustment factors,
  adjusted-price reconstruction, historical signal replay, A-share limit/T+1
  execution constraints, full transaction costs and deterministic parameter
  snapshots.
- Audited and tightened the Phase 2 factor path at the 19:30 decision cutoff.
  The static baseline is now `v1.1.0`; future financials, later same-day
  snapshots, mock bars and cross-provider adjustment-factor splicing are
  excluded.
- Added walk-forward daily simulation, Rank IC/IC_IR, ten-layer returns,
  performance/drawdown, turnover, cost, dual-benchmark and probability-
  calibration diagnostics. Gross G10-G1 remains explicitly non-tradable and
  uncosted because historical securities-lending data is unavailable.
- Completed the first 301-trading-day `composite-v1` baseline. All five alpha
  evidence gates failed: net long return was -19.40%, versus +20.56% for CSI 300
  and +7.94% for the adjusted equal-weight market. The product reports this
  negative finding without post-hoc tuning.
- Added asynchronous backtest APIs and the ninth product page, “策略研究”,
  with strict run configuration, global status polling, evidence gates,
  strategy/benchmark and IC charts, layer returns, limitations and reproducible
  run history. Desktop and responsive browser acceptance completed with zero
  application console errors.
- Verified 100% adjustment-factor coverage across 5,530 audited CN securities
  and 1,637,490 audited daily-bar rows. The suite now contains 590 offline tests
  plus strict mypy, Ruff and the production frontend build.
- Kept backtesting isolated from proposals, orders and execution. REAL trading
  remains disabled and `unlock_trade` remains unavailable over HTTP. The local
  in-process backtest worker is not restart-resumable; orphaned runs fail
  honestly after a one-hour lease, with a durable queue left to a later
  infrastructure milestone.

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
