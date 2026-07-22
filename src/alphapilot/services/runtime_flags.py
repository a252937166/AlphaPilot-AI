from __future__ import annotations

from sqlalchemy.orm import Session

from alphapilot.core.config import Settings
from alphapilot.db.models import RuntimeFlag, utcnow

TRADING_HALTED = "trading_halted"


def trading_is_halted(session: Session, settings: Settings) -> bool:
    """Read the latest committed switch outside the caller's older DB snapshot."""

    with Session(bind=session.get_bind()) as flag_session:
        row = flag_session.get(RuntimeFlag, TRADING_HALTED)
    return bool(row.value) if row is not None else settings.trading_halted


def initialize_runtime_flags(session: Session, settings: Settings) -> RuntimeFlag:
    """Seed the config default once; later process restarts preserve operator state."""

    row = session.get(RuntimeFlag, TRADING_HALTED, populate_existing=True)
    if row is None:
        row = RuntimeFlag(key=TRADING_HALTED, value=settings.trading_halted)
        session.add(row)
        session.flush()
    return row


def set_trading_halted(session: Session, halted: bool) -> RuntimeFlag:
    """Idempotently persist the operator switch in the caller's transaction."""

    row = session.get(RuntimeFlag, TRADING_HALTED, populate_existing=True)
    if row is None:
        row = RuntimeFlag(key=TRADING_HALTED, value=halted)
        session.add(row)
    else:
        row.value = halted
        row.updated_at = utcnow()
    session.flush()
    return row
