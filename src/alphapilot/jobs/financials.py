from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from math import isfinite
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select, update

from alphapilot.data.baostock_provider import BaoStockMarketDataProvider
from alphapilot.data.base import DataProviderError
from alphapilot.db.engine import get_session
from alphapilot.db.models import FinancialIndicator, Security
from alphapilot.jobs.registry import JobExecutionError, JobSpec, register

FINANCIAL_METRICS = frozenset(
    {
        "roe",
        "net_profit_yoy",
        "ocf_to_profit",
        "debt_ratio",
        "revenue_yoy",
    }
)
_MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _MetricValue:
    metric: str
    value: float | None
    report_period: str
    available_time: datetime
    payload: dict[str, Any]


@dataclass(slots=True)
class _FinancialProgress:
    started: float
    quarters_requested: int
    symbols_total: int
    symbols_processed: int = 0
    symbols_done: int = 0
    symbols_with_data: int = 0
    symbols_skipped: int = 0
    symbols_unsupported: int = 0
    symbols_failed: int = 0
    financial_quarters_queried: int = 0
    prior_revenue_queries: int = 0
    quarters_done: int = 0
    quarters_unavailable: int = 0
    metrics_inserted: int = 0
    metrics_updated: int = 0
    last_symbol: str | None = None
    failures: list[dict[str, str]] = field(default_factory=list)

    def record_failure(self, symbol: str, exc: Exception) -> None:
        self.symbols_failed += 1
        if len(self.failures) < 20:
            self.failures.append(
                {
                    "symbol": symbol,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbols_total": self.symbols_total,
            "symbols_processed": self.symbols_processed,
            "symbols_done": self.symbols_done,
            "symbols_with_data": self.symbols_with_data,
            "symbols_skipped": self.symbols_skipped,
            "symbols_unsupported": self.symbols_unsupported,
            "symbols_failed": self.symbols_failed,
            "quarters_requested": self.quarters_requested,
            "financial_quarters_queried": self.financial_quarters_queried,
            "prior_revenue_queries": self.prior_revenue_queries,
            "quarters_done": self.quarters_done,
            "quarters_unavailable": self.quarters_unavailable,
            "metrics_inserted": self.metrics_inserted,
            "metrics_updated": self.metrics_updated,
            "last_symbol": self.last_symbol,
            "failures": list(self.failures),
            "duration_seconds": round(monotonic() - self.started, 2),
        }


def _quarter_label(year: int, quarter: int) -> str:
    return f"{year}Q{quarter}"


def _completed_quarters(count: int, today: date | None = None) -> list[tuple[int, int]]:
    """Return completed calendar quarters in oldest-to-newest order."""

    if count <= 0:
        raise ValueError("quarters must be positive")
    current = today or datetime.now(_MARKET_TIMEZONE).date()
    year = current.year
    quarter = (current.month - 1) // 3 + 1
    result: list[tuple[int, int]] = []
    for _ in range(count):
        quarter -= 1
        if quarter == 0:
            year -= 1
            quarter = 4
        result.append((year, quarter))
    result.reverse()
    return result


def _record(frame: pd.DataFrame) -> dict[str, Any] | None:
    if frame.empty:
        return None
    raw = frame.iloc[-1].to_dict()
    return {str(key).strip().lower(): value for key, value in raw.items()}


def _float_or_none(value: object) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _raw_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _date_or_none(value: object) -> date | None:
    if value is None or str(value).strip() == "":
        return None
    parsed = pd.to_datetime(str(value), errors="coerce")
    if pd.isna(parsed):
        return None
    return pd.Timestamp(parsed).date()


def _profit_revenue(
    frame: pd.DataFrame,
    *,
    requested_year: int,
    requested_quarter: int,
) -> float | None:
    row = _record(frame)
    if row is None:
        return None
    stat_date = _date_or_none(row.get("statdate"))
    if stat_date is None:
        raise DataProviderError(
            f"BaoStock profit data missing statDate for {requested_year}Q{requested_quarter}"
        )
    observed_period = _quarter_label(
        stat_date.year,
        (stat_date.month - 1) // 3 + 1,
    )
    expected_period = _quarter_label(requested_year, requested_quarter)
    if observed_period != expected_period:
        raise DataProviderError(
            "BaoStock profit period mismatch: "
            f"requested={expected_period}, observed={observed_period}"
        )
    return _float_or_none(row.get("mbrevenue"))


def _availability(stat_date: date, pub_date: date | None) -> tuple[datetime, str, bool]:
    if pub_date is not None:
        # BaoStock only provides a date. Waiting until the next local day prevents
        # an intraday backtest from assuming the report existed before publication.
        local_time = datetime.combine(
            pub_date + timedelta(days=1),
            time.min,
            tzinfo=_MARKET_TIMEZONE,
        )
        return local_time.astimezone(UTC), "provider_pub_date_end_of_day", False
    local_time = datetime.combine(
        stat_date + timedelta(days=45),
        time.min,
        tzinfo=_MARKET_TIMEZONE,
    )
    return local_time.astimezone(UTC), "stat_date_plus_45_days", True


def _normalize_quarter(
    frames: dict[str, pd.DataFrame],
    *,
    requested_year: int,
    requested_quarter: int,
    prior_revenue: float | None,
) -> tuple[list[_MetricValue], float | None]:
    records = {dataset: _record(frame) for dataset, frame in frames.items()}
    present = {dataset: row for dataset, row in records.items() if row is not None}
    if not present:
        return [], None

    stat_dates: dict[str, date] = {}
    for dataset, row in present.items():
        parsed = _date_or_none(row.get("statdate"))
        if parsed is None:
            raise DataProviderError(
                f"BaoStock {dataset} financials missing statDate for "
                f"{requested_year}Q{requested_quarter}"
            )
        stat_dates[dataset] = parsed
    expected_period = _quarter_label(requested_year, requested_quarter)
    observed_periods = {
        _quarter_label(item.year, (item.month - 1) // 3 + 1) for item in stat_dates.values()
    }
    if observed_periods != {expected_period}:
        raise DataProviderError(
            f"BaoStock financial period mismatch: requested={expected_period}, "
            f"observed={sorted(observed_periods)}"
        )
    fallback_stat_date = max(stat_dates.values())

    profit = records.get("profit") or {}
    growth = records.get("growth") or {}
    cash_flow = records.get("cash_flow") or {}
    balance = records.get("balance") or {}
    revenue = _float_or_none(profit.get("mbrevenue"))
    revenue_yoy = (
        revenue / prior_revenue - 1.0
        if revenue is not None and prior_revenue is not None and prior_revenue != 0.0
        else None
    )
    prior_period = _quarter_label(requested_year - 1, requested_quarter)
    values: dict[str, tuple[float | None, str, Any, str]] = {
        "roe": (
            _float_or_none(profit.get("roeavg")),
            "profit.roeAvg",
            _raw_text(profit.get("roeavg")),
            "profit",
        ),
        "net_profit_yoy": (
            _float_or_none(growth.get("yoyni")),
            "growth.YOYNI",
            _raw_text(growth.get("yoyni")),
            "growth",
        ),
        "ocf_to_profit": (
            _float_or_none(cash_flow.get("cfotonp")),
            "cash_flow.CFOToNP",
            _raw_text(cash_flow.get("cfotonp")),
            "cash_flow",
        ),
        "debt_ratio": (
            _float_or_none(balance.get("liabilitytoasset")),
            "balance.liabilityToAsset",
            _raw_text(balance.get("liabilitytoasset")),
            "balance",
        ),
        "revenue_yoy": (
            revenue_yoy,
            "derived.profit.MBRevenue_yoy",
            {
                "current_main_business_revenue": _raw_text(profit.get("mbrevenue")),
                "prior_main_business_revenue": prior_revenue,
            },
            "profit",
        ),
    }
    metrics: list[_MetricValue] = []
    for metric, (value, source_field, raw_source_value, dataset) in values.items():
        source_row = records.get(dataset) or {}
        source_stat_date = stat_dates.get(dataset, fallback_stat_date)
        source_pub_date = _date_or_none(source_row.get("pubdate"))
        available_time, basis, approximate = _availability(
            source_stat_date,
            source_pub_date,
        )
        payload: dict[str, Any] = {
            "available_time_basis": basis,
            "approx": approximate,
            "stat_date": source_stat_date.isoformat(),
            "pub_dates": [source_pub_date.isoformat()] if source_pub_date else [],
            "source_field": source_field,
            "raw_source_value": raw_source_value,
            "value_unit": "ratio_decimal",
            "main_business_revenue": revenue,
        }
        if metric == "revenue_yoy":
            payload["prior_report_period"] = prior_period
            payload["prior_main_business_revenue"] = prior_revenue
            if value is None:
                if revenue is None:
                    payload["unavailable_reason"] = "missing_current_revenue"
                elif prior_revenue is None:
                    payload["unavailable_reason"] = "missing_prior_year_revenue"
                else:
                    payload["unavailable_reason"] = "zero_prior_year_revenue"
        metrics.append(
            _MetricValue(
                metric=metric,
                value=value,
                report_period=expected_period,
                available_time=available_time,
                payload=payload,
            )
        )
    return metrics, revenue


def _supported_symbol(symbol: str, board: str | None) -> bool:
    return board != "北交所" and not symbol.startswith(("4", "8", "92"))


def sync_financials(
    quarters: int = 8,
    *,
    symbols: list[str] | None = None,
    batch_size: int = 25,
) -> dict[str, Any]:
    """Synchronize quarterly financial metrics with resumable per-period checkpoints."""

    if quarters <= 0:
        raise ValueError("quarters must be positive")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    requested_symbols = None
    if symbols is not None:
        requested_symbols = {
            "".join(character for character in symbol if character.isdigit()) for symbol in symbols
        }
        if any(len(symbol) != 6 for symbol in requested_symbols):
            raise ValueError("symbols must contain six-digit A-share codes")

    periods = _completed_quarters(quarters)
    period_labels = {_quarter_label(year, quarter) for year, quarter in periods}
    revenue_lookup_labels = period_labels | {
        _quarter_label(year - 1, quarter) for year, quarter in periods
    }
    provider = BaoStockMarketDataProvider()
    consecutive_failures = 0

    with get_session() as session:
        securities_query = select(Security.symbol, Security.board).order_by(Security.symbol)
        if requested_symbols is not None:
            securities_query = securities_query.where(Security.symbol.in_(requested_symbols))
        securities = session.execute(securities_query).all()
        existing_keys: set[tuple[str, str, str]] = set()
        existing_metrics: dict[tuple[str, str], set[str]] = {}
        revenues: dict[tuple[str, str], float] = {}
        if securities:
            security_symbols = [str(symbol) for symbol, _board in securities]
            existing_rows = session.execute(
                select(
                    FinancialIndicator.symbol,
                    FinancialIndicator.report_period,
                    FinancialIndicator.metric,
                    FinancialIndicator.payload,
                ).where(
                    FinancialIndicator.symbol.in_(security_symbols),
                    FinancialIndicator.report_period.in_(revenue_lookup_labels),
                )
            ).all()
            for symbol, report_period, metric, payload in existing_rows:
                symbol_text = str(symbol)
                period_text = str(report_period)
                metric_text = str(metric)
                key = (symbol_text, period_text, metric_text)
                existing_keys.add(key)
                existing_metrics.setdefault((symbol_text, period_text), set()).add(metric_text)
                if isinstance(payload, dict):
                    revenue = _float_or_none(payload.get("main_business_revenue"))
                    if revenue is not None:
                        revenues[(symbol_text, period_text)] = revenue

        progress = _FinancialProgress(
            started=monotonic(),
            quarters_requested=quarters,
            symbols_total=len(securities),
        )
        for processed, (symbol, board) in enumerate(securities, start=1):
            symbol = str(symbol)
            progress.symbols_processed = processed
            progress.last_symbol = symbol
            if not _supported_symbol(symbol, board):
                progress.symbols_unsupported += 1
                consecutive_failures = 0
            else:
                missing_periods = [
                    (year, quarter)
                    for year, quarter in periods
                    if not existing_metrics.get(
                        (symbol, _quarter_label(year, quarter)), set()
                    ).issuperset(FINANCIAL_METRICS)
                ]
                if not missing_periods:
                    progress.symbols_skipped += 1
                    consecutive_failures = 0
                else:
                    symbol_failed = False
                    symbol_error: Exception | None = None
                    symbol_with_data = False
                    pending_observations: list[_MetricValue] = []
                    for year, quarter in missing_periods:
                        report_period = _quarter_label(year, quarter)
                        try:
                            progress.financial_quarters_queried += 1
                            frames = provider.get_quarterly_financials(symbol, year, quarter)
                            prior_period = _quarter_label(year - 1, quarter)
                            prior_revenue = revenues.get((symbol, prior_period))
                            current_revenue = _profit_revenue(
                                frames.get("profit", pd.DataFrame()),
                                requested_year=year,
                                requested_quarter=quarter,
                            )
                            if current_revenue is not None and prior_revenue is None:
                                progress.prior_revenue_queries += 1
                                prior_revenue = _profit_revenue(
                                    provider.get_quarterly_profit(
                                        symbol,
                                        year - 1,
                                        quarter,
                                    ),
                                    requested_year=year - 1,
                                    requested_quarter=quarter,
                                )
                                if prior_revenue is not None:
                                    revenues[(symbol, prior_period)] = prior_revenue
                            observations, revenue = _normalize_quarter(
                                frames,
                                requested_year=year,
                                requested_quarter=quarter,
                                prior_revenue=prior_revenue,
                            )
                        except Exception as exc:
                            progress.record_failure(symbol, exc)
                            consecutive_failures += 1
                            symbol_failed = True
                            symbol_error = exc
                            break
                        if not observations:
                            progress.quarters_unavailable += 1
                            continue

                        symbol_with_data = True
                        progress.quarters_done += 1
                        if revenue is not None:
                            revenues[(symbol, report_period)] = revenue
                        pending_observations.extend(observations)

                    # BaoStock can take seconds per request. Apply the collected
                    # rows only after all network calls for this symbol have ended,
                    # then commit immediately so SQLite write locks stay short.
                    for observation in pending_observations:
                        key = (symbol, observation.report_period, observation.metric)
                        values = {
                            "value": observation.value,
                            "source": "baostock",
                            "available_time": observation.available_time,
                            "payload": observation.payload,
                        }
                        if key in existing_keys:
                            session.execute(
                                update(FinancialIndicator)
                                .where(
                                    FinancialIndicator.symbol == symbol,
                                    FinancialIndicator.report_period == observation.report_period,
                                    FinancialIndicator.metric == observation.metric,
                                )
                                .values(**values)
                            )
                            progress.metrics_updated += 1
                        else:
                            session.add(
                                FinancialIndicator(
                                    symbol=symbol,
                                    report_period=observation.report_period,
                                    metric=observation.metric,
                                    **values,
                                )
                            )
                            existing_keys.add(key)
                            progress.metrics_inserted += 1
                        existing_metrics.setdefault((symbol, observation.report_period), set()).add(
                            observation.metric
                        )
                    if pending_observations:
                        session.commit()
                        session.expunge_all()

                    if not symbol_failed:
                        progress.symbols_done += 1
                        progress.symbols_with_data += int(symbol_with_data)
                        consecutive_failures = 0
                    elif consecutive_failures >= 20:
                        session.commit()
                        raise JobExecutionError(
                            "financial sync stopped after 20 consecutive symbol failures; "
                            f"processed={processed}, last_symbol={symbol}",
                            stats=progress.as_dict(),
                        ) from symbol_error

            if processed % batch_size == 0:
                logger.info(
                    "financial sync progress processed=%s total=%s inserted=%s failed=%s",
                    processed,
                    len(securities),
                    progress.metrics_inserted,
                    progress.symbols_failed,
                )

        session.commit()

    return progress.as_dict()


def register_financials_job() -> None:
    register(
        JobSpec(
            name="sync_financials",
            func=sync_financials,
            trigger=CronTrigger(
                day_of_week="sat",
                hour=10,
                minute=0,
                timezone=_MARKET_TIMEZONE,
            ),
        )
    )
