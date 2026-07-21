from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from statistics import fmean, median
from time import monotonic, sleep
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.data.base import DataProviderError
from alphapilot.data.sina_provider import SinaDailyBarProvider
from alphapilot.db.engine import get_session
from alphapilot.db.models import MarketSentiment, MarketSnapshotAgg, Security, WatchlistItem
from alphapilot.engines.sentiment import compute as compute_sentiment
from alphapilot.futu.client import FutuClient, get_futu_client
from alphapilot.jobs.registry import JobSpec, register
from alphapilot.services.events import emit

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
MARKET_OPEN = time(9, 25)
MARKET_CLOSE = time(15, 5)
FUTU_BATCH_SIZE = 400
FUTU_MAX_REQUESTS = 50
LIMIT_TOLERANCE_PCT = 0.2
CAPITAL_PRICE_ANOMALY_PCT = 7.0
CAPITAL_TURNOVER_ANOMALY_PCT = 10.0


@dataclass(frozen=True, slots=True)
class SecurityMeta:
    symbol: str
    board: str | None
    is_st: bool


@dataclass(frozen=True, slots=True)
class NormalizedQuote:
    symbol: str
    source: str
    last: float
    prev_close: float
    high: float
    amount: float
    change_pct: float
    market_cap: float | None
    float_cap: float | None
    pe_ttm: float | None
    pb: float | None
    turnover_rate: float | None


def _is_market_window(now: datetime) -> bool:
    local = now.astimezone(MARKET_TIMEZONE)
    return local.weekday() < 5 and MARKET_OPEN <= local.time() <= MARKET_CLOSE


def _is_bse(meta: SecurityMeta) -> bool:
    return meta.board == "北交所" or meta.symbol.startswith(("4", "8", "92"))


def _futu_code(symbol: str) -> str:
    market = "SH" if symbol.startswith("6") else "SZ"
    return f"{market}.{symbol}"


def _symbol_digits(value: object) -> str | None:
    raw = str(value).strip()
    if "." in raw:
        raw = raw.rsplit(".", 1)[-1]
    digits = "".join(character for character in raw if character.isdigit())
    if len(digits) != 6:
        return None
    return digits


def _finite_float(value: object) -> float | None:
    try:
        result = float(str(value))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _first_finite(record: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _finite_float(record.get(key))
        if value is not None:
            return value
    return None


def _is_suspended(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value) if value is not None else False


def _fetch_futu_snapshot(
    client: FutuClient,
    codes: list[str],
    *,
    pause_seconds: float = 0.2,
) -> tuple[pd.DataFrame, int, list[dict[str, str]]]:
    """Fetch 400-code batches, isolating a rejected code only when necessary."""

    frames: list[pd.DataFrame] = []
    failures: list[dict[str, str]] = []
    request_count = 0

    def fetch(batch: list[str]) -> None:
        nonlocal request_count
        if not batch:
            return
        if request_count >= FUTU_MAX_REQUESTS:
            failures.extend(
                {"code": code, "error": "Futu retry request budget exhausted"} for code in batch
            )
            return
        if request_count:
            sleep(pause_seconds)
        request_count += 1
        try:
            payload = client.quote_call_raw("get_market_snapshot", args=[batch])
            if not isinstance(payload, pd.DataFrame) or payload.empty:
                raise DataProviderError("Futu snapshot returned an empty payload")
            frames.append(payload.copy())
        except Exception as exc:
            if len(batch) == 1:
                failures.append(
                    {
                        "code": batch[0],
                        "error": f"{type(exc).__name__}: {exc}"[:500],
                    }
                )
                return
            midpoint = len(batch) // 2
            fetch(batch[:midpoint])
            fetch(batch[midpoint:])

    for offset in range(0, len(codes), FUTU_BATCH_SIZE):
        fetch(codes[offset : offset + FUTU_BATCH_SIZE])

    if not frames:
        raise DataProviderError("Futu returned no usable A-share snapshots")
    return pd.concat(frames, ignore_index=True), request_count, failures


def _load_eastmoney_limit_up_codes(trade_date: date) -> set[str]:
    """Read Eastmoney's limit-up pool with a bounded network timeout."""

    response = httpx.get(
        "https://push2ex.eastmoney.com/getTopicZTPool",
        params={
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "dpt": "wz.ztzt",
            "Pageindex": "0",
            "pagesize": "10000",
            "sort": "fbt:asc",
            "date": trade_date.strftime("%Y%m%d"),
        },
        headers={
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": "Mozilla/5.0 (compatible; AlphaPilot-AI/0.2)",
        },
        timeout=5.0,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise DataProviderError("Eastmoney limit-up pool returned an invalid payload")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise DataProviderError("Eastmoney limit-up pool has no data object")
    pool = data.get("pool")
    if not isinstance(pool, list):
        raise DataProviderError("Eastmoney limit-up pool has no pool list")
    result: set[str] = set()
    for item in pool:
        if not isinstance(item, Mapping):
            continue
        symbol = _symbol_digits(item.get("c"))
        if symbol is not None:
            result.add(symbol)
    return result


def _limit_pct(meta: SecurityMeta) -> float:
    if meta.is_st:
        return 5.0
    if meta.board in {"创业板", "科创板"}:
        return 20.0
    if _is_bse(meta):
        return 30.0
    return 10.0


def _normalize_quotes(
    futu_frame: pd.DataFrame,
    sina_frame: pd.DataFrame | None,
    metadata: Mapping[str, SecurityMeta],
) -> dict[str, NormalizedQuote]:
    quotes: dict[str, NormalizedQuote] = {}
    sources: list[tuple[str, pd.DataFrame]] = [("futu", futu_frame)]
    if sina_frame is not None:
        sources.append(("sina", sina_frame))

    for source, frame in sources:
        for raw_record in frame.to_dict(orient="records"):
            record = {str(key): value for key, value in raw_record.items()}
            symbol = _symbol_digits(record.get("code", record.get("symbol")))
            if symbol is None or symbol not in metadata:
                continue
            last = _first_finite(record, "last_price", "last")
            prev_close = _first_finite(record, "prev_close_price", "prev_close")
            if (
                last is None
                or prev_close is None
                or last <= 0
                or prev_close <= 0
                or _is_suspended(record.get("suspension"))
            ):
                continue
            high = _first_finite(record, "high_price", "high") or last
            amount = _first_finite(record, "turnover", "amount") or 0.0
            quotes[symbol] = NormalizedQuote(
                symbol=symbol,
                source=source,
                last=last,
                prev_close=prev_close,
                high=high,
                amount=max(amount, 0.0),
                change_pct=(last / prev_close - 1.0) * 100.0,
                market_cap=_first_finite(record, "total_market_val"),
                float_cap=_first_finite(record, "circular_market_val"),
                pe_ttm=_first_finite(record, "pe_ttm_ratio", "pe_ratio"),
                pb=_first_finite(record, "pb_ratio"),
                turnover_rate=_first_finite(record, "turnover_rate"),
            )
    return quotes


def _emit_capital_anomalies(
    session: Session,
    quotes: Mapping[str, NormalizedQuote],
    watchlist_symbols: set[str],
    occurred_at: datetime,
) -> int:
    """Emit at most one auditable intraday anomaly per tracked symbol and day."""

    candidates = 0
    local_day = occurred_at.astimezone(MARKET_TIMEZONE).date().isoformat()
    for symbol in sorted(watchlist_symbols.intersection(quotes)):
        quote = quotes[symbol]
        reasons: list[str] = []
        if abs(quote.change_pct) >= CAPITAL_PRICE_ANOMALY_PCT:
            reasons.append(f"涨跌幅 {quote.change_pct:+.2f}%")
        if quote.turnover_rate is not None and quote.turnover_rate >= CAPITAL_TURNOVER_ANOMALY_PCT:
            reasons.append(f"换手率 {quote.turnover_rate:.2f}%")
        if not reasons:
            continue

        direction = max(-1.0, min(1.0, quote.change_pct / 10.0))
        price_strength = min(1.0, abs(quote.change_pct) / 10.0)
        turnover_strength = (
            min(1.0, quote.turnover_rate / 20.0) if quote.turnover_rate is not None else 0.0
        )
        emit(
            session,
            symbol=symbol,
            event_type="capital_anomaly",
            title=f"{symbol} 盘中资金异动",
            direction=direction,
            strength=max(price_strength, turnover_strength),
            summary="，".join(reasons),
            source_ref=f"market-snapshot:{local_day}:{symbol}:capital-anomaly",
            occurred_at=occurred_at,
        )
        candidates += 1
    return candidates


def _persist_sentiment(
    session: Session,
    snapshot: MarketSnapshotAgg,
    payload: Mapping[str, Any],
) -> MarketSentiment:
    if snapshot.id is None:
        raise ValueError("市场快照尚未 flush，无法绑定情绪记录。")
    subs = payload.get("subs")
    details = payload.get("details")
    if not isinstance(subs, Mapping) or not isinstance(details, Mapping):
        raise ValueError("市场情绪输出缺少可持久化的子分或审计详情。")
    values = {
        "ts": snapshot.ts,
        "score": float(payload["score"]),
        "breadth_sub": float(subs["breadth"]),
        "limitup_sub": float(subs["limitup"]),
        "volume_sub": float(subs["volume"]),
        "volatility_sub": float(subs["volatility"]),
        "label": str(payload["label"]),
        "model_version": str(payload["model_version"]),
        "details": dict(details),
    }
    row = session.scalar(
        select(MarketSentiment).where(MarketSentiment.source_snapshot_id == int(snapshot.id))
    )
    if row is None:
        row = MarketSentiment(source_snapshot_id=int(snapshot.id), **values)
        session.add(row)
    else:
        for field, value in values.items():
            setattr(row, field, value)
    session.flush()
    return row


def poll_market_snapshot(force: bool = False) -> dict[str, Any]:
    """Collect full-market breadth, persist one aggregate, and refresh valuations."""

    started = monotonic()
    local_now = datetime.now(MARKET_TIMEZONE)
    if not force and not _is_market_window(local_now):
        return {"skipped": "closed"}

    with get_session() as session:
        rows = session.execute(
            select(Security.symbol, Security.board, Security.is_st).order_by(Security.symbol)
        ).all()
        watchlist_symbols = set(session.scalars(select(WatchlistItem.symbol)).all())
    metadata = {
        str(symbol): SecurityMeta(
            symbol=str(symbol),
            board=str(board) if board is not None else None,
            is_st=bool(is_st),
        )
        for symbol, board, is_st in rows
        if len(str(symbol)) == 6 and str(symbol).startswith(("0", "3", "4", "6", "8", "9"))
    }
    if len(metadata) < 100:
        raise DataProviderError(f"security universe unexpectedly small: {len(metadata)}")

    bse_symbols = [symbol for symbol, meta in metadata.items() if _is_bse(meta)]
    bse_symbol_set = set(bse_symbols)
    futu_symbols = [symbol for symbol in metadata if symbol not in bse_symbol_set]
    futu_codes = [_futu_code(symbol) for symbol in futu_symbols]
    futu_frame, futu_requests, futu_failures = _fetch_futu_snapshot(get_futu_client(), futu_codes)

    warnings: list[str] = []
    sina_frame: pd.DataFrame | None = None
    if bse_symbols:
        try:
            sina_frame = SinaDailyBarProvider(min_interval_seconds=0.2).get_snapshot(bse_symbols)
        except DataProviderError as exc:
            warnings.append(str(exc))

    quotes = _normalize_quotes(futu_frame, sina_frame, metadata)
    minimum_coverage = max(100, math.floor(len(metadata) * 0.9))
    if len(quotes) < minimum_coverage:
        raise DataProviderError(
            "market snapshot coverage below safety floor: "
            f"quoted={len(quotes)}, universe={len(metadata)}, required={minimum_coverage}"
        )

    changes = [quote.change_pct for quote in quotes.values()]
    threshold_limit_up: set[str] = set()
    limit_down = 0
    broken_boards = 0
    for symbol, quote in quotes.items():
        limit = _limit_pct(metadata[symbol])
        at_limit_up = quote.change_pct >= limit - LIMIT_TOLERANCE_PCT
        at_limit_down = quote.change_pct <= -limit + LIMIT_TOLERANCE_PCT
        high_change_pct = (quote.high / quote.prev_close - 1.0) * 100.0
        if at_limit_up:
            threshold_limit_up.add(symbol)
        if at_limit_down:
            limit_down += 1
        if high_change_pct >= limit - LIMIT_TOLERANCE_PCT and not at_limit_up:
            broken_boards += 1

    zt_source = "eastmoney-pool"
    limit_up_pool_size: int | None = None
    try:
        limit_up_pool = _load_eastmoney_limit_up_codes(local_now.date())
        limit_up_pool_size = len(limit_up_pool)
        limit_up = len(limit_up_pool.intersection(quotes))
    except Exception as exc:
        zt_source = "futu-threshold"
        limit_up = len(threshold_limit_up)
        warnings.append(f"Eastmoney limit-up pool unavailable: {type(exc).__name__}: {exc}")

    as_of = datetime.now(UTC)
    source = "futu+sina" if any(item.source == "sina" for item in quotes.values()) else "futu"
    aggregate = MarketSnapshotAgg(
        ts=as_of,
        advancers=sum(value > 0 for value in changes),
        decliners=sum(value < 0 for value in changes),
        unchanged=sum(value == 0 for value in changes),
        limit_up=limit_up,
        limit_down=limit_down,
        broken_boards=broken_boards,
        up_gt4=sum(value > 4.0 for value in changes),
        down_gt4=sum(value < -4.0 for value in changes),
        total_amount=sum(quote.amount for quote in quotes.values()),
        avg_change_pct=fmean(changes),
        median_change_pct=median(changes),
        source=source,
    )
    with get_session() as session:
        session.add(aggregate)
        session.flush()
        sentiment = compute_sentiment(
            session,
            aggregate,
            source_context={
                "zt_source": zt_source,
                "limit_up_pool_size": limit_up_pool_size,
                "limit_up_threshold": len(threshold_limit_up),
                "quoted": len(quotes),
                "universe": len(metadata),
            },
        )
        sentiment_row = _persist_sentiment(session, aggregate, sentiment)
        securities = session.scalars(select(Security).where(Security.symbol.in_(quotes))).all()
        for security in securities:
            quote = quotes[security.symbol]
            if quote.market_cap is not None:
                security.market_cap = quote.market_cap
            if quote.float_cap is not None:
                security.float_cap = quote.float_cap
            if quote.pe_ttm is not None:
                security.pe_ttm = quote.pe_ttm
            if quote.pb is not None:
                security.pb = quote.pb
            if quote.turnover_rate is not None:
                security.turnover_rate = quote.turnover_rate
            security.snapshot_at = as_of
        capital_anomalies = _emit_capital_anomalies(
            session,
            quotes,
            watchlist_symbols,
            as_of,
        )
        session.flush()
        aggregate_id = aggregate.id

    futu_returned = sum(quote.source == "futu" for quote in quotes.values())
    sina_returned = sum(quote.source == "sina" for quote in quotes.values())
    excluded_symbols = sorted(set(metadata).difference(quotes))
    return {
        "aggregate_id": aggregate_id,
        "as_of": as_of.isoformat(),
        "universe": len(metadata),
        "quoted": len(quotes),
        "excluded": len(metadata) - len(quotes),
        "excluded_symbols": excluded_symbols[:20],
        "futu_requested": len(futu_symbols),
        "futu_returned": futu_returned,
        "futu_requests": futu_requests,
        "futu_failures": futu_failures[:20],
        "sina_requested": len(bse_symbols),
        "sina_returned": sina_returned,
        "zt_source": zt_source,
        "limit_up_pool_size": limit_up_pool_size,
        "limit_up_threshold": len(threshold_limit_up),
        "capital_anomaly_candidates": capital_anomalies,
        "sentiment_id": sentiment_row.id,
        "sentiment": sentiment,
        "warnings": warnings,
        "duration_seconds": round(monotonic() - started, 2),
    }


def register_market_poll_job() -> None:
    register(
        JobSpec(
            name="poll_market_snapshot",
            func=poll_market_snapshot,
            trigger=IntervalTrigger(seconds=60, timezone=MARKET_TIMEZONE),
            enabled_key="market_poll_enabled",
        )
    )
