# AI Delivery Flow — Project Instructions

## Decision Discipline (ADRs) — MANDATORY

This project tracks every architectural/product decision in a **Decision Log** (ADRs), so
context follows the work no matter who — human or a fresh Claude session — picks up a task.
These rules OVERRIDE default behavior and apply to **every** task.

**Source of truth**
- Decision Log (Confluence): https://osavelyev.atlassian.net/wiki/spaces/~557058b71ec4cb4cec4df2b96f8e5302aff766/pages/1015810
- How-we-use-ADRs process page: see the "Second Opinion" Confluence folder.
- Decisions are identified as `DEC-001`, `DEC-002`, … Never reuse an id.

**At task start (prime):**
- Load the story → its epic → the PRD → the Decision Log (prime pulls linked Confluence).
- State **which DECs constrain this task** before planning. If you can't tell, stop and ask.

**When planning (plan-feature):**
- The plan must cite the DECs it honors.
- If the right implementation would **contradict** an Accepted decision, DO NOT silently deviate.
  Stop, and raise a new **Proposed** `DEC-xxx` (context / options / proposal) for approval.

**When reviewing / before commit (code-review, commit):**
- Check: does this change contradict any Accepted DEC? Did it introduce a decision that isn't logged?
- If a new decision was made during the work, **add it to the Decision Log first** (status
  `Accepted` once agreed), then reference it.

**Changing a decision:**
- Never edit a decision's substance in place. Set the old one to `Superseded`, add a new
  `DEC-xxx` that links back ("Supersedes DEC-00X"), and record the date and rationale.

**Commit / PR requirement (enforced by the commit-msg hook):**
- Any commit that changes `backend/app/**` or `frontend/src/**` must reference the decision(s)
  it implements/changes via a trailer, e.g. `Decisions: DEC-003`, OR explicitly opt out with
  `Decisions: none` when no architectural decision is involved.
- When a commit realizes a decision, also update that DEC's **"Implemented by"** entry in the
  Decision Log with the Jira key / commit so the log stays bidirectional.

**One-time setup (activates the hook):**
```bash
git config core.hooksPath .githooks
```

## Traceability chain
`Story (Jira) → Epic (Jira) → PRD (Confluence) → Decision Log (DEC-xxx) → Architecture (docs/architecture.md ⇄ Confluence 917506) → Design docs`.
Keep every new artifact linked up this chain so `prime` can load the full context from a ticket.

**Architecture doc is living context (DEC-011).** `docs/architecture.md` is the local **source of
truth** for how the system is built, mirrored to the Confluence HLD/LLD (page `917506`). It is read
as design context by `prime` (which also flags drift), drift-checked by `code-review`, and updated
— together with the Confluence mirror and its **Change Log** — by `commit` whenever a change alters
the architecture or realizes/changes a `DEC-xxx`.
