from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import chat, debates, health
from app.core.config import get_settings
from app.db.session import init_db

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev auto-create of tables on startup (DEC-008). Idempotent.
    init_db()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(debates.router)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": settings.app_name, "docs": "/docs"}
