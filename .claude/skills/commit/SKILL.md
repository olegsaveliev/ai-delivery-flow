---
name: commit
description: Creates a new git commit for all uncommitted changes with an atomic, conventionally-tagged message. Use when work is complete and ready to be committed.
---

# Commit: Create a New Commit

Create a new commit for all of our uncommitted changes.

## Process

1. Run `git status && git diff HEAD && git status --porcelain` to see what files are uncommitted.
2. **Keep the architecture doc current (before staging).** If the change alters the architecture
   (new/renamed/removed modules, changed data model, altered algorithm or component
   responsibilities) **or** realizes/changes a `DEC-xxx`:
   - Update `docs/architecture.md` to match, and add a dated row to its **Change Log**
     (`date | change | refs (ticket · DEC)`).
   - **Update the Confluence mirror in lockstep** — page `917506` ("Second Opinion — Architecture
     (HLD & LLD)"): fetch it (`getConfluencePage`, `contentFormat: html`), apply the same change +
     Change-Log row, and `updateConfluencePage` with a version message naming the ticket/DEC.
     Preserve existing markup and any `data-local-id`s.
   - If a new decision was made, ensure it's in the **Decision Log** first (per CLAUDE.md), then
     reference it. Stage the doc change with the rest of the commit.
   - If the change is purely internal (no architecture/decision impact), skip this step.
3. Add the untracked and changed files.
4. Write an atomic commit message with an appropriate, descriptive summary. Reference the
   `DEC-xxx` it implements (or `Decisions: none`) when it touches `backend/app/**` or
   `frontend/src/**` (the commit-msg hook enforces this).
5. Add a tag such as `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, etc. that reflects our work.

## Output

A single commit containing all uncommitted changes, with a conventional-commit-style message
(`<tag>: <atomic description>`) that accurately reflects the work done.

After the commit succeeds, print two clearly labelled summaries:

### What Changed
One short paragraph (3–6 sentences) describing the feature/fix/refactor that was committed — what problem it solves and what files were the key touch points. Write for a developer skimming the git log.

### AI Layer Changes
Only include this section if any files under `.claude/` were modified or added (CLAUDE.md, `.claude/references/`, `.claude/skills/`, `.claude/agents/`, etc.).

List each changed AI-layer file with a one-line note on what evolved and why. If nothing in `.claude/` changed, omit this section entirely.

### Architecture Sync
Only include this section if `docs/architecture.md` was updated. State what changed in the doc,
confirm the Confluence mirror (page `917506`) was updated to match (with its new version number),
and list the Change-Log row added. If no architecture/decision change was involved, omit this section.
