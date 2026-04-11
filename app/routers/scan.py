"""
v1 scan router — superseded by /api/v2/sessions.
Kept as a stub so existing clients receive a clear deprecation notice.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.auth import verify_api_key

router = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_api_key)])


@router.post("/scan")
async def scan_card_deprecated():
    return JSONResponse(
        status_code=410,
        content={"detail": "POST /api/v1/scan is deprecated. Use POST /api/v2/sessions instead."},
    )
