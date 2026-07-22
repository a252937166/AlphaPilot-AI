from __future__ import annotations

from datetime import date
from threading import Lock
from time import sleep
from typing import Any

import pandas as pd

from alphapilot.data.base import BarFrequency, DataProviderError, EmptyDailyBarsError

# BaoStock keeps one global socket per process, so calls are serialized.
_baostock_lock = Lock()
_logged_in = False

_FINANCIAL_QUERIES = {
    "profit": "query_profit_data",
    "growth": "query_growth_data",
    "cash_flow": "query_cash_flow_data",
    "balance": "query_balance_data",
}


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
        last_error = "unknown error"
        for attempt in range(3):
            result = bs.login()
            if result.error_code == "0":
                _logged_in = True
                return
            last_error = str(result.error_msg)
            if attempt < 2:
                sleep(float(attempt + 1))
        raise DataProviderError(f"BaoStock login failed after 3 attempts: {last_error}")

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        return self.get_bars(symbol, start, end, "d")

    def get_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        frequency: BarFrequency,
    ) -> pd.DataFrame:
        if frequency not in {"d", "w", "m"}:
            raise ValueError(f"Unsupported BaoStock bar frequency: {frequency}")
        bs = self._module()
        code = self._code(symbol)
        with _baostock_lock:
            self._ensure_login(bs)
            rs = bs.query_history_k_data_plus(
                code,
                "date,open,high,low,close,volume,amount",
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                frequency=frequency,
                adjustflag="3",  # unadjusted, matching the AKShare adapter
            )
            if rs.error_code != "0":
                raise DataProviderError(f"BaoStock query failed for {code}: {rs.error_msg}")
            rows: list[list[str]] = []
            while rs.next():
                rows.append(rs.get_row_data())

        if not rows:
            label = {"d": "daily", "w": "weekly", "m": "monthly"}[frequency]
            raise EmptyDailyBarsError(f"BaoStock returned no {label} bars for {code}")
        frame = pd.DataFrame(
            rows, columns=["date", "open", "high", "low", "close", "volume", "amount"]
        )
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        for column in ["open", "high", "low", "close", "volume", "amount"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame[["volume", "amount"]] = frame[["volume", "amount"]].fillna(0.0)
        result = (
            frame.dropna(subset=["date", "open", "high", "low", "close"])
            .sort_values("date")
            .reset_index(drop=True)
        )
        if result.empty:
            label = {"d": "daily", "w": "weekly", "m": "monthly"}[frequency]
            raise EmptyDailyBarsError(f"BaoStock returned no valid {label} bars for {code}")
        return result

    def get_stock_universe(self, trade_date: date) -> pd.DataFrame:
        """Return BaoStock's active security list for an actual trading day."""

        bs = self._module()
        with _baostock_lock:
            self._ensure_login(bs)
            rs = bs.query_all_stock(day=trade_date.isoformat())
            if rs.error_code != "0":
                raise DataProviderError(f"BaoStock universe query failed: {rs.error_msg}")
            rows: list[list[str]] = []
            while rs.next():
                rows.append(rs.get_row_data())
            columns = [str(field) for field in rs.fields]
        if not rows:
            raise DataProviderError(f"BaoStock returned no securities for {trade_date.isoformat()}")
        return pd.DataFrame(rows, columns=columns)

    def get_stock_industries(self) -> pd.DataFrame:
        """Return the current CSRC industry mapping through the shared connection."""

        bs = self._module()
        with _baostock_lock:
            self._ensure_login(bs)
            rs = bs.query_stock_industry()
            if rs.error_code != "0":
                raise DataProviderError(f"BaoStock industry query failed: {rs.error_msg}")
            rows: list[list[str]] = []
            while rs.next():
                rows.append(rs.get_row_data())
            columns = [str(field) for field in rs.fields]
        if not rows:
            raise DataProviderError("BaoStock returned no stock industries")
        return pd.DataFrame(rows, columns=columns)

    def _get_quarterly_financial_frames(
        self,
        symbol: str,
        year: int,
        quarter: int,
        datasets: tuple[str, ...],
    ) -> dict[str, pd.DataFrame]:
        global _logged_in

        if year < 1990 or quarter not in {1, 2, 3, 4}:
            raise ValueError("financial year/quarter is out of range")
        digits = "".join(character for character in symbol if character.isdigit())
        if len(digits) != 6 or digits.startswith(("4", "8", "92")):
            raise DataProviderError(
                f"BaoStock quarterly financials do not support symbol: {symbol}"
            )

        bs = self._module()
        code = self._code(symbol)
        frames: dict[str, pd.DataFrame] = {}
        with _baostock_lock:
            self._ensure_login(bs)
            for dataset in datasets:
                method_name = _FINANCIAL_QUERIES[dataset]
                query = getattr(bs, method_name, None)
                if not callable(query):
                    raise DataProviderError(
                        f"BaoStock module is missing financial query: {method_name}"
                    )
                result = query(code=code, year=year, quarter=quarter)
                if result.error_code != "0" and (
                    result.error_code == "10001001" or "未登录" in str(result.error_msg)
                ):
                    _logged_in = False
                    self._ensure_login(bs)
                    result = query(code=code, year=year, quarter=quarter)
                if result.error_code != "0":
                    raise DataProviderError(
                        f"BaoStock {dataset} query failed for {code}/{year}Q{quarter}: "
                        f"{result.error_msg}"
                    )
                rows: list[list[str]] = []
                while result.next():
                    rows.append(result.get_row_data())
                columns = [str(field) for field in result.fields]
                frames[dataset] = pd.DataFrame(rows, columns=columns)
        return frames

    def get_quarterly_financials(
        self, symbol: str, year: int, quarter: int
    ) -> dict[str, pd.DataFrame]:
        """Return BaoStock's four quarterly financial datasets under one socket lock."""

        return self._get_quarterly_financial_frames(
            symbol,
            year,
            quarter,
            tuple(_FINANCIAL_QUERIES),
        )

    def get_quarterly_profit(self, symbol: str, year: int, quarter: int) -> pd.DataFrame:
        """Return only the profit dataset for prior-year revenue derivation."""

        return self._get_quarterly_financial_frames(
            symbol,
            year,
            quarter,
            ("profit",),
        )["profit"]

    def get_dividend_data(self, symbol: str, year: int) -> pd.DataFrame:
        """Return one report year's dividend records, including empty results."""

        bs = self._module()
        code = self._code(symbol)
        with _baostock_lock:
            self._ensure_login(bs)
            rs = bs.query_dividend_data(code=code, year=str(year), yearType="report")
            if rs.error_code != "0":
                raise DataProviderError(
                    f"BaoStock dividend query failed for {code}/{year}: {rs.error_msg}"
                )
            rows: list[list[str]] = []
            while rs.next():
                rows.append(rs.get_row_data())
            columns = [str(field) for field in rs.fields]
        return pd.DataFrame(rows, columns=columns)

    def get_forecast_reports(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Return BaoStock earnings-preview publications for a bounded period."""

        bs = self._module()
        code = self._code(symbol)
        with _baostock_lock:
            self._ensure_login(bs)
            rs = bs.query_forecast_report(
                code=code,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
            )
            if rs.error_code != "0":
                raise DataProviderError(
                    f"BaoStock forecast query failed for {code}: {rs.error_msg}"
                )
            rows: list[list[str]] = []
            while rs.next():
                rows.append(rs.get_row_data())
            columns = [str(field) for field in rs.fields]
        return pd.DataFrame(rows, columns=columns)

    def get_snapshot(self, symbols: list[str]) -> pd.DataFrame:
        raise DataProviderError(
            "BaoStock has no real-time snapshot; route snapshots to futu or akshare."
        )
