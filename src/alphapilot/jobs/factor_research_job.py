from __future__ import annotations

import logging
import math
import os
from datetime import date
from pathlib import Path
from time import monotonic
from typing import Any

from alphapilot.backtest.data_health import build_data_health_report
from alphapilot.backtest.factor_research import (
    _calendar,
    all_factors_ic,
    factor_correlation,
    persist_factor_correlation,
    persist_factors_ic,
    research_factors_ic,
)
from alphapilot.backtest.factor_scope import (
    HISTORICAL_FACTOR_CANDIDATES,
    HISTORY_EXCLUDED_PIT_GAP_FACTORS,
    LIVE_ONLY_FACTORS,
)
from alphapilot.backtest.weights_rebuild import rebuild_weights
from alphapilot.core.config import get_settings
from alphapilot.db.engine import get_session
from alphapilot.jobs.registry import JobExecutionError, JobSpec, register

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]
_S6_EXTERNAL_EVIDENCE_ENV = "ALPHAPILOT_S6_EXTERNAL_PIT_EVIDENCE"
FORMAL_RESEARCH_JOB_NAME = "research_factors_m3"

MULTI_YEAR_TRAIN_FACTORS = (
    "momentum_20d",
    "momentum_60d",
    "volatility_20d",
    "turnover_change_5d",
    "pe_percentile",
    "pb_percentile",
)
PRELIMINARY_TRAIN_FACTORS = MULTI_YEAR_TRAIN_FACTORS
MISSING_PENDING_FINANCIAL_FACTORS = (
    "roe",
    "net_profit_yoy",
    "ocf_to_profit",
    "debt_ratio",
    "revenue_yoy",
)


def _validated_ratio(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("train_ratio must be a finite number within (0, 1)")
    ratio = float(value)
    if not math.isfinite(ratio) or not 0 < ratio < 1:
        raise ValueError("train_ratio must be a finite number within (0, 1)")
    return ratio


def _split_calendar(
    calendar: list[date],
    ratio: float,
) -> tuple[date, date, date, date, int]:
    train_size = int(len(calendar) * ratio)
    if train_size < 2 or len(calendar) - train_size < 2:
        raise ValueError("explicit research window needs at least two train/test sessions")
    return (
        calendar[0],
        calendar[train_size - 1],
        calendar[train_size],
        calendar[-1],
        train_size,
    )


def _table_results(table: Any) -> dict[str, dict[str, Any]]:
    return {
        str(row["factor"]): {
            "ic_mean": row["ic_mean"],
            "ic_ir": row["ic_ir"],
            "t_stat": row["t_stat"],
            "n_periods": int(row["n_periods"]),
            "long_short": row["long_short"],
        }
        for row in table.to_dict(orient="records")
    }


def _require_research_safety() -> dict[str, Any]:
    settings = get_settings()
    unsafe = {
        "trading_mode": settings.trading_mode != "research",
        "live_trading_enabled": settings.live_trading_enabled,
        "paper_trading_enabled": settings.paper_trading_enabled,
        "paper_auto_trading_enabled": settings.paper_auto_trading_enabled,
        "futu_enable_account_mutation": settings.futu_enable_account_mutation,
        "futu_enable_trade": settings.futu_enable_trade,
    }
    enabled = sorted(name for name, is_unsafe in unsafe.items() if is_unsafe)
    if enabled:
        raise JobExecutionError(
            f"formal research safety gate blocked: {', '.join(enabled)}",
            stats={
                "status": "blocked_safety",
                "safety_blockers": enabled,
                "research_started": False,
                "weights_written": False,
            },
        )
    return {
        "trading_mode": "research",
        "live_trading_enabled": False,
        "paper_trading_enabled": False,
        "paper_auto_trading_enabled": False,
        "futu_enable_account_mutation": False,
        "futu_enable_trade": False,
    }


def _database_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise RuntimeError("P3.3-S6 gate currently requires a file-backed SQLite database")
    raw_path = database_url.removeprefix(prefix)
    if not raw_path or raw_path == ":memory:" or "?" in raw_path:
        raise RuntimeError("P3.3-S6 gate requires a plain file-backed SQLite URL")
    path = Path(raw_path).expanduser()
    return path.resolve() if path.is_absolute() else (_ROOT / path).resolve()


def _require_s6_gate() -> dict[str, Any]:
    """Recompute the read-only S6 gate and fail closed before research starts."""

    try:
        evidence_value = os.environ.get(_S6_EXTERNAL_EVIDENCE_ENV, "").strip()
        if not evidence_value:
            raise RuntimeError(
                f"{_S6_EXTERNAL_EVIDENCE_ENV} must name the final signed evidence"
            )
        evidence_path = Path(evidence_value).expanduser()
        if not evidence_path.is_absolute():
            evidence_path = (_ROOT / evidence_path).resolve()
        report = build_data_health_report(
            _database_path(get_settings().database_url),
            external_pit_pairing_evidence=evidence_path,
        )
    except Exception as exc:
        raise JobExecutionError(
            f"S6 gate could not be evaluated: {type(exc).__name__}: {exc}",
            stats={
                "status": "blocked_s6",
                "s6_gate": {
                    "ready_for_s7": False,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                "research_started": False,
                "weights_written": False,
            },
        ) from exc
    gate = report.get("gate")
    pairing = report.get("external_pit_pairing")
    scope = report.get("historical_factor_scope")
    expected_scope = list(HISTORICAL_FACTOR_CANDIDATES)
    gate_ready = isinstance(gate, dict) and gate.get("ready_for_s7") is True
    pairing_accepted = (
        isinstance(pairing, dict) and pairing.get("accepted") is True
    )
    scope_exact = (
        isinstance(scope, dict)
        and scope.get("candidate_factors") == expected_scope
        and scope.get("candidate_count") == len(expected_scope)
    )
    if not (gate_ready and pairing_accepted and scope_exact):
        blockers = gate.get("blockers", []) if isinstance(gate, dict) else []
        if not pairing_accepted:
            blockers = [
                *blockers,
                {
                    "code": "EXTERNAL_PIT_PAIRING_NOT_ACCEPTED",
                    "kind": "manual_evidence",
                },
            ]
        if not scope_exact:
            blockers = [
                *blockers,
                {
                    "code": "HISTORICAL_FACTOR_SCOPE_MISMATCH",
                    "kind": "contract",
                },
            ]
        raise JobExecutionError(
            "S6 gate is not ready for S7",
            stats={
                "status": "blocked_s6",
                "s6_gate": {
                    "ready_for_s7": False,
                    "report_version": report.get("report_version"),
                    "generated_at": report.get("generated_at"),
                    "blockers": blockers,
                },
                "research_started": False,
                "weights_written": False,
            },
        )
    return {
        "ready_for_s7": True,
        "report_version": report.get("report_version"),
        "generated_at": report.get("generated_at"),
        "as_of_date": report.get("as_of_date"),
        "pit_manifest_sha256": (
            pairing.get("pit_manifest_sha256")
            if isinstance(pairing, dict)
            else None
        ),
        "external_pairing_sha256": (
            pairing.get("sha256") if isinstance(pairing, dict) else None
        ),
    }


def _window(
    start: date,
    end: date,
    *,
    sessions: int,
) -> dict[str, Any]:
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "sessions": sessions,
    }


def run_factor_research(
    *,
    start_date: date,
    end_date: date,
    do_rebuild: bool = False,
    train_ratio: float = 0.7,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the formal M3 full/train/test research on explicit date windows."""

    if end_date < start_date:
        raise ValueError("end_date must not be earlier than start_date")
    ratio = _validated_ratio(train_ratio)
    if do_rebuild and output_path is None:
        raise ValueError("do_rebuild=True requires an explicit output_path")

    # These are deliberately first: no Session, calendar, factor outcome, or
    # writable research table is touched until both current gates pass.
    safety = _require_research_safety()
    s6_gate = _require_s6_gate()
    started = monotonic()
    phase_durations: dict[str, float] = {}
    stats: dict[str, Any] = {
        "status": "formal_factor_research",
        "research_stage": "m3_s7_formal",
        "safety": safety,
        "s6_gate": s6_gate,
        "research_started": True,
        "train_ratio": ratio,
        "historical_factor_candidates": list(HISTORICAL_FACTOR_CANDIDATES),
        "excluded_factors": {
            **{
                factor: "history_excluded_pit_gap"
                for factor in HISTORY_EXCLUDED_PIT_GAP_FACTORS
            },
            **{factor: "live_only" for factor in LIVE_ONLY_FACTORS},
        },
        "samples": {},
        "correlation": {},
        "phase_durations_seconds": phase_durations,
        "do_rebuild": do_rebuild,
        "test_window_used": True,
        "weights_written": False,
    }

    try:
        phase_started = monotonic()
        with get_session() as session:
            calendar = _calendar(session, start_date, end_date)
        if not calendar:
            raise ValueError("explicit research window has no audited trading sessions")
        (
            train_start,
            train_end,
            test_start,
            test_end,
            train_size,
        ) = _split_calendar(calendar, ratio)
        phase_durations["calendar"] = round(monotonic() - phase_started, 3)
        windows = {
            "full": (calendar[0], calendar[-1], len(calendar)),
            "train": (train_start, train_end, train_size),
            "test": (test_start, test_end, len(calendar) - train_size),
        }
        stats["windows"] = {
            sample_tag: _window(start, end, sessions=sessions)
            for sample_tag, (start, end, sessions) in windows.items()
        }

        sample_tables: dict[str, Any] = {}
        for sample_tag in ("full", "train", "test"):
            sample_start, sample_end, _ = windows[sample_tag]
            logger.info(
                "formal factor research phase=%s window=%s..%s factors=%d",
                sample_tag,
                sample_start,
                sample_end,
                len(HISTORICAL_FACTOR_CANDIDATES),
            )
            phase_started = monotonic()
            with get_session() as session:
                table = all_factors_ic(
                    session,
                    sample_start,
                    sample_end,
                    sample_tag=sample_tag,
                    persist=False,
                )
            sample_tables[sample_tag] = table
            duration = round(monotonic() - phase_started, 3)
            phase_durations[f"ic_{sample_tag}"] = duration
            stats["samples"][sample_tag] = {
                "window": _window(
                    sample_start,
                    sample_end,
                    sessions=windows[sample_tag][2],
                ),
                "results": _table_results(table),
                "n_periods": {
                    factor: result["n_periods"]
                    for factor, result in _table_results(table).items()
                },
                "duration_seconds": duration,
            }

        logger.info(
            "formal factor research phase=train_correlation window=%s..%s",
            train_start,
            train_end,
        )
        phase_started = monotonic()
        with get_session() as session:
            correlation = factor_correlation(session, train_start, train_end)
        correlation_duration = round(monotonic() - phase_started, 3)
        phase_durations["train_correlation"] = correlation_duration

        logger.info("formal factor research phase=persist_atomic")
        phase_started = monotonic()
        with get_session() as session:
            for sample_tag in ("full", "train", "test"):
                sample_start, sample_end, _ = windows[sample_tag]
                persist_factors_ic(
                    session,
                    sample_tables[sample_tag],
                    sample_tag=sample_tag,  # type: ignore[arg-type]
                    start=sample_start,
                    end=sample_end,
                )
            stored_cells = persist_factor_correlation(
                session,
                correlation,
                sample_tag="train",
                start=train_start,
                end=train_end,
            )
        phase_durations["persist_atomic"] = round(
            monotonic() - phase_started,
            3,
        )
        stats["correlation"] = {
            "sample_tag": "train",
            "window": _window(train_start, train_end, sessions=train_size),
            "stored_cells": stored_cells,
            "method": correlation.attrs.get("method"),
            "minimum_pair_periods": correlation.attrs.get(
                "minimum_pair_periods"
            ),
            "decision_dates": correlation.attrs.get("decision_dates", []),
            "duration_seconds": correlation_duration,
            "lineage": {
                "job_name": FORMAL_RESEARCH_JOB_NAME,
                "sample_tag": "train",
                "start_date": train_start.isoformat(),
                "end_date": train_end.isoformat(),
            },
        }

        if do_rebuild:
            logger.info(
                "formal factor research phase=rebuild window=%s..%s output=%s",
                train_start,
                train_end,
                output_path,
            )
            phase_started = monotonic()
            with get_session() as session:
                rebuilt = rebuild_weights(
                    session,
                    train_start,
                    train_end,
                    output_path=output_path,
                )
            phase_durations["rebuild"] = round(monotonic() - phase_started, 3)
            stats["rebuild"] = rebuilt
            stats["weights_written"] = True
    except JobExecutionError:
        raise
    except Exception as exc:
        stats["status"] = "failed"
        stats["duration_seconds"] = round(monotonic() - started, 3)
        stats["failure"] = f"{type(exc).__name__}: {exc}"
        raise JobExecutionError(str(exc), stats=stats) from exc

    stats["duration_seconds"] = round(monotonic() - started, 3)
    logger.info(
        "formal factor research completed duration=%.3fs weights_written=%s",
        stats["duration_seconds"],
        stats["weights_written"],
    )
    return stats


def run_preliminary_train_ic(
    *,
    start_date: date,
    end_date: date,
    train_ratio: float = 0.7,
) -> dict[str, Any]:
    """Run the six currently valid historical factors on the sealed train window.

    The prior seven-factor preview remains an immutable audit artifact, but
    ``net_inflow_5d`` is no longer recomputed or persisted because historical
    constituent membership is not PIT-valid.
    """

    started = monotonic()
    ratio = _validated_ratio(train_ratio)
    with get_session() as session:
        multi_year_calendar = _calendar(session, start_date, end_date)
        (
            multi_year_train_start,
            multi_year_train_end,
            multi_year_test_start,
            multi_year_test_end,
            multi_year_train_size,
        ) = _split_calendar(multi_year_calendar, ratio)
        multi_year_table = research_factors_ic(
            session,
            MULTI_YEAR_TRAIN_FACTORS,
            multi_year_train_start,
            multi_year_train_end,
        )
    with get_session() as session:
        persist_factors_ic(
            session,
            multi_year_table,
            sample_tag="train",
            start=multi_year_train_start,
            end=multi_year_train_end,
        )

    multi_year_decisions = sum(
        index + 20 < multi_year_train_size
        for index in range(0, multi_year_train_size, 20)
    )
    decision_periods = multi_year_decisions
    duration_seconds = monotonic() - started
    return {
        "status": "preliminary_train_only",
        "sample_tag": "train",
        "factor_scope": "6_of_11_historical_factors",
        "historical_factor_candidates": list(HISTORICAL_FACTOR_CANDIDATES),
        "selected_factors": list(PRELIMINARY_TRAIN_FACTORS),
        "pending_financial_factors": list(MISSING_PENDING_FINANCIAL_FACTORS),
        "history_excluded_pit_gap": list(HISTORY_EXCLUDED_PIT_GAP_FACTORS),
        "cohorts": {
            "multi_year_price_valuation": {
                "factors": list(MULTI_YEAR_TRAIN_FACTORS),
                "full_calendar": {
                    "start": multi_year_calendar[0].isoformat(),
                    "end": multi_year_calendar[-1].isoformat(),
                    "sessions": len(multi_year_calendar),
                },
                "train_window": {
                    "start": multi_year_train_start.isoformat(),
                    "end": multi_year_train_end.isoformat(),
                    "sessions": multi_year_train_size,
                    "ratio": ratio,
                },
                "sealed_test_window": {
                    "start": multi_year_test_start.isoformat(),
                    "end": multi_year_test_end.isoformat(),
                    "sessions": len(multi_year_calendar) - multi_year_train_size,
                    "read_factor_outcomes": False,
                },
                "decision_periods": multi_year_decisions,
                "results": _table_results(multi_year_table),
            },
        },
        "decision_periods": decision_periods,
        "duration_seconds": round(duration_seconds, 2),
        "seconds_per_decision_period": (
            round(duration_seconds / decision_periods, 3)
            if decision_periods
            else None
        ),
        "limitations": [
            "仅 6/11 个历史候选因子；5 个财务因子等待 S2，非最终 M3 结论。",
            "只计算并落库各 cohort 的 train 样本；test/full 因子结果保持封存。",
            (
                "net_inflow_5d 因历史成分 PIT 缺口退出 S7/S9；"
                "不使用当前成分回填历史映射。"
            ),
            "未生成或修改任何因子权重。",
        ],
        "test_window_used": False,
        "weights_written": False,
    }


def register_factor_research_job() -> None:
    register(
        JobSpec(
            name="research_preliminary_train_ic",
            func=run_preliminary_train_ic,
            trigger=None,
        )
    )
    register(
        JobSpec(
            name=FORMAL_RESEARCH_JOB_NAME,
            func=run_factor_research,
            trigger=None,
        )
    )
