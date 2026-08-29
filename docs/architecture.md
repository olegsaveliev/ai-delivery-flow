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
> (flags drift) and `/commit` (updates both + the Change Log). See `.claude/skills/{prime,code-review,commit}`.

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

## Backend

Layered FastAPI service. The debate engine is the heart of the product.

| Path | Responsibility | Status |
| --- | --- | --- |
| `app/main.py` | FastAPI app, CORS, router registration | ✅ (routers: `health`, `chat`) |
| `app/core/config.py` | Settings from env/`.env`: model tiers, cost caps, `DATABASE_URL` | ✅ |
| `app/core/guardrails.py` | `DebateGuardrails` — hard caps on personas/rounds/tokens, enforced before every LLM call | ✅ (KAN-6) |
| `app/services/llm.py` | Guarded Anthropic wrapper: `run_turn` (per-turn skip), `complete` (single-shot, raises), model-tier routing, retry/backoff, cost logging | ✅ (KAN-6, KAN-8) |
| `app/services/personas.py` | `assign_personas(decision, context)` — three fixed archetypes, stance framed per decision | ✅ (KAN-5) |
| `app/prompts/personas.py` | Archetype specs (name, color, system prompt, stance template) | ✅ (KAN-5) |
| `app/services/orchestrator.py` | `run_debate` — sequential rounds, concurrent turns, persist-as-it-lands, skip tolerance | ✅ (KAN-7) |
| `app/services/judge.py` | `judge(debate)` — Opus synthesis → schema-validated `Verdict` + one repair retry; `persist_verdict` | ✅ (KAN-8) |
| `app/models/*.py` | ORM: `Debate`, `Persona`, `Turn`, `Verdict`, enums | ✅ (KAN-10) |
| `app/repositories/debates.py` | CRUD for the debate aggregate (create/add_persona/add_turn/set_verdict/get/list/delete) | ✅ (KAN-10) |
| `app/db/session.py`, `db/base.py` | Engine/session factory, `get_session()`, `init_db()` (SQLite auto-create) | ✅ (KAN-10) |
| `app/api/routes/debates.py` | `POST/GET/DELETE /api/debates` — run orchestrator → judge, persist, return | ⏳ planned (TICKET-6) |
| `app/schemas/*.py` | Pydantic request/response + in-memory contracts (`chat`, `persona`, `verdict`) | ✅ (`verdict` KAN-8; `debate` schema with T6) |

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
- `src/types/` — shared TypeScript types (mirror `backend/app/schemas`)
- `src/components/`, `src/pages/`, `src/hooks/` — UI building blocks (design owned by EPIC-E, DEC-010)

The live threaded debate UI (DEC-006) and streaming are **not built yet** (EPIC-B/C).

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
| DEC-004 / DEC-006 / DEC-009 / DEC-010 | Watch-only, threaded UI, shareable-verdict deferred, design system | ⏳ frontend epics |

## Change Log

Newest first. One row per architecture-affecting change; keep in lockstep with the Confluence mirror.

| Date | Change | Refs |
| --- | --- | --- |
| 2026-08-29 | Judge synthesis: Opus `judge(debate)` → schema-validated `Verdict` with exactly one repair retry, persisted via `persist_verdict`; added `LLMService.complete` (single-shot, raises) and the `verdict` schema. Marked judge/verdict ✅. | KAN-8 · DEC-007 |
| 2026-08-29 | Establish this file as the living source of truth + Confluence mirror; refresh to reflect the implemented debate engine (orchestrator, personas, guardrails, LLM tiers, SQLite models/repositories) and mark judge/API as planned; add Implementation Status + Change Log. Reconciled the Confluence HLD/LLD's stale "open questions" against DEC-001/003/004/005 and fixed its round algorithm to DEC-003. | KAN-7, DEC-003 |
| 2026-08-29 | Debate orchestrator: sequential 2 rounds, concurrent transcript-aware turns, persist-as-it-lands, skip tolerance. | KAN-7 · DEC-003/001/002/007/008 |
| 2026-08-28 | Guarded LLM turn client, model tiers, cost guardrails. | KAN-6 · DEC-007 |
| 2026-08-28 | Fixed persona archetypes & per-decision stance framing. | KAN-5 · DEC-001/002 |
| 2026-08-28 | SQLite data model & repository for debates. | KAN-10 · DEC-005/008 |
