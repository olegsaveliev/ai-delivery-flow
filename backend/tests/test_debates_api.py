import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — register every table on Base.metadata
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.services.llm import TurnResult, get_llm_service

# A judge-valid verdict payload (mirrors tests/test_judge.py).
VALID_JSON = json.dumps(
    {
        "recommendation": "Adopt SQLite for the MVP",
        "cases": [
            {"option": "SQLite", "argument": "Zero-config and fast enough for a single user."},
            {"option": "Postgres", "argument": "Scales further but adds operational cost now."},
        ],
        "tradeoffs": ["Simplicity vs future scale", "No concurrency vs zero setup"],
    }
)


@event.listens_for(Engine, "connect")
def _fk_pragma(dbapi_connection, connection_record) -> None:
    """Enforce SQLite foreign keys so cascade-delete on DELETE is meaningful."""
    if type(dbapi_connection).__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class StubLLM:
    """Combined stub: ``run_turn`` (orchestrator) + ``complete`` (judge). No network."""

    def run_turn(
        self,
        *,
        tier,
        messages,
        guardrails,
        round_number,
        persona_index,
        max_tokens=1024,
        system=None,
    ) -> TurnResult:
        return TurnResult(
            status="ok",
            model="stub",
            text=f"r{round_number}p{persona_index}",
            tokens_in=1,
            tokens_out=1,
        )

    def complete(self, *, tier, messages, system=None, max_tokens=2048) -> str:
        return VALID_JSON


class BadJudgeStubLLM(StubLLM):
    """Debate turns succeed, but the judge never returns valid JSON."""

    def complete(self, *, tier, messages, system=None, max_tokens=2048) -> str:
        return "not json at all"


@pytest.fixture
def client():
    """TestClient wired to an in-memory DB and a stubbed LLM.

    Instantiated WITHOUT the ``with`` block so app lifespan (``init_db``) never runs
    against the real SQLite file (mirrors tests/test_health.py).
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))

    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_llm_service] = lambda: StubLLM()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_post_runs_debate_and_persists(client):
    resp = client.post("/api/debates", json={"decision": "Adopt SQLite?", "context": "dev tooling"})

    assert resp.status_code == 201
    body = resp.json()
    assert body["id"]
    assert body["status"] == "completed"
    assert len(body["personas"]) == 3
    assert len(body["turns"]) == 6  # 2 rounds x 3 personas (DEC-003)
    assert body["verdict"]["recommendation"] == "Adopt SQLite for the MVP"
    assert len(body["verdict"]["cases"]) == 2
    assert body["verdict"]["tradeoffs"]


def test_get_returns_transcript_and_verdict(client):
    created = client.post("/api/debates", json={"decision": "Adopt SQLite?"}).json()

    resp = client.get(f"/api/debates/{created['id']}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == created["id"]
    assert len(body["turns"]) == 6
    assert body["verdict"]["recommendation"] == "Adopt SQLite for the MVP"


def test_get_missing_is_404(client):
    resp = client.get("/api/debates/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "debate not found"


def test_list_returns_history_summaries(client):
    client.post("/api/debates", json={"decision": "First?"})
    client.post("/api/debates", json={"decision": "Second?"})

    resp = client.get("/api/debates")

    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 2
    # Summaries carry no transcript/verdict.
    assert "turns" not in items[0]
    assert "verdict" not in items[0]
    assert {"id", "decision", "status", "created_at"} <= set(items[0])


def test_delete_removes_debate(client):
    created = client.post("/api/debates", json={"decision": "Adopt SQLite?"}).json()

    deleted = client.delete(f"/api/debates/{created['id']}")
    assert deleted.status_code == 204

    assert client.get(f"/api/debates/{created['id']}").status_code == 404


def test_delete_missing_is_404(client):
    resp = client.delete("/api/debates/does-not-exist")
    assert resp.status_code == 404


def test_empty_decision_is_422(client):
    resp = client.post("/api/debates", json={"decision": ""})
    assert resp.status_code == 422


def test_judge_failure_returns_null_verdict(client):
    # Debate still completes; verdict is null after the judge's one repair retry fails.
    app.dependency_overrides[get_llm_service] = lambda: BadJudgeStubLLM()

    resp = client.post("/api/debates", json={"decision": "Adopt SQLite?"})

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "completed"
    assert len(body["turns"]) == 6
    assert body["verdict"] is None
