# Trading Guardrails

## Default posture

- Research-only mode.
- Live trading disabled.
- No public API endpoint submits orders.
- Broker credentials stay in the local gateway.

## Required pre-trade checks

1. Trading mode and explicit user authorization.
2. Market-data freshness and conflict status.
3. Model confidence, model-drift state and model version approval.
4. Instrument tradability, session, suspension and price-limit rules.
5. Available cash, sellable quantity and settlement rules.
6. Single-name, sector, style and portfolio exposure limits.
7. Average-daily-value and market-impact limits.
8. Existing open order and idempotency-key check.
9. Daily loss, drawdown and kill-switch state.
10. Human confirmation where policy requires it.

## Staged release

1. `research`: no account access.
2. `observe`: read positions and balances only.
3. `alert`: generate recommendations only.
4. `confirm_to_trade`: explicit confirmation for every order.
5. `paper_auto`: automatic simulated execution under limits.
6. `limited_live_auto`: narrow approved strategies with kill switch and full audit.

A strategy must pass paper trading, shadow mode and limited-capital rollout before any broader automation.
