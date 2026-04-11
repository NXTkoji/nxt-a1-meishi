import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.routers import scan, confirm, contacts
from app.routers.v2 import (
    sessions as v2_sessions,
    cards as v2_cards,
    persons as v2_persons,
    organizations as v2_organizations,
    occasions as v2_occasions,
    corrections as v2_corrections,
    settings as v2_settings,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _run_migrations() -> None:
    """Apply any pending Alembic migrations synchronously at startup."""
    cfg = AlembicConfig("alembic.ini")
    alembic_command.upgrade(cfg, "head")
    logger.info("Database migrations up to date.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run Alembic in a thread — asyncio.run() inside env.py conflicts with uvicorn's loop
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _run_migrations)
    yield


app = FastAPI(
    title="NXT-A1 名片整理器",
    description="Business card scanner API powered by Claude Vision",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(scan.router)
app.include_router(confirm.router)
app.include_router(contacts.router)

# v2 routers
app.include_router(v2_sessions.router)
app.include_router(v2_cards.router)
app.include_router(v2_persons.router)
app.include_router(v2_organizations.router)
app.include_router(v2_occasions.router)
app.include_router(v2_corrections.router)
app.include_router(v2_settings.router)


@app.get("/api/v1/health")
async def health():
    return {"status": "ok"}


@app.get("/api/v2/images/{image_path:path}")
async def serve_permanent_image(image_path: str):
    """Serve a permanent card image by its relative path (e.g. {card_ext_id}/0.jpg)."""
    from app.config import settings
    path = settings.images_path / image_path
    if not path.exists():
        raise HTTPException(404, "Image not found")
    return FileResponse(str(path))


# ── Serve built React frontend ────────────────────────────────────────────────
# Serves frontend/dist as static files; SPA fallback for client-side routes.

_FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"

if _FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        index = _FRONTEND_DIST / "index.html"
        return FileResponse(str(index), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
