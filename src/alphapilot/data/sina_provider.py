from __future__ import annotations

from datetime import date
from time import monotonic, sleep
from typing import Any

import pandas as pd

from alphapilot.data.base import DataProviderError


class SinaDailyBarProvider:
    """Unadjusted daily bars from Sina, restricted to BaoStock's BSE gap."""

    name = "sina"

    def __init__(self, min_interval_seconds: float = 1.0) -> None:
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self._last_request_started: float | None = None

    @staticmethod
    def _symbol(symbol: str) -> str:
        digits = "".join(character for character in symbol if character.isdigit())
        if len(digits) != 6 or not digits.startswith(("4", "8", "9")):
            raise DataProviderError(f"Sina BSE fallback does not support symbol: {symbol}")
        return f"bj{digits}"

    @staticmethod
    def _module() -> Any:
        try:
            import akshare as ak
        except ImportError as exc:
            raise DataProviderError(
                'AKShare is not installed. Run: pip install -e ".[cn-data]"'
            ) from exc
        return ak

    def _throttle(self) -> None:
        if self._last_request_started is not None:
            remaining = self.min_interval_seconds - (
                monotonic() - self._last_request_started
            )
            if remaining > 0:
                sleep(remaining)
        self._last_request_started = monotonic()

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        ak = self._module()
        sina_symbol = self._symbol(symbol)
        last_error = "unknown error"
        frame: pd.DataFrame | None = None
        for attempt in range(3):
            self._throttle()
            try:
                candidate = ak.stock_zh_a_daily(
                    symbol=sina_symbol,
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                    adjust="",
                )
                if not isinstance(candidate, pd.DataFrame) or candidate.empty:
                    raise DataProviderError(f"Sina returned no daily bars for {sina_symbol}")
                frame = candidate
                break
            except Exception as exc:  # upstream emits several requests/JSON exception types
                last_error = str(exc)
                if attempt < 2:
                    sleep(float(attempt + 1))
        if frame is None:
            raise DataProviderError(
                f"Sina daily bars failed for {sina_symbol} after 3 attempts: {last_error}"
            )

        required = {"date", "open", "high", "low", "close", "volume", "amount"}
        missing = sorted(required.difference(str(column) for column in frame.columns))
        if missing:
            raise DataProviderError(f"Sina daily bars missing columns: {missing}")
        result = frame[list(required)].copy()
        result["date"] = pd.to_datetime(result["date"], errors="coerce")
        for column in ["open", "high", "low", "close", "volume", "amount"]:
            result[column] = pd.to_numeric(result[column], errors="coerce")
        result = (
            result.dropna(subset=["date", "open", "high", "low", "close", "volume"])
            .sort_values("date")
            .reset_index(drop=True)
        )
        if result.empty:
            raise DataProviderError(f"Sina returned no valid daily bars for {sina_symbol}")
        return result

    def get_snapshot(self, symbols: list[str]) -> pd.DataFrame:
        raise DataProviderError(
            "Sina fallback is daily-history only; route snapshots to Futu."
        )
