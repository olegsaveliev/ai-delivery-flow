from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — register every table on Base.metadata
from app.db.base import Base


@event.listens_for(Engine, "connect")
def _fk_pragma(dbapi_connection, connection_record) -> None:
    """Enforce SQLite foreign keys so cascade-delete tests are meaningful."""
    if type(dbapi_connection).__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def session() -> Iterator[Session]:
    """A Session bound to a shared in-memory SQLite DB.

    ``StaticPool`` + the pathless ``sqlite://`` URL keep ONE in-memory database
    across connections; otherwise each connection gets a fresh empty DB.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    # Belt-and-suspenders: assert FKs are on for this connection.
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))

    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
