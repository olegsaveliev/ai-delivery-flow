# Second Opinion — Product Requirements Document (MVP)

> **Status:** Draft for approval · **Owner:** PM · **Date:** 2026-08-28
> **Source of truth for scope:** the accepted [Decision Log](https://osavelyev.atlassian.net/wiki/spaces/~557058b71ec4cb4cec4df2b96f8e5302aff766/pages/1015810) (DEC-001…DEC-010).
> This PRD **references** the design docs rather than duplicating them:
> [Product Overview](https://osavelyev.atlassian.net/wiki/spaces/~557058b71ec4cb4cec4df2b96f8e5302aff766/pages/884737) ·
> [Architecture HLD & LLD](https://osavelyev.atlassian.net/wiki/spaces/~557058b71ec4cb4cec4df2b96f8e5302aff766/pages/917506) ·
> [Proposed Design](https://osavelyev.atlassian.net/wiki/spaces/~557058b71ec4cb4cec4df2b96f8e5302aff766/pages/950273).

## 1. Executive Summary

Second Opinion is a **decision-making arena**. A user brings a real decision — "Should I take this job?", "React or Svelte?", "Buy or rent?" — and the product convenes a panel of distinct AI personas who each argue a *different* position. The user watches the debate unfold; a neutral judge then synthesizes the arguments into a clear, balanced recommendation.

Most decision-support tools return a single confident answer, which quietly reinforces confirmation bias and hides trade-offs. Second Opinion makes the **disagreement the product**: the value is in seeing multiple well-argued perspectives and a transparent synthesis, not a black-box verdict.

**MVP goal:** Ship the smallest end-to-end loop that delivers the core delight — *pose a decision → 3 fixed personas debate for 2 rounds (watch-only) → an Opus judge synthesizes a balanced recommendation → saved locally.*

## 2. Mission

**Help people make better decisions by making them hear every side before they commit.**

Core principles (from the [Product Overview](https://osavelyev.atlassian.net/wiki/spaces/~557058b71ec4cb4cec4df2b96f8e5302aff766/pages/884737)):
- **Clarity over certainty** — help users understand a decision, not hand them false confidence.
- **Show the work** — every recommendation traces back to the arguments that produced it.
- **Respect the user's judgment** — we inform the decision; the human makes it.
- **Make it feel alive** — the debate should be something you *want* to watch.
- **The disagreement is the point** — optimize for a well-argued range of answers, not the blandest middle.

## 3. Target Users

| Persona | Decision they're stuck on | What Second Opinion gives them |
|---|---|---|
| **The Career Crossroader** | Job offers, quitting, relocating | Structured multi-angle pros/cons instead of a 2am spiral |
| **The Builder / Founder** | Tech stack, build-vs-buy, what to build next | A fast "advisory panel" without scheduling four meetings |
| **The Big-Purchase Deliberator** | Buy vs rent, which car/school | Confidence they weighed it seriously, not impulsively |

**Technical comfort:** general consumers; no technical knowledge assumed. **Primary need:** to feel they genuinely considered the alternatives before committing.

## 4. MVP Scope

**In Scope**
- ✅ Pose a decision in plain language, with optional free-text context (DEC-004 watch-only)
- ✅ **3 fixed persona archetypes** — The Advocate, The Skeptic, The Pragmatist — with stances framed per decision (DEC-001, DEC-002)
- ✅ **2-round** debate, personas responding to the transcript (DEC-003)
- ✅ Live **streaming** of the debate to the browser (SSE)
- ✅ **Threaded-chat** debate UI, color-coded per persona (DEC-006)
- ✅ **Judge synthesis** into a structured verdict: recommendation, strongest case per option, key trade-offs (DEC-007)
- ✅ **Local persistence** of debates + history view (DEC-008)
- ✅ Delete a debate (privacy)

**Out of Scope (deferred)**
- ❌ Mid-debate user interaction / replies (DEC-004) → fast-follow
- ❌ Authentication & multi-user (DEC-005)
- ❌ Shareable verdict link/image (DEC-009)
- ❌ Dynamic/generated persona sets, >3 personas, variable rounds (DEC-001/002/003)
- ❌ Hybrid/columned debate layout (DEC-006) → later
- ❌ Postgres / cloud deployment (DEC-008) → upgrade path only

## 5. User Stories

1. **As a decision-maker, I want to type my dilemma in one box, so that I can start without friction.**
   *e.g. "Should I take the senior role at a startup or stay at my stable job?"*
2. **As a user, I want to optionally add context/constraints, so that the debate reflects what I care about.**
   *e.g. "I value autonomy and have a 6-month growth goal."*
3. **As a viewer, I want to see three distinct personas argue different positions, so that I hear the sides I'd have missed.**
4. **As a viewer, I want the debate to stream live, so that it feels like watching a real discussion.**
5. **As a decision-maker, I want a synthesized recommendation with the case for each option and the key trade-offs, so that I can decide with a clear head.**
6. **As a skeptic, I want to read the full debate behind the verdict, so that I can trust the recommendation.**
7. **As a returning user, I want to see my past debates, so that I can revisit a decision.**
8. **As a privacy-conscious user, I want to delete a debate, so that personal decisions don't linger.**

**Technical story:** *As a developer, I want each persona turn to fail gracefully, so that one dropped turn doesn't break the debate.*

## 6. Core Architecture & Patterns

Full detail in [Architecture HLD & LLD](https://osavelyev.atlassian.net/wiki/spaces/~557058b71ec4cb4cec4df2b96f8e5302aff766/pages/917506). Summary:

- **Three tiers + an orchestration engine** (the heart of the product): React SPA → FastAPI → Orchestrator → Anthropic API + SQLite.
- **Orchestration pattern:** persona turns *within* a round run **concurrently**; rounds run **sequentially** (round N reads round N-1). Judge runs last on the full transcript.
- **Streaming:** Server-Sent Events push debate events (`personas_assigned`, `turn_delta`, `round_completed`, `verdict`, `done`, `error`) to the client.
- **Structured output:** the judge returns schema-validated JSON (Pydantic), with one repair retry on mismatch.

## 7. Features

| Feature | Description | Decisions |
|---|---|---|
| **Ask** | Single decision input + optional context expander + example prompts | DEC-004 |
| **Persona panel** | 3 fixed archetypes, stances framed per decision, distinct color/name | DEC-001, DEC-002 |
| **Debate engine** | 2 rounds, concurrent persona turns, transcript-aware | DEC-003 |
| **Live stream** | Token-by-token reveal, round progress, "skip to verdict" | — |
| **Judge & verdict** | Balanced recommendation + case-per-option + trade-offs, links to transcript | DEC-007 |
| **History** | List/reopen/delete past debates | DEC-005, DEC-008 |

## 8. Technology Stack

Proposed (not a hard commitment; builds on existing scaffold):

- **Frontend:** React + TypeScript (Vite)
- **Backend:** Python + FastAPI (async), Pydantic
- **LLM:** Anthropic Claude — personas `claude-sonnet-5`, judge `claude-opus-4-8`, utilities `claude-haiku-4-5-20251001` (DEC-007; confirm IDs at build time)
- **Streaming:** Server-Sent Events
- **Persistence:** SQLite (dev) → Postgres (future) (DEC-008)
- **Design:** Artifacts prototypes → Claude Design component library, synced via `/design-sync` (DEC-010)
- **Config:** `ANTHROPIC_API_KEY` via `.env`

## 9. Security & Configuration

- **Auth:** none for MVP — single-user, local (DEC-005).
- **Secrets:** API key in environment/`.env`, never committed.
- **Privacy:** decisions may be personal; no third-party sharing; user can delete a debate.
- **Cost guardrails:** hard caps on personas, rounds, and tokens per debate enforced before each LLM call.
- **Out of scope:** account security, rate-limiting by user, multi-tenant isolation.

## 10. API Specification (MVP)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/debates` | Create a debate from a decision → `debate_id` |
| `GET` | `/api/debates/{id}/stream` | SSE stream of debate events |
| `GET` | `/api/debates/{id}` | Fetch completed debate (transcript + verdict) |
| `GET` | `/api/debates` | List past debates |
| `DELETE` | `/api/debates/{id}` | Delete a debate |

Data model (Debate / Persona / Turn / Verdict) and SSE event contracts are specified in the HLD & LLD doc.

## 11. Success Criteria

**MVP is successful when:**
- ✅ A user can pose a decision and watch a 3-persona, 2-round debate stream to completion.
- ✅ The judge produces a balanced verdict with a recommendation, per-option case, and trade-offs.
- ✅ The verdict links back to the full transcript.
- ✅ Debates persist and appear in history; a debate can be deleted.
- ✅ A dropped persona turn degrades gracefully (debate still finishes).

**Quality indicators:** first persona message visible within a few seconds; users report the verdict feels *balanced* (more trusted than a single-answer tool); users want to run a second debate.

## 12. Implementation Phases

**Phase 1 — Backend debate loop (headless)**
- Goal: orchestrator runs a full debate + judge end-to-end from an API call.
- Deliverables: ✅ data model + SQLite ✅ persona/judge prompts ✅ orchestrator (concurrent turns, sequential rounds) ✅ `POST /api/debates` + fetch endpoints ✅ guardrails.
- Validation: a scripted decision returns a persisted transcript + verdict.

**Phase 2 — Streaming**
- Goal: debate events stream live.
- Deliverables: ✅ SSE endpoint ✅ event contract ✅ graceful stream close/errors.
- Validation: a client sees turns appear incrementally, then a verdict, then `done`.

**Phase 3 — Frontend experience**
- Goal: the full user-facing loop.
- Deliverables: ✅ Ask screen ✅ threaded live debate view ✅ verdict view ✅ history ✅ delete.
- Validation: a non-technical user completes pose → watch → read verdict unaided.

**Phase 4 — Polish & hardening**
- Goal: it feels alive and trustworthy.
- Deliverables: ✅ streaming reveal + round transitions ✅ empty/loading/error states ✅ dark mode ✅ accessibility (reduced-motion, keyboard, non-color persona cues) ✅ cost/latency logging.

## 13. Future Considerations

- Interactive debates (user nudges mid-stream) — DEC-004 fast-follow.
- Dynamic persona generation / more archetypes; variable rounds with convergence.
- Hybrid responsive debate layout (columns on desktop).
- Shareable verdicts (link/image) for virality — DEC-009.
- Accounts + cloud deployment + Postgres — DEC-005/008.

## 14. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| **LLM cost/latency balloons** with parallel persona calls | Hard caps (personas, rounds, tokens) enforced before each call; Sonnet for personas; log cost per debate |
| **Personas converge / sound the same** (no real disagreement) | Distinct archetype system prompts + explicit stance framing; judge prompted to surface genuine tension |
| **Judge produces a lopsided verdict** | Structured output requiring per-option case + trade-offs; schema validation + repair retry |
| **A persona turn fails mid-debate** | Per-turn retry w/ backoff; skip on repeated failure; always synthesize from available transcript |
| **Streaming client hangs** | Always emit terminal `done`/`error`; per-turn and per-debate timeouts |

## 15. Appendix — Related Documents

- [Product Overview & Onboarding](https://osavelyev.atlassian.net/wiki/spaces/~557058b71ec4cb4cec4df2b96f8e5302aff766/pages/884737)
- [Architecture — HLD & LLD](https://osavelyev.atlassian.net/wiki/spaces/~557058b71ec4cb4cec4df2b96f8e5302aff766/pages/917506)
- [Proposed Design](https://osavelyev.atlassian.net/wiki/spaces/~557058b71ec4cb4cec4df2b96f8e5302aff766/pages/950273)
- [Decision Log (DEC-001…DEC-009)](https://osavelyev.atlassian.net/wiki/spaces/~557058b71ec4cb4cec4df2b96f8e5302aff766/pages/1015810)
- Jira project: **KAN**

## Epics (created in Jira under KAN)

Stories are sliced from these epics with the `spec` skill (e.g. EPIC-A / KAN-1 → KAN-5…KAN-10).
EPIC-E was added after the original A–D scope to own design/UX — see DEC-010; its design
execution is deferred until the frontend phase.

1. **EPIC-A — Debate Orchestration Engine** ([KAN-1](https://osavelyev.atlassian.net/browse/KAN-1)) — backend loop: personas, rounds, judge, persistence, guardrails · *Phase 1*
2. **EPIC-B — Live Streaming** ([KAN-2](https://osavelyev.atlassian.net/browse/KAN-2)) — SSE endpoint + event contract · *Phase 2*
3. **EPIC-C — Web Experience** ([KAN-3](https://osavelyev.atlassian.net/browse/KAN-3)) — Ask → live debate → verdict → history UI · *Phase 3*
4. **EPIC-D — Polish, Accessibility & Observability** ([KAN-4](https://osavelyev.atlassian.net/browse/KAN-4)) — states, dark mode, a11y, cost/latency logging · *Phase 4*
5. **EPIC-E — Design System & UX** ([KAN-11](https://osavelyev.atlassian.net/browse/KAN-11)) — design language + core screen mockups + Claude Design component library (feeds EPIC-C) · *before Phase 3, parallel to 1–2*
