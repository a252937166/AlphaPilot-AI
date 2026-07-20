from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, cast

import pandas as pd

from alphapilot.data.base import DataProviderError


class AKShareMarketDataProvider:
    name = "akshare"

    @staticmethod
    def _module() -> Any:
        try:
            import akshare as ak
        except ImportError as exc:
            raise DataProviderError(
                'AKShare is not installed. Run: pip install -e ".[cn-data]"'
            ) from exc
        return ak

    @staticmethod
    def _digits(symbol: str) -> str:
        digits = "".join(character for character in symbol if character.isdigit())
        if len(digits) != 6:
            raise DataProviderError(f"Unsupported A-share symbol: {symbol}")
        return digits

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        ak = self._module()
        try:
            raw = ak.stock_zh_a_hist(
                symbol=self._digits(symbol),
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="",
            )
        except Exception as exc:  # provider errors vary by upstream website
            raise DataProviderError(f"AKShare daily bars failed for {symbol}: {exc}") from exc

        if raw is None or raw.empty:
            raise DataProviderError(f"AKShare returned no daily bars for {symbol}")

        rename = {
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
        }
        frame = raw.rename(columns=rename)
        required = ["date", "open", "high", "low", "close", "volume", "amount"]
        missing = [column for column in required if column not in frame.columns]
        if missing:
            raise DataProviderError(f"AKShare schema changed; missing columns: {missing}")

        result = frame[required].copy()
        result["date"] = pd.to_datetime(result["date"], errors="coerce")
        for column in required[1:]:
            result[column] = pd.to_numeric(result[column], errors="coerce")
        return cast(
            pd.DataFrame,
            result.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True),
        )

    def get_snapshot(self, symbols: list[str]) -> pd.DataFrame:
        ak = self._module()
        try:
            raw = ak.stock_zh_a_spot_em()
        except Exception as exc:
            raise DataProviderError(f"AKShare market snapshot failed: {exc}") from exc

        rename = {
            "代码": "symbol",
            "名称": "name",
            "最新价": "last",
            "涨跌幅": "change_pct",
            "成交量": "volume",
            "成交额": "amount",
        }
        frame = raw.rename(columns=rename)
        wanted = {self._digits(symbol) for symbol in symbols}
        frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
        frame = frame[frame["symbol"].isin(wanted)].copy()
        frame["as_of"] = datetime.now(UTC)
        for column in ["last", "change_pct", "volume", "amount"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return cast(
            pd.DataFrame,
            frame[["symbol", "last", "change_pct", "volume", "amount", "as_of"]],
        )
