---
name: spec
description: Slice a stable PRD or epic doc into PIV-sized, testable tickets with dependencies and execution order. Accepts a Confluence page id OR a local PRD/epic doc path; optionally a Jira epic key. Writes the breakdown to docs/specs/, publishes it as a child page of the PRD when the source is Confluence, and creates missing Jira issues under the epic when a key is passed — never duplicating existing ones.
argument-hint: "[confluence-page-id OR local-doc-path] [optional-jira-epic-key]"
---

# /spec — Turn a PRD into PIV-Sized Tickets

Turn a stable PRD into tickets that each fit one Plan–Implement–Validate
(PIV) cycle. Preserve the PRD's scope; do not rewrite it or implement code.

**Where it fits:** run in a fresh session after `/pickup`, once the PRD is
stable (draft the PRD in a separate session). Each resulting ticket then
enters its own PIV loop: `/pickup` → `/plan-feature` → `/execute`.

## Inputs

- `$1` (required): a Confluence page id or local PRD/epic document path.
  All digits → Confluence page id; otherwise → local path.
- `$2` (optional): a Jira epic key under which to create the tickets.

A Confluence source requests publication of a breakdown child page. A Jira
epic key requests creation of missing issues.

## 1. Load Context

- Read applicable repository instructions and the complete source document.
- For Confluence: `mcp__atlassian__getAccessibleAtlassianResources` for the
  `cloudId` (do not pick arbitrarily if several sites match), then
  `mcp__atlassian__getConfluencePage` with `contentFormat: "markdown"`.
  Record the page content, title, version, space, and URL.
- If `$2` is supplied: read it with `mcp__atlassian__getJiraIssue`, then
  fetch all pages of its children with `mcp__atlassian__searchJiraIssuesUsingJql`
  (`parent = <epic-key>`), including descriptions and status.
- Load the **Decision Log** (see CLAUDE.md) and note which `DEC-xxx` constrain
  this epic. If a slice would contradict an Accepted decision, do not
  silently deviate — mark it blocked and propose a new `DEC-xxx`.
- Reuse codebase context from `/pickup` when available. Otherwise inspect
  relevant docs (including `docs/architecture.md`), implementation, and
  tests to ground the decomposition.
- Read any existing breakdown for this source before creating a new one.

If the source cannot be read, report the blocker instead of inventing a
breakdown. If an integration is unavailable, complete independent local
work and report what remains blocked.

Treat the PRD as the authority for intended product scope and the code as
evidence of current implementation. If the Jira epic and the PRD conflict,
the PRD wins. Flag contradictions and missing requirements. Do not silently
resolve product decisions or invent scope; continue with unaffected slices
and mark blocked ones.

## 2. Decompose the Work

Each ticket should:

- Deliver one coherent outcome, preferably a vertical slice of behavior.
- Have explicit scope and observable acceptance criteria.
- Include a focused validation approach.
- Be small enough to plan, implement, and validate as one focused change.

Split independently useful outcomes or unrelated changes. Do not size
work by plan length or promise fixed execution times. Use prerequisite
or investigation tickets when a contract or unknown must be resolved first.
Avoid creating work already completed by existing issues or implementation;
identify any remaining gap instead.

Assign stable local IDs such as SPEC-01. Preserve IDs on reruns and do not
reuse retired IDs for different work.

## 3. Map Dependencies

State each ticket's prerequisites and arrange tickets into execution waves.
The dependency graph must have no cycles; resolve cycles by changing the
slices or extracting a shared prerequisite.

Mark tickets parallel-ready only when they do not depend on each other's
output and required shared contracts are settled. Note likely file overlap
or migration conflicts separately. Different files alone do not establish
independence.

## 4. Save the Breakdown

Write `docs/specs/<epic-slug>.md`. Reuse the existing path for the same
source and preserve manual notes and existing external mappings.

Use this structure:

```markdown
# Spec: <epic name>

## Source and goal
- PRD: <path or URL; version if available>
- Jira epic: <key and URL, if supplied>
- Breakdown page: <ID and URL, once published>
- Constraining decisions: <DEC-xxx list, or none>
- Goal: <2–3 sentences>

## Tickets

### SPEC-01 — <outcome>
- Outcome and scope:
- Acceptance criteria:
- Validation:
- Likely files/components: <estimate, or unknown>
- Decisions: <DEC-xxx this ticket honors, or none>
- Depends on: <none or stable ticket IDs>
- Open questions or blockers: <omit if none>
- Jira issue: <key and URL once linked; existing/new/pending/blocked>

## Dependencies and execution order
<Compact dependency list or Mermaid graph, plus execution waves.>
<Note likely integration conflicts where relevant.>

## Gaps and assumptions
<Unresolved decisions, source conflicts, or coverage limitations.>

## Publication status
<Confluence and Jira results, including any pending actions.>
```

Check that in-scope requirements are covered, acceptance criteria are
verifiable, and dependency references resolve. Do not file tickets whose
scope depends on an unresolved product decision; keep them marked blocked.

## 5. Publish to Confluence

If the source is a Confluence page:

- Publish the breakdown as a child of that source page, in its space,
  titled `Spec: <epic name> - Ticket Breakdown`.
- Use a saved breakdown page ID when available and verify its parent.
  Otherwise search for a matching child under the exact source page
  (`mcp__atlassian__getConfluencePageDescendants` or
  `mcp__atlassian__searchConfluenceUsingCql`). Do not update a page solely
  because its title matches elsewhere.
- Create the page with `mcp__atlassian__createConfluencePage` (`parentId` =
  the PRD page id) if absent. Before updating with
  `mcp__atlassian__updateConfluencePage`, read its latest content and
  version, preserve manual additions, and use the required version field.
  Report conflicting edits instead of overwriting them blindly.
- Save the resulting page ID and URL in the local breakdown.

For local sources, skip Confluence publication and say so. Do not invent a
destination. The repo copy stays the source the PIV loop reads; Confluence
is the shareable view.

## 6. Create Missing Jira Issues

If a Jira epic key was supplied (this step is then required):

- Confirm its project, supported issue types, and parent field
  (`mcp__atlassian__getJiraProjectIssueTypesMetadata` if unsure).
- Reconcile each slice against saved issue mappings and all existing epic
  children. Compare scope and acceptance criteria, not just titles.
- Reuse matching issues and record their keys and URLs. If coverage is
  partial or ambiguous, document the gap rather than creating a duplicate
  or silently changing the existing issue.
- Create missing, unblocked tickets under the epic with
  `mcp__atlassian__createJiraIssue`, using Story (or Task for pure chores)
  as supported by the project.
- Include scope, acceptance criteria, validation, decisions, and
  dependencies in each description. Include a stable source reference and
  local ticket ID so the issue can be recognized on a later run.
- Save each returned issue key and URL to the local breakdown immediately.

After creation, resolve dependency references to actual Jira keys. Add
Jira dependency links (`mcp__atlassian__createIssueLink`) when supported,
checking for existing links first. Update descriptions on newly created
issues as needed; preserve existing issue content and report any links
that could not be added.

If no epic key was supplied, keep the tickets local and tell the user which
epic key to pass to file them.

## 7. Reconcile and Report

Update the local breakdown with issue mappings, dependency links, and
publication results. Refresh the Confluence copy with the final mappings
when available, preserving manual content.

If a write fails or its result is uncertain, check whether it succeeded
before retrying. Do not blindly repeat creates. Preserve completed work,
continue independent steps, and leave precise pending actions so a rerun
can resume. Never claim publication or issue creation without confirmation.

Report concisely:

- Local breakdown path and Confluence link, if published.
- Tickets created, linked to existing issues, or blocked, with Jira links.
- Execution waves, constraining decisions, and unresolved questions.
- Any failed or skipped publication steps and what remains to be done.
- Next step: pick the first wave ticket and run `/pickup <key>`.
