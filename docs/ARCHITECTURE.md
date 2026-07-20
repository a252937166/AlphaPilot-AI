# Architecture

```text
Official/Free/Paid APIs
        │
        ▼
Ingestion + Source Router ──► Point-in-Time Data Lake/Warehouse
        │                              │
        │                              ├── Feature Store
        │                              ├── Observed Knowledge Graph
        │                              └── Data Quality & Lineage
        ▼
Prediction Services
  ├── Market Regime
  ├── Stock Ranking
  ├── Return Distribution
  ├── Sector Ranking
  └── Event Impact
        │
        ├──────────────► MiroFish Finance Bridge
        │                 Scenario Graph + Run Store
        │
        ▼
Meta Model + Probability Calibration
        │
        ├── Screening
        ├── Thesis Tracking
        ├── Alerts
        ├── Reports
        └── Trade Proposals
                              │
                              ▼
                       Pre-trade Guardrails
                              │
                              ▼
                     Local Broker Gateway
                    Futu / QMT / PTrade
```

## Service boundaries

- `data-ingestion`: collects and normalizes external datasets.
- `feature-engine`: creates point-in-time features with explicit availability timestamps.
- `prediction-engine`: trains and serves statistical models.
- `scenario-engine`: runs event simulations; never mutates observed facts.
- `alert-engine`: evaluates prediction and thesis transitions.
- `portfolio-risk`: enforces portfolio and order constraints.
- `trade-orchestrator`: stores proposals and approvals; it does not contain model logic.
- `local-trade-gateway`: holds broker connectivity and credentials outside the cloud service.

## Required temporal fields

Every record must retain:

- `event_time`: when the event occurred;
- `available_time`: when it became knowable to the market;
- `ingested_at`: when AlphaPilot obtained it;
- `source_id` and `source_version`;
- `is_simulated`, `scenario_id` and `run_id` when applicable.

A backtest may only use records where `available_time <= decision_time`.
