from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, cast

import pandas as pd

from alphapilot.core.config import Settings
from alphapilot.data.base import DataProviderError


class FutuMarketDataProvider:
    name = "futu"

    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def _module() -> Any:
        try:
            import futu
        except ImportError as exc:
            raise DataProviderError(
                'futu-api is not installed. Run: pip install -e ".[futu]"'
            ) from exc
        return futu

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        upper = symbol.upper().replace(" ", "")
        if "." in upper and upper.split(".", 1)[0] in {"SH", "SZ", "HK", "US"}:
            return upper
        digits = "".join(character for character in upper if character.isdigit())
        if len(digits) == 6:
            prefix = "SH" if digits.startswith(("5", "6", "9")) else "SZ"
            return f"{prefix}.{digits}"
        raise DataProviderError(
            f"Futu symbol must look like SH.600000, SZ.000001, HK.00700 or US.AAPL: {symbol}"
        )

    def get_snapshot(self, symbols: list[str]) -> pd.DataFrame:
        futu = self._module()
        codes = [self._normalize_symbol(symbol) for symbol in symbols]
        context = futu.OpenQuoteContext(host=self.settings.futu_host, port=self.settings.futu_port)
        try:
            ret, data = context.get_market_snapshot(codes)
            if ret != futu.RET_OK:
                raise DataProviderError(f"Futu snapshot failed: {data}")
            frame = data.rename(
                columns={
                    "code": "symbol",
                    "last_price": "last",
                    "change_rate": "change_pct",
                    "turnover": "amount",
                }
            ).copy()
            frame["as_of"] = datetime.now(UTC)
            return cast(
                pd.DataFrame,
                frame[["symbol", "last", "change_pct", "volume", "amount", "as_of"]],
            )
        finally:
            context.close()

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        futu = self._module()
        code = self._normalize_symbol(symbol)
        context = futu.OpenQuoteContext(host=self.settings.futu_host, port=self.settings.futu_port)
        frames: list[pd.DataFrame] = []
        page_req_key = None
        try:
            while True:
                ret, data, page_req_key = context.request_history_kline(
                    code=code,
                    start=start.isoformat(),
                    end=end.isoformat(),
                    ktype=futu.KLType.K_DAY,
                    autype=futu.AuType.NONE,
                    max_count=1000,
                    page_req_key=page_req_key,
                )
                if ret != futu.RET_OK:
                    raise DataProviderError(f"Futu daily bars failed for {code}: {data}")
                frames.append(data)
                if page_req_key is None:
                    break
        finally:
            context.close()

        if not frames:
            raise DataProviderError(f"Futu returned no daily bars for {code}")
        raw = pd.concat(frames, ignore_index=True)
        frame = raw.rename(columns={"time_key": "date", "turnover": "amount"}).copy()
        required = ["date", "open", "high", "low", "close", "volume", "amount"]
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        return (
            frame[required]
            .dropna(subset=["date", "close"])
            .sort_values("date")
            .reset_index(drop=True)
        )
