from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from time import monotonic, sleep
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from alphapilot.data.base import DataProviderError, EmptyDailyBarsError


class SinaDailyBarProvider:
    """Sina daily bars and the BSE snapshots missing from Futu OpenD."""

    name = "sina"
    _snapshot_url = "https://hq.sinajs.cn/list="
    _hfq_factor_url = "https://finance.sina.com.cn/realstock/company/{}/hfq.js"
    _snapshot_pattern = re.compile(r'^var hq_str_(bj\d{6})="(.*)";$')
    _market_timezone = ZoneInfo("Asia/Shanghai")

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
            remaining = self.min_interval_seconds - (monotonic() - self._last_request_started)
            if remaining > 0:
                sleep(remaining)
        self._last_request_started = monotonic()

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        return self._get_daily_bars(symbol, start, end, adjust="")

    def get_adjusted_closes(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """Return Sina/AKShare backward-adjusted closes for BSE symbols."""

        return self._get_daily_bars(symbol, start, end, adjust="hfq")[
            ["date", "close"]
        ].copy()

    def get_adjustment_factors(
        self,
        symbol: str,
        end: date,
    ) -> pd.DataFrame:
        """Fetch bounded-time Sina HFQ factor events without AKShare's unbounded I/O."""

        sina_symbol = self._symbol(symbol)
        self._throttle()
        try:
            with httpx.Client(
                timeout=10.0,
                follow_redirects=True,
                trust_env=False,
            ) as client:
                response = client.get(
                    self._hfq_factor_url.format(sina_symbol),
                    headers={
                        "Referer": "https://finance.sina.com.cn/",
                        "User-Agent": "Mozilla/5.0 (compatible; AlphaPilot-AI/0.3)",
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DataProviderError(
                f"Sina HFQ factor request failed for {sina_symbol}: "
                f"{type(exc).__name__}"
            ) from exc

        first_line = response.text.splitlines()[0] if response.text else ""
        _, separator, payload_text = first_line.partition("=")
        if not separator:
            raise DataProviderError(
                f"Sina HFQ factor payload is invalid for {sina_symbol}"
            )
        try:
            payload = json.loads(payload_text.rstrip(";"))
            items = payload["data"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise DataProviderError(
                f"Sina HFQ factor payload is invalid for {sina_symbol}"
            ) from exc
        if not isinstance(items, list) or not items:
            raise EmptyDailyBarsError(
                f"Sina returned no HFQ factors for {sina_symbol}"
            )
        frame = pd.DataFrame(items)
        if not {"d", "f"}.issubset(frame.columns):
            raise DataProviderError(
                f"Sina HFQ factor payload is missing fields for {sina_symbol}"
            )
        frame["date"] = pd.to_datetime(frame["d"], errors="coerce").dt.date
        frame["adj_factor"] = pd.to_numeric(frame["f"], errors="coerce")
        result = (
            frame.loc[
                frame["date"].map(
                    lambda value: isinstance(value, date) and value <= end
                )
                & frame["adj_factor"].gt(0),
                ["date", "adj_factor"],
            ]
            .dropna()
            .sort_values("date")
            .drop_duplicates("date", keep="last")
            .reset_index(drop=True)
        )
        if result.empty:
            raise EmptyDailyBarsError(
                f"Sina returned no usable HFQ factors for {sina_symbol}"
            )
        return result

    def _get_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        adjust: str,
    ) -> pd.DataFrame:
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
                    adjust=adjust,
                )
                if not isinstance(candidate, pd.DataFrame):
                    raise DataProviderError(
                        f"Sina returned an invalid daily-bar payload for {sina_symbol}"
                    )
                if candidate.empty:
                    raise EmptyDailyBarsError(
                        f"Sina returned no daily bars for {sina_symbol}"
                    )
                frame = candidate
                break
            except EmptyDailyBarsError as exc:
                last_error = str(exc)
                if attempt < 2:
                    sleep(float(attempt + 1))
                else:
                    raise
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
            raise EmptyDailyBarsError(
                f"Sina returned no valid daily bars for {sina_symbol}"
            )
        return result

    def get_snapshot(self, symbols: list[str]) -> pd.DataFrame:
        if not symbols:
            raise DataProviderError("Sina snapshot requires at least one symbol")

        codes = [self._symbol(symbol) for symbol in symbols]
        records: list[dict[str, Any]] = []
        headers = {
            "Referer": "https://finance.sina.com.cn/",
            "User-Agent": "Mozilla/5.0 (compatible; AlphaPilot-AI/0.2)",
        }
        for offset in range(0, len(codes), 200):
            self._throttle()
            batch = codes[offset : offset + 200]
            try:
                response = httpx.get(
                    f"{self._snapshot_url}{','.join(batch)}",
                    headers=headers,
                    timeout=5.0,
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise DataProviderError(f"Sina BSE snapshot failed: {exc}") from exc

            text = response.content.decode("gb18030", errors="replace")
            for line in text.splitlines():
                match = self._snapshot_pattern.match(line.strip())
                if match is None:
                    continue
                fields = match.group(2).split(",")
                if len(fields) < 10 or not fields[0].strip():
                    continue
                quote_time = datetime.now(UTC)
                if len(fields) > 31 and fields[30] and fields[31]:
                    try:
                        local_time = datetime.fromisoformat(f"{fields[30]}T{fields[31]}").replace(
                            tzinfo=self._market_timezone
                        )
                        quote_time = local_time.astimezone(UTC)
                    except ValueError:
                        pass
                records.append(
                    {
                        "symbol": match.group(1)[2:],
                        "name": fields[0].strip(),
                        "open_price": fields[1],
                        "prev_close_price": fields[2],
                        "last_price": fields[3],
                        "high_price": fields[4],
                        "low_price": fields[5],
                        "volume": fields[8],
                        "turnover": fields[9],
                        "suspension": False,
                        "as_of": quote_time,
                    }
                )

        if not records:
            raise DataProviderError("Sina returned no valid BSE snapshots")
        frame = pd.DataFrame.from_records(records)
        for column in (
            "open_price",
            "prev_close_price",
            "last_price",
            "high_price",
            "low_price",
            "volume",
            "turnover",
        ):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame
