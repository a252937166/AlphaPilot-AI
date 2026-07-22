from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from threading import Lock
from time import monotonic

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alphapilot.data.provenance import (
    AUDITED_DAILY_BAR_SOURCES,
    MIN_AUDITED_DAILY_BAR_COVERAGE,
    MIN_AUDITED_SECURITY_UNIVERSE,
)
from alphapilot.db.models import DailyBar, SectorForecast, Security

_REFERENCE_CACHE_TTL_SECONDS = 30.0
_REFERENCE_CACHE_LIMIT = 32
_reference_cache_lock = Lock()
_reference_cache: dict[tuple[str, date, int], tuple[float, date | None, int]] = {}


@dataclass(frozen=True, slots=True)
class DailyBarCoverage:
    trade_date: date
    symbol_count: int
    reference_trade_date: date | None
    reference_symbol_count: int
    ratio: float
    minimum_ratio: float
    complete: bool


def audited_daily_symbol_count(session: Session, trade_date: date) -> int:
    value = session.scalar(
        select(func.count(func.distinct(DailyBar.symbol))).where(
            DailyBar.trade_date == trade_date,
            func.length(DailyBar.symbol) == 6,
            DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
            DailyBar.close > 0,
        )
    )
    return int(value or 0)


def _audited_reference_high_water(
    session: Session,
    trade_date: date,
) -> tuple[date | None, int]:
    universe_count = int(
        session.scalar(
            select(func.count(Security.symbol)).where(
                func.length(Security.symbol) == 6,
                Security.list_status == "listed",
            )
        )
        or 0
    )
    if universe_count >= MIN_AUDITED_SECURITY_UNIVERSE:
        reference_date = session.scalar(
            select(func.max(SectorForecast.trade_date)).where(
                SectorForecast.trade_date < trade_date
            )
        )
        if reference_date is None:
            reference_date = session.scalar(
                select(func.max(DailyBar.trade_date)).where(
                    DailyBar.symbol == "SH.000001",
                    DailyBar.trade_date < trade_date,
                    DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
                    DailyBar.close > 0,
                )
            )
        return reference_date, universe_count

    latest_id = int(session.scalar(select(func.max(DailyBar.id))) or 0)
    bind = session.get_bind()
    key = (str(bind.engine.url), trade_date, latest_id)
    now = monotonic()
    with _reference_cache_lock:
        cached = _reference_cache.get(key)
        if cached is not None and now - cached[0] <= _REFERENCE_CACHE_TTL_SECONDS:
            return cached[1], cached[2]

    count_expression = func.count(func.distinct(DailyBar.symbol))
    row = session.execute(
        select(DailyBar.trade_date, count_expression)
        .where(
            DailyBar.trade_date < trade_date,
            func.length(DailyBar.symbol) == 6,
            DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
            DailyBar.close > 0,
        )
        .group_by(DailyBar.trade_date)
        .order_by(count_expression.desc(), DailyBar.trade_date.desc())
        .limit(1)
    ).one_or_none()
    reference_date = row[0] if row is not None else None
    reference_count = int(row[1] or 0) if row is not None else 0
    with _reference_cache_lock:
        _reference_cache[key] = (now, reference_date, reference_count)
        if len(_reference_cache) > _REFERENCE_CACHE_LIMIT:
            oldest = min(_reference_cache, key=lambda item: _reference_cache[item][0])
            _reference_cache.pop(oldest, None)
    return reference_date, reference_count


def audited_daily_coverage(session: Session, trade_date: date) -> DailyBarCoverage:
    """Compare one session with a durable trusted market-universe baseline.

    A finite rolling window lets a long enough outage evict the last complete
    session and eventually self-validate (for example 47/47). The security
    master is upsert-only and its sync rejects tiny payloads, so it cannot be
    displaced by consecutive partial daily-bar imports. Fresh databases without
    that master fall back to the historical maximum; a short cache keyed by the
    latest row id keeps that bootstrap path inexpensive.
    """

    target_count = audited_daily_symbol_count(session, trade_date)
    reference_date, reference_count = _audited_reference_high_water(session, trade_date)
    if reference_date is None:
        return DailyBarCoverage(
            trade_date=trade_date,
            symbol_count=target_count,
            reference_trade_date=None,
            reference_symbol_count=0,
            ratio=0.0,
            minimum_ratio=MIN_AUDITED_DAILY_BAR_COVERAGE,
            complete=False,
        )

    ratio = target_count / reference_count if reference_count > 0 else 0.0
    return DailyBarCoverage(
        trade_date=trade_date,
        symbol_count=target_count,
        reference_trade_date=reference_date,
        reference_symbol_count=reference_count,
        ratio=ratio,
        minimum_ratio=MIN_AUDITED_DAILY_BAR_COVERAGE,
        complete=(
            reference_count > 0 and ratio >= MIN_AUDITED_DAILY_BAR_COVERAGE
        ),
    )
