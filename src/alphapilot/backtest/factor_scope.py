from __future__ import annotations

from alphapilot.engines.factors import FACTOR_SET

# P3.3 architecture ruling (2026-07-25): only these eleven factors have
# defensible historical PIT inputs.  S7 research and S9 weight rebuilding must
# import this tuple instead of deriving a scope from the runtime factor set.
HISTORICAL_FACTOR_CANDIDATES = (
    "momentum_20d",
    "momentum_60d",
    "volatility_20d",
    "turnover_change_5d",
    "roe",
    "net_profit_yoy",
    "ocf_to_profit",
    "debt_ratio",
    "revenue_yoy",
    "pe_percentile",
    "pb_percentile",
)

# The historical sector-flow rows were aggregated with today's constituent
# basket.  Re-labelling current membership as historical would be look-ahead,
# so this signal is forward-only until daily PIT membership snapshots mature.
HISTORY_EXCLUDED_PIT_GAP_FACTORS = ("net_inflow_5d",)
LIVE_ONLY_FACTORS = ("sector_strength",)

_PARTITION = (
    *HISTORICAL_FACTOR_CANDIDATES,
    *HISTORY_EXCLUDED_PIT_GAP_FACTORS,
    *LIVE_ONLY_FACTORS,
)
if len(_PARTITION) != len(set(_PARTITION)) or set(_PARTITION) != set(FACTOR_SET):
    raise RuntimeError("historical factor scope must partition the runtime factor set")
