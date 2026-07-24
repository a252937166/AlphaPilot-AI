from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from time import sleep
from typing import Any
from urllib import error, request

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from alphapilot.core.config import get_settings
from alphapilot.data.baostock_provider import BaoStockMarketDataProvider
from alphapilot.data.provenance import AUDITED_DAILY_BAR_SOURCES
from alphapilot.data.sina_provider import SinaDailyBarProvider
from alphapilot.db.models import AdjFactor, DailyBar, Security

logger = logging.getLogger(__name__)

TUSHARE_API_URL = "http://api.tushare.pro"
_REQUEST_TIMEOUT_SECONDS = 30.0
_REQUEST_RETRY_DELAYS = (1.0, 3.0, 10.0)
_REQUEST_INTERVAL_SECONDS = 0.12
_SQLITE_LOCK_RETRY_DELAYS = (0.5, 1.5, 3.0)
_PROVIDER_FACTOR_SOURCES = frozenset({"baostock-hfq", "sina-hfq"})


class TushareAPIError(RuntimeError):
    """Tushare returned a transport, protocol, or business error."""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable

    @property
    def rate_limited(self) -> bool:
        return self.code == 40203 or "频率超限" in str(self)


def _direct_opener() -> request.OpenerDirector:
    """Do not send the credential-bearing request through ambient proxies."""

    return request.build_opener(request.ProxyHandler({}))


def tushare_call(
    token: str,
    api_name: str,
    params: Mapping[str, Any],
    fields: str = "",
) -> pd.DataFrame:
    """Call one Tushare Pro HTTP endpoint and return its tabular payload."""

    resolved_token = token.strip()
    if not resolved_token:
        raise TushareAPIError("Tushare token 未配置。")
    if not api_name.strip():
        raise ValueError("api_name must not be empty")

    payload = json.dumps(
        {
            "api_name": api_name,
            "token": resolved_token,
            "params": dict(params),
            "fields": fields,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    http_request = request.Request(
        TUSHARE_API_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with _direct_opener().open(
            http_request,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        ) as response:
            raw = response.read().decode("utf-8")
    except (error.HTTPError, error.URLError, TimeoutError) as exc:
        raise TushareAPIError(
            f"Tushare {api_name} 请求失败：{type(exc).__name__}",
            retryable=True,
        ) from exc

    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TushareAPIError(f"Tushare {api_name} 返回了无效 JSON。") from exc
    if not isinstance(body, dict):
        raise TushareAPIError(f"Tushare {api_name} 响应不是对象。")

    code = body.get("code")
    if code != 0:
        message = str(body.get("msg") or "未提供原因").strip()
        numeric_code = int(code) if isinstance(code, int | float) else None
        raise TushareAPIError(
            f"Tushare {api_name} 返回错误 code={code}：{message[:300]}",
            code=numeric_code,
        )
    data = body.get("data")
    if not isinstance(data, dict):
        raise TushareAPIError(f"Tushare {api_name} 响应缺少 data。")
    response_fields = data.get("fields")
    items = data.get("items")
    if not isinstance(response_fields, list) or not isinstance(items, list):
        raise TushareAPIError(f"Tushare {api_name} 响应缺少 fields/items。")
    return pd.DataFrame(items, columns=[str(field) for field in response_fields])


def _tushare_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper().split(".", maxsplit=1)[0]
    if len(normalized) != 6 or not normalized.isdigit():
        raise ValueError(f"unsupported A-share symbol: {symbol!r}")
    if normalized.startswith(("4", "8", "92")):
        suffix = "BJ"
    elif normalized.startswith(("5", "6", "9")):
        suffix = "SH"
    else:
        suffix = "SZ"
    return f"{normalized}.{suffix}"


def _parse_adj_rows(frame: pd.DataFrame, symbol: str) -> list[tuple[date, float]]:
    required = {"trade_date", "adj_factor"}
    missing = required.difference(str(column) for column in frame.columns)
    if missing:
        raise TushareAPIError(f"Tushare adj_factor 缺少字段：{sorted(missing)}")

    parsed: list[tuple[date, float]] = []
    for record in frame.to_dict(orient="records"):
        try:
            trade_date = pd.Timestamp(str(record["trade_date"])).date()
            factor = float(record["adj_factor"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TushareAPIError(
                f"Tushare adj_factor 含无效行：symbol={symbol}"
            ) from exc
        if not math.isfinite(factor) or factor <= 0:
            raise TushareAPIError(
                f"Tushare adj_factor 非正有限数：symbol={symbol}, date={trade_date}"
            )
        parsed.append((trade_date, factor))
    return parsed


def _call_adj_factor(
    token: str,
    symbol: str,
    start: date,
    end: date,
) -> pd.DataFrame:
    retries = 0
    while True:
        try:
            return tushare_call(
                token,
                "adj_factor",
                {
                    "ts_code": _tushare_symbol(symbol),
                    "start_date": start.strftime("%Y%m%d"),
                    "end_date": end.strftime("%Y%m%d"),
                },
                "ts_code,trade_date,adj_factor",
            )
        except TushareAPIError as exc:
            if not exc.retryable or retries >= len(_REQUEST_RETRY_DELAYS):
                raise
            delay = _REQUEST_RETRY_DELAYS[retries]
            retries += 1
            sleep(delay)


def _target_windows(
    session: Session,
    symbols: Sequence[str] | None,
) -> list[tuple[str, date, date]]:
    query = (
        select(
            DailyBar.symbol,
            func.min(DailyBar.trade_date),
            func.max(DailyBar.trade_date),
        )
        .join(Security, Security.symbol == DailyBar.symbol)
        .where(DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES))
        .group_by(DailyBar.symbol)
        .order_by(DailyBar.symbol)
    )
    if symbols is not None:
        requested = sorted({str(symbol).strip() for symbol in symbols if str(symbol).strip()})
        if not requested:
            return []
        query = query.where(DailyBar.symbol.in_(requested))
    return [
        (str(symbol), first_date, last_date)
        for symbol, first_date, last_date in session.execute(query).all()
        if isinstance(first_date, date) and isinstance(last_date, date)
    ]


def _provider_adj_rows(
    session: Session,
    symbol: str,
    start: date,
    end: date,
    baostock: BaoStockMarketDataProvider,
    sina: SinaDailyBarProvider,
) -> tuple[list[tuple[date, float]], str]:
    is_bse = symbol.startswith(("4", "8", "92"))
    source = "sina-hfq" if is_bse else "baostock-hfq"
    anchor = session.execute(
        select(AdjFactor.trade_date, AdjFactor.adj_factor)
        .where(
            AdjFactor.symbol == symbol,
            AdjFactor.trade_date < start,
        )
        .order_by(AdjFactor.trade_date.desc(), AdjFactor.id.desc())
        .limit(1)
    ).first()
    if anchor is None:
        anchor = session.execute(
            select(AdjFactor.trade_date, AdjFactor.adj_factor)
            .where(
                AdjFactor.symbol == symbol,
                AdjFactor.trade_date > end,
            )
            .order_by(AdjFactor.trade_date, AdjFactor.id)
            .limit(1)
        ).first()
    anchor_date = anchor[0] if anchor is not None else None
    anchor_factor = float(anchor[1]) if anchor is not None else None
    query_start = min(start, anchor_date) if isinstance(anchor_date, date) else start
    query_end = max(end, anchor_date) if isinstance(anchor_date, date) else end
    local = pd.DataFrame(
        session.execute(
            select(DailyBar.trade_date, DailyBar.close)
            .where(
                DailyBar.symbol == symbol,
                DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
                DailyBar.trade_date >= query_start,
                DailyBar.trade_date <= query_end,
            )
            .order_by(DailyBar.trade_date)
        ).all(),
        columns=["date", "raw_close"],
    )
    if local.empty:
        return [], source
    raw_factors: dict[date, float] = {}
    if is_bse:
        events = sina.get_adjustment_factors(symbol, query_end)
        local_dates = local[["date"]].copy()
        local_dates["date"] = pd.to_datetime(
            local_dates["date"],
            errors="coerce",
        )
        factor_events = events.copy()
        factor_events["date"] = pd.to_datetime(
            factor_events["date"],
            errors="coerce",
        )
        factor_events["adj_factor"] = pd.to_numeric(
            factor_events["adj_factor"],
            errors="coerce",
        )
        factor_events = factor_events.dropna().sort_values("date")
        merged_factors = pd.merge_asof(
            local_dates.dropna().sort_values("date"),
            factor_events,
            on="date",
            direction="backward",
        )
        for record in merged_factors.to_dict(orient="records"):
            trade_date_value = record["date"]
            factor = float(record["adj_factor"])
            if (
                isinstance(trade_date_value, pd.Timestamp)
                and math.isfinite(factor)
                and factor > 0
            ):
                raw_factors[trade_date_value.date()] = factor
    else:
        adjusted = baostock.get_adjusted_closes(
            symbol,
            query_start,
            query_end,
        )
        normalized = adjusted[["date", "close"]].copy()
        normalized["date"] = pd.to_datetime(
            normalized["date"],
            errors="coerce",
        ).dt.date
        normalized["adjusted_close"] = pd.to_numeric(
            normalized["close"],
            errors="coerce",
        )
        merged = local.merge(
            normalized[["date", "adjusted_close"]],
            on="date",
            how="inner",
        )
        for record in merged.to_dict(orient="records"):
            trade_date_value = record["date"]
            raw_close = float(record["raw_close"])
            adjusted_close = float(record["adjusted_close"])
            if (
                isinstance(trade_date_value, date)
                and math.isfinite(raw_close)
                and math.isfinite(adjusted_close)
                and raw_close > 0
                and adjusted_close > 0
            ):
                raw_factors[trade_date_value] = adjusted_close / raw_close

    scale = 1.0
    if isinstance(anchor_date, date) and anchor_factor is not None:
        provider_anchor = raw_factors.get(anchor_date)
        if provider_anchor is None or provider_anchor <= 0:
            raise TushareAPIError(
                f"复权因子无法锚定既有标尺：symbol={symbol}, "
                f"anchor={anchor_date.isoformat()}"
            )
        scale = anchor_factor / provider_anchor
    return (
        [
            (trade_date_value, factor * scale)
            for trade_date_value, factor in sorted(raw_factors.items())
            if start <= trade_date_value <= end
        ],
        source,
    )


def _upsert_adj_rows(
    session: Session,
    symbol: str,
    parsed: Sequence[tuple[date, float]],
    source: str,
) -> tuple[int, int]:
    existing = {
        row.trade_date: row
        for row in session.scalars(
            select(AdjFactor).where(
                AdjFactor.symbol == symbol,
                AdjFactor.trade_date.in_([trade_date for trade_date, _ in parsed]),
            )
        )
    }
    inserted = 0
    updated = 0
    for trade_date, factor in parsed:
        row = existing.get(trade_date)
        if row is None:
            session.add(
                AdjFactor(
                    symbol=symbol,
                    trade_date=trade_date,
                    adj_factor=factor,
                    source=source,
                )
            )
            inserted += 1
        elif not math.isclose(row.adj_factor, factor, rel_tol=0, abs_tol=1e-12):
            row.adj_factor = factor
            row.source = source
            updated += 1
    return inserted, updated


def _is_sqlite_write_lock(exc: Exception) -> bool:
    return isinstance(exc, OperationalError) and "database is locked" in str(exc).lower()


def _save_adj_rows_with_lock_retry(
    session: Session,
    symbol: str,
    parsed: Sequence[tuple[date, float]],
    source: str,
) -> tuple[int, int]:
    retry_count = 0
    while True:
        try:
            inserted, updated = _upsert_adj_rows(
                session,
                symbol,
                parsed,
                source,
            )
            session.commit()
            return inserted, updated
        except OperationalError as exc:
            session.rollback()
            if not _is_sqlite_write_lock(exc) or retry_count >= len(
                _SQLITE_LOCK_RETRY_DELAYS
            ):
                raise
            delay = _SQLITE_LOCK_RETRY_DELAYS[retry_count]
            retry_count += 1
            logger.warning(
                "adj factors SQLite lock symbol=%s retry=%s/%s delay=%ss",
                symbol,
                retry_count,
                len(_SQLITE_LOCK_RETRY_DELAYS),
                delay,
            )
            sleep(delay)


def sync_adj_factors(
    session: Session,
    symbols: Sequence[str] | None = None,
    *,
    refresh_latest: bool = False,
    start_date: date | None = None,
) -> dict[str, Any]:
    """Sync factors, optionally backfilling to the first audited daily bar."""

    token = (get_settings().tushare_token or "").strip()
    if not token:
        raise TushareAPIError(
            "ALPHAPILOT_TUSHARE_TOKEN 未配置；请仅写入本地 .env。"
        )

    windows = _target_windows(session, symbols)
    stats: dict[str, Any] = {
        "total": len(windows),
        "processed": 0,
        "synced": 0,
        "skipped": 0,
        "failed_count": 0,
        "rows_inserted": 0,
        "rows_updated": 0,
        "failures": [],
        "primary_source": "tushare",
        "source_counts": {},
        "tushare_rate_limited": False,
        "refresh_latest": refresh_latest,
        "requested_start_date": start_date.isoformat() if start_date else None,
        "historical_backfill_symbols": 0,
    }
    target_symbols = {symbol for symbol, _, _ in windows}
    baostock = BaoStockMarketDataProvider()
    sina = SinaDailyBarProvider(min_interval_seconds=0.25)
    tushare_rate_limited = False

    for index, (symbol, first_bar, last_bar) in enumerate(windows, start=1):
        stats["processed"] = index
        latest_row = session.execute(
            select(
                AdjFactor.trade_date,
                AdjFactor.source,
            )
            .where(AdjFactor.symbol == symbol)
            .order_by(AdjFactor.trade_date.desc(), AdjFactor.id.desc())
            .limit(1)
        ).first()
        earliest_factor = session.scalar(
            select(func.min(AdjFactor.trade_date)).where(
                AdjFactor.symbol == symbol
            )
        )
        latest_factor = latest_row[0] if latest_row is not None else None
        existing_source = str(latest_row[1]) if latest_row is not None else None
        target_start = max(first_bar, start_date) if start_date is not None else first_bar
        request_windows: list[tuple[date, date]] = []
        historical_backfill = False
        if isinstance(earliest_factor, date) and earliest_factor > target_start:
            request_windows.append(
                (target_start, earliest_factor - timedelta(days=1))
            )
            historical_backfill = True
        if not isinstance(latest_factor, date):
            request_windows.append((target_start, last_bar))
        elif latest_factor < last_bar:
            request_windows.append((latest_factor + timedelta(days=1), last_bar))
        elif refresh_latest and latest_factor == last_bar:
            request_windows.append((latest_factor, last_bar))
        if not request_windows:
            stats["skipped"] += 1
            continue

        try:
            source = "tushare"
            parsed: list[tuple[date, float]] = []
            if existing_source in _PROVIDER_FACTOR_SOURCES:
                for window_start, window_end in request_windows:
                    window_rows, source = _provider_adj_rows(
                        session,
                        symbol,
                        window_start,
                        window_end,
                        baostock,
                        sina,
                    )
                    if source != existing_source:
                        raise TushareAPIError(
                            f"复权因子来源不一致：symbol={symbol}, "
                            f"existing={existing_source}, resolved={source}"
                        )
                    parsed.extend(window_rows)
            elif existing_source == "tushare":
                if tushare_rate_limited:
                    raise TushareAPIError(
                        f"{symbol} 历史因子来自 Tushare，当前频控下拒绝跨源拼接。",
                        code=40203,
                    )
                for window_start, window_end in request_windows:
                    try:
                        frame = _call_adj_factor(
                            token,
                            symbol,
                            window_start,
                            window_end,
                        )
                    except TushareAPIError as exc:
                        if exc.rate_limited:
                            tushare_rate_limited = True
                            stats["tushare_rate_limited"] = True
                        raise
                    parsed.extend(_parse_adj_rows(frame, symbol))
            elif existing_source is not None:
                raise TushareAPIError(
                    f"不支持的既有复权因子来源：symbol={symbol}, source={existing_source}"
                )
            elif tushare_rate_limited:
                for window_start, window_end in request_windows:
                    window_rows, source = _provider_adj_rows(
                        session,
                        symbol,
                        window_start,
                        window_end,
                        baostock,
                        sina,
                    )
                    parsed.extend(window_rows)
            else:
                try:
                    for window_start, window_end in request_windows:
                        frame = _call_adj_factor(
                            token,
                            symbol,
                            window_start,
                            window_end,
                        )
                        parsed.extend(_parse_adj_rows(frame, symbol))
                except TushareAPIError as exc:
                    if not exc.rate_limited:
                        raise
                    tushare_rate_limited = True
                    stats["tushare_rate_limited"] = True
                    parsed = []
                    for window_start, window_end in request_windows:
                        window_rows, source = _provider_adj_rows(
                            session,
                            symbol,
                            window_start,
                            window_end,
                            baostock,
                            sina,
                        )
                        parsed.extend(window_rows)
            if not parsed:
                requested_window = (
                    f"{request_windows[0][0].isoformat()}.."
                    f"{request_windows[-1][1].isoformat()}"
                )
                raise TushareAPIError(
                    f"复权因子无数据：symbol={symbol}, "
                    f"{requested_window}"
                )
            inserted, updated = _save_adj_rows_with_lock_retry(
                session,
                symbol,
                parsed,
                source,
            )
            stats["rows_inserted"] += inserted
            stats["rows_updated"] += updated
            stats["synced"] += 1
            if historical_backfill:
                stats["historical_backfill_symbols"] += 1
            source_counts = stats["source_counts"]
            if isinstance(source_counts, dict):
                source_counts[source] = int(source_counts.get(source, 0)) + 1
        except Exception as exc:
            session.rollback()
            stats["failed_count"] += 1
            failures = stats["failures"]
            if isinstance(failures, list) and len(failures) < 20:
                failures.append(
                    {
                        "symbol": symbol,
                        "error": f"{type(exc).__name__}: {exc}"[:500],
                    }
                )
        if index % 100 == 0 or index == len(windows):
            logger.info(
                "adj factor sync progress processed=%s total=%s rows=%s failed=%s",
                index,
                len(windows),
                stats["rows_inserted"],
                stats["failed_count"],
            )
        if index < len(windows):
            sleep(_REQUEST_INTERVAL_SECONDS)

    covered = {
        str(symbol)
        for symbol in session.scalars(select(AdjFactor.symbol).distinct())
        if str(symbol) in target_symbols
    }
    stats["covered_symbols"] = len(covered)
    stats["coverage"] = (
        round(len(covered) / len(target_symbols), 6) if target_symbols else 1.0
    )
    return stats


def adjusted_close_frame(
    session: Session,
    symbol: str,
    start: date,
    end: date,
) -> pd.DataFrame:
    """Reconstruct adjusted close prices and expose any factor fallback."""

    rows = session.execute(
        select(
            DailyBar.trade_date,
            DailyBar.close,
            AdjFactor.adj_factor,
        )
        .outerjoin(
            AdjFactor,
            (AdjFactor.symbol == DailyBar.symbol)
            & (AdjFactor.trade_date == DailyBar.trade_date),
        )
        .where(
            DailyBar.symbol == symbol,
            DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
            DailyBar.trade_date >= start,
            DailyBar.trade_date <= end,
        )
        .order_by(DailyBar.trade_date)
    ).all()
    frame = pd.DataFrame(rows, columns=["date", "close", "adj_factor"])
    if frame.empty:
        frame["adj_close"] = pd.Series(dtype=float)
        frame["degraded"] = pd.Series(dtype=bool)
        frame.attrs["degraded"] = False
        frame.attrs["warnings"] = []
        return frame

    missing_mask = frame["adj_factor"].isna()
    warnings: list[str] = []
    if bool(missing_mask.any()):
        missing_dates = [
            value.isoformat()
            for value in frame.loc[missing_mask, "date"].tolist()
            if isinstance(value, date)
        ]
        warning = (
            f"{symbol} 有 {len(missing_dates)} 个交易日缺少复权因子，"
            "已用 1.0 兜底；该区间收益为降级口径。"
        )
        warnings.append(warning)
        logger.warning("%s missing_dates_sample=%s", warning, missing_dates[:5])
    frame["degraded"] = missing_mask.astype(bool)
    frame["adj_factor"] = frame["adj_factor"].fillna(1.0).astype(float)
    frame["close"] = frame["close"].astype(float)
    frame["adj_close"] = frame["close"] * frame["adj_factor"]
    frame.attrs["degraded"] = bool(missing_mask.any())
    frame.attrs["warnings"] = warnings
    return frame


def daily_returns(
    session: Session,
    symbol: str,
    start: date,
    end: date,
) -> pd.Series:
    """Return daily returns from close×adjustment-factor prices."""

    frame = adjusted_close_frame(session, symbol, start, end)
    if frame.empty:
        result = pd.Series(dtype=float, name="return")
    else:
        result = (
            frame.set_index("date")["adj_close"]
            .pct_change(fill_method=None)
            .dropna()
            .rename("return")
        )
    result.attrs["degraded"] = bool(frame.attrs.get("degraded", False))
    result.attrs["warnings"] = list(frame.attrs.get("warnings", []))
    return result
