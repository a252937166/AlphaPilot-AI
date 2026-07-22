from __future__ import annotations

from typing import Self

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency
from alphapilot.services import notifications as notification_service

router = APIRouter(prefix="/v1/notifications", tags=["notifications"])


class NotificationReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ids: list[int] = Field(default_factory=list)
    all_: bool = Field(default=False, alias="all")

    @field_validator("ids")
    @classmethod
    def validate_ids(cls, values: list[int]) -> list[int]:
        if len(values) > 200:
            raise ValueError("一次最多标记 200 条通知。")
        if any(value <= 0 for value in values):
            raise ValueError("通知 id 必须为正整数。")
        return values

    @model_validator(mode="after")
    def validate_selection(self) -> Self:
        if self.all_ == bool(self.ids):
            raise ValueError("ids 与 all=true 必须且只能提供一种。")
        return self


@router.get("")
def list_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(db_session_dependency),
) -> dict[str, object]:
    rows = notification_service.list_notifications(
        session,
        unread_only=unread_only,
        limit=limit,
    )
    return {"notifications": [notification_service.notification_payload(row) for row in rows]}


@router.post("/read")
def read_notifications(
    request: NotificationReadRequest,
    session: Session = Depends(db_session_dependency),
) -> dict[str, int]:
    updated = notification_service.mark_read(
        session,
        ids=request.ids,
        all_notifications=request.all_,
    )
    return {
        "updated": updated,
        "unread_count": notification_service.unread_count(session),
    }


@router.get("/unread-count")
def get_unread_count(
    session: Session = Depends(db_session_dependency),
) -> dict[str, int]:
    return {"unread_count": notification_service.unread_count(session)}
