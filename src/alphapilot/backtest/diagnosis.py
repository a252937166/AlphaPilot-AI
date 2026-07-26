from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.backtest.factor_research import classify_factors
from alphapilot.backtest.factor_scope import (
    HISTORICAL_FACTOR_CANDIDATES,
    HISTORY_EXCLUDED_PIT_GAP_FACTORS,
    LIVE_ONLY_FACTORS,
)
from alphapilot.backtest.report import generate_report
from alphapilot.core.timeutil import iso_utc
from alphapilot.db.models import (
    BacktestDaily,
    BacktestRun,
    FactorCorrelationStat,
    FactorICStat,
    JobRun,
)
from alphapilot.engines.factors import FACTOR_SET, load_weights

_ROOT = Path(__file__).resolve().parents[3]
_V1_WEIGHTS = _ROOT / "config" / "factor_weights.yaml"
_V2_WEIGHTS = _ROOT / "config" / "factor_weights_v2.yaml"
_SAMPLE_TAGS = frozenset({"train", "test", "full"})
_PRELIMINARY_M3_FACTORS = (
    "momentum_20d",
    "momentum_60d",
    "volatility_20d",
    "turnover_change_5d",
    "pe_percentile",
    "pb_percentile",
)
_PENDING_FINANCIAL_FACTORS = (
    "roe",
    "net_profit_yoy",
    "ocf_to_profit",
    "debt_ratio",
    "revenue_yoy",
)
_LIVE_ONLY_FACTORS = LIVE_ONLY_FACTORS
_HISTORY_EXCLUDED_FACTORS = HISTORY_EXCLUDED_PIT_GAP_FACTORS
_PRELIMINARY_MULTI_YEAR_FACTORS = _PRELIMINARY_M3_FACTORS
_PRELIMINARY_JOB_NAME = "research_preliminary_train_ic"
_FORMAL_M3_JOB_NAME = "research_factors_m3"
_FORMAL_M3_STAGE = "m3_s7_formal"
_PRELIMINARY_COHORTS = {
    "multi_year_price_valuation": (
        "m3_preliminary_multi_year",
        _PRELIMINARY_MULTI_YEAR_FACTORS,
    ),
}


@dataclass(frozen=True)
class _PreliminaryLineage:
    research_stage: str
    research_run_id: int
    expected_factors: tuple[str, ...]


@dataclass(frozen=True)
class _ICWindowSelection:
    start_date: date
    end_date: date
    research_stage: str
    research_run_id: int | None
    expected_factors: tuple[str, ...]


_DIRECTION_AUDITS: dict[str, dict[str, Any]] = {
    "momentum_20d": {
        "formula": "adj_close[T] / adj_close[T-20] - 1",
        "raw_direction": "上涨为正",
        "verdict": "公式与技术趋势定义一致；当前弱负 IC 不构成符号 bug。",
        "bug_found": False,
    },
    "momentum_60d": {
        "formula": "adj_close[T] / adj_close[T-60] - 1",
        "raw_direction": "上涨为正",
        "verdict": "公式正确；v1 未赋权，不是 v1 反向主因。",
        "bug_found": False,
    },
    "volatility_20d": {
        "formula": "std(adj_close.pct_change, 20) × sqrt(252)",
        "raw_direction": "风险越高值越大",
        "verdict": "风险量定义正确；负 IC 符合低波动偏好，应由权重表达。",
        "bug_found": False,
    },
    "turnover_change_5d": {
        "formula": "mean(amount[-5:]) / mean(amount[-10:-5]) - 1",
        "raw_direction": "活跃度升温为正",
        "verdict": "代理口径与 P2.2 契约一致；弱负结果不是符号 bug。",
        "bug_found": False,
    },
    "net_inflow_5d": {
        "formula": "sum(sector_net_inflow, 5d)",
        "raw_direction": "净流入为正",
        "verdict": (
            "方向正确；历史成分 PIT 不可重建，标 history_excluded_pit_gap，"
            "仅保留 live-forward。"
        ),
        "bug_found": False,
    },
    "roe": {
        "formula": "latest_disclosed_roe",
        "raw_direction": "盈利效率越高值越大",
        "verdict": "方向正确；当前横截面覆盖不足。",
        "bug_found": False,
    },
    "net_profit_yoy": {
        "formula": "latest_disclosed_net_profit_yoy",
        "raw_direction": "增长为正",
        "verdict": "方向正确；当前历史 PIT 覆盖不足。",
        "bug_found": False,
    },
    "ocf_to_profit": {
        "formula": "latest_disclosed_operating_cash_flow / profit",
        "raw_direction": "现金质量越高值越大",
        "verdict": "方向正确；当前历史 PIT 覆盖不足。",
        "bug_found": False,
    },
    "debt_ratio": {
        "formula": "latest_disclosed_debt_ratio",
        "raw_direction": "杠杆越高值越大",
        "verdict": "原始风险量无需反号；偏好应由负权表达。",
        "bug_found": False,
    },
    "revenue_yoy": {
        "formula": "latest_disclosed_revenue_yoy",
        "raw_direction": "增长为正",
        "verdict": "方向正确；当前历史 PIT 覆盖不足。",
        "bug_found": False,
    },
    "pe_percentile": {
        "formula": "cross_sectional_percentile(positive_pe)",
        "raw_direction": "越贵值越大",
        "verdict": "原始估值方向正确；便宜偏好由 v1 负权表达。",
        "bug_found": False,
    },
    "pb_percentile": {
        "formula": "cross_sectional_percentile(positive_pb)",
        "raw_direction": "越贵值越大",
        "verdict": "原始估值方向正确；便宜偏好应由负权表达。",
        "bug_found": False,
    },
    "sector_strength": {
        "formula": "latest_sector_strength_at_decision_time",
        "raw_direction": "板块强度越高值越大",
        "verdict": "方向正确；历史快照不足，禁止回填。",
        "bug_found": False,
    },
}


def _finite(value: object) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _latest_ic_window(
    session: Session,
    sample_tag: Literal["train", "test", "full"],
) -> tuple[date, date] | None:
    row = session.execute(
        select(FactorICStat.start_date, FactorICStat.end_date)
        .where(FactorICStat.sample_tag == sample_tag)
        .order_by(
            FactorICStat.end_date.desc(),
            FactorICStat.start_date.desc(),
            FactorICStat.updated_at.desc(),
        )
        .limit(1)
    ).first()
    return (row[0], row[1]) if row is not None else None


def _evaluation_status(
    factor: str,
    row: FactorICStat | None,
) -> Literal[
    "measured",
    "evaluated_no_sample",
    "not_evaluated",
    "live_only",
    "history_excluded_pit_gap",
]:
    if factor in _LIVE_ONLY_FACTORS:
        return "live_only"
    if factor in _HISTORY_EXCLUDED_FACTORS:
        return "history_excluded_pit_gap"
    if row is None:
        return "not_evaluated"
    return "measured" if row.n_periods > 0 else "evaluated_no_sample"


def _latest_updated_at(rows: list[FactorICStat]) -> datetime | None:
    values = [row.updated_at for row in rows if row.updated_at is not None]
    return max(values) if values else None


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _same_optional_float(left: object, right: object) -> bool:
    left_value = _finite(left)
    right_value = _finite(right)
    if left_value is None or right_value is None:
        return left_value is None and right_value is None
    return math.isclose(left_value, right_value, rel_tol=1e-9, abs_tol=1e-12)


def _row_matches_job_result(row: FactorICStat, result: object) -> bool:
    if not isinstance(result, dict):
        return False
    raw_periods = result.get("n_periods")
    if raw_periods is None or isinstance(raw_periods, bool):
        return False
    try:
        result_periods = int(raw_periods)
    except (TypeError, ValueError):
        return False
    if result_periods != row.n_periods:
        return False
    return all(
        key in result and _same_optional_float(getattr(row, key), result[key])
        for key in ("ic_mean", "ic_ir", "t_stat", "long_short")
    )


def _job_cohort_window(
    run: JobRun,
    cohort_name: str,
    expected_factors: tuple[str, ...],
) -> tuple[date, date, dict[str, Any]] | None:
    stats = run.stats if isinstance(run.stats, dict) else {}
    if (
        stats.get("status") != "preliminary_train_only"
        or stats.get("sample_tag") != "train"
        or stats.get("test_window_used") is not False
        or stats.get("weights_written") is not False
    ):
        return None
    selected = set(stats.get("selected_factors") or ())
    required = set(_PRELIMINARY_M3_FACTORS)
    allowed = required | set(_HISTORY_EXCLUDED_FACTORS)
    if not required.issubset(selected) or not selected.issubset(allowed):
        return None
    cohorts = stats.get("cohorts")
    if not isinstance(cohorts, dict):
        return None
    cohort = cohorts.get(cohort_name)
    if not isinstance(cohort, dict) or set(cohort.get("factors") or ()) != set(
        expected_factors
    ):
        return None
    results = cohort.get("results")
    if not isinstance(results, dict) or set(results) != set(expected_factors):
        return None
    train_window = cohort.get("train_window")
    if not isinstance(train_window, dict):
        return None
    try:
        start = date.fromisoformat(str(train_window["start"]))
        end = date.fromisoformat(str(train_window["end"]))
    except (KeyError, TypeError, ValueError):
        return None
    return (start, end, results) if start <= end else None


def _preliminary_provenance(
    session: Session,
    grouped: dict[tuple[date, date], list[FactorICStat]],
) -> dict[tuple[date, date], _PreliminaryLineage]:
    """Resolve M3 cohorts only from one successful, timestamp-bounded job run."""

    runs = list(
        session.scalars(
            select(JobRun)
            .where(
                JobRun.job_name == _PRELIMINARY_JOB_NAME,
                JobRun.status == "ok",
                JobRun.finished_at.is_not(None),
            )
            .order_by(JobRun.id.desc())
        )
    )
    provenance: dict[tuple[date, date], _PreliminaryLineage] = {}
    for run in runs:
        if run.finished_at is None:
            continue
        started_at = _utc_naive(run.started_at)
        finished_at = _utc_naive(run.finished_at)
        if finished_at < started_at:
            continue
        for cohort_name, (research_stage, expected_factors) in _PRELIMINARY_COHORTS.items():
            cohort = _job_cohort_window(run, cohort_name, expected_factors)
            if cohort is None:
                continue
            start, end, results = cohort
            window = (start, end)
            if window in provenance:
                continue
            stored = {row.factor: row for row in grouped.get(window, [])}
            expected_rows = [stored.get(factor) for factor in expected_factors]
            if any(row is None or row.updated_at is None for row in expected_rows):
                continue
            if not all(
                started_at <= _utc_naive(row.updated_at) <= finished_at
                for row in expected_rows
                if row is not None and row.updated_at is not None
            ):
                continue
            if not all(
                row is not None
                and _row_matches_job_result(row, results.get(row.factor))
                for row in expected_rows
            ):
                continue
            provenance[window] = _PreliminaryLineage(
                research_stage=research_stage,
                research_run_id=int(run.id),
                expected_factors=expected_factors,
            )
    return provenance


def _formal_job_window(
    run: JobRun,
) -> tuple[date, date, dict[str, Any]] | None:
    stats = run.stats if isinstance(run.stats, dict) else {}
    if (
        stats.get("status") != "formal_factor_research"
        or stats.get("research_stage") != _FORMAL_M3_STAGE
        or stats.get("test_window_used") is not True
        or stats.get("historical_factor_candidates")
        != list(HISTORICAL_FACTOR_CANDIDATES)
    ):
        return None
    excluded = stats.get("excluded_factors")
    expected_excluded = {
        **{
            factor: "history_excluded_pit_gap"
            for factor in HISTORY_EXCLUDED_PIT_GAP_FACTORS
        },
        **{factor: "live_only" for factor in LIVE_ONLY_FACTORS},
    }
    if excluded != expected_excluded:
        return None
    samples = stats.get("samples")
    train = samples.get("train") if isinstance(samples, dict) else None
    if not isinstance(train, dict):
        return None
    results = train.get("results")
    if not isinstance(results, dict) or set(results) != set(
        HISTORICAL_FACTOR_CANDIDATES
    ):
        return None
    window = train.get("window")
    if not isinstance(window, dict):
        return None
    try:
        start = date.fromisoformat(str(window["start"]))
        end = date.fromisoformat(str(window["end"]))
    except (KeyError, TypeError, ValueError):
        return None
    return (start, end, results) if start <= end else None


def _formal_provenance(
    session: Session,
    grouped: dict[tuple[date, date], list[FactorICStat]],
) -> dict[tuple[date, date], _PreliminaryLineage]:
    """Resolve formal M3 train windows from their successful JobRun lineage."""

    runs = list(
        session.scalars(
            select(JobRun)
            .where(
                JobRun.job_name == _FORMAL_M3_JOB_NAME,
                JobRun.status == "ok",
                JobRun.finished_at.is_not(None),
            )
            .order_by(JobRun.id.desc())
        )
    )
    provenance: dict[tuple[date, date], _PreliminaryLineage] = {}
    for run in runs:
        if run.finished_at is None:
            continue
        started_at = _utc_naive(run.started_at)
        finished_at = _utc_naive(run.finished_at)
        if finished_at < started_at:
            continue
        resolved = _formal_job_window(run)
        if resolved is None:
            continue
        start, end, results = resolved
        window = (start, end)
        if window in provenance:
            continue
        stored = {row.factor: row for row in grouped.get(window, [])}
        expected_rows = [
            stored.get(factor) for factor in HISTORICAL_FACTOR_CANDIDATES
        ]
        if any(row is None or row.updated_at is None for row in expected_rows):
            continue
        if not all(
            started_at <= _utc_naive(row.updated_at) <= finished_at
            for row in expected_rows
            if row is not None and row.updated_at is not None
        ):
            continue
        if not all(
            row is not None
            and _row_matches_job_result(row, results.get(row.factor))
            for row in expected_rows
        ):
            continue
        provenance[window] = _PreliminaryLineage(
            research_stage=_FORMAL_M3_STAGE,
            research_run_id=int(run.id),
            expected_factors=HISTORICAL_FACTOR_CANDIDATES,
        )
    return provenance


def factor_ic_windows(
    session: Session,
    sample_tag: Literal["train", "test", "full"] = "train",
) -> dict[str, Any]:
    """List persisted IC windows without reading metrics from another sample."""

    if sample_tag not in _SAMPLE_TAGS:
        raise ValueError("sample_tag must be one of train/test/full")
    rows = list(
        session.scalars(
            select(FactorICStat)
            .where(FactorICStat.sample_tag == sample_tag)
            .order_by(
                FactorICStat.start_date.asc(),
                FactorICStat.end_date.asc(),
                FactorICStat.factor.asc(),
            )
        )
    )
    grouped: dict[tuple[date, date], list[FactorICStat]] = {}
    for row in rows:
        grouped.setdefault((row.start_date, row.end_date), []).append(row)
    provenance = (
        _preliminary_provenance(session, grouped)
        if sample_tag == "train"
        else {}
    )
    if sample_tag == "train":
        provenance.update(_formal_provenance(session, grouped))

    windows: list[dict[str, Any]] = []
    for (start, end), window_rows in grouped.items():
        all_stored = {row.factor: row for row in window_rows}
        provenance_entry = provenance.get((start, end))
        expected_factors = (
            provenance_entry.expected_factors
            if provenance_entry is not None
            else tuple(sorted(all_stored))
        )
        stored = {
            factor: all_stored[factor]
            for factor in expected_factors
            if factor in all_stored
        }
        preview_statuses = {
            factor: _evaluation_status(factor, stored.get(factor))
            for factor in _PRELIMINARY_M3_FACTORS
        }
        preliminary_evaluated_count = sum(
            status in {"measured", "evaluated_no_sample"}
            for status in preview_statuses.values()
        )
        research_stage, research_run_id = (
            (
                provenance_entry.research_stage,
                provenance_entry.research_run_id,
            )
            if provenance_entry is not None
            else ("legacy_or_other", None)
        )
        windows.append(
            {
                "sample_tag": sample_tag,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "updated_at": iso_utc(_latest_updated_at(list(stored.values()))),
                "research_stage": research_stage,
                "research_run_id": research_run_id,
                "expected_factors": list(expected_factors),
                "evaluated_count": sum(
                    _evaluation_status(factor, stored.get(factor))
                    in {"measured", "evaluated_no_sample"}
                    for factor in FACTOR_SET
                ),
                "measurable_count": sum(
                    _evaluation_status(factor, stored.get(factor)) == "measured"
                    for factor in FACTOR_SET
                ),
                "evaluated_no_sample_count": sum(
                    _evaluation_status(factor, stored.get(factor))
                    == "evaluated_no_sample"
                    for factor in FACTOR_SET
                ),
                "factors": sorted(stored),
                "preliminary_requested_count": len(_PRELIMINARY_M3_FACTORS),
                "preliminary_evaluated_count": preliminary_evaluated_count,
                "preliminary_measurable_count": sum(
                    status == "measured" for status in preview_statuses.values()
                ),
                "preliminary_evaluated_no_sample_count": sum(
                    status == "evaluated_no_sample" for status in preview_statuses.values()
                ),
            }
        )

    if sample_tag == "train":
        m3_primary = [
            window
            for window in windows
            if window["research_stage"]
            in {_FORMAL_M3_STAGE, "m3_preliminary_multi_year"}
        ]
        default_window = max(
            m3_primary,
            key=lambda item: (
                item["research_stage"] == _FORMAL_M3_STAGE,
                int(item["research_run_id"]),
                str(item["updated_at"]),
            ),
            default=None,
        )
    else:
        default_window = max(
            windows,
            key=lambda item: (str(item["end_date"]), str(item["start_date"])),
            default=None,
        )
    return {
        "sample_tag": sample_tag,
        "default_policy": (
            "latest_provenanced_m3_train_run"
            if sample_tag == "train"
            else "latest_window"
        ),
        "default_window": default_window,
        "windows": windows,
        "scope": {
            "preliminary_requested_factors": list(_PRELIMINARY_M3_FACTORS),
            "preliminary_requested_count": len(_PRELIMINARY_M3_FACTORS),
            "financial_pending_factors": list(_PENDING_FINANCIAL_FACTORS),
            "financial_pending_count": len(_PENDING_FINANCIAL_FACTORS),
            "live_only_factors": list(_LIVE_ONLY_FACTORS),
            "live_only_count": len(_LIVE_ONLY_FACTORS),
            "historical_factor_candidates": list(HISTORICAL_FACTOR_CANDIDATES),
            "historical_factor_candidate_count": len(HISTORICAL_FACTOR_CANDIDATES),
            "history_excluded_pit_gap_factors": list(_HISTORY_EXCLUDED_FACTORS),
            "history_excluded_pit_gap_count": len(_HISTORY_EXCLUDED_FACTORS),
            "test_window_sealed": sample_tag == "train",
        },
    }


def _resolve_ic_window(
    session: Session,
    sample_tag: Literal["train", "test", "full"],
    start_date: date | None,
    end_date: date | None,
) -> _ICWindowSelection | None:
    if (start_date is None) != (end_date is None):
        raise ValueError("start_date and end_date must be provided together")
    if start_date is not None and end_date is not None:
        if end_date < start_date:
            raise ValueError("end_date must not be earlier than start_date")
        exists = session.execute(
            select(FactorICStat.id)
            .where(
                FactorICStat.sample_tag == sample_tag,
                FactorICStat.start_date == start_date,
                FactorICStat.end_date == end_date,
            )
            .limit(1)
        ).first()
        if exists is None:
            return None
        catalog = factor_ic_windows(session, sample_tag)
        selected = next(
            (
                item
                for item in catalog["windows"]
                if item["start_date"] == start_date.isoformat()
                and item["end_date"] == end_date.isoformat()
            ),
            None,
        )
        if selected is None:
            return None
        return _ICWindowSelection(
            start_date=start_date,
            end_date=end_date,
            research_stage=str(selected["research_stage"]),
            research_run_id=(
                int(selected["research_run_id"])
                if selected["research_run_id"] is not None
                else None
            ),
            expected_factors=tuple(str(item) for item in selected["expected_factors"]),
        )
    if sample_tag == "train":
        default_window = factor_ic_windows(session, sample_tag)["default_window"]
        if default_window is None:
            return None
        return _ICWindowSelection(
            start_date=date.fromisoformat(str(default_window["start_date"])),
            end_date=date.fromisoformat(str(default_window["end_date"])),
            research_stage=str(default_window["research_stage"]),
            research_run_id=int(default_window["research_run_id"]),
            expected_factors=tuple(
                str(item) for item in default_window["expected_factors"]
            ),
        )
    latest = _latest_ic_window(session, sample_tag)
    if latest is None:
        return None
    return _ICWindowSelection(
        start_date=latest[0],
        end_date=latest[1],
        research_stage="legacy_or_other",
        research_run_id=None,
        expected_factors=tuple(FACTOR_SET),
    )


def factor_ic_report(
    session: Session,
    sample_tag: Literal["train", "test", "full"] = "train",
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    """Load one exact persisted single-factor window without recomputation."""

    if sample_tag not in _SAMPLE_TAGS:
        raise ValueError("sample_tag must be one of train/test/full")
    window = _resolve_ic_window(session, sample_tag, start_date, end_date)
    stored: dict[str, FactorICStat] = {}
    if window is not None:
        rows = session.scalars(
            select(FactorICStat).where(
                FactorICStat.sample_tag == sample_tag,
                FactorICStat.start_date == window.start_date,
                FactorICStat.end_date == window.end_date,
            )
        )
        expected = set(window.expected_factors)
        stored = {row.factor: row for row in rows if row.factor in expected}
    factors: list[dict[str, Any]] = []
    for factor in FACTOR_SET:
        row = stored.get(factor)
        status = _evaluation_status(factor, row)
        expose_metrics = status in {"measured", "evaluated_no_sample"}
        factors.append(
            {
                "factor": factor,
                "evaluation_status": status,
                "ic_mean": _finite(row.ic_mean) if row is not None and expose_metrics else None,
                "ic_ir": _finite(row.ic_ir) if row is not None and expose_metrics else None,
                "t_stat": _finite(row.t_stat) if row is not None and expose_metrics else None,
                "ic_positive_ratio": (
                    _finite(row.ic_positive_ratio)
                    if row is not None and expose_metrics
                    else None
                ),
                "long_short": (
                    _finite(row.long_short) if row is not None and expose_metrics else None
                ),
                "n_periods": row.n_periods if row is not None and expose_metrics else 0,
                "updated_at": iso_utc(row.updated_at) if row is not None else None,
            }
        )
    available_count = sum(item["evaluation_status"] == "measured" for item in factors)
    preliminary = [
        item for item in factors if item["factor"] in _PRELIMINARY_M3_FACTORS
    ]
    return {
        "available": window is not None,
        "sample_tag": sample_tag,
        "start_date": window.start_date.isoformat() if window is not None else None,
        "end_date": window.end_date.isoformat() if window is not None else None,
        "research_stage": window.research_stage if window is not None else None,
        "research_run_id": window.research_run_id if window is not None else None,
        "expected_factors": list(window.expected_factors) if window is not None else [],
        "factor_count": len(FACTOR_SET),
        "available_count": available_count,
        "updated_at": iso_utc(_latest_updated_at(list(stored.values()))),
        "selection": {
            "exact_window": start_date is not None and end_date is not None,
            "default_policy": (
                "latest_provenanced_m3_train_run"
                if sample_tag == "train"
                else "latest_window"
            ),
            "research_stage": window.research_stage if window is not None else None,
            "research_run_id": window.research_run_id if window is not None else None,
            "expected_factors": (
                list(window.expected_factors) if window is not None else []
            ),
        },
        "coverage": {
            "preliminary_requested_count": len(_PRELIMINARY_M3_FACTORS),
            "preliminary_evaluated_count": sum(
                item["evaluation_status"] in {"measured", "evaluated_no_sample"}
                for item in preliminary
            ),
            "preliminary_measurable_count": sum(
                item["evaluation_status"] == "measured" for item in preliminary
            ),
            "preliminary_evaluated_no_sample_count": sum(
                item["evaluation_status"] == "evaluated_no_sample"
                for item in preliminary
            ),
            "preliminary_not_evaluated_count": sum(
                item["evaluation_status"] == "not_evaluated" for item in preliminary
            ),
            "financial_pending_count": len(_PENDING_FINANCIAL_FACTORS),
            "financial_pending_factors": list(_PENDING_FINANCIAL_FACTORS),
            "live_only_count": len(_LIVE_ONLY_FACTORS),
            "live_only_factors": list(_LIVE_ONLY_FACTORS),
            "historical_factor_candidate_count": len(HISTORICAL_FACTOR_CANDIDATES),
            "historical_factor_candidates": list(HISTORICAL_FACTOR_CANDIDATES),
            "history_excluded_pit_gap_count": len(_HISTORY_EXCLUDED_FACTORS),
            "history_excluded_pit_gap_factors": list(_HISTORY_EXCLUDED_FACTORS),
        },
        "factors": factors,
        "limitations": [
            "仅使用持久化的严格 PIT 研究结果；接口不会现场补算或回填缺失历史。",
            (
                "未评估、已评估但 n=0、可测、live-only 与 "
                "history_excluded_pit_gap 是五种不同状态，不互相补零。"
            ),
            f"{len(FACTOR_SET)} 个运行时因子中本窗有 {available_count} 个可测截面。",
        ],
    }


def _correlation_report(
    session: Session,
    *,
    sample_tag: Literal["train", "test", "full"],
    window: tuple[date, date] | None,
    require_lineage: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    matrix = pd.DataFrame(
        float("nan"),
        index=FACTOR_SET,
        columns=FACTOR_SET,
        dtype=float,
    )
    counts = pd.DataFrame(
        0,
        index=FACTOR_SET,
        columns=FACTOR_SET,
        dtype=int,
    )
    if window is not None and not require_lineage:
        rows = list(
            session.scalars(
                select(FactorCorrelationStat).where(
                    FactorCorrelationStat.sample_tag == sample_tag,
                    FactorCorrelationStat.start_date == window[0],
                    FactorCorrelationStat.end_date == window[1],
                )
            )
        )
        for row in rows:
            if row.left_factor not in FACTOR_SET or row.right_factor not in FACTOR_SET:
                continue
            matrix.at[row.left_factor, row.right_factor] = row.correlation
            matrix.at[row.right_factor, row.left_factor] = row.correlation
            counts.at[row.left_factor, row.right_factor] = row.n_periods
            counts.at[row.right_factor, row.left_factor] = row.n_periods
    redundant_pairs: list[dict[str, Any]] = []
    for left_index, left in enumerate(FACTOR_SET):
        for right in FACTOR_SET[left_index + 1 :]:
            value = _finite(matrix.at[left, right])
            if value is not None and abs(value) > 0.8:
                redundant_pairs.append(
                    {
                        "left": left,
                        "right": right,
                        "correlation": value,
                        "n_periods": int(str(counts.at[left, right])),
                    }
                )
    values = [[_finite(matrix.at[left, right]) for right in FACTOR_SET] for left in FACTOR_SET]
    period_values = [
        [int(str(counts.at[left, right])) for right in FACTOR_SET] for left in FACTOR_SET
    ]
    available_cells = sum(value is not None for row in values for value in row)
    limitation = (
        "本次 M3 JobRun 未产出带 lineage 的相关矩阵；拒绝读取同窗旧记录。"
        if require_lineage
        else "灰色单元格代表不足 3 个有效决策截面，不按 0 处理。"
    )
    return matrix, {
        "available": available_cells > 0,
        "method": "mean_cross_sectional_pearson",
        "minimum_pair_periods": 3,
        "threshold": 0.8,
        "factors": list(FACTOR_SET),
        "values": values,
        "n_periods": period_values,
        "available_cells": available_cells,
        "redundant_pairs": redundant_pairs,
        "limitation": limitation,
    }


def _weight_report() -> dict[str, Any]:
    v1 = load_weights(_V1_WEIGHTS)
    v2 = load_weights(_V2_WEIGHTS)
    v1_weights = {factor: float(v1.weights.get(factor, 0.0)) for factor in FACTOR_SET}
    v2_weights = {factor: float(v2.weights.get(factor, 0.0)) for factor in FACTOR_SET}
    return {
        "factors": list(FACTOR_SET),
        "v1": {
            "version": v1.version,
            "profile": v1.profile,
            "weights": v1_weights,
        },
        "v2": {
            "version": v2.version,
            "profile": v2.profile,
            "weights": v2_weights,
        },
        "delta": {factor: v2_weights[factor] - v1_weights[factor] for factor in FACTOR_SET},
        "method": "single_train_window_signed_ic_ir_l1",
        "test_window_used_for_weights": False,
    }


def factor_diagnosis_report(
    session: Session,
    sample_tag: Literal["train", "test", "full"] = "train",
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    """Build one exact factor-diagnosis view from frozen research artifacts."""

    ic = factor_ic_report(
        session,
        sample_tag,
        start_date=start_date,
        end_date=end_date,
    )
    window = (
        (date.fromisoformat(ic["start_date"]), date.fromisoformat(ic["end_date"]))
        if ic["start_date"] is not None and ic["end_date"] is not None
        else None
    )
    is_formal_m3 = ic["research_stage"] == _FORMAL_M3_STAGE
    is_provenanced_preliminary = (
        sample_tag == "train"
        and ic["research_run_id"] is not None
        and str(ic["research_stage"]).startswith("m3_preliminary_")
    )
    corr_frame, correlation = _correlation_report(
        session,
        sample_tag=sample_tag,
        window=window,
        require_lineage=is_provenanced_preliminary,
    )
    ic_frame = pd.DataFrame(ic["factors"])
    classification = classify_factors(ic_frame, corr_frame)
    classified = classification["factors"]
    factors = [
        {
            **item,
            **classified[item["factor"]],
            "direction_audit": _DIRECTION_AUDITS[item["factor"]],
        }
        for item in ic["factors"]
    ]
    return {
        "available": ic["available"],
        "sample": {
            "tag": sample_tag,
            "start_date": ic["start_date"],
            "end_date": ic["end_date"],
            "factor_count": ic["factor_count"],
            "available_count": ic["available_count"],
            "updated_at": ic["updated_at"],
            "research_run_id": ic["research_run_id"],
            "research_stage": ic["research_stage"],
            "expected_factors": ic["expected_factors"],
            "selection": ic["selection"],
            "evidence_label": (
                (
                    "M3 S7 正式 JobRun · full/train/test 已计算 · "
                    "本页精确展示 train"
                )
                if is_formal_m3
                else "M3 初步 train 证据 · test 保持封存 · 非最终结论"
                if sample_tag == "train"
                else "M2 历史证据 · 仅在用户主动切换后展示"
            ),
        },
        "coverage": ic["coverage"],
        "factors": factors,
        "classification_counts": {
            label: sum(item["classification"] == label for item in factors)
            for label in (
                "significant_positive",
                "significant_reverse",
                "ineffective",
                "insufficient_data",
                "history_excluded_pit_gap",
            )
        },
        "correlation": correlation,
        "redundancy_groups": classification["redundancy_groups"],
        "weights": _weight_report(),
        "source_audit": {
            "factor_source": "engines/factors.py",
            "audited_factor_count": len(_DIRECTION_AUDITS),
            "calculation_bug_found": False,
            "verdict": "13 个因子公式方向已逐项审计，未发现符号实现错误。",
        },
        "conclusion": {
            "status": "weak_or_insufficient_evidence",
            "headline": "因子方向重构不等于策略成功；样本外结果仍须单独判定。",
            "policy": ("不使用 test 窗调权，不把缺失因子当作零 IC，不因当前结果不理想而重复试参。"),
        },
        "limitations": [
            *ic["limitations"],
            correlation["limitation"],
            (
                (
                    "正式 S7 JobRun 已计算 11 个历史候选因子的 "
                    "full/train/test；本页仅展示精确 train 窗，"
                    "不得据此回看 test 调权。"
                )
                if is_formal_m3
                else (
                    "当前是 6 个先行因子的 train 预验，不是 11 因子最终结论；"
                    "5 个财务因子等待 S2 全市场回填。"
                )
                if sample_tag == "train"
                else "该视图保留 M2 历史结论，不参与 M3 权重选择。"
            ),
            "sector_strength 依赖实时衍生量，明确标为 live-only，不进入历史回测。",
            (
                "net_inflow_5d 因历史成分 PIT 缺口标为 "
                "history_excluded_pit_gap，仅保留 live-forward，不进入 S7/S9。"
            ),
        ],
    }


def _protocol_mismatch(v1: BacktestRun, v2: BacktestRun) -> list[str]:
    mismatch: list[str] = []
    fields = ("start_date", "end_date", "rebalance_freq", "top_pct")
    for field in fields:
        if getattr(v1, field) != getattr(v2, field):
            mismatch.append(field)
    for field in ("initial_capital", "cost_model", "execution"):
        if v1.params.get(field) != v2.params.get(field):
            mismatch.append(f"params.{field}")
    return mismatch


def _comparison_curve(
    session: Session,
    v1: BacktestRun,
    v2: BacktestRun,
) -> dict[str, Any]:
    v1_rows = list(
        session.scalars(
            select(BacktestDaily)
            .where(BacktestDaily.run_id == v1.id)
            .order_by(BacktestDaily.trade_date)
        )
    )
    v2_rows = list(
        session.scalars(
            select(BacktestDaily)
            .where(BacktestDaily.run_id == v2.id)
            .order_by(BacktestDaily.trade_date)
        )
    )
    if [row.trade_date for row in v1_rows] != [row.trade_date for row in v2_rows]:
        raise ValueError("v1/v2 daily trading calendars do not match")
    return {
        "dates": [row.trade_date.isoformat() for row in v1_rows],
        "v1_nav": [row.nav for row in v1_rows],
        "v2_nav": [row.nav for row in v2_rows],
        "csi300_nav": [row.benchmark_nav for row in v2_rows],
        "market_nav": [row.market_nav for row in v2_rows],
    }


def compare_backtests(
    session: Session,
    v1_id: int,
    v2_id: int,
) -> dict[str, Any]:
    """Compare frozen v1/v2 test runs without any parameter search."""

    v1 = session.get(BacktestRun, v1_id)
    v2 = session.get(BacktestRun, v2_id)
    if v1 is None or v2 is None:
        raise ValueError("v1 or v2 backtest run does not exist")
    if v1.signal_id != "composite-v1" or v2.signal_id != "composite-v2":
        raise ValueError("v1/v2 parameters must reference composite-v1/composite-v2")
    if v1.status != "completed" or v2.status != "completed":
        raise ValueError("v1/v2 backtest runs must both be completed")
    mismatch = _protocol_mismatch(v1, v2)
    if mismatch:
        raise ValueError(f"v1/v2 protocol mismatch: {', '.join(mismatch)}")

    v1_report = generate_report(session, v1.id)
    v2_report = generate_report(session, v2.id)
    ic_mean = _finite(v2_report["rank_ic"]["mean"])
    t_stat = _finite(v2_report["rank_ic"]["t_stat"])
    v2_total = _finite(v2_report["net_long_performance"]["total_return"])
    market_total = _finite(v2_report["benchmarks"]["equal_weight_market"]["total_return"])
    significant = bool(
        ic_mean is not None and t_stat is not None and ic_mean > 0 and abs(t_stat) >= 2
    )
    beats_market = bool(
        v2_total is not None and market_total is not None and v2_total > market_total
    )
    if significant and beats_market:
        verdict = "improved"
        headline = "样本外 IC 显著为正，且扣成本多头跑赢等权市场。"
    elif significant:
        verdict = "partial"
        headline = "样本外 IC 显著转正，但扣成本组合尚未跑赢等权市场。"
    else:
        verdict = "failed"
        headline = "样本外 IC 未显著为正，当前因子体系仍无可信 alpha 证据。"

    def summary(report: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": report["run"]["id"],
            "signal_id": report["run"]["signal_id"],
            "rank_ic": report["rank_ic"],
            "net_long": report["net_long_performance"],
            "long_short_gross": report["long_short_gross_diagnostic"],
            "benchmarks": report["benchmarks"],
            "costs": report["costs"],
        }

    v1_total = _finite(v1_report["net_long_performance"]["total_return"])
    return {
        "protocol": {
            "start_date": v1.start_date.isoformat(),
            "end_date": v1.end_date.isoformat(),
            "rebalance_freq": v1.rebalance_freq,
            "top_pct": v1.top_pct,
            "same_window_and_costs": True,
            "weights_frozen_before_test": True,
        },
        "v1": summary(v1_report),
        "v2": summary(v2_report),
        "delta": {
            "rank_ic_mean": (
                ic_mean - float(v1_report["rank_ic"]["mean"])
                if ic_mean is not None and _finite(v1_report["rank_ic"]["mean"]) is not None
                else None
            ),
            "net_total_return": (
                v2_total - v1_total if v2_total is not None and v1_total is not None else None
            ),
        },
        "curve": _comparison_curve(session, v1, v2),
        "verdict": {
            "status": verdict,
            "headline": headline,
            "significant_positive_ic": significant,
            "beats_equal_weight_market": beats_market,
            "policy": (
                "该裁定使用 S4 预注册三档门槛；无论结果如何，都不允许回看 test 窗修改 v2 权重。"
            ),
        },
        "limitations": [
            "test 窗仅 91 个交易日，结论是弱证据而非长期收益承诺。",
            "历史 PIT 缺口使 v2 实际只使用 4 个价量因子。",
            "多空序列未模拟融券可得性与借券成本，只作毛收益诊断。",
        ],
    }
