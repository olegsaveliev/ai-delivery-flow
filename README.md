# AI Delivery Flow

Full-stack AI application.

- **`frontend/`** — React + TypeScript (Vite)
- **`backend/`** — Python FastAPI service (Anthropic Claude integration)
- **`docs/`** — architecture and design notes

## Prerequisites

- Node.js 20+
- Python 3.11+

## Quick start

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # add your ANTHROPIC_API_KEY
uvicorn app.main:app --reload # http://localhost:8000
```

### Frontend

```bash
cd frontend
npm install
cp .env.example .env          # points at http://localhost:8000 by default
npm run dev                   # http://localhost:5173
```

## Project layout

```
ai-delivery-flow/
├── frontend/          # React + Vite SPA
│   └── src/
│       ├── api/       # backend client
│       ├── components/
│       ├── hooks/
│       ├── pages/
│       └── types/
├── backend/           # FastAPI app
│   └── app/
│       ├── api/routes/
│       ├── core/      # config, settings
│       ├── schemas/   # pydantic models
│       └── services/  # LLM + business logic
└── docs/
```

## How we work — the AI delivery flow

Work goes through Claude Code skills (`.claude/skills/`) with a **"Claude proposes, you approve"**
model. Everything is traceable along this chain:

`Story (Jira) → Epic (Jira) → PRD (Confluence) → Decision Log (DEC-xxx) → Architecture (docs/architecture.md) → Design docs`

The **Decision Log** (ADRs, `DEC-001`, `DEC-002`, …) is the context spine. See `CLAUDE.md` for the
mandatory rules.

### One-time setup

```bash
git config core.hooksPath .githooks   # activates the commit-msg hook (requires a "Decisions:" trailer)
```

The Atlassian MCP (Jira project **KAN** + Confluence) is configured in `.mcp.json`. It needs a
one-time OAuth login.

### Starting from scratch (new product or feature area)

**PM phase**
1. **Discuss** the idea with Claude: problem, users, scope, constraints.
2. **`/create-prd`** formalizes the discussion into a PRD (local `docs/` copy + Confluence).
3. **Log decisions** by adding the key architectural and product choices to the Decision Log as `DEC-xxx`.
4. **Create epics**: there is no skill for this. Ask Claude in plain words, e.g.
   `create EPIC-F <name> in KAN, governed by DEC-00X`. Claude proposes the description, and
   once you approve, creates the Jira epic. Each epic description links the PRD, the Decision Log
   and the design doc, and lists its governing DECs. Then sync the new epic and its Jira key into
   the PRD (Confluence + local copy).
5. **Approval gate**: review and approve the PRD and epics before any stories are written.

**Dev phase, per epic**
6. **`/spec <confluence-page-id | doc-path> KAN-<epic>`** slices the epic into stories. It writes
   `docs/specs/epic-*.md`, publishes a Confluence child page of the PRD, and creates the Jira
   stories under the epic.

**Build phase, per story (the PIV loop)**
7. `/prime KAN-xx` → `/plan-feature …` → `/execute <plan>` → `/validate` → `/code-review` → `/commit`

### Skills reference

| Skill | What it does | Usage |
|---|---|---|
| **create-prd** | Turns the discussed requirements into a PRD | `/create-prd [output-filename]` |
| **spec** | Slices an epic or PRD into stories with a dependency graph, writes them to `docs/specs/`, publishes to Confluence, and creates the Jira stories under the epic | `/spec <confluence-id or doc-path> [KAN-epic]` |
| **prime** | Run at task start. Loads story → epic → PRD → Decision Log and the architecture doc, and states which DECs apply | `/prime KAN-12 [confluence-ids]` |
| **plan-feature** | Deep codebase analysis → one-pass implementation plan that cites the DECs it honors | `/plan-feature <feature description>` |
| **execute** | Implements a plan task by task, validating each step | `/execute <path-to-plan>` |
| **design-sync** | Syncs one UI component between Claude Design and `frontend/src/components` (DEC-010) | `/design-sync <Component> --pull\|--push` |
| **validate** | Pre-PR quality gate: tests, type-check, lint, build (✅/❌) | `/validate` |
| **code-review** | Reviews changes for bugs, security issues and DEC conflicts, and writes a report | `/code-review` |
| **commit** | Atomic conventional commit with the `Decisions:` trailer. Updates the architecture doc and Decision Log when needed | `/commit` |

### Decision rules (short version)

- Before planning, state which DECs constrain the task.
- Never silently contradict an Accepted DEC. Raise a new **Proposed** `DEC-xxx` instead.
- Never edit a DEC in place. Mark it `Superseded` and add a new DEC that links back to it.
- Commits touching `backend/app/**` or `frontend/src/**` need `Decisions: DEC-xxx` or `Decisions: none`.

## Status

🚧 Early development.
