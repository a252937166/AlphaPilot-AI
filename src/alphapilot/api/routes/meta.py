from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency
from alphapilot.db.models import Security

router = APIRouter(prefix="/v1/meta", tags=["metadata"])


@router.get("/industries")
def industries(session: Session = Depends(db_session_dependency)) -> dict[str, Any]:
    normalized = func.trim(Security.industry_csrc)
    values = session.scalars(
        select(normalized)
        .where(
            Security.list_status == "listed",
            Security.industry_csrc.is_not(None),
            normalized != "",
        )
        .distinct()
        .order_by(normalized)
    ).all()
    industries = [str(value) for value in values]
    return {"count": len(industries), "industries": industries}
