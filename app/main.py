import asyncio
import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from app.routers import scan, confirm, contacts
from app.routers.v2 import (
    sessions as v2_sessions,
    cards as v2_cards,
    persons as v2_persons,
    organizations as v2_organizations,
    occasions as v2_occasions,
    countries as v2_countries,
    corrections as v2_corrections,
    settings as v2_settings,
    export as v2_export,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _run_migrations() -> None:
    """Apply any pending Alembic migrations synchronously at startup."""
    cfg = AlembicConfig("alembic.ini")
    alembic_command.upgrade(cfg, "head")
    logger.info("Database migrations up to date.")


async def _sweep_stale_sessions() -> None:
    """
    Age out scan sessions the user walked away from. Never block startup on
    housekeeping — log and carry on if it fails.
    """
    from app.db.engine import AsyncSessionLocal
    from app.services.session_janitor import sweep_stale_sessions

    try:
        async with AsyncSessionLocal() as db:
            await sweep_stale_sessions(db)
    except Exception:
        logger.exception("Stale-session sweep failed; continuing startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run Alembic in a thread — asyncio.run() inside env.py conflicts with uvicorn's loop
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _run_migrations)
    await _sweep_stale_sessions()
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
app.include_router(v2_countries.router)
app.include_router(v2_corrections.router)
app.include_router(v2_settings.router)
app.include_router(v2_export.router)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError):
    """
    A violated database constraint is a conflict, not an unknown server fault.
    Without this the client gets a bare "500 Internal Server Error" with no clue
    which constraint failed — see the confirm/card_sides duplicate-side_order bug.
    """
    logger.exception("Integrity error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=409,
        content={"detail": f"Database constraint violated: {exc.orig}"},
    )


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
        # Root-level files in dist/ (favicon.svg, icons.svg, ...) live outside the /assets
        # mount, so without this branch they fall through to the SPA fallback below and are
        # returned as index.html — which is why the browser tab showed no favicon.
        # index.html itself is excluded so it keeps the no-cache headers set below.
        if full_path and full_path != "index.html":
            candidate = (_FRONTEND_DIST / full_path).resolve()
            # Reject ../ traversal that would escape dist/.
            if candidate.is_relative_to(_FRONTEND_DIST.resolve()) and candidate.is_file():
                media_type, _ = mimetypes.guess_type(candidate.name)
                return Response(content=candidate.read_bytes(),
                                media_type=media_type or "application/octet-stream")

        # FileResponse uses aiofiles (async I/O) which fails on Google Drive FUSE.
        # Synchronous read_bytes() works reliably on Drive-backed paths.
        content = (_FRONTEND_DIST / "index.html").read_bytes()
        return Response(content=content, media_type="text/html",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
