# Code Review — KAN-10: Data Model & SQLite Persistence Layer

**Reviewed:** 2026-08-28 · **Branch:** main · **Ticket:** [KAN-10](https://osavelyev.atlassian.net/browse/KAN-10)
**Governing decisions:** DEC-008 (SQLite dev persistence), DEC-005 (single-user, no ownership column)

## Stats

- Files Modified: 5 (`.gitignore`, `backend/.env.example`, `backend/app/core/config.py`, `backend/app/main.py`, `backend/pyproject.toml`)
- Files Added: 13 (`app/db/{__init__,base,session}.py`, `app/models/{__init__,enums,debate,persona,turn,verdict}.py`, `app/repositories/{__init__,debates}.py`, `tests/{conftest,test_repository}.py`)
- Files Deleted: 0
- New lines: ~430
- Deleted lines: 1

## Verdict

**Overall: solid, ships as-is.** The models match the shared contracts exactly, DEC-005 (no owner/tenant column) and DEC-008 (SQLite only, no Alembic/Postgres types) are both honored, cascade + FK enforcement is correct and table-level verified, and the full gate is green (ruff clean, 10/10 pytest, round-trip smoke passes with no warnings). No critical, high, or medium-severity defects. Five low/informational items below, each with a concrete fix — none block commit.

---

## Findings

```
severity: low
file: backend/app/repositories/debates.py
line: 113
issue: list_debates ordering is nondeterministic when created_at ties
detail: order_by(Debate.created_at.desc()) has no tiebreaker. created_at defaults to
        datetime.utcnow, and empirically 74% of back-to-back utcnow() calls on this
        machine return an IDENTICAL timestamp. Two debates created in the same
        microsecond therefore sort in engine-arbitrary order — the "newest first"
        contract that TICKET-6's history list depends on is not guaranteed. The unit
        test test_list_debates_newest_first only passes because it manually rewrites
        created_at to force a gap; it would be flaky against real rapid inserts / seeding.
        Real-world impact is bounded by DEC-005 (single user; a debate takes many LLM
        seconds to create, so API-driven ties are unlikely) — hence low, not medium.
suggestion: Add a stable monotonic tiebreaker. Cleanest is an autoincrement surrogate
        (e.g. a `seq: Mapped[int] = mapped_column(Integer, autoincrement=True)` or an
        Integer identity column) and order_by(seq.desc()); or, minimally,
        order_by(Debate.created_at.desc(), Debate.id.desc()) to at least make the order
        deterministic (note: uuid hex is not time-ordered, so this fixes determinism,
        not true recency, for same-instant rows).
```

```
severity: low
file: backend/app/models/debate.py
line: 24
issue: datetime.utcnow is deprecated (also turn.py:33)
detail: datetime.utcnow() is deprecated as of Python 3.12 and emits a DeprecationWarning
        (19 per test run locally on 3.14). The project targets py311 (pyproject
        target-version = "py311") where it is not yet deprecated, so this is not urgent,
        but it is scheduled for removal and produces noisy warnings on newer interpreters.
suggestion: Use a naive-UTC callable that isn't deprecated, e.g.
        `default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)`, or define a
        small `_utcnow()` helper and reuse it across models. Keep the column naive to
        match the current DateTime type. Apply to both Debate.created_at and
        Turn.created_at.
```

```
severity: low
file: backend/tests/conftest.py
line: 37
issue: throwaway-connection PRAGMA is redundant and the comment mislabels it
detail: Lines 37-38 open a separate connection, set PRAGMA foreign_keys=ON, then close it.
        The comment says "assert FKs are on" but it SETS, not asserts. Actual FK
        enforcement for the yielded session comes from the global _fk_pragma connect
        listener (line 13), which fires for every new DBAPI connection. With StaticPool
        reusing a single connection the listener has already enabled FKs before this
        block runs, so these two lines change nothing. Not a bug — tests correctly verify
        cascade at table level — but the code gives false confidence.
suggestion: Either delete lines 36-38, or turn it into a real assertion:
        `assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1` inside the fixture
        after creating the session, so a future regression in the listener is caught.
```

```
severity: low
file: backend/app/db/session.py
line: 12
issue: duplicate process-wide connect listener (session.py + conftest.py)
detail: Both session.py (_set_sqlite_pragma) and conftest.py (_fk_pragma) register
        @event.listens_for(Engine, "connect") at module scope. During the full test run
        app.main imports session.py (via init_db) AND conftest is loaded, so both global
        listeners are active and each SQLite connect runs PRAGMA foreign_keys=ON twice.
        Harmless (idempotent) but redundant, and a class-level Engine listener applies to
        every engine in the process, which is broader than necessary.
suggestion: Keep the single canonical listener in session.py and have conftest rely on it
        (drop conftest's own listener), or scope the listener to the specific engine
        instance via event.listen(engine, "connect", ...) instead of the Engine class.
        Low priority; no functional impact.
```

```
severity: low
file: backend/app/models/debate.py
line: 32
issue: passive_deletes=True weakens the stated "belt-and-suspenders" cascade
detail: The comment describes ORM cascade + DB FK cascade as belt-and-suspenders, but
        passive_deletes=True tells the ORM NOT to delete unloaded children and defer to
        the DB FK ON DELETE CASCADE. delete_debate() uses session.get() (children not
        loaded), so in that path cascade relies SOLELY on PRAGMA foreign_keys=ON being
        active — the ORM "suspenders" are intentionally disabled. It works because the
        connect listener is always registered in every real entry point (verified: FK
        default is OFF without it, and the delete-cascade test asserts children are gone
        at the table level). Flagging only so the comment's "belt-and-suspenders" framing
        isn't mistaken for redundant safety on the unloaded-children path.
suggestion: No code change required. Optionally tighten the comment to say the DB FK
        cascade is the primary mechanism and passive_deletes avoids the double-delete;
        the ORM delete-orphan cascade still covers the loaded-collection path.
```

---

## Checks that passed (no issues)

- **DEC-005:** no `owner_id` / `user_id` / tenant column on any model; `list_debates` has no ownership filter. ✓
- **DEC-008:** SQLite only — no Alembic, no Postgres-specific column types; `Enum(native_enum=False)` and `JSON` are portable; auto-create on startup via lifespan. ✓
- **Security:** all queries parameterized through the ORM (no string-built SQL); no injection surface; no secrets committed; `.db` files git-ignored. ✓
- **Contracts:** Debate/Persona/Turn/Verdict fields, enum values (`advocate|skeptic|pragmatist`, `ok|skipped`), and relationships match `epic-a-...md:18-27` exactly; `cases` shape `{option, argument}`, `unique=True` one-verdict-per-debate. ✓
- **Mutable defaults:** JSON columns use `default=list` (callable) — no shared-mutable-default trap; `__mapper_args__` annotated `ClassVar` to satisfy RUF012. ✓
- **Session hygiene:** `get_session()` commits on success, rolls back + re-raises on error, always closes; `expire_on_commit=False` keeps eager-loaded objects usable post-commit and `get_debate`/`list_debates` eager-load personas/turns/verdict so no DetachedInstanceError on the returned graph. ✓
- **No import cycle:** debate.py's bottom imports of persona/turn/verdict don't cycle (children import only `base` + `enums`); `Base.metadata` resolves to all four tables. ✓
- **set_verdict upsert:** delete-existing-then-insert respects the `unique` FK; verified one verdict survives a double `set_verdict`. ✓

## Recommendation

Proceed to commit with trailer `Decisions: DEC-008, DEC-005`. The finding worth acting on before the history endpoint (TICKET-6) is the `list_debates` tiebreaker (finding 1) — cheap to add now while the schema is being frozen, and avoids a re-freeze later. The remaining four are low-cost cleanups that can ride along or be deferred.
