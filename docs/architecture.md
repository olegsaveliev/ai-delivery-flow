# Architecture

> **Living design doc — local source of truth.** This file is kept current as the code and
> decisions evolve, and mirrored to Confluence so the two never drift.
>
> - **Confluence mirror (full HLD/LLD):** [Second Opinion — Architecture (HLD & LLD)](https://osavelyev.atlassian.net/wiki/spaces/~557058b71ec4cb4cec4df2b96f8e5302aff766/pages/917506) (page `917506`)
> - **Decision Log (source of truth for decisions):** [DEC-xxx](https://osavelyev.atlassian.net/wiki/spaces/~557058b71ec4cb4cec4df2b96f8e5302aff766/pages/1015810) (page `1015810`)
> - **PRD:** `docs/second-opinion-prd.md` (Confluence `1048577`)
>
> **Maintenance rule:** any change that alters the architecture here **or** realizes/changes a
> `DEC-xxx` must (1) update this file, (2) push the same update to the Confluence mirror, and
> (3) add a dated row to the [Change Log](#change-log). This is enforced by `/code-review`
> (flags drift) and `/commit` (updates both + the Change Log). See `.claude/skills/{pickup,code-review,commit}`.

## Overview

Second Opinion is a full-stack application. A user poses a decision; the backend assigns three
fixed personas that debate it over a fixed number of rounds; an Opus judge synthesizes a balanced
verdict; everything is persisted locally.

```
┌────────────┐        HTTP/JSON        ┌────────────┐        ┌──────────────┐
│  Frontend  │  ───────────────────▶   │  Backend   │  ───▶  │ Anthropic API │
│ React+Vite │                         │  FastAPI   │        │   (Claude)    │
└────────────┘                         └─────┬──────┘        └──────────────┘
                                             │
                                       ┌─────▼──────┐
                                       │  SQLite    │
                                       │ (DEC-008)  │
                                       └────────────┘
```

The debate engine (EPIC-A) is headless: no streaming and no live UI yet — those are EPIC-B/C.
EPIC-B has started: the stream **event contract** and SSE wire encoding exist (KAN-17), but nothing
emits or serves them yet (orchestrator emission is KAN-19; the run lifecycle and `/stream` endpoint are
B-T4/B-T6, designed by DEC-012 — Accepted 2026-09-24). `LLMService.stream_turn` exists (KAN-18) but is not
yet called.

## Backend

Layered FastAPI service. The debate engine is the heart of the product.

| Path | Responsibility | Status |
| --- | --- | --- |
| `app/main.py` | FastAPI app, CORS, router registration | ✅ (routers: `health`, `chat`, `debates`) |
| `app/core/config.py` | Settings from env/`.env`: model tiers, cost caps, `DATABASE_URL`, retry/backoff, `turn_timeout_seconds` (per-turn streaming budget, > 0) | ✅ (timeout KAN-18) |
| `app/core/guardrails.py` | `DebateGuardrails` — hard caps on personas/rounds/tokens, enforced before every LLM call | ✅ (KAN-6) |
| `app/services/llm.py` | Guarded Anthropic wrapper: `run_turn` (per-turn skip), `stream_turn` (streamed persona turn → `on_delta`, per-turn budget, never raises), `complete` (single-shot, raises), model-tier routing, retry/backoff, cost logging | ✅ (KAN-6, KAN-8, `stream_turn` KAN-18) |
| `app/services/personas.py` | `assign_personas(decision, context)` — three fixed archetypes, stance framed per decision | ✅ (KAN-5) |
| `app/prompts/personas.py` | Archetype specs (name, color, system prompt, stance template) | ✅ (KAN-5) |
| `app/services/orchestrator.py` | `run_debate` — sequential rounds, concurrent turns, persist-as-it-lands, skip tolerance | ✅ (KAN-7) |
| `app/services/judge.py` | `judge(debate)` — Opus synthesis → schema-validated `Verdict` + one repair retry; `persist_verdict` | ✅ (KAN-8) |
| `app/models/*.py` | ORM: `Debate`, `Persona`, `Turn`, `Verdict`, enums | ✅ (KAN-10) |
| `app/repositories/debates.py` | CRUD for the debate aggregate (create/add_persona/add_turn/set_verdict/get/list/delete) | ✅ (KAN-10) |
| `app/db/session.py`, `db/base.py` | Engine/session factory, `get_session()`, `init_db()` (SQLite auto-create) | ✅ (KAN-10) |
| `app/api/routes/debates.py` | `POST/GET/DELETE /api/debates` — run orchestrator → judge, persist, return; 404 on missing id | ✅ (KAN-9) |
| `app/schemas/*.py` | Pydantic request/response + in-memory contracts (`chat`, `persona`, `verdict`, `debate`, `events`) | ✅ (`debate` KAN-9, `events` KAN-17) |
| `app/schemas/events.py` | Stream event contract: 8 frozen event models discriminated by `type`, `DebateEvent` union + `parse_event`, `SequencedEvent{seq, event}` envelope | ✅ (KAN-17) |
| `app/services/events.py` | SSE wire encoding: `EventSequencer` (per-debate seq from 1), `encode_sse`, `EventSink` protocol, `InMemoryEventSink` | ✅ (KAN-17) |

### Debate engine (implemented — DEC-003, DEC-001/002, DEC-008)

`run_debate(session, debate)`:

1. **Assign & persist personas** — `assign_personas` (DEC-001/002): exactly three fixed archetypes
   (The Advocate, The Skeptic, The Pragmatist), each with a stance framed to the decision.
2. **Sequential rounds, concurrent turns** — a **fixed 2 rounds** (DEC-003, no convergence
   heuristic). Within a round the three persona turns run **concurrently**; because KAN-6's
   `LLMService` is synchronous, each turn is fanned out via `asyncio.to_thread` while `run_debate`
   is a coroutine. Rounds run **sequentially** — round N is given the transcript of rounds `< N`.
3. **Persist-as-it-lands** (DEC-008) — each turn commits the moment its call returns
   (`asyncio.as_completed`); a later failure never loses an earlier turn. **All DB writes stay on
   the orchestrator's thread** (a SQLAlchemy `Session` is not thread-safe); only `run_turn` runs in
   worker threads.
4. **Skip tolerance** — a failed or guardrail-blocked turn is persisted as `SKIPPED` (empty
   content) and the debate continues. Status lifecycle: `PENDING → RUNNING → COMPLETED`.
5. Returns the `Debate` with turns in transcript order — the input the judge (TICKET-5) consumes.

### Judge (implemented — DEC-007)

`judge(debate)` runs **last**, on the full transcript, using the **Opus** tier:

1. Renders the OK turns as `[Round N] {persona}: {content}` (skipping `SKIPPED`/empty turns) into a
   single Opus prompt that asks for a **JSON** verdict.
2. **Structured output via prompt + validation** (not native structured outputs, deliberately — so
   the DEC-007 repair path is real): parse the JSON and validate it against the Pydantic
   `schemas.verdict.Verdict` (`recommendation`, `cases: [{option, argument}]`, `tradeoffs: [str]`).
3. **Exactly one repair retry** on a parse/schema failure (re-prompt with the bad output + error);
   a second failure raises `JudgeSchemaError`.
4. `judge` is pure (no HTTP/session/persistence) so the API (TICKET-6) composes
   `run_debate → judge → persist_verdict`. `persist_verdict` upserts the single `Verdict` row via
   the repository (linkable to its transcript by `debate_id`).

The judge call goes through `LLMService.complete` — a single-shot completion that routes to the tier
model and **raises** on persistent failure (unlike `run_turn`, which degrades to a skipped turn); it
is intentionally outside the debate `DebateGuardrails` (those cap the persona rounds).

### Debate API (implemented — DEC-004, DEC-005, DEC-008)

`app/api/routes/debates.py` mounts the engine at `/api/debates` (router registered in `main.py`):

- **`POST /api/debates`** — body `{decision, context?}`; creates the debate, `await run_debate` →
  `judge` → `persist_verdict`, and returns the completed aggregate (201). A judge failure after its
  one repair retry keeps the transcript and returns `verdict: null` (status stays `COMPLETED`) rather
  than discarding the debate.
- **`GET /api/debates/{id}`** — full transcript + verdict; **404** on missing id.
- **`GET /api/debates`** — history list (lightweight summaries), newest first.
- **`DELETE /api/debates/{id}`** — removes the debate and cascades to its children (privacy); **404**
  on missing id; **204** on success.

Watch-only (DEC-004): no mid-debate input endpoint — a debate runs to completion inside `POST`. No
auth / no ownership scoping (DEC-005). Routes use a request-scoped `get_db` dependency
(`db/session.py`); because `set_verdict` writes by `debate_id` without touching the in-session
`Debate.verdict` relationship, `POST` calls `session.expire_all()` before its final reload.

### Stream event contract (implemented — DEC-003, DEC-004; KAN-17)

The frozen server → client contract for live streaming (EPIC-B). Watch-only (DEC-004): there are no
inbound event types. Defined in `app/schemas/events.py`, encoded by `app/services/events.py`, mirrored
in `frontend/src/types/debateEvents.ts`.

| Event | Payload (besides `type`) | Meaning |
| --- | --- | --- |
| `personas_assigned` | `debate_id`, `personas: PersonaOut[]` | The 3 personas are persisted (same shape as `GET /api/debates/{id}`; UI color is derived from `archetype`) |
| `turn_started` | `round`, `persona_id` | A persona turn begins |
| `turn_delta` | `round`, `persona_id`, `text` | One streamed text chunk |
| `turn_completed` | `round`, `persona_id`, `turn_id`, `status: ok\|skipped`, `content` | Turn persisted (DEC-008). `content` is authoritative and **replaces** the accumulated deltas; a skipped turn has `content: ""` and partial text is discarded |
| `round_completed` | `round` | All turns of a round landed — twice per debate (DEC-003) |
| `verdict` | `recommendation`, `cases`, `tradeoffs` | The judge verdict, one non-streamed event (DEC-007) |
| `done` | `debate_id`, `status: completed` | Terminal: success |
| `error` | `code`, `message` | Terminal: failure (`code` is open; DEC-012 fixes the codes as `judge_failed`, `timeout`, `internal`, `interrupted`) |

- **Wire frame:** `id: <seq>\nevent: <type>\ndata: <single-line JSON>\n\n` (LF only; the JSON includes
  `type`, excludes `seq`).
- **Sequencing:** events never carry `seq`. Producers emit bare events to an `EventSink`; the sink that owns
  the debate's stream stamps them with a per-debate `EventSequencer` (1, 2, 3, …) into `SequencedEvent`s.
  The sequencer is event-loop-only (not thread-safe).
- **Strictness:** event models are top-level `frozen` + `extra="forbid"`; `parse_event` rejects unknown or
  missing `type`.
- Not yet wired: orchestrator emission (KAN-19) and the broker / `GET /api/debates/{id}/stream` endpoint
  (B-T4/B-T6, per DEC-012 — Accepted: background run + in-process broker with in-memory replay).

### Streaming persona turns (implemented — DEC-007; KAN-18)

`LLMService.stream_turn(..., on_delta)` is the streaming sibling of `run_turn` on the persona tier. It is
synchronous and meant to run in a worker thread; `on_delta(text)` is a plain sync callback, called once per
non-empty text delta in order, on that thread (KAN-19 will bridge it onto the event loop). Nothing calls it
yet — the orchestrator still uses `run_turn` until KAN-19.

- **Same guarantees as `run_turn`:** guardrails checked before the call, cost/latency logged, usage recorded,
  never raises (degrades to a `skipped` `TurnResult`, `text == ""` even if deltas were already emitted).
- **Single retry layer:** the stream uses a client with SDK retries disabled (`with_options(max_retries=0)`,
  same connection pool); our loop retries only *transient* failures (connection/connect-timeout, 408/409/429/5xx,
  overloaded/api error events, transport errors; `x-should-retry` honored, `retry-after` not) and only
  **before the first delta**. A failure after a delta skips without retrying so no client sees duplicated text.
- **Per-turn budget:** `turn_timeout_seconds` (default 60) is one wall-clock budget across attempts and backoff,
  enforced via each attempt's request timeout (remaining budget; connect ≤ 5 s) and at every text delta; a spent
  budget yields `reason == "timeout"`. **Soft bound:** a stream that sends only keepalive pings keeps resetting
  the read timeout and can exceed the budget — the per-debate timeout (EPIC-B B-T5) is the outer bound.
- **Usage on aborted/retried attempts:** charged best-effort from the stream snapshot (input exact once
  `message_start` arrived; output may be under-counted).

### Model tiers (DEC-007)

Personas = `claude-sonnet-5`; judge = `claude-opus-4-8`; cheap utilities = `claude-haiku-4-5-20251001`.
IDs live in `config.py`; `LLMService.model_for(tier)` resolves them.

### Data model (SQLite — DEC-008, DEC-005)

```
Debate   { id, decision, context?, status(pending|running|completed|failed), created_at }
Persona  { id, debate_id→Debate, archetype(advocate|skeptic|pragmatist), name, stance }
Turn     { id, debate_id→Debate, persona_id→Persona, round, content, status(ok|skipped), created_at }
Verdict  { id, debate_id→Debate (unique), recommendation, cases(json), tradeoffs(json) }
```

Single-user, no ownership column (DEC-005). Transcript = `Turn` rows ordered by `(round, created_at, id)`;
round N reads rounds `< N`. FK cascade deletes children with a debate.

## Frontend

- `src/api/client.ts` — typed fetch client for the backend
- `src/types/` — shared TypeScript types (mirror `backend/app/schemas`); `debateEvents.ts` mirrors the
  stream event contract (KAN-17)
- `src/components/`, `src/pages/`, `src/hooks/` — UI building blocks (design owned by EPIC-E, DEC-010)

The live threaded debate UI (DEC-006) and streaming are **not built yet** (EPIC-B/C). Today
`frontend/` is only a bare Vite + TS scaffold — `src/components`, `src/hooks`, `src/pages` are
empty `.gitkeep` placeholders; there is no UI.

> **Frontend gate (DEC-010).** Design comes before frontend. EPIC-C (Web Experience, KAN-3)
> does **not** start until EPIC-E (Design System & UX, KAN-11) delivers the design: E-T1 tokens
> and the **E-T2 approved look** are the gate, with E-T3 states/a11y and the E-T4/E-T5 components
> syncing into `frontend/src/components` via `/design-sync`. Backend (EPIC-A/B) is headless and
> runs independently — the gate binds the UI only. See `docs/specs/epic-e-design-system-ux.md`.

## Conventions

- Keep request/response shapes mirrored between `backend/app/schemas` and `frontend/src/types`.
- Secrets live only in `.env` (never committed).
- Every architectural/product decision is recorded as a `DEC-xxx` in the Decision Log **before**
  it lands; commits touching `backend/app/**` or `frontend/src/**` cite the DEC(s) they realize.

## Decisions realized in the current build

| DEC | Decision | Realized by |
| --- | --- | --- |
| DEC-001 / DEC-002 | Three fixed persona archetypes, stances framed per decision | KAN-5 |
| DEC-007 | Model routing (Sonnet personas / Opus judge / Haiku utilities) + structured, schema-validated verdict with one repair retry | KAN-6 (routing) · KAN-8 (judge) |
| DEC-005 / DEC-008 | Single-user local; SQLite persistence | KAN-10 |
| DEC-003 | Fixed 2 rounds, no convergence heuristic | KAN-7 |
| DEC-004 / DEC-005 / DEC-008 | Debate HTTP API — watch-only surface (create/read/list/delete), no auth, SQLite-persisted | KAN-9 |
| DEC-003 / DEC-004 | Stream event contract — server → client only, `round_completed` per round (contract only; not yet emitted/served) | KAN-17 |
| DEC-006 / DEC-009 / DEC-010 | Threaded UI, shareable-verdict deferred, design system | ⏳ frontend epics |

## Change Log

Newest first. One row per architecture-affecting change; keep in lockstep with the Confluence mirror.

| Date | Change | Refs |
| --- | --- | --- |
| 2026-09-24 | DEC-012 Accepted (no code change): streaming run lifecycle = background run in a lifespan task registry + per-debate in-process broker with an in-memory event log for replay (SQLite-synthesized replay after the run), restart sweep, abandon-on-timeout with `max_active_debates` cap, `POST` → 202. Updated the not-yet-wired notes and error codes to match. Implemented by B-T4/B-T6/B-T5 (KAN-20/21/22). | DEC-012 |
| 2026-09-24 | Streaming persona turns (EPIC-B B-T2): `LLMService.stream_turn(..., on_delta)` on the persona tier — guardrails first, single retry layer (SDK retries off) for transient pre-first-delta failures only, one wall-clock `turn_timeout_seconds` budget (soft for ping-only streams; B-T5 bounds it), best-effort usage on aborted attempts, never raises. `turn_timeout_seconds` config; `anthropic>=1.2` pin. Not yet called (KAN-19). | KAN-18 · DEC-007 |
| 2026-09-24 | Stream event contract (EPIC-B B-T1): 8 frozen event models + `DebateEvent` union + `parse_event` (`schemas/events.py`), SSE encoding with per-debate `EventSequencer`, `EventSink` protocol and in-memory sink (`services/events.py`), TS mirror `frontend/src/types/debateEvents.ts`. Not yet emitted or served. | KAN-17 · DEC-003/004 |
| 2026-08-29 | Documented the **frontend gate** (design-before-frontend): EPIC-C waits on EPIC-E; recorded that `frontend/` is a bare scaffold with no UI. No code change — spec/doc alignment after slicing KAN-11 into KAN-12…16. | KAN-11 · DEC-010 |
| 2026-08-29 | Debate HTTP API: `/api/debates` router (POST create-and-run → orchestrator → judge → persist; GET one/list; DELETE) + `debate` request/response schemas + request-scoped `get_db`. Watch-only, no auth. Marked API ✅. | KAN-9 · DEC-004/005/008 |
| 2026-08-29 | Judge synthesis: Opus `judge(debate)` → schema-validated `Verdict` with exactly one repair retry, persisted via `persist_verdict`; added `LLMService.complete` (single-shot, raises) and the `verdict` schema. Marked judge/verdict ✅. | KAN-8 · DEC-007 |
| 2026-08-29 | Establish this file as the living source of truth + Confluence mirror; refresh to reflect the implemented debate engine (orchestrator, personas, guardrails, LLM tiers, SQLite models/repositories) and mark judge/API as planned; add Implementation Status + Change Log. Reconciled the Confluence HLD/LLD's stale "open questions" against DEC-001/003/004/005 and fixed its round algorithm to DEC-003. | KAN-7, DEC-003 |
| 2026-08-29 | Debate orchestrator: sequential 2 rounds, concurrent transcript-aware turns, persist-as-it-lands, skip tolerance. | KAN-7 · DEC-003/001/002/007/008 |
| 2026-08-28 | Guarded LLM turn client, model tiers, cost guardrails. | KAN-6 · DEC-007 |
| 2026-08-28 | Fixed persona archetypes & per-decision stance framing. | KAN-5 · DEC-001/002 |
| 2026-08-28 | SQLite data model & repository for debates. | KAN-10 · DEC-005/008 |
