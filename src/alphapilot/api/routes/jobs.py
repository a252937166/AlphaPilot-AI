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
    recent = session.scalars(
        select(JobRun).order_by(JobRun.started_at.desc(), JobRun.id.desc()).limit(limit)
    ).all()
    latest_by_job = [
        row
        for name in sorted(JOBS)
        if (
            row := session.scalars(
                select(JobRun)
                .where(JobRun.job_name == name)
                .order_by(JobRun.started_at.desc(), JobRun.id.desc())
                .limit(1)
            ).first()
        )
        is not None
    ]
    latest_by_job.sort(key=lambda row: (row.started_at, row.id), reverse=True)
    selected = latest_by_job[:limit]
    selected_ids = {row.id for row in selected}
    for row in recent:
        if len(selected) >= limit:
            break
        if row.id not in selected_ids:
            selected.append(row)
            selected_ids.add(row.id)
    selected.sort(key=lambda row: (row.started_at, row.id), reverse=True)
    return {"runs": [_serialize(row) for row in selected]}


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
