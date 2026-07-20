# MiroFish Finance Integration

## Why process separation is mandatory

MiroFish currently models social entities and social-media actions. AlphaPilot needs passive financial entities, investor constraints, probability calibration and market clearing. Directly embedding the social simulator into the trading service would mix synthetic narratives with observed data and complicate AGPL obligations.

## Target topology

```text
AlphaPilot Observed Data
        │
        ▼
Finance Scenario Builder
        │  event, entities, graph snapshot, assumptions
        ▼
MiroFish Finance Bridge (separate deployment)
        │
        ├── financial ontology
        ├── investor archetypes
        ├── scenario-only graph
        ├── deterministic seeds
        └── multi-run simulation
        │
        ▼
ScenarioResponse contract
        │
        ▼
AlphaPilot Meta Model and Report Agent
```

## Required changes in the bridge

- Replace the fixed social ontology with Company, Security, Sector, Product, Commodity, Policy, Investor, Analyst, Regulator, Supplier, Customer and Event entities.
- Separate active agents from passive graph entities.
- Replace post/like/follow parameters with belief update, risk budget, information delay, trade intent and holding horizon.
- Store `ObservedGraph`, `ScenarioGraph` and `RunStore` separately.
- Never write synthetic events to the observed graph.
- Return structured distributions, agent disagreement and assumptions, not a single target price.
- Calibrate scenario outcomes against historical event studies.

## API contract

AlphaPilot includes `MiroFishFinanceBridge`, expecting:

```text
POST /v1/finance/scenarios
```

Input and output use `ScenarioRequest` and `ScenarioResponse`. The endpoint is not part of the upstream MiroFish project; it must be implemented in a dedicated adapter service.
