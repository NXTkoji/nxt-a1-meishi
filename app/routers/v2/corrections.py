"""
Corrections router — log user corrections for few-shot learning.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_api_key
from app.db.session import get_db
from app.schemas.api import CorrectionCreate, CorrectionOut
from app.services.correction_store import log_correction

router = APIRouter(
    prefix="/api/v2/corrections",
    tags=["corrections"],
    dependencies=[Depends(verify_api_key)],
)


@router.post("", response_model=CorrectionOut, status_code=status.HTTP_201_CREATED)
async def create_correction(body: CorrectionCreate, db: AsyncSession = Depends(get_db)):
    correction = await log_correction(
        db,
        card_id=body.card_id,
        field_path=body.field_path,
        claude_value=body.claude_value,
        user_value=body.user_value,
        correction_type=body.correction_type,
        card_image_hash=body.card_image_hash,
    )
    return correction
