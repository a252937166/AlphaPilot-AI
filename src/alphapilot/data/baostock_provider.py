from __future__ import annotations

from datetime import date
from threading import Lock
from typing import Any

import pandas as pd

from alphapilot.data.base import DataProviderError

# BaoStock keeps one global socket per process, so calls are serialized.
_baostock_lock = Lock()
_logged_in = False


class BaoStockMarketDataProvider:
    """A-share daily history from BaoStock; it has no real-time snapshot."""

    name = "baostock"

    @staticmethod
    def _module() -> Any:
        try:
            import baostock as bs
        except ImportError as exc:
            raise DataProviderError(
                'BaoStock is not installed. Run: pip install -e ".[cn-data]"'
            ) from exc
        return bs

    @staticmethod
    def _code(symbol: str) -> str:
        upper = symbol.upper().replace(" ", "")
        if "." in upper:
            market, digits = upper.split(".", 1)
            if market in {"SH", "SZ"} and digits.isdigit() and len(digits) == 6:
                return f"{market.lower()}.{digits}"
            raise DataProviderError(f"Unsupported BaoStock symbol: {symbol}")
        digits = "".join(character for character in upper if character.isdigit())
        if len(digits) != 6:
            raise DataProviderError(f"Unsupported A-share symbol: {symbol}")
        prefix = "sh" if digits.startswith(("5", "6", "9")) else "sz"
        return f"{prefix}.{digits}"

    def _ensure_login(self, bs: Any) -> None:
        global _logged_in
        if _logged_in:
            return
        result = bs.login()
        if result.error_code != "0":
            raise DataProviderError(f"BaoStock login failed: {result.error_msg}")
        _logged_in = True

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        bs = self._module()
        code = self._code(symbol)
        with _baostock_lock:
            self._ensure_login(bs)
            rs = bs.query_history_k_data_plus(
                code,
                "date,open,high,low,close,volume,amount",
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                frequency="d",
                adjustflag="3",  # unadjusted, matching the AKShare adapter
            )
            if rs.error_code != "0":
                raise DataProviderError(f"BaoStock query failed for {code}: {rs.error_msg}")
            rows: list[list[str]] = []
            while rs.next():
                rows.append(rs.get_row_data())

        if not rows:
            raise DataProviderError(f"BaoStock returned no daily bars for {code}")
        frame = pd.DataFrame(
            rows, columns=["date", "open", "high", "low", "close", "volume", "amount"]
        )
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        for column in ["open", "high", "low", "close", "volume", "amount"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        result = (
            frame.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
        )
        if result.empty:
            raise DataProviderError(f"BaoStock returned no valid daily bars for {code}")
        return result

    def get_snapshot(self, symbols: list[str]) -> pd.DataFrame:
        raise DataProviderError(
            "BaoStock has no real-time snapshot; route snapshots to futu or akshare."
        )
