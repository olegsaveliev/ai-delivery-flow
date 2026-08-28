# Architecture

## Overview

AI Delivery Flow is a full-stack application:

```
┌────────────┐        HTTP/JSON        ┌────────────┐        ┌──────────────┐
│  Frontend  │  ───────────────────▶   │  Backend   │  ───▶  │ Anthropic API │
│ React+Vite │                         │  FastAPI   │        │   (Claude)    │
└────────────┘                         └────────────┘        └──────────────┘
```

## Backend

- `app/main.py` — FastAPI app, CORS, router registration
- `app/core/config.py` — settings from environment (`.env`)
- `app/api/routes/` — endpoint modules (`health`, `chat`)
- `app/schemas/` — Pydantic request/response models
- `app/services/llm.py` — Anthropic Claude wrapper

## Frontend

- `src/api/client.ts` — typed fetch client for the backend
- `src/types/` — shared TypeScript types
- `src/components/`, `src/pages/`, `src/hooks/` — UI building blocks

## Conventions

- Keep request/response shapes mirrored between `backend/app/schemas` and `frontend/src/types`.
- Secrets live only in `.env` (never committed).
