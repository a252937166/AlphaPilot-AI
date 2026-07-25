from __future__ import annotations

AUDITED_DAILY_BAR_SOURCES = frozenset(
    {"akshare", "baostock", "futu", "futu-close", "sina"}
)
AUDITED_SECTOR_FLOW_SOURCES = frozenset(
    {"em", "futu-daily", "futu-snapshot", "futu-top5"}
)
MIN_AUDITED_DAILY_BAR_COVERAGE = 0.80
MIN_AUDITED_SECURITY_UNIVERSE = 100
