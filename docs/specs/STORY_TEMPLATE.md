# Story Template

Every story created (by the `spec` skill or by hand) uses this shape so the ADR gate travels
with the ticket. Keep the **Governing decisions** and **Definition of Done** sections.

---

## Summary
_As a [user], I want to [action], so that [benefit]._

## Context
Brief description. Link up the chain: **Epic** · **PRD** · relevant **design doc**.

## Governing decisions
List the `DEC-xxx` this story implements or is constrained by (from the Decision Log).
- DEC-00X — <one-line why it applies>

> If building this reveals a needed decision that isn't logged, STOP and raise a new
> Proposed `DEC-xxx` before continuing (do not silently deviate).

## Acceptance criteria
- [ ] …
- [ ] …

## Definition of Done
- [ ] Acceptance criteria met and validated (`/validate` green)
- [ ] Code reviewed (`/code-review`) with no unaddressed high-severity findings
- [ ] **Decision Log updated** if any decision was made/changed; and each realized DEC's
      "Implemented by" entry updated with this ticket / commit
- [ ] Commit references the governing `DEC-xxx` (or `Decisions: none` if not applicable)
- [ ] Docs/links updated so the traceability chain stays intact
