# Feature: KAN-9 / TICKET-6 — Debate API endpoints

The following plan should be complete, but its important that you validate documentation and codebase patterns and task sanity before you start implementing.

Pay special attention to naming of existing utils types and models. Import from the right files etc.

## Feature Description

Expose the fully-built headless debate engine (orchestrator + judge) over HTTP so the whole loop
is usable from a client. A caller POSTs a decision; the backend runs the debate to completion,
synthesizes a verdict, persists everything, and returns the completed debate. The caller can then
fetch a single debate (transcript + verdict), list their history, and delete a debate for privacy.

This is the **last backend piece of EPIC-A** — all upstream dependencies (repository KAN-10,
orchestrator KAN-7, judge KAN-8) are merged.

## User Story

As a decision-maker
I want to POST a decision and later fetch, list, and delete debates
So that the whole loop is usable over HTTP and my history is mine to manage.

## Problem Statement

The debate engine is headless: `run_debate` (orchestrator) and `judge` are pure service functions
with no HTTP surface. There is no way to trigger a debate, read a persisted transcript/verdict, or
manage history without writing Python. The API layer that composes `run_debate → judge →
persist_verdict` and maps the repository CRUD onto REST does not exist yet.

## Solution Statement

Add a `debates` FastAPI router (`/api/debates`) with four endpoints (POST create-and-run, GET one,
GET list, DELETE one), a `schemas/debate.py` request/response contract mirroring the ORM aggregate,
and a request-scoped DB session dependency. Register the router in `main.py`. Cover it with an
integration test that stubs the LLM (no network) and asserts a scripted decision produces a
persisted transcript + verdict, and that delete removes it.

## Feature Metadata

**Feature Type**: New Capability
**Estimated Complexity**: Medium
**Primary Systems Affected**: `app/api/routes` (new router), `app/schemas` (new contract), `app/db/session.py` (new FastAPI dependency), `app/main.py` (router registration)
**Dependencies**: None new — FastAPI, Pydantic v2, SQLAlchemy 2.0 already in `pyproject.toml`

**Governing decisions (MUST honor):**
- **DEC-005** — no auth, single-user MVP: no ownership column, no auth middleware, no user scoping.
- **DEC-008** — SQLite persistence (already wired via the repository).
- **DEC-004** — **watch-only**: create/read/list/delete only. Do **NOT** add any mid-debate input
  endpoint (no "inject message", no "advance round"). A debate runs to completion inside POST.
- Transitively realized (do not re-implement): DEC-003 (fixed 2 rounds — orchestrator), DEC-007
  (judge on Opus tier — judge service).

---

## CONTEXT REFERENCES

### Relevant Codebase Files — IMPORTANT: YOU MUST READ THESE FILES BEFORE IMPLEMENTING!

- `backend/app/services/orchestrator.py` (lines 86-192) — Why: `run_debate(session, debate, *, llm=None, ...)` is an **async coroutine** returning the reloaded `Debate`. The POST handler awaits this. Note it commits internally and keeps all DB writes on its own thread.
- `backend/app/services/judge.py` (lines 67-117) — Why: `judge(debate, *, llm=None) -> Verdict` (pure, raises `JudgeSchemaError`/`JudgeError` on failure) and `persist_verdict(session, debate, verdict) -> VerdictRow`. The POST handler composes `run_debate → judge → persist_verdict`.
- `backend/app/repositories/debates.py` (entire, 1-139) — Why: `create_debate`, `get_debate` (returns `None` on missing → 404), `list_debates` (newest first), `delete_debate` (returns `bool` → 404 on False). Commit is the caller's responsibility.
- `backend/app/api/routes/chat.py` (entire, 1-14) — Why: the router + `Depends(get_llm_service)` pattern to mirror exactly (prefix, tags, `response_model`).
- `backend/app/api/routes/health.py` (entire) — Why: minimal `APIRouter` shape.
- `backend/app/main.py` (entire, 1-37) — Why: router registration pattern (`app.include_router(...)`); add `debates` here. Line 6 imports `from app.api.routes import chat, health`.
- `backend/app/db/session.py` (entire, 1-75) — Why: `get_session()` contextmanager (commit/rollback/close) + lazy `get_session_factory()`. You will add a FastAPI generator dependency `get_db` here.
- `backend/app/schemas/verdict.py` (entire) — Why: `Verdict` + `Case` pydantic models to reuse for the verdict portion of the response.
- `backend/app/schemas/chat.py` (entire) — Why: schema style (`BaseModel`, `Field(..., min_length=...)`).
- `backend/app/models/debate.py`, `models/turn.py`, `models/persona.py`, `models/verdict.py`, `models/enums.py` — Why: exact ORM field names/types the response schema must mirror. `DebateStatus`/`TurnStatus`/`Archetype` are `str` enums (serialize to their value). **Read `models/turn.py` and `models/persona.py` to confirm field names before writing `TurnOut`/`PersonaOut`.**
- `backend/tests/test_orchestrator.py` (lines 11-53) — Why: `StubLLM` implementing `run_turn` — mirror for the integration test's combined stub.
- `backend/tests/test_judge.py` (lines 11-33, `VALID_JSON` + `StubJudgeLLM.complete`) — Why: the `complete` stub half and a ready-made valid verdict JSON payload.
- `backend/tests/test_health.py` (entire) — Why: `TestClient(app)` **without** the `with` context manager, so app lifespan (`init_db()`) does NOT run and no `./second_opinion.db` file is created during tests.
- `backend/tests/conftest.py` (entire) — Why: the in-memory `StaticPool` SQLite + `create_all` + FK-pragma pattern to reuse for the test's `get_db` override.

### New Files to Create

- `backend/app/schemas/debate.py` — request (`DebateCreateRequest`) + response (`DebateOut`, `DebateSummary`, `PersonaOut`, `TurnOut`, and verdict reuse) contracts, ORM-serializable.
- `backend/app/api/routes/debates.py` — the `/api/debates` router: POST create-and-run, GET one, GET list, DELETE one.
- `backend/tests/test_debates_api.py` — integration test with `TestClient` + dependency overrides (in-memory DB + combined LLM stub).

### Files to Update

- `backend/app/db/session.py` — ADD `get_db()` FastAPI generator dependency.
- `backend/app/main.py` — import and register the `debates` router.
- `docs/architecture.md` — (at **commit** time, not execution) flip `app/api/routes/debates.py` from ⏳ planned to ✅ (KAN-9), note `schemas/debate.py`, update the "routers: health, chat" note and Change Log. Confluence mirror `917506` in lockstep. **Do not edit during implementation** — the `/commit` skill owns this.

### Relevant Documentation — YOU SHOULD READ THESE BEFORE IMPLEMENTING!

- [FastAPI — SQL (Relational) Databases / dependency `get_db`](https://fastapi.tiangolo.com/tutorial/sql-databases/#create-a-dependency) — Section: yield-based DB session dependency. Why: canonical `get_db` generator shape (`try/yield/finally close`).
- [FastAPI — Dependencies with yield](https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/) — Why: correct teardown ordering and exception behavior for the session dependency.
- [FastAPI — Testing / dependency overrides](https://fastapi.tiangolo.com/advanced/testing-dependencies/#use-the-appdependency_overrides-attribute) — Why: `app.dependency_overrides[get_db]` / `[get_llm_service]` for the integration test.
- [FastAPI — Response Model](https://fastapi.tiangolo.com/tutorial/response-model/) and [Response Status Code](https://fastapi.tiangolo.com/tutorial/response-status-code/) — Why: `response_model=`, `status_code=201` for POST, `204` for DELETE.
- [Pydantic v2 — Model config `from_attributes`](https://docs.pydantic.dev/latest/concepts/models/#arbitrary-class-instances) — Why: serialize ORM rows to response models (`ConfigDict(from_attributes=True)`).

### Patterns to Follow

**Router pattern** (from `app/api/routes/chat.py`):
```python
from fastapi import APIRouter, Depends
router = APIRouter(prefix="/chat", tags=["chat"])

@router.post("", response_model=ChatResponse)
def chat(request: ChatRequest, llm: LLMService = Depends(get_llm_service)) -> ChatResponse:
    return llm.chat(request.messages)
```
→ Use `prefix="/api/debates"`, `tags=["debates"]`. POST path is `""` (the prefix is the full path).

**Session context-manager pattern** (from `app/db/session.py:56-67`) — mirror its commit/rollback/close for the new `get_db` generator dependency.

**Repository call pattern** — repos `flush` but never commit; the caller commits. For read/list/delete endpoints, `get_db`'s teardown commit is enough. For POST, `run_debate`/`persist_verdict` are followed by an explicit reload via `repo.get_debate`.

**Naming Conventions:** snake_case functions/modules, PascalCase pydantic models, `*Out` suffix for response models (new convention — justify in NOTES), `*Request` suffix for request bodies (matches `ChatRequest`).

**Error handling:** raise `fastapi.HTTPException(status_code=404, detail=...)` for missing ids (mirror how repos signal absence: `get_debate` → `None`, `delete_debate` → `False`).

**Logging:** module-level `logger = logging.getLogger(__name__)` (matches every service); log a warning on judge failure inside POST.

**Enum serialization:** `DebateStatus`/`TurnStatus`/`Archetype` subclass `str, Enum` — pydantic serializes them to their `.value` automatically; the response schema can type them as the enum or as `str`.

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation
Build the request/response contract and the DB dependency the routes need.

**Tasks:**
- CREATE `schemas/debate.py` with ORM-serializable response models + the create request.
- ADD `get_db()` FastAPI dependency to `db/session.py`.

### Phase 2: Core Implementation
Implement the four endpoints, composing the existing services.

**Tasks:**
- CREATE `api/routes/debates.py` with POST (create + `await run_debate` + `judge` + `persist_verdict` + reload), GET one (404), GET list, DELETE (404 → 204).

### Phase 3: Integration
Wire the router into the app.

**Tasks:**
- UPDATE `main.py` to import and `include_router(debates.router)`.

### Phase 4: Testing & Validation
Prove the loop end-to-end with no network.

**Tasks:**
- CREATE `tests/test_debates_api.py`: TestClient + overrides for `get_db` (in-memory StaticPool) and `get_llm_service` (combined `run_turn`+`complete` stub); assert create→persist→verdict, get, list, delete→404.

---

## STEP-BY-STEP TASKS

IMPORTANT: Execute every task in order, top to bottom. Each task is atomic and independently testable.

### CREATE backend/app/schemas/debate.py

- **IMPLEMENT**:
  - `DebateCreateRequest(BaseModel)`: `decision: str = Field(..., min_length=1)`, `context: str | None = Field(default=None)`.
  - `PersonaOut(BaseModel)`: `id: str`, `archetype: Archetype`, `name: str`, `stance: str`. `model_config = ConfigDict(from_attributes=True)`. (Import `Archetype` from `app.models.enums`. **Confirm field names against `models/persona.py`** — do not assume a `color` column exists on the ORM.)
  - `TurnOut(BaseModel)`: `id: str`, `round: int`, `persona_id: str`, `content: str`, `status: TurnStatus`, `created_at: datetime`. `from_attributes=True`. (**Confirm against `models/turn.py`.**)
  - `VerdictOut(BaseModel)`: `recommendation: str`, `cases: list[Case]`, `tradeoffs: list[str]`. `from_attributes=True`. Import `Case` from `app.schemas.verdict` (reuse; the ORM stores `cases` as `list[dict]{option,argument}` — pydantic validates each into `Case`).
  - `DebateOut(BaseModel)`: `id: str`, `decision: str`, `context: str | None`, `status: DebateStatus`, `created_at: datetime`, `personas: list[PersonaOut]`, `turns: list[TurnOut]`, `verdict: VerdictOut | None`. `from_attributes=True`.
  - `DebateSummary(BaseModel)`: `id: str`, `decision: str`, `status: DebateStatus`, `created_at: datetime`. `from_attributes=True` (lightweight list-view row — no children).
- **PATTERN**: `app/schemas/chat.py` (Field usage), `app/schemas/verdict.py` (Case/Verdict).
- **IMPORTS**: `from datetime import datetime`; `from pydantic import BaseModel, ConfigDict, Field`; `from app.models.enums import Archetype, DebateStatus, TurnStatus`; `from app.schemas.verdict import Case`.
- **GOTCHA**: Pydantic v2 uses `model_config = ConfigDict(from_attributes=True)` (NOT the v1 `class Config: orm_mode`). Reusing `verdict.Verdict` directly would also work, but define `VerdictOut` for a stable API contract independent of the internal judge schema. Type enums as the enum classes — they serialize to their string value automatically.
- **VALIDATE**: `cd backend && python3 -c "from app.schemas.debate import DebateOut, DebateCreateRequest, DebateSummary, PersonaOut, TurnOut, VerdictOut; print('ok')"`

### ADD get_db to backend/app/db/session.py

- **IMPLEMENT**: a FastAPI generator dependency:
  ```python
  def get_db() -> Iterator[Session]:
      """Request-scoped session for FastAPI routes (commit on success, rollback on error)."""
      with get_session() as session:
          yield session
  ```
  Reuse the existing `get_session()` contextmanager (it already commits on success / rolls back on error / closes). `Iterator` is already imported at the top of the file.
- **PATTERN**: `get_session()` at `app/db/session.py:56-67`.
- **IMPORTS**: none new (`Iterator` from `collections.abc` already imported; `Session` already imported).
- **GOTCHA**: Do NOT re-implement commit/close logic — delegate to `get_session()` so behavior stays single-sourced. FastAPI runs this sync generator dependency in a threadpool; the yielded session is used by the async POST handler on the event loop thread. SQLite is opened with `check_same_thread=False` (see `_connect_args`), so cross-thread use is allowed for the single-user MVP.
- **VALIDATE**: `cd backend && python3 -c "from app.db.session import get_db; print('ok')"`

### CREATE backend/app/api/routes/debates.py

- **IMPLEMENT**: `router = APIRouter(prefix="/api/debates", tags=["debates"])` and four handlers:
  1. `POST ""` → `create_debate(status_code=201, response_model=DebateOut)`, **async**:
     ```python
     @router.post("", response_model=DebateOut, status_code=201)
     async def create_debate(
         request: DebateCreateRequest,
         session: Session = Depends(get_db),
         llm: LLMService = Depends(get_llm_service),
     ) -> DebateOut:
         debate = repo.create_debate(session, decision=request.decision, context=request.context)
         session.commit()
         completed = await run_debate(session, debate, llm=llm)
         try:
             verdict = judge(completed, llm=llm)
             persist_verdict(session, completed, verdict)
             session.commit()
         except JudgeError:
             logger.warning("judge failed for debate=%s; returning transcript with null verdict", completed.id)
         reloaded = repo.get_debate(session, completed.id)
         return DebateOut.model_validate(reloaded)
     ```
  2. `GET "/{debate_id}"` → `response_model=DebateOut`; `repo.get_debate(...)`; `if debate is None: raise HTTPException(404, "debate not found")`; else return.
  3. `GET ""` → `response_model=list[DebateSummary]`; `return repo.list_debates(session)`.
  4. `DELETE "/{debate_id}"` → `status_code=204`; `if not repo.delete_debate(session, debate_id): raise HTTPException(404, "debate not found")`; return `None`.
- **PATTERN**: `app/api/routes/chat.py` (router + `Depends`); `app/services/orchestrator.py:86` (run_debate await); `app/services/judge.py:67,109` (judge/persist_verdict).
- **IMPORTS**:
  ```python
  import logging
  from fastapi import APIRouter, Depends, HTTPException
  from sqlalchemy.orm import Session
  from app.db.session import get_db
  from app.repositories import debates as repo
  from app.schemas.debate import DebateCreateRequest, DebateOut, DebateSummary
  from app.services.judge import JudgeError, judge, persist_verdict
  from app.services.llm import LLMService, get_llm_service
  from app.services.orchestrator import run_debate
  logger = logging.getLogger(__name__)
  ```
- **GOTCHA**:
  - The POST handler MUST be `async def` because `run_debate` is a coroutine you `await`. GET/DELETE can be plain `def`.
  - `judge()` takes the `llm` service (which must expose BOTH `run_turn` — used by run_debate — and `complete` — used by judge). The real `LLMService` has both; the test stub must too.
  - On `JudgeError`, keep the completed transcript and return with `verdict=None` (status stays `COMPLETED`). Do NOT let a judge failure 500 the whole request or lose the transcript. (See NOTES for the rationale/trade-off.)
  - DELETE returns `204` → the function returns `None` and must have NO `response_model`.
  - DEC-004: expose ONLY these four routes — no endpoint that mutates a debate mid-run.
- **VALIDATE**: `cd backend && python3 -c "from app.api.routes.debates import router; print([r.path for r in router.routes])"`

### UPDATE backend/app/main.py

- **IMPLEMENT**: add `debates` to the routes import and register it: `app.include_router(debates.router)`.
- **PATTERN**: existing `app.include_router(health.router)` / `chat.router` at `main.py:30-31`.
- **IMPORTS**: change line 6 to `from app.api.routes import chat, debates, health`.
- **GOTCHA**: The debates router already carries `prefix="/api/debates"` — do NOT pass a prefix again in `include_router`.
- **VALIDATE**: `cd backend && python3 -c "from app.main import app; print(sorted({r.path for r in app.routes}))"` — expect `/api/debates` and `/api/debates/{debate_id}` present.

### CREATE backend/tests/test_debates_api.py

- **IMPLEMENT**:
  - A combined stub LLM exposing both methods:
    ```python
    class StubLLM:
        def run_turn(self, *, tier, messages, guardrails, round_number, persona_index, max_tokens=1024, system=None):
            return TurnResult(status="ok", model="stub", text=f"r{round_number}p{persona_index}", tokens_in=1, tokens_out=1)
        def complete(self, *, tier, messages, system=None, max_tokens=2048):
            return VALID_JSON  # the judge-valid JSON payload
    ```
    (Reuse `VALID_JSON` shape from `tests/test_judge.py:11-20`.)
  - A `client` fixture that: builds an in-memory `StaticPool` engine, `Base.metadata.create_all(engine)`, defines `override_get_db` yielding a session (commit on exit / close in finally), sets `app.dependency_overrides[get_db] = override_get_db` and `app.dependency_overrides[get_llm_service] = lambda: StubLLM()`, yields `TestClient(app)`, then `app.dependency_overrides.clear()`.
  - Tests:
    - `test_post_runs_debate_and_persists`: POST `{"decision": "Adopt SQLite?", "context": "dev tooling"}` → 201; body has `id`, `status == "completed"`, 3 personas, 6 turns, and `verdict.recommendation` non-empty.
    - `test_get_returns_transcript_and_verdict`: POST then GET `/api/debates/{id}` → 200, same verdict + turns.
    - `test_get_missing_is_404`: GET a random id → 404.
    - `test_list_returns_history`: POST twice, GET `/api/debates` → 200, list length ≥ 2, items are summaries (no `turns` key).
    - `test_delete_removes_debate`: POST, DELETE `/api/debates/{id}` → 204, then GET → 404.
    - `test_empty_decision_is_422`: POST `{"decision": ""}` → 422 (min_length validation).
  - Instantiate `client = TestClient(app)` inside the fixture WITHOUT the `with` block so app lifespan/`init_db()` does not touch the real DB file (mirror `test_health.py`).
- **PATTERN**: `tests/conftest.py:22-47` (StaticPool + create_all + FK pragma), `tests/test_orchestrator.py:11-47` (run_turn stub), `tests/test_judge.py:11-33` (complete stub + VALID_JSON), `tests/test_health.py` (bare TestClient).
- **IMPORTS**: `import json`, `import pytest`, `from fastapi.testclient import TestClient`, `from sqlalchemy import create_engine`, `from sqlalchemy.orm import sessionmaker`, `from sqlalchemy.pool import StaticPool`, `import app.models  # noqa: F401`, `from app.db.base import Base`, `from app.db.session import get_db`, `from app.main import app`, `from app.services.llm import TurnResult, get_llm_service`.
- **GOTCHA**:
  - The override session must use `expire_on_commit=False` (mirror `conftest.py:40`) so ORM objects returned across `run_debate`'s internal commits stay usable.
  - Enable the SQLite FK pragma for the test engine (mirror `conftest.py:13-20,37-38`) so cascade-delete on DELETE is real.
  - The override must yield ONE session that lives for the whole request (run_debate commits many times against it) — do not open a fresh session per repo call.
  - `TurnResult` is imported from `app.services.llm`.
- **VALIDATE**: `cd backend && python3 -m pytest tests/test_debates_api.py -q`

---

## TESTING STRATEGY

### Unit Tests
No new pure-unit surface beyond schema validation (implicitly covered by the 422 test and response
serialization). The response schemas are exercised through the integration test.

### Integration Tests
`tests/test_debates_api.py` is the core deliverable — full HTTP round-trip through the real router,
real orchestrator, real judge, real repository, and an in-memory SQLite DB, with only the LLM
stubbed (no network). This satisfies the ticket's integration-test acceptance criterion: scripted
decision → persisted transcript + verdict; delete removes it.

### Edge Cases
- Empty `decision` → 422 (Pydantic `min_length=1`).
- GET / DELETE unknown id → 404.
- Judge failure path (JudgeError) → transcript persisted, `verdict: null`, still 201. (Optional
  extra test: override `get_llm_service` with a stub whose `complete` returns non-JSON twice; assert
  201 with `verdict is None`. Include if cheap.)
- `context` omitted → accepted (nullable), debate runs.

### E2E / Browser Automation
Not applicable — EPIC-A is a headless backend with no UI (the live threaded UI is EPIC-B/C,
DEC-006, not built). Validate via HTTP (Level 4) instead of a browser. Explicitly skip Level 5.

---

## VALIDATION COMMANDS

Execute every command to ensure zero regressions and 100% feature correctness. Run from the repo
root unless a `cd` is shown.

### Level 1: Syntax & Style
```bash
cd backend && ruff check app tests && ruff format --check app tests
```
(If `ruff` is not the configured linter, check `pyproject.toml` `[tool.*]` and use whatever is
declared; do not introduce a new tool.)

### Level 2: Unit Tests
```bash
cd backend && python3 -m pytest tests/test_debates_api.py -q
```

### Level 3: Integration / Full Suite (no regressions)
```bash
cd backend && python3 -m pytest -q
```
Expect the pre-existing suite (guardrails, health, judge, llm, orchestrator, personas, repository)
plus the new `test_debates_api.py` to all pass.

### Level 4: Manual Validation
Requires a real `ANTHROPIC_API_KEY` in `backend/.env` (this hits the live API). Optional — the
integration test already proves the wiring with a stub.
```bash
cd backend && uvicorn app.main:app --reload
# In another shell:
curl -s -X POST localhost:8000/api/debates -H 'content-type: application/json' \
  -d '{"decision":"Should we adopt SQLite for the MVP?","context":"single-user tool"}' | python3 -m json.tool
# copy the returned id:
curl -s localhost:8000/api/debates | python3 -m json.tool
curl -s localhost:8000/api/debates/<id> | python3 -m json.tool
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE localhost:8000/api/debates/<id>   # 204
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/api/debates/<id>             # 404
# OpenAPI shows the 4 routes:
curl -s localhost:8000/openapi.json | python3 -c "import sys,json;print(sorted(json.load(sys.stdin)['paths']))"
```

### Level 5: E2E / Browser Automation
Skipped — no UI in EPIC-A (see Testing Strategy).

### Level 6: Additional Validation (Optional)
Confirm the architecture-doc drift is captured for `/commit` (do not fix here):
```bash
grep -n "debates.py" docs/architecture.md   # still marked ⏳ planned (TICKET-6) — flip at commit time
```

---

## ACCEPTANCE CRITERIA

- [ ] `POST /api/debates` creates from `{decision, context?}`, runs orchestrator → judge, persists, returns `id` + completed debate (201).
- [ ] `GET /api/debates/{id}` returns full transcript + verdict; 404 on missing id.
- [ ] `GET /api/debates` returns the history list (summaries).
- [ ] `DELETE /api/debates/{id}` removes the debate (204); 404 on missing id.
- [ ] Pydantic request/response schemas exist in `schemas/debate.py`; router registered in `main.py`.
- [ ] Integration test: scripted decision → persisted transcript + verdict; delete removes it.
- [ ] No auth / no ownership scoping (DEC-005); no mid-debate mutation endpoint (DEC-004).
- [ ] All validation commands pass; existing suite has zero regressions.
- [ ] Code follows the existing router/schema/session conventions.

---

## COMPLETION CHECKLIST

- [ ] All tasks completed in order
- [ ] Each task validation passed immediately
- [ ] All validation commands executed successfully
- [ ] Full test suite passes (`python3 -m pytest -q`)
- [ ] No linting or type checking errors
- [ ] Level 5 agent-browser E2E — N/A (no UI), documented as skipped
- [ ] Manual testing (Level 4) confirms feature works (optional — needs live API key)
- [ ] Acceptance criteria all met
- [ ] Architecture-doc drift noted for `/commit` (debates.py ⏳→✅ KAN-9, judge ⏳→✅ KAN-8, Change Log + Confluence 917506)
- [ ] Code reviewed for quality and maintainability

---

## NOTES

**Session/threading design.** `run_debate` is `async`; FastAPI runs the sync `get_db` generator
dependency in a threadpool but hands the session to the async POST handler on the event-loop thread.
`run_debate` keeps all DB writes on its own (the handler's) thread by design, and SQLite is opened
with `check_same_thread=False`. For the single-user MVP (DEC-005) this is safe; a future move to
per-request async sessions would only matter under concurrency EPIC-A doesn't target.

**Judge-failure policy (design decision).** A `JudgeError` after the one repair retry leaves a fully
persisted, valid transcript. Rather than 500 the request and discard that work, POST logs a warning
and returns the completed debate with `verdict: null` (status stays `COMPLETED`). Rationale: the
expensive part (the debate) succeeded; the client can re-request a verdict later (a future
re-judge endpoint), and a null verdict is an honest signal. If the team prefers a hard failure,
the alternative is `status = FAILED` + HTTP 502 — this contradicts nothing in the DECs, so it is a
free choice; flag it at review if the reviewer wants the stricter behavior. This is a small local
behavior, not an architectural decision, so no new DEC is required (confirm with reviewer).

**`*Out` naming.** New suffix for response models to disambiguate from the internal `Verdict`/`Persona`
domain schemas (which carry different fields, e.g. schema `Persona` has a `color` the ORM row lacks).
Keeps the API contract decoupled from internal shapes. Justified deviation from having a single
schema per concept.

**No new DEC needed.** This ticket realizes DEC-004/005/008 as already accepted; it introduces no
new architectural decision. The commit must carry a `Decisions: DEC-004, DEC-005, DEC-008` trailer
and update those DECs' "Implemented by" entries with KAN-9.

**Confidence: 8.5/10** for one-pass success. Main residual risks: (1) exact ORM field names on
`Turn`/`Persona` must be confirmed by reading those model files (the plan flags this); (2) FastAPI
sync-dependency-in-threadpool + async-handler session sharing is sound for SQLite here but is the
one non-obvious interaction to watch if a test flakes.
```
