from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from alphapilot.core.config import Settings
from alphapilot.data.base import BarFrequency, DataProviderError
from alphapilot.futu.client import FutuClient, FutuClientError, get_futu_client

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


class FutuMarketDataProvider:
    name = "futu"

    def __init__(self, settings: Settings, client: FutuClient | None = None):
        self.settings = settings
        self.client = client or get_futu_client()

    @staticmethod
    def _snapshot_time(value: object) -> datetime | None:
        if isinstance(value, pd.Timestamp):
            parsed = value.to_pydatetime()
        elif isinstance(value, datetime):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(str(value))
            except ValueError:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=MARKET_TIMEZONE)
        return parsed.astimezone(UTC)

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
                    "open_price": "open",
                    "high_price": "high",
                    "low_price": "low",
                    "turnover": "amount",
                    "pe_ttm_ratio": "pe_ttm",
                    "total_market_val": "market_cap",
                    "circular_market_val": "float_cap",
                    "pb_ratio": "pb",
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
        if "pe_ttm" not in frame.columns:
            frame["pe_ttm"] = frame.get("pe_ratio")
        elif "pe_ratio" in frame.columns:
            frame["pe_ttm"] = frame["pe_ttm"].fillna(frame["pe_ratio"])
        optional_columns = [
            "open",
            "high",
            "low",
            "turnover_rate",
            "pe_ttm",
            "market_cap",
            "float_cap",
            "pb",
        ]
        for column in optional_columns:
            if column not in frame.columns:
                frame[column] = None
        if "update_time" in frame.columns:
            frame["as_of"] = frame["update_time"].map(self._snapshot_time)
        else:
            frame["as_of"] = None
        return frame[
            [
                "symbol",
                "last",
                "change_pct",
                "open",
                "high",
                "low",
                "volume",
                "amount",
                "turnover_rate",
                "pe_ttm",
                "market_cap",
                "float_cap",
                "pb",
                "as_of",
            ]
        ]

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        return self.get_bars(symbol, start, end, "d")

    def get_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        frequency: BarFrequency,
    ) -> pd.DataFrame:
        ktype_by_frequency = {"d": "K_DAY", "w": "K_WEEK", "m": "K_MON"}
        if frequency not in ktype_by_frequency:
            raise ValueError(f"Unsupported Futu bar frequency: {frequency}")
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
                        "ktype": ktype_by_frequency[frequency],
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
            raise DataProviderError(f"Futu {frequency} bars failed for {code}: {exc}") from exc

        if not frames:
            raise DataProviderError(f"Futu returned no {frequency} bars for {code}")
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
