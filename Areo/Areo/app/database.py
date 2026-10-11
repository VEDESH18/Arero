"""Database engine and session management.

The engine is created lazily via `init_db` so tests can point the app at a
temporary database before anything touches it.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)
_engine: Engine | None = None


def init_db(database_url: str) -> Engine:
    """Create the engine, bind the session factory and create tables."""
    global _engine
    if _engine is not None:
        _engine.dispose()

    connect_args = {}
    if database_url.startswith("sqlite"):
        # Background worker threads use their own sessions/connections.
        connect_args = {"check_same_thread": False, "timeout": 30}

    _engine = create_engine(database_url, connect_args=connect_args)

    if database_url.startswith("sqlite"):
        # WAL lets the API read job status while workers write certificate rows.
        @event.listens_for(_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    SessionLocal.configure(bind=_engine)

    from app import models  # noqa: F401  (register models on Base.metadata)
    from app.migrations import run_migrations

    Base.metadata.create_all(_engine)
    run_migrations(_engine)
    return _engine


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
