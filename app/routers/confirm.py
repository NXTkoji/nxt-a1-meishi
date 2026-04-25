from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.auth import verify_api_key

router = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_api_key)])


@router.post("/confirm")
async def confirm_card_deprecated():
    """
    Removed: automatic sync on confirm is no longer supported.
    Use POST /api/v2/export to export cards explicitly.
    """
    return JSONResponse(
        status_code=410,
        content={"detail": "Auto-sync on confirm has been removed. Use POST /api/v2/export."},
    )
