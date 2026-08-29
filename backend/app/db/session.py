from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.base import Base


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:
    """Enforce foreign keys on SQLite connections (off by default)."""
    # Only issue the PRAGMA against SQLite; other backends enforce FKs natively.
    if type(dbapi_connection).__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _connect_args() -> dict:
    """SQLite needs ``check_same_thread=False`` under FastAPI's threadpool."""
    if get_settings().database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Lazy singleton engine bound to ``DATABASE_URL`` (mirrors services/llm.py)."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            get_settings().database_url,
            connect_args=_connect_args(),
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Lazy singleton session factory bound to the engine."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            expire_on_commit=False,
        )
    return _SessionLocal


@contextmanager
def get_session() -> Iterator[Session]:
    """Yield a session; commit on success, rollback on error, always close."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """Request-scoped session for FastAPI routes (commit on success, rollback on error).

    Delegates to :func:`get_session` so commit/rollback/close behavior stays
    single-sourced. FastAPI runs this sync generator dependency in a threadpool.
    """
    with get_session() as session:
        yield session


def init_db() -> None:
    """Create all tables (dev auto-create — DEC-008). Idempotent."""
    import app.models  # noqa: F401 — register every table on Base.metadata

    Base.metadata.create_all(get_engine())
