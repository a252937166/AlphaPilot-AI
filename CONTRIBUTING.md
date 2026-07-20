# Contributing

1. Create a focused branch: `agent/<description>` or `feature/<description>`.
2. Keep data-provider code isolated behind `MarketDataProvider`.
3. Do not mix observed facts, simulated facts and model inference.
4. Add tests for every risk rule and model-contract change.
5. Never commit API keys, account IDs, proprietary market data or raw brokerage payloads.
6. Run `make lint test` before opening a pull request.
7. Any change that can affect order execution requires explicit review of `docs/TRADING_GUARDRAILS.md`.
