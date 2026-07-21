from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency
from alphapilot.core.timeutil import iso_utc
from alphapilot.db.models import JobRun
from alphapilot.jobs.registry import JOBS, run_job

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


def _serialize(record: JobRun) -> dict[str, Any]:
    return {
        "id": record.id,
        "job_name": record.job_name,
        "started_at": iso_utc(record.started_at),
        "finished_at": iso_utc(record.finished_at),
        "status": record.status,
        "stats": record.stats,
        "error": record.error,
    }


@router.get("/runs")
def list_runs(
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    rows = session.scalars(
        select(JobRun).order_by(JobRun.started_at.desc(), JobRun.id.desc()).limit(limit)
    ).all()
    return {"runs": [_serialize(row) for row in rows]}


@router.post("/{name}/run")
def trigger_job(
    name: str,
    force: bool = Query(default=False),
) -> dict[str, Any]:
    if name not in JOBS:
        raise HTTPException(status_code=404, detail=f"未注册任务：{name}")
    if force and name != "poll_market_snapshot":
        raise HTTPException(status_code=400, detail=f"任务 {name} 不支持 force 参数")
    kwargs = {"force": True} if force else {}
    return {"run": _serialize(run_job(name, **kwargs))}
