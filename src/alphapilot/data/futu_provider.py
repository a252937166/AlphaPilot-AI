from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd

from alphapilot.core.config import Settings
from alphapilot.data.base import DataProviderError
from alphapilot.futu.client import FutuClient, FutuClientError, get_futu_client


class FutuMarketDataProvider:
    name = "futu"

    def __init__(self, settings: Settings, client: FutuClient | None = None):
        self.settings = settings
        self.client = client or get_futu_client()

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        upper = symbol.upper().replace(" ", "")
        supported_markets = {"SH", "SZ", "HK", "US", "SG", "MY", "JP", "CC", "EC"}
        if "." in upper and upper.split(".", 1)[0] in supported_markets:
            return upper
        digits = "".join(character for character in upper if character.isdigit())
        if len(digits) == 6:
            prefix = "SH" if digits.startswith(("5", "6", "9")) else "SZ"
            return f"{prefix}.{digits}"
        raise DataProviderError(
            "Futu symbol must include a supported market prefix, for example "
            f"SH.600000, HK.00700, US.AAPL, SG.D05 or JP.7203: {symbol}"
        )

    def get_snapshot(self, symbols: list[str]) -> pd.DataFrame:
        codes = [self._normalize_symbol(symbol) for symbol in symbols]
        frames: list[pd.DataFrame] = []
        try:
            # OpenD accepts at most 400 codes in one snapshot request.
            for offset in range(0, len(codes), 400):
                data = self.client.quote_call_raw(
                    "get_market_snapshot", args=[codes[offset : offset + 400]]
                )
                if not isinstance(data, pd.DataFrame):
                    raise DataProviderError("Futu snapshot returned an invalid payload.")
                frames.append(data)
        except FutuClientError as exc:
            raise DataProviderError(f"Futu snapshot failed: {exc}") from exc

        if not frames:
            raise DataProviderError("Futu snapshot requires at least one symbol.")
        frame = (
            pd.concat(frames, ignore_index=True)
            .rename(
                columns={
                    "code": "symbol",
                    "last_price": "last",
                    "turnover": "amount",
                }
            )
            .copy()
        )
        # Snapshot payloads carry prev_close_price instead of a change rate.
        last = pd.to_numeric(frame["last"], errors="coerce")
        if "prev_close_price" in frame.columns:
            prev_close = pd.to_numeric(frame["prev_close_price"], errors="coerce")
            frame["change_pct"] = (last / prev_close - 1) * 100
        else:
            frame["change_pct"] = None
        if "volume" not in frame.columns:
            frame["volume"] = None
        frame["as_of"] = datetime.now(UTC)
        return frame[["symbol", "last", "change_pct", "volume", "amount", "as_of"]]

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        code = self._normalize_symbol(symbol)
        frames: list[pd.DataFrame] = []
        page_req_key = None
        try:
            while True:
                result = self.client.quote_call_raw(
                    "request_history_kline",
                    kwargs={
                        "code": code,
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                        "ktype": "K_DAY",
                        "autype": "None",
                        "max_count": 1000,
                        "page_req_key": page_req_key,
                    },
                )
                if not isinstance(result, tuple) or len(result) != 2:
                    raise DataProviderError("Futu history returned an invalid payload.")
                data, page_req_key = result
                if not isinstance(data, pd.DataFrame):
                    raise DataProviderError("Futu history returned an invalid data frame.")
                frames.append(data)
                if page_req_key is None:
                    break
        except FutuClientError as exc:
            raise DataProviderError(f"Futu daily bars failed for {code}: {exc}") from exc

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
