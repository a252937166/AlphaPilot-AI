from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency
from alphapilot.backtest.costs import CostModel
from alphapilot.backtest.diagnosis import (
    compare_backtests,
    factor_diagnosis_report,
    factor_ic_report,
    factor_ic_windows,
)
from alphapilot.backtest.engine import (
    BacktestConfig,
    create_backtest_run,
    run_backtest,
)
from alphapilot.backtest.report import generate_report
from alphapilot.backtest.weights_rebuild import train_test_split
from alphapilot.core.timeutil import iso_utc
from alphapilot.data.provenance import AUDITED_DAILY_BAR_SOURCES
from alphapilot.db.engine import get_session
from alphapilot.db.models import (
    BacktestDaily,
    BacktestRun,
    DailyBar,
    Security,
)

router = APIRouter(prefix="/v1/backtest", tags=["backtest"])
_STALE_RUN_TIMEOUT = timedelta(hours=1)


class CostModelRequest(BaseModel):
    commission_bps: float = Field(default=2.5, ge=0)
    commission_min: float = Field(default=5.0, ge=0)
    stamp_duty_bps: float = Field(default=10.0, ge=0)
    transfer_bps: float = Field(default=0.2, ge=0)
    slippage_bps: float = Field(default=5.0, ge=0)


class BacktestRunRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    signal_id: Literal[
        "composite-v1",
        "composite-v2",
        "composite-v3",
    ] = "composite-v1"
    window: Literal["full", "train", "test"] | None = None
    start_date: date | None = None
    end_date: date | None = None
    rebalance_freq: Literal["5d", "10d", "20d"] = "5d"
    top_pct: float = Field(default=0.1, gt=0, le=1)
    initial_capital: float = Field(default=1_000_000.0, gt=0)
    cost_model: CostModelRequest = Field(default_factory=CostModelRequest)


def _serialize_run(run: BacktestRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "name": run.name,
        "signal_id": run.signal_id,
        "start_date": run.start_date.isoformat(),
        "end_date": run.end_date.isoformat(),
        "rebalance_freq": run.rebalance_freq,
        "top_pct": run.top_pct,
        "params": run.params,
        "status": run.status,
        "error": run.error,
        "summary": run.summary,
        "created_at": iso_utc(run.created_at),
        "report_available": run.status == "completed",
    }


def _expire_stale_runs(session: Session) -> int:
    """Fail orphaned in-process jobs so one restart cannot block future runs."""

    cutoff = datetime.now(UTC) - _STALE_RUN_TIMEOUT
    stale = list(
        session.scalars(
            select(BacktestRun).where(
                BacktestRun.status == "running",
                BacktestRun.created_at < cutoff,
            )
        )
    )
    for run in stale:
        run.status = "failed"
        run.error = "异步回测超过 1 小时未结束，已判定任务失联；API 进程可能在运行期间重启。"
        run.summary = {
            "failure_stage": "background_lease",
            "error": run.error,
        }
    if stale:
        session.flush()
    return len(stale)


def _available_range(session: Session) -> tuple[date, date]:
    first, last = session.execute(
        select(
            func.min(DailyBar.trade_date),
            func.max(DailyBar.trade_date),
        )
        .join(Security, Security.symbol == DailyBar.symbol)
        .where(
            Security.market == "CN",
            DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
        )
    ).one()
    if not isinstance(first, date) or not isinstance(last, date):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="没有可用于严格回测的审计 A 股日线。",
        )
    return first, last


def _request_config(
    session: Session,
    body: BacktestRunRequest,
    available: tuple[date, date],
) -> BacktestConfig:
    available_start, available_end = available
    if body.window is not None and (body.start_date is not None or body.end_date is not None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="window 与显式 start_date/end_date 不能同时使用。",
        )
    if body.window is None:
        start_date = body.start_date or available_start
        end_date = body.end_date or available_end
    else:
        try:
            train_start, train_end, test_start, test_end = train_test_split(session)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        windows = {
            "full": (train_start, test_end),
            "train": (train_start, train_end),
            "test": (test_start, test_end),
        }
        start_date, end_date = windows[body.window]
    if start_date < available_start or end_date > available_end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "请求区间超出审计日线覆盖："
                f"{available_start.isoformat()} 至 {available_end.isoformat()}。"
            ),
        )
    if end_date < start_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="结束日期不能早于开始日期。",
        )
    try:
        cost = CostModel(**body.cost_model.model_dump())
        return BacktestConfig(
            name=body.name or f"{body.signal_id} 回测",
            signal_id=body.signal_id,
            start_date=start_date,
            end_date=end_date,
            rebalance_freq=body.rebalance_freq,
            top_pct=body.top_pct,
            initial_capital=body.initial_capital,
            cost_model=cost,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


def _run_queued_backtest(run_id: int, cfg: BacktestConfig) -> None:
    try:
        with get_session() as session:
            run_backtest(session, cfg, run_id=run_id)
    except Exception as exc:
        with get_session() as session:
            run = session.get(BacktestRun, run_id)
            if run is not None and run.status == "running":
                run.status = "failed"
                run.error = f"{type(exc).__name__}: {exc}"[:2000]
                run.summary = {
                    "failure_stage": "background_dispatch",
                    "error": run.error,
                }


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
def start_backtest(
    body: BacktestRunRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    _expire_stale_runs(session)
    running = session.scalar(
        select(func.count()).select_from(BacktestRun).where(BacktestRun.status == "running")
    )
    if running:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="已有回测正在运行；请等待完成后再启动新的全市场回测。",
        )
    cfg = _request_config(session, body, _available_range(session))
    try:
        run_id = create_backtest_run(session, cfg)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    background_tasks.add_task(_run_queued_backtest, run_id, cfg)
    queued = session.get(BacktestRun, run_id)
    if queued is None:
        raise HTTPException(status_code=500, detail="回测审计记录创建失败。")
    return {
        "run": _serialize_run(queued),
        "message": "回测已进入后台队列；页面将自动轮询状态。",
    }


@router.get("")
def list_backtests(
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    _expire_stale_runs(session)
    rows = list(
        session.scalars(
            select(BacktestRun)
            .order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc())
            .limit(limit)
        )
    )
    return {"runs": [_serialize_run(row) for row in rows]}


@router.get("/factors/ic")
def get_factor_ic(
    sample_tag: Literal["train", "test", "full"] = Query(default="train"),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    try:
        return factor_ic_report(
            session,
            sample_tag,
            start_date=start_date,
            end_date=end_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/factors/windows")
def get_factor_ic_windows(
    sample_tag: Literal["train", "test", "full"] = Query(default="train"),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    return factor_ic_windows(session, sample_tag)


@router.get("/factors/diagnosis")
def get_factor_diagnosis(
    sample_tag: Literal["train", "test", "full"] = Query(default="train"),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    try:
        return factor_diagnosis_report(
            session,
            sample_tag,
            start_date=start_date,
            end_date=end_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/compare")
def get_backtest_comparison(
    v1_id: int = Query(alias="v1", ge=1),
    v2_id: int = Query(alias="v2", ge=1),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    try:
        return compare_backtests(session, v1_id, v2_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _run_or_404(session: Session, run_id: int) -> BacktestRun:
    _expire_stale_runs(session)
    run = session.get(BacktestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"回测不存在：{run_id}")
    return run


@router.get("/{run_id}")
def get_backtest(
    run_id: int,
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    return {"run": _serialize_run(_run_or_404(session, run_id))}


@router.get("/{run_id}/report")
def get_backtest_report(
    run_id: int,
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    run = _run_or_404(session, run_id)
    if run.status == "running":
        raise HTTPException(status_code=409, detail="回测仍在运行，报告尚未生成。")
    if run.status == "failed":
        raise HTTPException(
            status_code=409,
            detail=f"回测失败，无法生成报告：{run.error or '未提供原因'}",
        )
    try:
        return generate_report(session, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{run_id}/daily")
def get_backtest_daily(
    run_id: int,
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    run = _run_or_404(session, run_id)
    rows = list(
        session.scalars(
            select(BacktestDaily)
            .where(BacktestDaily.run_id == run_id)
            .order_by(BacktestDaily.trade_date)
        )
    )
    return {
        "run_id": run.id,
        "status": run.status,
        "dates": [row.trade_date.isoformat() for row in rows],
        "nav": [row.nav for row in rows],
        "benchmark_nav": [row.benchmark_nav for row in rows],
        "market_nav": [row.market_nav for row in rows],
        "rank_ic": [row.rank_ic for row in rows],
        "long_ret": [row.long_ret for row in rows],
        "ls_ret": [row.ls_ret for row in rows],
        "turnover": [row.turnover for row in rows],
        "n_eligible": [row.n_eligible for row in rows],
        "group_returns": [row.group_returns for row in rows],
    }
