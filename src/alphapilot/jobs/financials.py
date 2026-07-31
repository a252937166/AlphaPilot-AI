from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from math import isfinite
from time import monotonic, sleep
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from alphapilot.data.baostock_provider import (
    BaoStockMarketDataProvider,
    BaoStockRequestBudgetExceeded,
)
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
_SQLITE_LOCK_RETRY_DELAYS = (0.5, 1.5, 3.0)
_UNAVAILABLE_PROFILE_KEY = "financial_no_data_periods"
_FATAL_PROVIDER_ERROR_MARKERS = (
    "10001011",
    "BaoStock login failed",
    "BaoStock process lock is held",
)
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
    symbol_min: int | None = None
    symbol_max_exclusive: int | None = None
    provider_request_budget: int | None = None
    provider_requests_estimated: int = 0
    provider_probe_requests: int = 0
    provider_probe_rows: int | None = None
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
    quarters_skipped_unavailable_checkpoint: int = 0
    unavailable_checkpoints_added: int = 0
    metrics_inserted: int = 0
    metrics_updated: int = 0
    last_symbol: str | None = None
    resume_symbol: str | None = None
    stopped_for_request_budget: bool = False
    is_complete: bool = True
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
            "symbol_min": self.symbol_min,
            "symbol_max_exclusive": self.symbol_max_exclusive,
            "symbols_processed": self.symbols_processed,
            "symbols_done": self.symbols_done,
            "symbols_with_data": self.symbols_with_data,
            "symbols_skipped": self.symbols_skipped,
            "symbols_unsupported": self.symbols_unsupported,
            "symbols_failed": self.symbols_failed,
            "quarters_requested": self.quarters_requested,
            "provider_request_budget": self.provider_request_budget,
            "provider_requests_estimated": self.provider_requests_estimated,
            "provider_probe_requests": self.provider_probe_requests,
            "provider_probe_rows": self.provider_probe_rows,
            "financial_quarters_queried": self.financial_quarters_queried,
            "prior_revenue_queries": self.prior_revenue_queries,
            "quarters_done": self.quarters_done,
            "quarters_unavailable": self.quarters_unavailable,
            "quarters_skipped_unavailable_checkpoint": (
                self.quarters_skipped_unavailable_checkpoint
            ),
            "unavailable_checkpoints_added": self.unavailable_checkpoints_added,
            "metrics_inserted": self.metrics_inserted,
            "metrics_updated": self.metrics_updated,
            "last_symbol": self.last_symbol,
            "resume_symbol": self.resume_symbol,
            "stopped_for_request_budget": self.stopped_for_request_budget,
            "is_complete": self.is_complete,
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


def _is_sqlite_write_lock(exc: Exception) -> bool:
    return isinstance(exc, OperationalError) and "database is locked" in str(exc).lower()


def _is_fatal_provider_error(exc: Exception) -> bool:
    return isinstance(exc, DataProviderError) and any(
        marker in str(exc) for marker in _FATAL_PROVIDER_ERROR_MARKERS
    )


def _provider_query_count(
    provider: BaoStockMarketDataProvider,
    *,
    fallback: int,
) -> int:
    """Prefer the provider's exact counter while retaining lightweight test doubles."""

    count = getattr(provider, "financial_query_count", None)
    return int(count) if isinstance(count, int) else fallback


def _save_observations_with_lock_retry(
    symbol: str,
    observations: list[_MetricValue],
    existing_keys: set[tuple[str, str, str]],
    existing_metrics: dict[tuple[str, str], set[str]],
) -> tuple[int, int]:
    """Persist one symbol in a fresh short transaction without refetching."""

    retry_count = 0
    while True:
        new_keys: list[tuple[str, str, str]] = []
        touched_metrics: list[tuple[str, str, str]] = []
        inserted = 0
        updated = 0
        try:
            with get_session() as write_session:
                for observation in observations:
                    key = (symbol, observation.report_period, observation.metric)
                    values = {
                        "value": observation.value,
                        "source": "baostock",
                        "available_time": observation.available_time,
                        "payload": observation.payload,
                    }
                    if key in existing_keys:
                        write_session.execute(
                            update(FinancialIndicator)
                            .where(
                                FinancialIndicator.symbol == symbol,
                                FinancialIndicator.report_period == observation.report_period,
                                FinancialIndicator.metric == observation.metric,
                            )
                            .values(**values)
                        )
                        updated += 1
                    else:
                        write_session.add(
                            FinancialIndicator(
                                symbol=symbol,
                                report_period=observation.report_period,
                                metric=observation.metric,
                                **values,
                            )
                        )
                        new_keys.append(key)
                        inserted += 1
                    touched_metrics.append(key)
                write_session.commit()
            existing_keys.update(new_keys)
            for observed_symbol, report_period, metric in touched_metrics:
                existing_metrics.setdefault(
                    (observed_symbol, report_period),
                    set(),
                ).add(metric)
            return inserted, updated
        except OperationalError as exc:
            if not _is_sqlite_write_lock(exc) or retry_count >= len(_SQLITE_LOCK_RETRY_DELAYS):
                raise
            delay = _SQLITE_LOCK_RETRY_DELAYS[retry_count]
            retry_count += 1
            logger.warning(
                "financials SQLite lock symbol=%s retry=%s/%s delay=%ss",
                symbol,
                retry_count,
                len(_SQLITE_LOCK_RETRY_DELAYS),
                delay,
            )
            sleep(delay)


def _load_existing_observations(
    symbol: str,
    report_periods: set[str],
) -> tuple[
    set[tuple[str, str, str]],
    dict[tuple[str, str], set[str]],
    dict[tuple[str, str], float],
]:
    """Load one symbol's checkpoints without a full-universe SQL ``IN`` clause."""

    with get_session() as session:
        existing_rows = session.execute(
            select(
                FinancialIndicator.symbol,
                FinancialIndicator.report_period,
                FinancialIndicator.metric,
                FinancialIndicator.payload,
            ).where(
                FinancialIndicator.symbol == symbol,
                FinancialIndicator.report_period.in_(report_periods),
            )
        ).all()

    existing_keys: set[tuple[str, str, str]] = set()
    existing_metrics: dict[tuple[str, str], set[str]] = {}
    revenues: dict[tuple[str, str], float] = {}
    for observed_symbol, report_period, metric, payload in existing_rows:
        symbol_text = str(observed_symbol)
        period_text = str(report_period)
        metric_text = str(metric)
        key = (symbol_text, period_text, metric_text)
        existing_keys.add(key)
        existing_metrics.setdefault((symbol_text, period_text), set()).add(metric_text)
        if isinstance(payload, dict):
            revenue = _float_or_none(payload.get("main_business_revenue"))
            if revenue is not None:
                revenues[(symbol_text, period_text)] = revenue
    return existing_keys, existing_metrics, revenues


def _unavailable_checkpoints(profile: dict[str, Any]) -> set[str]:
    raw_periods = profile.get(_UNAVAILABLE_PROFILE_KEY, [])
    if not isinstance(raw_periods, list):
        return set()
    return {
        period
        for item in raw_periods
        if isinstance(item, str) and (period := item.strip()) and len(period) == 6
    }


def _save_unavailable_checkpoints_with_lock_retry(
    symbol: str,
    report_periods: set[str],
) -> int:
    """Persist cold-backfill no-data periods without creating fake factor rows."""

    if not report_periods:
        return 0
    retry_count = 0
    while True:
        try:
            with get_session() as session:
                security = session.scalar(select(Security).where(Security.symbol == symbol))
                if security is None:
                    raise ValueError(f"security disappeared during financial sync: {symbol}")
                profile = dict(security.profile) if isinstance(security.profile, dict) else {}
                existing = _unavailable_checkpoints(profile)
                merged = existing | report_periods
                added = len(merged - existing)
                if added:
                    profile[_UNAVAILABLE_PROFILE_KEY] = sorted(merged)
                    security.profile = profile
                    session.commit()
                return added
        except OperationalError as exc:
            if not _is_sqlite_write_lock(exc) or retry_count >= len(_SQLITE_LOCK_RETRY_DELAYS):
                raise
            delay = _SQLITE_LOCK_RETRY_DELAYS[retry_count]
            retry_count += 1
            logger.warning(
                "financial checkpoint SQLite lock symbol=%s retry=%s/%s delay=%ss",
                symbol,
                retry_count,
                len(_SQLITE_LOCK_RETRY_DELAYS),
                delay,
            )
            sleep(delay)


def sync_financials(
    quarters: int = 8,
    *,
    symbols: list[str] | None = None,
    symbol_min: int | None = None,
    symbol_max_exclusive: int | None = None,
    batch_size: int = 25,
    max_provider_requests: int | None = None,
    use_unavailable_checkpoints: bool = False,
    probe_before_run: bool = False,
) -> dict[str, Any]:
    """Synchronize quarterly financial metrics with resumable per-period checkpoints."""

    if quarters <= 0:
        raise ValueError("quarters must be positive")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if max_provider_requests is not None and max_provider_requests < 5:
        raise ValueError("max_provider_requests must be at least 5")
    if symbol_min is not None and not 0 <= symbol_min <= 999_999:
        raise ValueError("symbol_min must be between 0 and 999999")
    if symbol_max_exclusive is not None and not 1 <= symbol_max_exclusive <= 1_000_000:
        raise ValueError("symbol_max_exclusive must be between 1 and 1000000")
    if (
        symbol_min is not None
        and symbol_max_exclusive is not None
        and symbol_min >= symbol_max_exclusive
    ):
        raise ValueError("symbol_min must be less than symbol_max_exclusive")
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
        securities_query = select(
            Security.symbol,
            Security.board,
            Security.profile,
        ).order_by(Security.symbol)
        securities: list[tuple[str, str | None, dict[str, Any]]] = [
            (
                str(symbol),
                board,
                dict(profile) if isinstance(profile, dict) else {},
            )
            for symbol, board, profile in session.execute(securities_query).all()
        ]
    if requested_symbols is not None:
        securities = [
            (symbol, board, profile)
            for symbol, board, profile in securities
            if str(symbol) in requested_symbols
        ]
    if symbol_min is not None or symbol_max_exclusive is not None:
        securities = [
            (symbol, board, profile)
            for symbol, board, profile in securities
            if (symbol_min is None or int(symbol) >= symbol_min)
            and (symbol_max_exclusive is None or int(symbol) < symbol_max_exclusive)
        ]

    progress = _FinancialProgress(
        started=monotonic(),
        quarters_requested=quarters,
        symbols_total=len(securities),
        symbol_min=symbol_min,
        symbol_max_exclusive=symbol_max_exclusive,
        provider_request_budget=max_provider_requests,
    )
    configure_limit = getattr(provider, "set_financial_query_limit", None)
    if callable(configure_limit):
        configure_limit(max_provider_requests)
    if probe_before_run:
        try:
            progress.provider_probe_rows = provider.probe_financial_query()
            progress.provider_probe_requests = _provider_query_count(
                provider,
                fallback=1,
            )
            progress.provider_requests_estimated = progress.provider_probe_requests
        except Exception as exc:
            progress.provider_probe_requests = _provider_query_count(
                provider,
                fallback=max(progress.provider_requests_estimated, 1),
            )
            progress.provider_requests_estimated = progress.provider_probe_requests
            raise JobExecutionError(
                "BaoStock startup query probe failed; backfill did not start: "
                f"{type(exc).__name__}: {exc}",
                stats=progress.as_dict(),
            ) from exc

    request_budget_reached = False
    for processed, (symbol, board, profile) in enumerate(securities, start=1):
        symbol = str(symbol)
        progress.symbols_processed = processed
        progress.last_symbol = symbol
        if not _supported_symbol(symbol, board):
            progress.symbols_unsupported += 1
            consecutive_failures = 0
        else:
            existing_keys, existing_metrics, revenues = _load_existing_observations(
                symbol,
                revenue_lookup_labels,
            )
            unavailable_periods = (
                _unavailable_checkpoints(profile) if use_unavailable_checkpoints else set()
            )
            progress.quarters_skipped_unavailable_checkpoint += len(
                unavailable_periods & period_labels
            )
            missing_periods = [
                (year, quarter)
                for year, quarter in periods
                if not existing_metrics.get(
                    (symbol, _quarter_label(year, quarter)), set()
                ).issuperset(FINANCIAL_METRICS)
                and _quarter_label(year, quarter) not in unavailable_periods
            ]
            if not missing_periods:
                progress.symbols_skipped += 1
                consecutive_failures = 0
            else:
                symbol_failed = False
                symbol_error: Exception | None = None
                symbol_with_data = False
                pending_observations: list[_MetricValue] = []
                pending_unavailable_periods: set[str] = set()
                for year, quarter in missing_periods:
                    # Each quarterly bundle makes four BaoStock API calls. Keep
                    # room for the optional prior-year profit query as well, so
                    # a quota stop never persists a permanently incomplete
                    # revenue_yoy observation. The cold runner uses 40k/day,
                    # below BaoStock's published 50k daily ceiling.
                    if (
                        max_provider_requests is not None
                        and progress.provider_requests_estimated + 5 > max_provider_requests
                    ):
                        progress.stopped_for_request_budget = True
                        progress.is_complete = False
                        progress.resume_symbol = symbol
                        request_budget_reached = True
                        break
                    report_period = _quarter_label(year, quarter)
                    try:
                        progress.financial_quarters_queried += 1
                        before_bundle_count = progress.provider_requests_estimated
                        frames = provider.get_quarterly_financials(symbol, year, quarter)
                        progress.provider_requests_estimated = _provider_query_count(
                            provider,
                            fallback=before_bundle_count + 4,
                        )
                        prior_period = _quarter_label(year - 1, quarter)
                        prior_revenue = revenues.get((symbol, prior_period))
                        current_revenue = _profit_revenue(
                            frames.get("profit", pd.DataFrame()),
                            requested_year=year,
                            requested_quarter=quarter,
                        )
                        if current_revenue is not None and prior_revenue is None:
                            progress.prior_revenue_queries += 1
                            before_prior_count = progress.provider_requests_estimated
                            prior_revenue = _profit_revenue(
                                provider.get_quarterly_profit(
                                    symbol,
                                    year - 1,
                                    quarter,
                                ),
                                requested_year=year - 1,
                                requested_quarter=quarter,
                            )
                            progress.provider_requests_estimated = _provider_query_count(
                                provider,
                                fallback=before_prior_count + 1,
                            )
                            if prior_revenue is not None:
                                revenues[(symbol, prior_period)] = prior_revenue
                        observations, revenue = _normalize_quarter(
                            frames,
                            requested_year=year,
                            requested_quarter=quarter,
                            prior_revenue=prior_revenue,
                        )
                    except BaoStockRequestBudgetExceeded:
                        progress.provider_requests_estimated = _provider_query_count(
                            provider,
                            fallback=progress.provider_requests_estimated,
                        )
                        progress.stopped_for_request_budget = True
                        progress.is_complete = False
                        progress.resume_symbol = symbol
                        request_budget_reached = True
                        break
                    except Exception as exc:
                        progress.provider_requests_estimated = _provider_query_count(
                            provider,
                            fallback=progress.provider_requests_estimated,
                        )
                        if _is_fatal_provider_error(exc):
                            # Login, host-lock, and blacklist failures are not
                            # per-symbol data issues. Stop on the first symbol
                            # so JobRun.error keeps the upstream root cause and
                            # the job never multiplies connection attempts by
                            # the 20-symbol circuit-breaker threshold.
                            raise
                        symbol_failed = True
                        symbol_error = exc
                        break
                    if not observations:
                        progress.quarters_unavailable += 1
                        if use_unavailable_checkpoints:
                            pending_unavailable_periods.add(report_period)
                        continue

                    symbol_with_data = True
                    progress.quarters_done += 1
                    if revenue is not None:
                        revenues[(symbol, report_period)] = revenue
                    pending_observations.extend(observations)

                # BaoStock can take seconds per request. Persist only after all
                # provider calls for this symbol have ended, in a new short
                # transaction so concurrent workers cannot poison a stale WAL
                # read snapshot.
                if pending_observations:
                    try:
                        inserted, updated = _save_observations_with_lock_retry(
                            symbol,
                            pending_observations,
                            existing_keys,
                            existing_metrics,
                        )
                        progress.metrics_inserted += inserted
                        progress.metrics_updated += updated
                    except Exception as exc:
                        symbol_failed = True
                        symbol_error = exc
                if pending_unavailable_periods and not symbol_failed:
                    try:
                        progress.unavailable_checkpoints_added += (
                            _save_unavailable_checkpoints_with_lock_retry(
                                symbol,
                                pending_unavailable_periods,
                            )
                        )
                    except Exception as exc:
                        symbol_failed = True
                        symbol_error = exc

                if not symbol_failed:
                    progress.symbols_with_data += int(symbol_with_data)
                    if not request_budget_reached:
                        progress.symbols_done += 1
                    if not symbol_with_data and not request_budget_reached:
                        # A completed-but-not-yet-published quarter and a
                        # pre-listing quarter are honest no-data outcomes, not
                        # provider failures. Count an otherwise unchanged
                        # symbol as skipped so an idempotent rerun is visible
                        # in JobRun stats without inventing placeholder rows.
                        progress.symbols_skipped += 1
                    consecutive_failures = 0
                else:
                    assert symbol_error is not None
                    progress.record_failure(symbol, symbol_error)
                    if _is_sqlite_write_lock(symbol_error):
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                    if consecutive_failures >= 20:
                        raise JobExecutionError(
                            "financial sync stopped after 20 consecutive symbol failures; "
                            f"processed={processed}, last_symbol={symbol}",
                            stats=progress.as_dict(),
                        ) from symbol_error

        if request_budget_reached:
            logger.info(
                "financial sync stopped at request budget used=%s budget=%s resume_symbol=%s",
                progress.provider_requests_estimated,
                max_provider_requests,
                progress.resume_symbol,
            )
            break

        if processed % batch_size == 0:
            logger.info(
                "financial sync progress processed=%s total=%s inserted=%s failed=%s",
                processed,
                len(securities),
                progress.metrics_inserted,
                progress.symbols_failed,
            )

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
            enabled_key="baostock_financial_sync_enabled",
        )
    )
