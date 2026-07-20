# ADR 0001: Keep MiroFish behind a finance bridge

- Status: Accepted
- Date: 2026-07-20

## Context

The upstream MiroFish project is a social multi-agent simulator under AGPL-3.0. AlphaPilot needs statistical prediction, point-in-time market data, strict auditability and broker isolation.

## Decision

MiroFish will run as a separate optional service. AlphaPilot communicates with it through finance-specific request/response contracts. No upstream source file is copied into the core repository.

## Consequences

- The core system can remain independently licensed and audited.
- Synthetic scenario data cannot accidentally mutate observed facts.
- The bridge requires additional deployment and API maintenance.
- Legal review is still required before commercial deployment of a modified MiroFish service.
