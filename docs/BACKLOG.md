# Prioritized Backlog

## P0 — Data integrity

- [x] Security master with exchange-normalized symbols.
- [ ] Trading calendar service.
- [ ] Raw payload and content-hash storage.
- [x] Financial Point-in-Time `available_time` cutoff at the actual decision time.
- [x] Corporate-action and adjustment-factor service for the audited M1 universe.
- [ ] Financial statement versioning and actual disclosure times.
- [ ] Provider conflict quarantine.
- [ ] Historical delisted-security coverage.

## P0 — Research and validation

- [x] Deterministic PIT walk-forward backtester for the static `composite-v1` signal.
- [x] Transaction costs, slippage, T+1 and limit-up/limit-down handling.
- [ ] Factor library neutralization and capacity-aware portfolio construction.
- [x] Cross-sectional ranking baseline.
- [x] Calibration/Brier infrastructure with an explicit unavailable state for non-probability signals.
- [x] IC, IC_IR, layered returns, turnover and drawdown metrics.
- [ ] Capacity metrics based on historical liquidity and market impact.
- [ ] Market-regime stratified evaluation.
- [x] Pre-registered out-of-sample factor redesign after the failed M1 alpha gates
  (M2 completed honestly: v2 remained insignificant and underperformed after costs).
- [ ] Multi-year PIT history plus new alpha sources, followed by a pre-registered M3
  walk-forward; do not reuse or retune the completed 91-day M2 test window.

## P1 — Product

- [x] Persistent watchlists and portfolios.
- [x] Investment-thesis object and version history.
- [x] Forecast drift triggers.
- [x] Sector prediction dashboard.
- [x] Market breadth and risk dashboard.
- [x] Pre-market and post-market reports.
- [x] Alert delivery and acknowledgment workflow.

## P1 — AI and scenarios

- [ ] Disclosure document ingestion.
- [ ] LLM event JSON schema and citations.
- [ ] Historical similar-event retrieval.
- [ ] Observed/Scenario graph separation.
- [ ] MiroFish finance bridge.
- [ ] Scenario calibration against event studies.

## P2 — Trading

- [x] Account and position read-only sync.
- [x] Paper-order gateway.
- [x] Proposal approval and rejection UI.
- [x] Idempotent order state machine.
- [x] Order reconciliation and execution attribution.
- [x] Global kill switch and incident playbook.
- [ ] Limited live rollout only after governance approval.

## P1 — Performance and infrastructure

- [ ] Batch/cached PIT factor queries; the audited 301-day SQLite baseline currently takes about 38 minutes.
- [ ] Durable, restart-resumable backtest queue with progress checkpoints and cancellation.
- [ ] Route-level frontend code splitting; the production bundle still emits Vite's >500 kB warning.
- [x] Offline PostgreSQL DDL/readiness inventory with machine-readable blockers and a cutover runbook.
- [ ] PostgreSQL/TimescaleDB migration and concurrent-writer soak testing.
