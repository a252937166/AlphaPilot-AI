# Product Specification

## Product statement

AlphaPilot AI is a probabilistic market-intelligence and trading-assistance system. It continuously converts market data, fundamentals, disclosures, macro signals and scenario simulations into ranked opportunities, monitored investment theses, structured alerts and controlled trade proposals.

## Primary workflows

### 1. Quality-stock discovery

1. Build a point-in-time investable universe.
2. Apply hard exclusions for listing age, liquidity, abnormal audit opinions, ST/delisting state and missing data.
3. Compute quality, growth, cash-flow, valuation, momentum, volatility, event and sector features.
4. Predict 1-, 5-, 20- and 60-day relative-return distributions.
5. Calibrate probabilities by market regime.
6. Optimize a diversified candidate portfolio under liquidity and risk limits.

### 2. Continuous thesis tracking

Each tracked stock has an immutable investment-thesis version containing base case, catalysts, risks, invalidation rules, expected horizon and model confidence. New disclosures, price anomalies, sector rotation and prediction drift trigger an automatic thesis review.

### 3. Automated alerts

Alerts use a normalized action taxonomy: WATCH, BUY_CANDIDATE, ADD, HOLD, REDUCE, EXIT, STOP and REVIEW_REQUIRED. Every alert contains confidence, expected position change, reasons, invalidation, expiration, source IDs and model version.

### 4. Market and sector prediction

The system ranks sectors over 1, 5 and 20 trading days using breadth, leader strength, volume diffusion, fund flows, earnings revisions, valuation, policy events, relative strength and crowding risk. A market-regime model controls exposure and model weights.

### 5. Automated review

- Pre-market: overseas markets, macro, disclosures, holdings and planned alerts.
- Intraday: anomalies, model drift, sector switching and urgent risk events.
- Post-market: attribution, forecast scoring, missed signals and next-session watchlist.
- Weekly: calibration, IC, turnover, drawdown, false-alert rate and data incidents.

## Non-goals for the first production release

- High-frequency trading.
- Unrestricted LLM-controlled execution.
- Guaranteed returns or deterministic target prices.
- Commercial redistribution of unlicensed market data.
- Using synthetic agent statements as observed facts.
