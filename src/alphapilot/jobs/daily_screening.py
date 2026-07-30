from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from time import monotonic
from typing import Any

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alphapilot.db.engine import get_session
from alphapilot.db.models import CompositeScore, ScreeningRun, StyleDaily
from alphapilot.domain.models import ScreeningRequest, ScreeningResponse
from alphapilot.jobs.factors import MARKET_TIMEZONE
from alphapilot.jobs.prediction_reconcile import prediction_outputs_readiness
from alphapilot.jobs.registry import JobExecutionError, JobSpec, register
from alphapilot.services.screening_v2 import (
    ScreeningUnavailableError,
    persist_screening_response,
    run_factor_screen,
    screening_filters,
)

AUTO_SCREEN_TOP_N = 50
AUTO_SCREEN_HORIZON_DAYS = 20


@dataclass(frozen=True, slots=True)
class _ScreenContext:
    target_date: date
    readiness: dict[str, Any]
    model_version: str
    source_fingerprint: str
    idempotency_key: str


def _automatic_request() -> ScreeningRequest:
    return ScreeningRequest(
        universe="all",
        top_n=AUTO_SCREEN_TOP_N,
        sort_by="score",
        horizon_days=AUTO_SCREEN_HORIZON_DAYS,
    )


def _screen_source(
    session: Session,
    target_date: date,
) -> tuple[str, str]:
    model_version = session.scalar(
        select(CompositeScore.model_version)
        .where(CompositeScore.trade_date == target_date)
        .limit(1)
    )
    style_row = session.get(StyleDaily, target_date)
    if not isinstance(model_version, str) or style_row is None:
        raise JobExecutionError(
            "最新评分或风格截面缺失，自动选股未生成。",
            stats={
                "date": target_date.isoformat(),
                "reason": "screening_source_missing",
            },
        )
    fingerprint = str(style_row.source_fingerprint or "").strip()
    if not fingerprint:
        raise JobExecutionError(
            "最新风格截面缺少来源指纹，自动选股未生成。",
            stats={
                "date": target_date.isoformat(),
                "reason": "style_source_fingerprint_missing",
            },
        )
    return model_version, fingerprint


def _idempotency_key(
    *,
    target_date: date,
    model_version: str,
    source_fingerprint: str,
    factor_job_run_id: int | None,
    style_job_run_id: int | None,
    request: ScreeningRequest,
) -> str:
    payload = {
        "date": target_date.isoformat(),
        "model_version": model_version,
        "source_fingerprint": source_fingerprint,
        "factor_job_run_id": factor_job_run_id,
        "style_job_run_id": style_job_run_id,
        "filters": screening_filters(request),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"daily-screen:{target_date.isoformat()}:{digest[:32]}"


def _existing_run(session: Session, idempotency_key: str) -> ScreeningRun | None:
    return session.scalar(
        select(ScreeningRun).where(ScreeningRun.idempotency_key == idempotency_key).limit(1)
    )


def _job_run_id(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _screen_context(
    session: Session,
    request: ScreeningRequest,
) -> tuple[_ScreenContext | None, dict[str, Any]]:
    target_date, ready, readiness = prediction_outputs_readiness(session)
    if target_date is None or not ready:
        return None, readiness
    model_version, source_fingerprint = _screen_source(session, target_date)
    return (
        _ScreenContext(
            target_date=target_date,
            readiness=readiness,
            model_version=model_version,
            source_fingerprint=source_fingerprint,
            idempotency_key=_idempotency_key(
                target_date=target_date,
                model_version=model_version,
                source_fingerprint=source_fingerprint,
                factor_job_run_id=_job_run_id(readiness.get("factor_job_run_id")),
                style_job_run_id=_job_run_id(readiness.get("style_job_run_id")),
                request=request,
            ),
        ),
        readiness,
    )


def _validate_response(
    response: ScreeningResponse,
    context: _ScreenContext,
) -> None:
    candidate_dates = {
        candidate.trade_date
        for candidate in response.candidates
        if candidate.trade_date is not None
    }
    if (
        not response.candidates
        or candidate_dates != {context.target_date}
        or response.model_version != context.model_version
    ):
        raise JobExecutionError(
            "自动选股候选为空、日期或模型版本不一致，未保存结果。",
            stats={
                **context.readiness,
                "reason": "screening_candidate_contract_mismatch",
                "candidate_count": len(response.candidates),
                "candidate_dates": sorted(item.isoformat() for item in candidate_dates),
                "screen_model_version": response.model_version,
            },
        )
    missing_styles = [
        candidate.symbol for candidate in response.candidates if candidate.style is None
    ]
    if missing_styles:
        raise JobExecutionError(
            "自动选股候选缺少风格标签，未保存结果。",
            stats={
                **context.readiness,
                "reason": "screening_candidate_style_missing",
                "missing_style_count": len(missing_styles),
                "missing_style_symbols": missing_styles[:20],
            },
        )


def _already_current_stats(
    *,
    context: _ScreenContext,
    existing: ScreeningRun,
    started: float,
) -> dict[str, Any]:
    return {
        **context.readiness,
        "screen_run_id": existing.id,
        "screen_model_version": existing.model_version,
        "source_fingerprint": context.source_fingerprint,
        "candidate_count": len(existing.candidates),
        "skipped": "already_current",
        "duration_seconds": round(monotonic() - started, 2),
    }


def generate_daily_screening() -> dict[str, Any]:
    """Persist one honest full-market shortlist per complete prediction lineage."""

    started = monotonic()
    request = _automatic_request()
    with get_session() as session:
        initial, readiness = _screen_context(session, request)
        if initial is None:
            return {
                **readiness,
                "screen_run_id": None,
                "skipped": "prediction_outputs_not_current",
                "duration_seconds": round(monotonic() - started, 2),
            }

        existing = _existing_run(session, initial.idempotency_key)
        if existing is not None:
            return _already_current_stats(
                context=initial,
                existing=existing,
                started=started,
            )

        try:
            response = run_factor_screen(session, request)
        except ScreeningUnavailableError as exc:
            raise JobExecutionError(
                "自动选股期间输入发生变化，未保存结果。",
                stats={
                    **initial.readiness,
                    "reason": "screening_inputs_changed",
                },
            ) from exc
        _validate_response(response, initial)

    # Close the long read transaction before upgrading to a write. A second
    # session rechecks lineage so a concurrent EOD refresh cannot persist a
    # snapshot built from superseded inputs.
    with get_session() as session:
        final, readiness = _screen_context(session, request)
        if final is None or final.idempotency_key != initial.idempotency_key:
            return {
                **readiness,
                "screen_run_id": None,
                "skipped": "screening_inputs_changed",
                "duration_seconds": round(monotonic() - started, 2),
            }
        existing = _existing_run(session, final.idempotency_key)
        if existing is not None:
            return _already_current_stats(
                context=final,
                existing=existing,
                started=started,
            )

    # The insert gets a third, write-only Session. Reusing the validation
    # Session would upgrade an established WAL read snapshot and can fail with
    # SQLITE_BUSY_SNAPSHOT when the 30/60-second jobs commit concurrently.
    try:
        with get_session() as session:
            record = persist_screening_response(
                session,
                request,
                response,
                idempotency_key=final.idempotency_key,
            )
    except IntegrityError:
        # A concurrent winner is read only after the failed write transaction
        # has fully rolled back and closed, so this Session can see its commit.
        with get_session() as session:
            existing = _existing_run(session, final.idempotency_key)
            if existing is None:
                raise
            return _already_current_stats(
                context=final,
                existing=existing,
                started=started,
            )
    return {
        **final.readiness,
        "screen_run_id": record.id,
        "screen_model_version": response.model_version,
        "source_fingerprint": final.source_fingerprint,
        "requested": response.requested,
        "eligible": response.succeeded,
        "candidate_count": len(response.candidates),
        "forecast_failure_count": len(response.failed),
        "skipped": None,
        "duration_seconds": round(monotonic() - started, 2),
    }


def register_daily_screening_job() -> None:
    register(
        JobSpec(
            name="daily_screening",
            func=generate_daily_screening,
            trigger=CronTrigger(
                day_of_week="mon-sat",
                hour="0-1,7-9,19-23",
                minute="12,27,42,57",
                timezone=MARKET_TIMEZONE,
            ),
        )
    )
