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

## Status

🚧 Early development.
