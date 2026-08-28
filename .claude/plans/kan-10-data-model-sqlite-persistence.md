# Feature: KAN-10 — Data Model & SQLite Persistence Layer (EPIC-A / TICKET-1)

The following plan should be complete, but it's important that you validate documentation and
codebase patterns and task sanity before you start implementing. Pay special attention to naming
of existing utils, types, and models. Import from the right files.

> **Jira:** [KAN-10](https://osavelyev.atlassian.net/browse/KAN-10) · **Epic:** [KAN-1](https://osavelyev.atlassian.net/browse/KAN-1) (EPIC-A)
> **PRD:** `docs/second-opinion-prd.md` (Confluence 1048577) · **Epic spec:** `docs/specs/epic-a-debate-orchestration-engine.md`
> **Story shape:** `docs/specs/STORY_TEMPLATE.md`

## Feature Description

The foundation ticket of EPIC-A (the headless Debate Orchestration Engine). It introduces the
persistence layer for the "Second Opinion" debate engine: SQLAlchemy models for **Debate,
Persona, Turn, Verdict**, a SQLite engine/session setup that auto-creates tables on startup,
and a repository module exposing the CRUD operations the orchestrator (TICKET-4), judge
(TICKET-5), and API (TICKET-6) will build on. Crucially, it **freezes the shared data contracts**
so downstream Wave-1/Wave-2 tickets can be built in parallel against a stable schema.

## User Story

As a developer,
I want Debate/Persona/Turn/Verdict persisted in SQLite,
so that a debate survives the request and can be listed, re-opened, and deleted.

## Problem Statement

Today the backend is a stateless chat starter — nothing survives a request. EPIC-A needs a debate
(3 personas × 2 rounds + a judge verdict) to be durable so it can be re-opened, listed as history,
and deleted for privacy. Downstream tickets (T4 orchestrator, T5 judge, T6 API) cannot begin until
the data contracts and storage operations they share are frozen and available.

## Solution Statement

Add a small, sync SQLAlchemy 2.0 persistence layer:
- `app/db/` — declarative `Base`, engine bound to `DATABASE_URL`, `SessionLocal` factory, `init_db()`.
- `app/models/` — one module per entity (`debate`, `persona`, `turn`, `verdict`) + shared `enums`.
- `app/repositories/debates.py` — explicit-session functions: `create_debate`, `add_persona`,
  `add_turn`, `set_verdict`, `get_debate`, `list_debates`, `delete_debate` (cascades children).
- Wire `init_db()` into FastAPI startup (dev auto-create). Add `DATABASE_URL` to config + `.env.example`.
- Unit tests using an in-memory SQLite engine proving a create → read-back → delete round-trip.

## Feature Metadata

**Feature Type**: New Capability (foundation layer)
**Estimated Complexity**: Medium
**Primary Systems Affected**: `backend/app/db`, `backend/app/models`, `backend/app/repositories`, `backend/app/core/config.py`, `backend/app/main.py`, `backend/pyproject.toml`
**Dependencies**: `SQLAlchemy>=2.0` (NEW — not currently installed)

---

## GOVERNING DECISIONS (ADR gate — MANDATORY per CLAUDE.md)

This task is constrained by these Accepted decisions from the Decision Log
(https://osavelyev.atlassian.net/wiki/spaces/~557058b71ec4cb4cec4df2b96f8e5302aff766/pages/1015810):

- **DEC-008 — SQLite for dev persistence.** Postgres is a *future upgrade path only*. Do NOT add
  Postgres, Alembic migrations, or Postgres-specific column types. Auto-create tables on startup
  is acceptable for dev. Keep the schema DB-agnostic so the Postgres path stays open.
- **DEC-005 — Single-user / local.** There is NO auth and NO tenant. Therefore **no `owner_id` /
  `user_id` / tenant column** on any model. Do not add one.

**Do not contradict these.** If implementation appears to require deviating (e.g. you find you need
migrations, or an ownership column), STOP and raise a new **Proposed DEC-xxx** for approval per the
STORY_TEMPLATE gate — do not silently deviate.

**Two implementation choices this plan makes that are NOT yet in the Decision Log** — call them out
at commit time; if the reviewer considers either architectural, log a new DEC before committing:
1. **Sync SQLAlchemy (not async).** Rationale: matches the existing sync codebase (`services/llm.py`
   uses the sync `Anthropic` client; tests use sync `TestClient`), and DEC-005 (single-user/local)
   means no concurrency pressure. The T4 orchestrator runs persona turns concurrently, but each turn
   persist is a fast local SQLite write; a sync repo called from async code is acceptable for dev.
   Async is a future upgrade path alongside the DEC-008 Postgres move.
2. **String UUID primary keys (`uuid4().hex`), generated app-side, stored as `String`.** Rationale:
   DB-agnostic (survives the DEC-008 Postgres path), non-enumerable IDs for the T6 API URLs, and no
   dependence on SQLite autoincrement semantics.

These are judgment calls consistent with DEC-005/DEC-008, not contradictions of them. Treat the
commit trailer as `Decisions: DEC-008, DEC-005`.

---

## CONTEXT REFERENCES

### Relevant Codebase Files — READ THESE BEFORE IMPLEMENTING

- `backend/app/core/config.py` (lines 1-23) — Why: exact `Settings`/`pydantic-settings` pattern and
  the `@lru_cache get_settings()` singleton you will extend with `DATABASE_URL`.
- `backend/app/main.py` (lines 1-25) — Why: FastAPI app construction + router registration; you add
  the startup hook that calls `init_db()` here.
- `backend/app/services/llm.py` (lines 25-33) — Why: the project's lazy-singleton idiom
  (`_service` global + getter). Mirror this shape for the engine/session accessor.
- `backend/app/schemas/chat.py` (lines 1-16) — Why: Pydantic `Field(..., description=...)` style and
  `list[...]` typing conventions to match if you add any schema.
- `backend/tests/test_health.py` (lines 1-18) — Why: the test style (module-level fixtures, plain
  `assert`, no classes) your `test_repository.py` should follow.
- `backend/pyproject.toml` (lines 1-34) — Why: where to add the `SQLAlchemy` dependency; note
  `pythonpath = ["."]`, ruff line-length 100, target py311, pytest config already present.
- `docs/specs/epic-a-debate-orchestration-engine.md` (lines 18-40) — Why: the **shared contracts**
  (authoritative field list) and the exact repository function list this ticket must expose.
- `docs/specs/STORY_TEMPLATE.md` — Why: the Definition of Done this ticket is graded against.

### New Files to Create

- `backend/app/db/__init__.py` — package marker.
- `backend/app/db/base.py` — `DeclarativeBase` subclass `Base` (single metadata for all models).
- `backend/app/db/session.py` — engine (from `DATABASE_URL`), `SessionLocal` factory, `get_session()`
  context manager, `init_db()` (create_all), SQLite `PRAGMA foreign_keys=ON` event listener.
- `backend/app/models/__init__.py` — import all models so `Base.metadata` is fully populated on import.
- `backend/app/models/enums.py` — `Archetype` (advocate|skeptic|pragmatist), `TurnStatus` (ok|skipped),
  `DebateStatus` (e.g. pending|running|completed|failed).
- `backend/app/models/debate.py` — `Debate` model + relationships to personas/turns/verdict.
- `backend/app/models/persona.py` — `Persona` model.
- `backend/app/models/turn.py` — `Turn` model.
- `backend/app/models/verdict.py` — `Verdict` model (JSON `cases` + `tradeoffs`).
- `backend/app/repositories/__init__.py` — package marker.
- `backend/app/repositories/debates.py` — the repository functions.
- `backend/tests/conftest.py` — pytest fixture: in-memory SQLite engine + session (StaticPool).
- `backend/tests/test_repository.py` — round-trip unit tests.

### Files to Update

- `backend/app/core/config.py` — add `database_url: str = "sqlite:///./second_opinion.db"`.
- `backend/app/main.py` — call `init_db()` on startup (lifespan or `@app.on_event("startup")`).
- `backend/pyproject.toml` — add `"SQLAlchemy>=2.0"` to `dependencies`.
- `backend/.env.example` — document `DATABASE_URL=sqlite:///./second_opinion.db`.
- `.gitignore` — ensure `*.db` / `second_opinion.db` is ignored (verify; add if missing).

### Relevant Documentation — READ BEFORE IMPLEMENTING

- SQLAlchemy 2.0 ORM Declarative Mapping — https://docs.sqlalchemy.org/en/20/orm/declarative_styles.html#using-a-declarative-base-class
  - Section: `DeclarativeBase` + `Mapped` / `mapped_column`. Why: the modern typed-model style to use.
- SQLAlchemy 2.0 Relationship cascade — https://docs.sqlalchemy.org/en/20/orm/cascades.html#delete-orphan
  - Section: `cascade="all, delete-orphan"`. Why: `delete_debate` must cascade to personas/turns/verdict.
- SQLAlchemy SQLite FK enforcement — https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#foreign-key-support
  - Section: `PRAGMA foreign_keys=ON` via a `connect` event listener. Why: SQLite ignores FKs unless enabled.
- SQLAlchemy testing with in-memory SQLite — https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#threading-pooling-behavior
  - Section: `StaticPool` + `check_same_thread=False`. Why: keep one in-memory DB across the test session.
- SQLAlchemy JSON type — https://docs.sqlalchemy.org/en/20/core/type_basics.html#sqlalchemy.types.JSON
  - Why: `Verdict.cases` / `tradeoffs` are stored as JSON (works on SQLite and Postgres).

### Patterns to Follow

**Settings (extend, don't restructure) — `backend/app/core/config.py`:**
```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_name: str = "AI Delivery Flow"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    cors_origins: str = "http://localhost:5173"
    database_url: str = "sqlite:///./second_opinion.db"   # <-- ADD (env: DATABASE_URL)
```
GOTCHA: `pydantic-settings` maps `database_url` ⇆ env var `DATABASE_URL` case-insensitively; no alias needed.

**Lazy-singleton accessor (mirror `services/llm.py:25-33`) — for the engine in `db/session.py`:**
```python
_engine = None
def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url, connect_args=_connect_args())
    return _engine
```

**Naming conventions:** snake_case files/functions, PascalCase classes, table names plural snake_case
(`debates`, `personas`, `turns`, `verdicts`). Enum values are lowercase strings matching the contract
(`"advocate"`, `"skeptic"`, `"pragmatist"`, `"ok"`, `"skipped"`).

**Test style (mirror `tests/test_health.py`):** module-level fixtures, plain functions, `assert`.

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation (dependency, config, base, enums)
Add SQLAlchemy, wire `DATABASE_URL`, create the declarative `Base`, and define the shared enums.
These are the contracts everything else imports.

### Phase 2: Core Implementation (models + session)
Define the four ORM models with correct relationships/cascades, then the engine/session/`init_db()`.

### Phase 3: Integration (repository + startup)
Implement the repository functions and wire `init_db()` into FastAPI startup.

### Phase 4: Testing & Validation
In-memory fixture + round-trip tests (create → read back → delete-cascades). Run the full gate.

---

## STEP-BY-STEP TASKS

Execute in order, top to bottom. Each task is atomic and independently testable.

### UPDATE `backend/pyproject.toml`
- **IMPLEMENT**: Add `"SQLAlchemy>=2.0"` to the `[project].dependencies` list.
- **PATTERN**: existing dependency list `backend/pyproject.toml:6-13`.
- **GOTCHA**: Keep it in the main `dependencies`, NOT `optional-dependencies.dev` (runtime dep).
- **VALIDATE**: `cd backend && python -m pip install -e ".[dev]" && python -c "import sqlalchemy, sys; print(sqlalchemy.__version__)"`

### UPDATE `backend/app/core/config.py`
- **IMPLEMENT**: Add `database_url: str = "sqlite:///./second_opinion.db"` field to `Settings`.
- **PATTERN**: mirror the existing scalar fields `config.py:11-14`.
- **GOTCHA**: Do NOT add any ownership/tenant setting (DEC-005). Relative sqlite path is fine for dev.
- **VALIDATE**: `cd backend && python -c "from app.core.config import get_settings; print(get_settings().database_url)"`

### UPDATE `backend/.env.example`
- **IMPLEMENT**: Append a documented `DATABASE_URL=sqlite:///./second_opinion.db` block.
- **PATTERN**: existing commented blocks in `.env.example`.
- **VALIDATE**: `grep DATABASE_URL backend/.env.example`

### CREATE `backend/app/db/__init__.py`
- **IMPLEMENT**: empty package marker.
- **VALIDATE**: `test -f backend/app/db/__init__.py`

### CREATE `backend/app/db/base.py`
- **IMPLEMENT**: `class Base(DeclarativeBase): pass` — the single shared metadata.
- **IMPORTS**: `from sqlalchemy.orm import DeclarativeBase`
- **GOTCHA**: All models must inherit THIS `Base` so one `create_all` builds every table.
- **VALIDATE**: `cd backend && python -c "from app.db.base import Base; print(Base.metadata)"`

### CREATE `backend/app/models/enums.py`
- **IMPLEMENT**: `str, Enum` classes: `Archetype{ADVOCATE="advocate",SKEPTIC="skeptic",PRAGMATIST="pragmatist"}`,
  `TurnStatus{OK="ok",SKIPPED="skipped"}`, `DebateStatus{PENDING="pending",RUNNING="running",COMPLETED="completed",FAILED="failed"}`.
- **IMPORTS**: `from enum import Enum`
- **GOTCHA**: Subclass `(str, Enum)` so values serialize as the plain contract strings and store as TEXT.
- **VALIDATE**: `cd backend && python -c "from app.models.enums import Archetype; print(Archetype.ADVOCATE.value)"`

### CREATE `backend/app/models/debate.py`
- **IMPLEMENT**: `Debate` — `id: str` PK (`default=lambda: uuid4().hex`), `decision: str`,
  `context: str | None`, `status: DebateStatus` (`default=DebateStatus.PENDING`, store as string),
  `created_at: datetime` (`default=datetime.utcnow`). Relationships: `personas`, `turns`, `verdict`
  (uselist=False), all with `cascade="all, delete-orphan"` and `back_populates`.
- **IMPORTS**: `from datetime import datetime`, `from uuid import uuid4`, `from sqlalchemy import String, DateTime`,
  `from sqlalchemy.orm import Mapped, mapped_column, relationship`, `from app.db.base import Base`,
  `from app.models.enums import DebateStatus`.
- **GOTCHA**: Store enum as its `.value` — use `mapped_column(String, default=DebateStatus.PENDING.value)`
  OR SQLAlchemy `Enum(DebateStatus, native_enum=False)`. Prefer `Enum(..., native_enum=False, length=...)`
  for DB-agnostic TEXT storage (DEC-008 Postgres path). NO owner column (DEC-005).
- **VALIDATE**: `cd backend && python -c "from app.models.debate import Debate; print(Debate.__tablename__)"`

### CREATE `backend/app/models/persona.py`
- **IMPLEMENT**: `Persona` — `id: str` PK (uuid4 hex), `debate_id: str` FK→`debates.id`
  (`ForeignKey("debates.id", ondelete="CASCADE")`), `archetype: Archetype`, `name: str`, `stance: str`.
  `back_populates="personas"` to Debate.
- **IMPORTS**: as above + `from sqlalchemy import ForeignKey`, `from app.models.enums import Archetype`.
- **GOTCHA**: `__tablename__ = "personas"`. FK must match the exact `debates.id` column name.
- **VALIDATE**: `cd backend && python -c "from app.models.persona import Persona; print(Persona.__tablename__)"`

### CREATE `backend/app/models/turn.py`
- **IMPLEMENT**: `Turn` — `id: str` PK, `debate_id: str` FK→`debates.id` (CASCADE),
  `persona_id: str` FK→`personas.id`, `round: int`, `content: str`,
  `status: TurnStatus` (`default=OK`), `created_at: datetime` (`default=datetime.utcnow`).
  `back_populates="turns"`.
- **IMPORTS**: as above + `TurnStatus`.
- **GOTCHA**: `round` is a Python builtin/SQL keyword — the COLUMN name `round` is fine in SQLite/PG,
  but name the attribute `round` and it's acceptable; do not shadow builtins inside functions.
- **VALIDATE**: `cd backend && python -c "from app.models.turn import Turn; print(Turn.__tablename__)"`

### CREATE `backend/app/models/verdict.py`
- **IMPLEMENT**: `Verdict` — `id: str` PK, `debate_id: str` FK→`debates.id` (CASCADE, `unique=True`),
  `recommendation: str`, `cases: list` (`mapped_column(JSON)`), `tradeoffs: list` (`mapped_column(JSON)`).
  `back_populates="verdict"`.
- **IMPORTS**: `from sqlalchemy import JSON`, plus the usual.
- **GOTCHA**: `cases` shape = `[{"option": str, "argument": str}]`; `tradeoffs` = list. Store raw JSON;
  Pydantic validation of these shapes lands in TICKET-5 (judge), not here. `unique=True` enforces
  one verdict per debate. Type the attr as `Mapped[list]` (or `Mapped[list[dict]]`).
- **VALIDATE**: `cd backend && python -c "from app.models.verdict import Verdict; print(Verdict.__tablename__)"`

### CREATE `backend/app/models/__init__.py`
- **IMPLEMENT**: import all four models (and enums) so importing `app.models` populates `Base.metadata`.
  `from app.models.debate import Debate` … re-export in `__all__`.
- **GOTCHA**: `init_db()` must import this package (or the models) BEFORE `create_all`, or tables are missing.
- **VALIDATE**: `cd backend && python -c "import app.models; from app.db.base import Base; print(sorted(Base.metadata.tables))"`
  (expect `['debates', 'personas', 'turns', 'verdicts']`)

### CREATE `backend/app/db/session.py`
- **IMPLEMENT**:
  - `_connect_args()` → `{"check_same_thread": False}` when the URL is sqlite, else `{}`.
  - `get_engine()` lazy singleton (mirror `services/llm.py`), `create_engine(get_settings().database_url, connect_args=...)`.
  - `SessionLocal = sessionmaker(bind=..., autoflush=False, expire_on_commit=False)` — build lazily off `get_engine()`.
  - `@contextmanager get_session()` yielding a session, commit on success, rollback on exception, close in finally.
  - `init_db()`: `import app.models` then `Base.metadata.create_all(get_engine())`.
  - SQLite FK pragma: `@event.listens_for(Engine, "connect")` handler issuing `PRAGMA foreign_keys=ON`
    (guard so it only runs for SQLite connections).
- **IMPORTS**: `from contextlib import contextmanager`, `from sqlalchemy import create_engine, event`,
  `from sqlalchemy.engine import Engine`, `from sqlalchemy.orm import sessionmaker, Session`,
  `from app.core.config import get_settings`, `from app.db.base import Base`.
- **GOTCHA**: `expire_on_commit=False` so returned ORM objects remain usable after the session closes
  (the round-trip test and later API reads depend on this). Enable FK pragma or `delete_debate` cascade
  relies solely on ORM-level cascade (both is safest).
- **VALIDATE**: `cd backend && python -c "from app.db.session import init_db, get_session; init_db(); print('ok')" && rm -f backend/second_opinion.db`

### CREATE `backend/app/repositories/__init__.py`
- **IMPLEMENT**: empty package marker.
- **VALIDATE**: `test -f backend/app/repositories/__init__.py`

### CREATE `backend/app/repositories/debates.py`
- **IMPLEMENT** explicit-session functions (each takes `session: Session` as first arg):
  - `create_debate(session, decision, context=None) -> Debate` — add + flush, return object.
  - `add_persona(session, debate_id, archetype, name, stance) -> Persona`.
  - `add_turn(session, debate_id, persona_id, round, content, status=TurnStatus.OK) -> Turn`.
  - `set_verdict(session, debate_id, recommendation, cases, tradeoffs) -> Verdict` — upsert: replace
    existing verdict for the debate if present (respect `unique=True`).
  - `get_debate(session, debate_id) -> Debate | None` — with personas, ordered turns (by `round`,
    then `created_at`), and verdict eager-loaded.
  - `list_debates(session) -> list[Debate]` — newest first (`order_by(Debate.created_at.desc())`);
    NO ownership filter (DEC-005).
  - `delete_debate(session, debate_id) -> bool` — load + `session.delete(debate)`; returns False if missing.
    Children removed via cascade.
- **IMPORTS**: `from sqlalchemy import select`, `from sqlalchemy.orm import Session, selectinload`,
  the models, and `TurnStatus`.
- **GOTCHA**: Use SQLAlchemy 2.0 `select(...)` + `session.scalars(...)`, not legacy `session.query`.
  For ordered turns use `selectinload(Debate.turns)` and order the relationship or re-query turns
  ordered. Commit is the caller's / context-manager's responsibility — use `flush()` inside functions
  to populate defaults (ids/timestamps) and return live objects. Keep functions free of HTTP concerns.
- **VALIDATE**: covered by `test_repository.py` below.

### CREATE `backend/tests/conftest.py`
- **IMPLEMENT**: pytest fixture `session` yielding a SQLAlchemy `Session` bound to an **in-memory**
  engine: `create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)`,
  `import app.models`, `Base.metadata.create_all(engine)`, issue `PRAGMA foreign_keys=ON`, yield a
  session from a `sessionmaker`, teardown drops all.
- **IMPORTS**: `import pytest`, `from sqlalchemy import create_engine`, `from sqlalchemy.pool import StaticPool`,
  `from sqlalchemy.orm import sessionmaker`, `from app.db.base import Base`, `import app.models`.
- **GOTCHA**: `StaticPool` + `sqlite://` (no path) keeps ONE in-memory DB across connections; without it
  each connection gets a fresh empty DB and tables vanish. Enable the FK pragma here too so cascade tests
  are meaningful.
- **VALIDATE**: `cd backend && python -m pytest tests/conftest.py --collect-only -q`

### CREATE `backend/tests/test_repository.py`
- **IMPLEMENT** tests using the `session` fixture:
  - `test_create_and_get_debate_roundtrip`: create_debate → add 3 personas → add turns (2 rounds) →
    set_verdict → `get_debate` returns matching decision, 3 personas, ordered turns, and the verdict.
  - `test_list_debates_newest_first`: create two, assert order + count, no owner filtering.
  - `test_delete_debate_cascades`: create full debate → `delete_debate` returns True →
    `get_debate` is None AND personas/turns/verdict rows are gone (query the tables directly).
  - `test_delete_missing_returns_false`.
  - `test_set_verdict_replaces_existing`: setting a verdict twice keeps exactly one (unique constraint).
- **PATTERN**: plain functions + `assert`, mirror `tests/test_health.py`.
- **VALIDATE**: `cd backend && python -m pytest tests/test_repository.py -v`

### UPDATE `backend/app/main.py`
- **IMPLEMENT**: call `init_db()` on startup. Prefer a lifespan handler:
  `@asynccontextmanager async def lifespan(app): init_db(); yield` and pass `lifespan=lifespan` to
  `FastAPI(...)`. (Or `@app.on_event("startup")` — but lifespan is the non-deprecated form.)
- **IMPORTS**: `from contextlib import asynccontextmanager`, `from app.db.session import init_db`.
- **GOTCHA**: Preserve existing CORS + router registration. `init_db()` must run before requests; it's
  idempotent (`create_all` no-ops on existing tables). Don't create the DB file during import — only in
  the startup hook (keeps test collection clean).
- **VALIDATE**: `cd backend && python -c "from app.main import app; print([r.path for r in app.routes])" && rm -f backend/second_opinion.db`

### UPDATE `.gitignore`
- **IMPLEMENT**: ensure `*.db` and/or `backend/second_opinion.db` is ignored.
- **VALIDATE**: `git check-ignore backend/second_opinion.db || echo "ADD IT"`

---

## TESTING STRATEGY

### Unit Tests
`backend/tests/test_repository.py` against an in-memory SQLite DB via the `conftest.py` `session`
fixture. Cover every repository function. Assert the round-trip preserves the shared-contract fields
and that ordering (turns by round, debates newest-first) is correct.

### Integration Tests
Light integration: `test_init_db_creates_tables` (optional) confirming `init_db()` creates all four
tables against a temp file DB, and that `app.main.app` imports with the lifespan wired. The full
HTTP-level integration lands in TICKET-6, not here.

### Edge Cases
- `get_debate` / `delete_debate` on a non-existent id (None / False).
- Cascade delete removes personas, turns, AND verdict rows (verify at table level, not just via ORM).
- `set_verdict` called twice on the same debate keeps exactly one verdict (unique constraint respected).
- A `skipped` turn persists with `status="skipped"` and is returned in the transcript.
- Empty history: `list_debates` returns `[]`.

### E2E / Browser Automation
**Not applicable.** EPIC-A is an explicitly headless backend loop (no frontend, no HTTP surface in
this ticket — the API arrives in TICKET-6). There is no UI route to drive. Skip Level 5; do not
fabricate a browser flow. Manual validation is the Python round-trip snippet in Level 4 below.

---

## VALIDATION COMMANDS

Run from `backend/` unless noted. This is the `/validate` gate for a backend-only ticket.

### Level 1: Syntax & Style
```bash
cd backend && ruff check . && ruff format --check .
```

### Level 2: Unit Tests
```bash
cd backend && python -m pytest tests/test_repository.py -v
```

### Level 3: Full Suite (no regressions)
```bash
cd backend && python -m pytest -q
```
(Existing `tests/test_health.py` must still pass.)

### Level 4: Manual Validation (round-trip smoke, replaces browser E2E)
```bash
cd backend && python - <<'PY'
from app.db.session import get_session, init_db
from app.repositories import debates as repo
from app.models.enums import Archetype, TurnStatus
init_db()
with get_session() as s:
    d = repo.create_debate(s, decision="Adopt SQLite?", context="dev tooling")
    repo.add_persona(s, d.id, Archetype.ADVOCATE, "Ada", "Yes, ship it")
    repo.add_turn(s, d.id, repo.list_debates(s)[0].personas[0].id, round=1, content="I argue yes", status=TurnStatus.OK)
    repo.set_verdict(s, d.id, recommendation="Use SQLite for dev",
                     cases=[{"option":"SQLite","argument":"simple"}], tradeoffs=["not for scale"])
with get_session() as s:
    got = repo.get_debate(s, d.id)
    print("READ BACK:", got.decision, "| personas:", len(got.personas), "| turns:", len(got.turns), "| verdict:", got.verdict.recommendation)
    assert repo.delete_debate(s, d.id) is True
with get_session() as s:
    assert repo.get_debate(s, d.id) is None
    print("DELETE CASCADE: ok")
PY
rm -f second_opinion.db
```

### Level 5: E2E / Browser Automation
**N/A** — headless backend ticket (see Testing Strategy). Do not run `agent-browser`.

### Level 6: ADR / Decision-gate check (project-specific, MANDATORY)
```bash
# Hook must be active:
git config core.hooksPath   # -> .githooks
# Commit must carry the trailer (the commit-msg hook enforces it on backend/app/** changes):
#   Decisions: DEC-008, DEC-005
```
Also update the Decision Log "Implemented by" entries for DEC-008 and DEC-005 with KAN-10 / the commit
SHA (per CLAUDE.md bidirectional-log rule) before considering the ticket done.

---

## ACCEPTANCE CRITERIA

- [ ] SQLAlchemy models for **Debate, Persona, Turn, Verdict** exactly match the shared contracts
      (fields, enums, relationships) in `epic-a-...md:18-27`.
- [ ] NO ownership/tenant column on any model (DEC-005); SQLite only, no Postgres/Alembic (DEC-008).
- [ ] Engine/session setup exists; `init_db()` auto-creates all four tables on startup; SQLite path
      comes from `DATABASE_URL` config.
- [ ] Repository exposes `create_debate`, `add_persona`, `add_turn`, `set_verdict`, `get_debate`
      (transcript + verdict), `list_debates`, `delete_debate` (cascades children).
- [ ] Unit tests prove create → read-back → delete round-trip, plus cascade + ordering + edge cases.
- [ ] `ruff check`, `ruff format --check`, and full `pytest` all pass (zero errors).
- [ ] No regression in `tests/test_health.py`.
- [ ] `.db` file git-ignored; `.env.example` documents `DATABASE_URL`.
- [ ] Commit references `Decisions: DEC-008, DEC-005`; Decision Log "Implemented by" updated.

---

## COMPLETION CHECKLIST

- [ ] All tasks completed in order; each task's VALIDATE passed immediately.
- [ ] Level 1 (ruff) clean.
- [ ] Level 2 + Level 3 (pytest) green.
- [ ] Level 4 manual round-trip prints READ BACK + DELETE CASCADE ok.
- [ ] Level 5 correctly skipped (backend-only, documented).
- [ ] Level 6 ADR gate satisfied (hook active, trailer present, Decision Log updated).
- [ ] Acceptance criteria all met.
- [ ] Code reviewed (`/code-review`) with no unaddressed high-severity findings.

---

## NOTES

**Why these shapes are frozen here:** TICKET-2 (personas), TICKET-3 (LLM client), TICKET-4
(orchestrator), TICKET-5 (judge), and TICKET-6 (API) all import against these models. Getting field
names and enums exactly right now avoids a costly re-freeze. Match the contract spelling precisely:
`archetype` values `advocate|skeptic|pragmatist`; turn `status` `ok|skipped`; verdict `cases` items are
`{option, argument}`.

**Design decisions & trade-offs (flag at commit; log a DEC if the reviewer deems architectural):**
- *Sync SQLAlchemy* over async — matches the sync codebase and DEC-005 single-user scope; async is a
  future upgrade path bundled with the DEC-008 Postgres move. If T4's concurrent turn-persist proves
  this wrong, raise a Proposed DEC then.
- *String UUID PKs* generated app-side — DB-agnostic and non-enumerable for T6 URLs.
- *`Enum(..., native_enum=False)` / TEXT storage* for enums — portable to Postgres, readable in SQLite.
- *JSON columns* for `Verdict.cases`/`tradeoffs` — validation of their internal shape is TICKET-5's job.
- *Both ORM cascade + SQLite FK pragma* — belt-and-suspenders so `delete_debate` reliably cascades.

**Scope guard:** Do NOT build Pydantic request/response schemas or HTTP routes here — that's TICKET-6.
Do NOT implement persona-assignment logic — that's TICKET-2. This ticket is models + session +
repository + tests only.

**Confidence score for one-pass implementation: 9/10.** The only real risks are (a) SQLite FK/cascade
config subtlety and (b) getting enum storage DB-agnostic — both are explicitly addressed above with
doc links.
