from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from alphapilot.core.config import Settings, get_settings

_lock = Lock()
_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _build_engine(settings: Settings) -> Engine:
    url = settings.database_url
    kwargs: dict[str, object] = {"echo": settings.database_echo, "future": True}
    if url.startswith("sqlite"):
        # A file-backed SQLite database must have its parent directory in place,
        # and FastAPI serves requests from multiple threads.
        db_path = url.removeprefix("sqlite:///")
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection: object, _record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            # WAL lets readers coexist with a writer, but SQLite still serializes
            # concurrent writers. Give scheduler/API transactions time to finish
            # beyond the driver's short default before returning SQLITE_BUSY.
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=FULL")
            cursor.close()

    return engine


def get_engine(settings: Settings | None = None) -> Engine:
    global _engine, _session_factory
    with _lock:
        if _engine is None:
            _engine = _build_engine(settings or get_settings())
            _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
        return _engine


def reset_engine_cache() -> None:
    """Dispose the cached engine; used by tests that swap databases."""
    global _engine, _session_factory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _session_factory = None


def init_db(settings: Settings | None = None) -> None:
    from alphapilot.db.migrate import run_migrations
    from alphapilot.db.models import Base

    engine = get_engine(settings)
    Base.metadata.create_all(engine)
    run_migrations(engine)


@contextmanager
def get_session() -> Iterator[Session]:
    get_engine()
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
